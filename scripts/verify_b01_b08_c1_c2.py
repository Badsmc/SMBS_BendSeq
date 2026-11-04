#!/usr/bin/env python3
"""Diagnostic script for C1 (3D placement) and C2 (3D rigid transfer) validation.

Evaluates real SheetMetal model B01-B08 from FreeCAD:
- Path: /Users/badmc/Desktop/Новая папка/box.100out-50in-(ok) 2.FCStd
- Object: Body_Unfold (Label: 'Тело_Unfold')

Verifications performed:
1. 3D placement correctness on active bend line (strictly on machine X axis: Y=0, Z=0).
2. Sheet surface normal orientation (fixed panel reference normal maps to machine +Z).
3. Preservation of state of all prior completed bends.
4. Continuous rigid 3D transfer (SLERP + translation, Euclidean distance invariance).
5. Generation of structured JSON report.
"""
from __future__ import annotations

import json
import math
import os
import sys
import hashlib
from typing import Any, Dict, List

# Ensure FreeCAD library and local workspace are in sys.path
fc_lib_path = "/Applications/FreeCAD.app/Contents/Resources/lib"
if os.path.exists(fc_lib_path) and fc_lib_path not in sys.path:
    sys.path.insert(0, fc_lib_path)

cwd = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if cwd not in sys.path:
    sys.path.insert(0, cwd)

import FreeCAD
from freecad_smbs.commands.pressbrake_2d import build_part_session
from smbs_core.kinematics2d.workpiece_placement_3d import WorkpiecePlacement3D
from smbs_core.kinematics2d.workpiece_transfer_3d import build_bend_transfer, validate_rigid_3d
from smbs_core.kinematics2d.workpiece_placement_validation_3d import validate_workpiece_placement


DEFAULT_MODEL_PATH = "/Users/badmc/Desktop/\u041d\u043e\u0432\u0430\u044f \u043f\u0430\u043f\u043a\u0430/box.100out-50in-(ok) 2.FCStd"


