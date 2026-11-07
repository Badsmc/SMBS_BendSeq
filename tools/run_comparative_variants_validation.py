"""Comparative Physical Validation for Collision Resolution on B02.

Evaluates candidates dynamically using authoritative FreeCAD OCCT geometry and 2.5D kinematics.
All outcomes, statuses, and overlap volumes are computed strictly from the current execution
without pre-recorded strings or zero-volume substitutions when measurements are absent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _p in ("/Applications/FreeCAD.app/Contents/Resources/lib",):
    if os.path.exists(_p) and _p not in sys.path:
        sys.path.append(_p)

import FreeCAD as App
try:
    from PySide6 import QtWidgets
except ImportError:
    from PySide import QtWidgets
from smbs_core.demo_paths import demo_part_path
from freecad_smbs.commands.pressbrake_2d import build_part_session, _PreviewPanel
from smbs_core.kinematics2d.physical_validation_25d import (
    check_current_25d_pose, check_active_25d_bend_trajectory,
    PhysicalEvidence,
)
from smbs_core.kinematics2d.tooling_25d import ToolKind
import tools.run_25d_physical_gui_acceptance as acc


def evaluate_pose_and_traj(
    panel,
    session,
    active_idx: int,
    desired_midpoint_xm: float = 0.0,
    run_traj: bool = True,
) -> dict:
    """Evaluate stationary initial pose and optionally full 5-phase adaptive trajectory.

    Never substitutes 0.0 when a 3D overlap measurement is absent or unevaluated.
    """
    session.select_bend_index(active_idx)
    panel._sync_tooling_to_active_bend()
    bend_id = session.bend_order[active_idx]
    target_angle_deg = float(session.fold_state.bends[bend_id].angle_deg)

    pose_res = check_current_25d_pose(
        session, panel.press_cycle, panel.tooling_setup,
        native_3d=True, desired_midpoint_xm=desired_midpoint_xm,
    )
    pose_p_vol = None
    pose_d_vol = None
    if getattr(pose_res, "raw_report", None) is not None:
        cr = pose_res.raw_report
        if getattr(cr, "punch_result", None) is not None and cr.punch_result.overlap_volume_mm3 is not None:
            pose_p_vol = float(cr.punch_result.overlap_volume_mm3)
        if getattr(cr, "die_result", None) is not None and cr.die_result.overlap_volume_mm3 is not None:
            pose_d_vol = float(cr.die_result.overlap_volume_mm3)

    traj_res = None
    traj_native = "NOT_EVALUATED"
    states_count = 0
    unresolved_count = 0
    traj_max_p_vol = None
    traj_max_d_vol = None

    if run_traj and pose_res.status != PhysicalEvidence.COLLISION:
        traj_res = check_active_25d_bend_trajectory(
            session, panel.tooling_setup, desired_midpoint_xm=desired_midpoint_xm,
        )
        traj_native = traj_res.native_status
        raw = traj_res.raw_report
        if raw and hasattr(raw, "phase_results"):
            states_count = raw.total_states_evaluated
            unresolved_count = sum(len(pr.unresolved_intervals) for pr in raw.phase_results)
            if raw.phase_results:
                traj_max_p_vol = max(float(pr.max_punch_overlap_volume_mm3) for pr in raw.phase_results)
                traj_max_d_vol = max(float(pr.max_die_overlap_volume_mm3) for pr in raw.phase_results)

    measured_p = [v for v in (pose_p_vol, traj_max_p_vol) if v is not None]
    measured_d = [v for v in (pose_d_vol, traj_max_d_vol) if v is not None]
    max_p_vol = max(measured_p) if measured_p else None
    max_d_vol = max(measured_d) if measured_d else None

    traj_status_str = traj_res.status.value if traj_res is not None else "NOT_EVALUATED"

    has_collision = (
        pose_res.status == PhysicalEvidence.COLLISION
        or (traj_res is not None and traj_res.status == PhysicalEvidence.COLLISION)
    )
    has_unverified = (
        pose_res.status in (PhysicalEvidence.UNVERIFIED, PhysicalEvidence.SECTION_CLEAR_3D_UNVERIFIED)
        or (traj_res is not None and traj_res.status == PhysicalEvidence.UNVERIFIED)
    )
    is_step_qualified = (
        not has_collision
        and not has_unverified
        and run_traj
        and pose_res.status == PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS
        and traj_res is not None
        and traj_res.status == PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS
    )

    if has_collision:
        step_status = "COLLISION"
    elif is_step_qualified:
        step_status = "SAMPLED_3D_CLEAR_NOT_CONTINUOUS"
    else:
        step_status = "INCOMPLETE/UNRESOLVED"

    return {
        "bend_id": bend_id,
        "step": active_idx + 1,
        "target_angle_deg": target_angle_deg,
        "desired_midpoint_xm": float(desired_midpoint_xm),
        "pose_status": pose_res.status.value,
        "pose_native_3d": pose_res.native_status,
        "pose_punch_overlap_volume_mm3": pose_p_vol,
        "pose_die_overlap_volume_mm3": pose_d_vol,
        "traj_status": traj_status_str,
        "traj_native": traj_native,
        "trajectory_max_punch_overlap_volume_mm3": traj_max_p_vol,
        "trajectory_max_die_overlap_volume_mm3": traj_max_d_vol,
        "states_evaluated": states_count,
        "unresolved_intervals": unresolved_count,
        "punch_overlap_volume_mm3": max_p_vol,
        "die_overlap_volume_mm3": max_d_vol,
        "step_status": step_status,
        "is_step_qualified": is_step_qualified,
        "reason": pose_res.reason if pose_res.status == PhysicalEvidence.COLLISION else (traj_res.reason if traj_res else pose_res.reason),
    }


def summarize_variant_steps(steps: list[dict], run_traj: bool) -> dict:
    """Derive overall_status, first_blocking_result, and outcome strictly from evaluated step dicts."""
    has_collision = any(s["step_status"] == "COLLISION" for s in steps)
    all_qualified = bool(steps) and all(s["is_step_qualified"] for s in steps)

    first_blocking = None
    # Prioritize reporting an actual COLLISION if one occurred in the evaluated steps
    for s in steps:
        if s["step_status"] == "COLLISION":
            loc = "TRAJECTORY" if s["traj_status"] == "COLLISION" else "POSE"
            first_blocking = (
                f"COLLISION at {s['bend_id']} in {loc} "
                f"(punch_overlap={s['punch_overlap_volume_mm3']}, die_overlap={s['die_overlap_volume_mm3']})"
            )
            break
    if first_blocking is None:
        for s in steps:
            if not s["is_step_qualified"]:
                first_blocking = (
                    f"INCOMPLETE/UNRESOLVED at {s['bend_id']} "
                    f"(pose={s['pose_status']}, traj={s['traj_status']})"
                )
                break

    if has_collision:
        overall_status = "COLLISION"
        outcome = f"REJECTED ({first_blocking})"
    elif all_qualified and run_traj:
        overall_status = "SAMPLED_3D_CLEAR_NOT_CONTINUOUS"
        outcome = f"EVALUATED_STEPS_SAMPLED_CLEAR ({len(steps)} steps)"
    else:
        overall_status = "INCOMPLETE/UNRESOLVED"
        outcome = f"INCOMPLETE/UNRESOLVED ({first_blocking or 'trajectory_not_evaluated'})"

    return {
        "overall_status": overall_status,
        "first_blocking_result": first_blocking,
        "outcome": outcome,
    }


def run_comparative_study(*, run_traj: bool = False):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    fixture = demo_part_path("box.100out-50in-(ok).FCStd")
    source_hash = hashlib.sha256(fixture.read_bytes()).hexdigest()
    t_start = datetime.now(timezone.utc).isoformat()
    git_info = acc.get_git_info()

    puancon_path = ROOT / "toollibrary" / "punch" / "puancon.FCStd"
    matrica_path = ROOT / "toollibrary" / "die" / "matrica.FCStd"
    ap1017_path = ROOT / "toollibrary" / "punch" / "PUNCH_AP_1017_88°_H104,65_R0,8.FCStd"
    tdie_path = ROOT / "toollibrary" / "die" / "T-DIE_AT_3015_88°_V12_H80.FCStd"

    results = {}

    def get_session_and_panel():
        doc = App.openDocument(str(fixture), True)
        unfold = next(o for o in doc.Objects if o.Name.endswith("_Unfold"))
        session = build_part_session(doc, [unfold])
        panel = _PreviewPanel()
        panel.session = session
        panel._ensure_press_cycle_controller()
        return doc, session, panel

    def close_all(doc, panel):
        try:
            panel.close()
            panel.deleteLater()
        except Exception:
            pass
        try:
            App.closeDocument(doc.Name)
        except Exception:
            pass

    def eval_two_step_variant(title: str, p_path: Path, d_path: Path, shift_b01: float, shift_b02: float) -> dict:
        doc, session, panel = get_session_and_panel()
        try:
            if p_path.stem != "puancon":
                panel.select_punch(panel.tool_library.load_tool(p_path.stem, ToolKind.PUNCH))
            if d_path.stem != "matrica":
                panel.select_die(panel.tool_library.load_tool(d_path.stem, ToolKind.DIE))
            panel.align_tooling()
            session.fold_state.reset_all()

            steps = []
            r1 = evaluate_pose_and_traj(panel, session, 0, desired_midpoint_xm=shift_b01, run_traj=run_traj)
            steps.append(r1)

            # Advance to B02 only if B01 did not fail in strict mode (or if B01 pose was clear when run_traj=False)
            b01_blocked = (
                r1["pose_status"] != PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS.value
                or (run_traj and r1["traj_status"] != PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS.value)
            )
            if not b01_blocked:
                target_b01 = float(session.fold_state.bends["B01"].angle_deg)
                session.fold_state.set_angle("B01", target_b01)
                r2 = evaluate_pose_and_traj(panel, session, 1, desired_midpoint_xm=shift_b02, run_traj=run_traj)
                steps.append(r2)

            summary = summarize_variant_steps(steps, run_traj=run_traj)
            return {
                "title": title,
                "punch_file": p_path.name,
                "punch_sha256": hashlib.sha256(p_path.read_bytes()).hexdigest(),
                "die_file": d_path.name,
                "die_sha256": hashlib.sha256(d_path.read_bytes()).hexdigest(),
                "shifts_mm": {"B01": shift_b01, "B02": shift_b02},
                "run_traj": run_traj,
                "steps": steps,
                **summary,
            }
        finally:
            close_all(doc, panel)

    results["Variant_A"] = eval_two_step_variant(
        "Baseline (puancon + matrica, shift s=0)",
        puancon_path, matrica_path, 0.0, 0.0,
    )
    results["Variant_B"] = eval_two_step_variant(
        "Alternative Real Punch PUNCH_AP_1017 + Baseline Matrica (shift s=0)",
        ap1017_path, matrica_path, 0.0, 0.0,
    )
    results["Variant_C"] = eval_two_step_variant(
        "Baseline Puancon + New Die T-DIE_AT_3015 (shift s=0)",
        puancon_path, tdie_path, 0.0, 0.0,
    )
    results["Variant_D"] = eval_two_step_variant(
        "Compatible Set (PUNCH_AP_1017 + T-DIE_AT_3015, shift s=0)",
        ap1017_path, tdie_path, 0.0, 0.0,
    )
    results["Variant_E"] = eval_two_step_variant(
        "Compatible Set (PUNCH_AP_1017 + T-DIE_AT_3015) with Longitudinal Shift s=+2.4mm on B02",
        ap1017_path, tdie_path, 0.0, 2.4,
    )

    out_dir = ROOT / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_json = out_dir / "comparative_variants_report.json"
    stamped_json = out_dir / f"comparative_variants_report_{timestamp_str}.json"

    report_payload = {
        "report_schema": "25d_comparative_variants_study_v3",
        "timestamp": t_start,
        "git": git_info,
        "freecad_version": ".".join(App.Version()[:3]),
        "model_fixture": fixture.name,
        "model_sha256": source_hash,
        "run_traj": run_traj,
        "variants": results,
    }

    json_str = json.dumps(report_payload, indent=2)
    out_json.write_text(json_str, encoding="utf-8")
    stamped_json.write_text(json_str, encoding="utf-8")
    return report_payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-traj", action="store_true", help="Evaluate adaptive trajectory in addition to stationary pose")
    args = parser.parse_args()
    run_comparative_study(run_traj=args.run_traj)