def compute_build_id() -> str:
    path = os.path.join(cwd, "freecad_smbs", "commands", "pressbrake_2d.py")
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def run_diagnostics(model_path: str = DEFAULT_MODEL_PATH, output_json_path: str | None = None) -> Dict[str, Any]:
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Model file not found: {model_path}")

    doc = FreeCAD.openDocument(model_path)
    obj = None
    for o in doc.Objects:
        if getattr(o, "Label", "") == "\u0422\u0435\u043b\u043e_Unfold" or o.Name == "Body_Unfold":
            obj = o
            break
    if obj is None:
        raise RuntimeError("Could not find Body_Unfold / Тело_Unfold in document")

    session = build_part_session(doc, [obj])
    fold_state = session.fold_state
    ordered_bends = list(session.ordered_bends)
    panel_verts = session.panel_vertices_xy
    build_id = compute_build_id()

    report: Dict[str, Any] = {
        "project_version": "0.1.0",
        "build_id": build_id,
        "model_file": model_path,
        "object_name": obj.Name,
        "object_label": obj.Label,
        "fixed_panel": session.fixed_panel,
        "total_panels": len(session.graph.panels),
        "total_bends": len(ordered_bends),
        "kinematic_pose_halt_active": True,
        "viewer_25d_connected": False,
        "status": "PASS",
        "bend_verifications": [],
        "transfer_verifications": [],
        "summary": {}
    }

    # Reset fold angles to 0
    fold_state.reset_all()

    # Step-by-step verification of bends
    for i, rb in enumerate(ordered_bends):
        bid = rb.bend_id
        target_deg = rb.angle_deg
        bend_info = fold_state.bends[bid]

        bend_rec: Dict[str, Any] = {
            "bend_id": bid,
            "step_index": i + 1,
            "fixed_panel": bend_info.fixed_panel,
            "moving_panel": bend_info.moving_panel,
            "target_angle_deg": target_deg,
            "placement_3d": {},
            "surface_orientation": {},
            "state_preservation": {},
            "status": "PASS",
            "errors": []
        }

        # 1. 3D placement computation before this bend is folded
        try:
            placement = WorkpiecePlacement3D.compute_placement(fold_state, bid)
            rep = validate_workpiece_placement(fold_state, bid, placement, panel_verts)
            p0, p1, u_dir = WorkpiecePlacement3D.get_world_bend_axis(fold_state, bid)
            p0_m, p1_m = placement.apply(p0), placement.apply(p1)

            bend_rec["placement_3d"] = {
                "max_axis_error_mm": rep.max_axis_error_mm,
                "midpoint_error_mm": rep.midpoint_error_mm,
                "panel_count": rep.panel_count,
                "vertex_count": rep.vertex_count,
                "p0_machine": [round(c, 6) for c in p0_m],
                "p1_machine": [round(c, 6) for c in p1_m],
                "status": "PASS"
            }
        except Exception as e:
            bend_rec["status"] = "FAIL"
            bend_rec["errors"].append(f"Placement error: {e}")

        # 2. Sheet surface normal check (fixed panel surface normal maps to +Z_m)
        try:
            fixed_rot = fold_state.transforms[bend_info.fixed_panel].rotation
            norm_part = tuple(fixed_rot[j][2] for j in range(3))
            norm_mach = tuple(
                sum(placement.rotation[r][c] * norm_part[c] for c in range(3))
                for r in range(3)
            )
            dist_to_z = math.dist(norm_mach, (0.0, 0.0, 1.0))
            bend_rec["surface_orientation"] = {
                "machine_normal": [round(c, 6) for c in norm_mach],
                "deviation_from_plus_z": dist_to_z,
                "status": "PASS" if dist_to_z < 1e-6 else "FAIL"
            }
            if dist_to_z >= 1e-6:
                bend_rec["status"] = "FAIL"
                bend_rec["errors"].append(f"Surface normal deviation too high: {dist_to_z}")
        except Exception as e:
            bend_rec["status"] = "FAIL"
            bend_rec["errors"].append(f"Orientation error: {e}")

        # 3. Fold active bend to target angle
        fold_state.set_angle(bid, target_deg)

        # 4. State preservation of all prior bends
        preserved = True
        prior_details = {}
        for prev_j in range(i + 1):
            prev_rb = ordered_bends[prev_j]
            act_ang = fold_state.angles[prev_rb.bend_id]
            exp_ang = prev_rb.angle_deg
            diff = abs(act_ang - exp_ang)
            prior_details[prev_rb.bend_id] = {
                "expected_deg": exp_ang,
                "actual_deg": act_ang,
                "error_deg": diff
            }
            if diff > 1e-6:
                preserved = False
        bend_rec["state_preservation"] = {
            "all_preserved": preserved,
            "checked_bends_count": i + 1,
            "bends": prior_details
        }
        if not preserved:
            bend_rec["status"] = "FAIL"
            bend_rec["errors"].append("Prior bend state lost or altered during folding")

        report["bend_verifications"].append(bend_rec)
        if bend_rec["status"] != "PASS":
            report["status"] = "FAIL"

    # Step-by-step verification of rigid transfers between consecutive bends
    # Note: For each transfer B_i -> B_{i+1}, prior bends B_0..B_i are at completed target angles.
    fold_state.reset_all()
    for i in range(len(ordered_bends) - 1):
        b_curr = ordered_bends[i]
        b_next = ordered_bends[i+1]

        # Fold current bend to its target before transferring to next
        fold_state.set_angle(b_curr.bend_id, b_curr.angle_deg)

        transfer_rec: Dict[str, Any] = {
            "from_bend": b_curr.bend_id,
            "to_bend": b_next.bend_id,
            "step_index": i + 1,
            "angular_displacement_deg": 0.0,
            "translation_distance_mm": 0.0,
            "duration_s": 0.0,
            "isometry_invariant": True,
            "max_distance_distortion_mm": 0.0,
            "rigid_frames_valid": True,
            "status": "PASS",
            "errors": []
        }

        try:
            transfer = build_bend_transfer(
                fold_state,
                b_curr.bend_id,
                b_next.bend_id,
                max_linear_mm_s=250.0,
                max_angular_deg_s=90.0,
                support_plane_zm=0.0
            )
            transfer_rec["angular_displacement_deg"] = transfer.angular_displacement_deg
            transfer_rec["translation_distance_mm"] = transfer.translation_distance_mm
            transfer_rec["duration_s"] = transfer.duration_s

            # Verify intermediate frame rigidity and distance invariance (Euclidean isometry)
            pA_part = (0.0, 0.0, 0.0)
            pB_part = (100.0, 50.0, 0.0)
            d_initial = math.dist(pA_part, pB_part)
            max_dist_err = 0.0

            for frame_idx in range(11):
                t_frame = (frame_idx / 10.0) * transfer.duration_s
                pose_frame = transfer.pose_at(t_frame)
                validate_rigid_3d(pose_frame)

                pA_m = pose_frame.apply(pA_part)
                pB_m = pose_frame.apply(pB_part)
                d_m = math.dist(pA_m, pB_m)
                err = abs(d_m - d_initial)
                if err > max_dist_err:
                    max_dist_err = err

            transfer_rec["max_distance_distortion_mm"] = max_dist_err
            if max_dist_err > 1e-6:
                transfer_rec["isometry_invariant"] = False
                transfer_rec["status"] = "FAIL"
                transfer_rec["errors"].append(f"Euclidean distance changed during transfer: {max_dist_err}")

        except Exception as e:
            transfer_rec["status"] = "FAIL"
            transfer_rec["rigid_frames_valid"] = False
            transfer_rec["errors"].append(f"Transfer construction error: {e}")

        report["transfer_verifications"].append(transfer_rec)
        if transfer_rec["status"] != "PASS":
            report["status"] = "FAIL"

    report["summary"] = {
        "bends_passed": sum(1 for b in report["bend_verifications"] if b["status"] == "PASS"),
        "bends_total": len(report["bend_verifications"]),
        "transfers_passed": sum(1 for t in report["transfer_verifications"] if t["status"] == "PASS"),
        "transfers_total": len(report["transfer_verifications"]),
        "overall_status": report["status"]
    }

    if output_json_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_json_path)), exist_ok=True)
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

    return report


if __name__ == "__main__":
    out_file = os.path.join(cwd, "docs", "reports", "b01_b08_c1_c2_validation.json")
    print(f"Running C1/C2 diagnostic on real SheetMetal model: {DEFAULT_MODEL_PATH}")
    res = run_diagnostics(output_json_path=out_file)
    print("=" * 60)
    print(f"Overall Status: {res['status']}")
    print(f"Bends Verified: {res['summary']['bends_passed']} / {res['summary']['bends_total']}")
    print(f"Transfers Verified: {res['summary']['transfers_passed']} / {res['summary']['transfers_total']}")
    print(f"Report saved to: {out_file}")
    print("=" * 60)
    if res["status"] != "PASS":
        sys.exit(1)

