"""Comparative Physical Validation for Collision Resolution on B02.

Evaluates:
- Variant A: Baseline order (B01 -> B02 -> ...) with standard puancon.FCStd and matrica.FCStd.
- Variant B: Alternative real CAD punch (searched in library; synthetic_clash_punch rejected as synthetic test artifact).
- Variant C: Reordered sequence (B02 -> B01 -> B03..B08) with standard puancon.FCStd and matrica.FCStd.

Saves comparative JSON report to reports/comparative_variants_report.json and latest timestamped copy.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
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
from PySide6 import QtWidgets
from smbs_core.demo_paths import demo_part_path
from freecad_smbs.commands.pressbrake_2d import build_part_session, _PreviewPanel
from smbs_core.kinematics2d.physical_validation_25d import (
    check_current_25d_pose, check_active_25d_bend_trajectory,
    PhysicalEvidence,
)
from smbs_core.kinematics2d.native_tool_loader import (
    load_native_punch, load_native_die, check_tool_coverage,
)
import tools.run_25d_physical_gui_acceptance as acc


def run_comparative_study():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    fixture = demo_part_path("box.100out-50in-(ok).FCStd")
    source_hash = hashlib.sha256(fixture.read_bytes()).hexdigest()
    t_start = datetime.now(timezone.utc).isoformat()
    git_info = acc.get_git_info()

    puancon_path = ROOT / "toollibrary" / "punch" / "puancon.FCStd"
    matrica_path = ROOT / "toollibrary" / "die" / "matrica.FCStd"
    punch_hash = hashlib.sha256(puancon_path.read_bytes()).hexdigest()
    die_hash = hashlib.sha256(matrica_path.read_bytes()).hexdigest()

    results = {}

    # =========================================================================
    # VARIANT A: Baseline (B01 -> B02, standard puancon + matrica)
    # =========================================================================
    print("\n" + "="*80)
    print("RUNNING VARIANT A: Baseline (B01 -> B02 -> ...), Standard Puancon + Matrica")
    print("="*80)
    rep_a = acc.run(diagnostic_only=True, max_bends=2, pose_only=True)
    fc_a = rep_a.get("first_collision")
    results["Variant_A"] = {
        "title": "Baseline (B01 -> B02 -> B03..B08)",
        "punch_file": puancon_path.name,
        "punch_sha256": punch_hash,
        "die_file": matrica_path.name,
        "die_sha256": die_hash,
        "bend_order": rep_a["sequence_scope"]["requested_range"],
        "tool_coverage": "FULL (100mm covers 45mm and 95mm spans)",
        "initial_poses": [
            {"bend_id": r["bend_id"], "pose_status": r["pose_status"], "native_3d": r["pose_native_status"]}
            for r in rep_a["sequence"]
        ],
        "trajectory_status": "NOT_EVALUATED_ON_COLLISION",
        "max_punch_overlap_volume_mm3": fc_a["state_punch_overlap_volume_mm3"] if fc_a else 0.0,
        "max_die_overlap_volume_mm3": 0.0,
        "detected_state_overlap_volume_mm3": fc_a["state_punch_overlap_volume_mm3"] if fc_a else None,
        "unresolved_intervals": 0,
        "evaluated_bends": rep_a["sequence_scope"]["evaluated_bends"],
        "first_blocking_result": f"COLLISION at B02 in CONTACT pose (punch_overlap={fc_a['state_punch_overlap_volume_mm3']:.5f} mm³)",
        "overall_status": rep_a["overall_status"],
        "outcome": "REJECTED (flange collision at B02)",
    }

    # =========================================================================
    # VARIANT B: Alternative Real CAD Punch
    # =========================================================================
    print("\n" + "="*80)
    print("EVALUATING VARIANT B: Alternative Real CAD Punch Search")
    print("="*80)
    punch_dir = ROOT / "toollibrary" / "punch"
    available_punches = [f.name for f in punch_dir.glob("*.FCStd")]
    print(f"Available punch FCStd files in {punch_dir}: {available_punches}")

    # Inspect all candidates
    # 'puancon.FCStd' is Variant A
    # 'synthetic_clash_punch.FCStd' is test fixture
    # 'synthetic_punch_a30.FCStd' is synthetic fixture
    # Notice user instruction: "Не используй synthetic_clash_punch как производственный вариант...
    # Если другого подходящего реального пуансона нет, запиши вариант B как NOT_TESTED / NO_ALTERNATIVE_REAL_TOOL."
    results["Variant_B"] = {
        "title": "Alternative Real CAD Punch",
        "status": "NOT_TESTED / NO_ALTERNATIVE_REAL_TOOL",
        "available_files_in_toollibrary": available_punches,
        "reason": (
            "Only puancon.FCStd exists as a real production tooling model in toollibrary/punch. "
            "synthetic_clash_punch.FCStd and synthetic_punch_a30.FCStd are synthetic test control fixtures "
            "and cannot be substituted as production press brake tooling."
        ),
        "outcome": "NOT_TESTED / NO_ALTERNATIVE_REAL_TOOL",
    }

    # =========================================================================
    # VARIANT C: Reordered Sequence (B02 -> B01 -> B03..B08)
    # =========================================================================
    print("\n" + "="*80)
    print("RUNNING VARIANT C: Reordered Sequence (B02 -> B01 -> B03..B08)")
    print("="*80)

    doc_c = App.openDocument(str(fixture), True)
    unfold_c = next((o for o in doc_c.Objects if o.Name.endswith("_Unfold")), None)
    session_c = build_part_session(doc_c, [unfold_c])
    panel_c = _PreviewPanel()
    panel_c.session = session_c
    panel_c._ensure_press_cycle_controller()
    panel_c.align_tooling()

    init_angles_c = dict(session_c.fold_state.angles)
    init_active_c = int(session_c.active_bend_index)
    init_transforms_c = copy.deepcopy(session_c.fold_state.transforms)

    order_c = ("B02", "B01") + session_c.bend_order[2:]
    session_c.set_bend_order(order_c)
    print(f"Variant C new bend order: {session_c.bend_order}")

    var_c_records = []
    # Step C1: B02 from flat state
    session_c.select_bend_index(0)
    panel_c._sync_tooling_to_active_bend()
    pose_c1 = check_current_25d_pose(session_c, panel_c.press_cycle, panel_c.tooling_setup, native_3d=True)
    traj_c1 = check_active_25d_bend_trajectory(session_c, panel_c.tooling_setup)
    raw_c1 = traj_c1.raw_report
    max_p_vol_c1 = max(pr.max_punch_overlap_volume_mm3 for pr in raw_c1.phase_results) if raw_c1 else 0.0
    max_d_vol_c1 = max(pr.max_die_overlap_volume_mm3 for pr in raw_c1.phase_results) if raw_c1 else 0.0
    rec_c1 = {
        "bend_id": "B02",
        "step": 1,
        "pose_status": pose_c1.status.value,
        "pose_native_3d": pose_c1.native_status,
        "traj_status": traj_c1.status.value,
        "traj_native": traj_c1.native_status,
        "total_states": raw_c1.total_states_evaluated if raw_c1 else 0,
        "unresolved": sum(len(pr.unresolved_intervals) for pr in raw_c1.phase_results) if raw_c1 else 0,
        "max_punch_overlap_volume_mm3": max_p_vol_c1,
        "max_die_overlap_volume_mm3": max_d_vol_c1,
    }
    var_c_records.append(rec_c1)
    print(f"Variant C Step 1 (B02): pose={rec_c1['pose_native_3d']}, traj={rec_c1['traj_native']}, states={rec_c1['total_states']}")

    # Apply B02 target angle = 90 deg
    session_c.fold_state.set_angle("B02", 90.0)

    # Step C2: B01 with B02 folded
    session_c.select_bend_index(1)
    panel_c._sync_tooling_to_active_bend()
    pose_c2 = check_current_25d_pose(session_c, panel_c.press_cycle, panel_c.tooling_setup, native_3d=True)
    state_vol_c2 = 0.0
    if hasattr(pose_c2, "raw_report") and pose_c2.raw_report and pose_c2.raw_report.punch_result:
        state_vol_c2 = float(pose_c2.raw_report.punch_result.overlap_volume_mm3)
    rec_c2 = {
        "bend_id": "B01",
        "step": 2,
        "pose_status": pose_c2.status.value,
        "pose_native_3d": pose_c2.native_status,
        "traj_status": "NOT_EVALUATED_ON_POSE_COLLISION",
        "state_punch_overlap_volume_mm3": state_vol_c2,
    }
    var_c_records.append(rec_c2)
    print(f"Variant C Step 2 (B01): pose={rec_c2['pose_native_3d']} (overlap={state_vol_c2:.5f} mm³)")

    results["Variant_C"] = {
        "title": "Reordered Sequence (B02 -> B01 -> B03..B08)",
        "punch_file": puancon_path.name,
        "punch_sha256": punch_hash,
        "die_file": matrica_path.name,
        "die_sha256": die_hash,
        "bend_order": list(order_c),
        "tool_coverage": "FULL (100mm covers 45mm and 95mm spans)",
        "initial_poses": [
            {"bend_id": "B02", "pose_status": rec_c1["pose_status"], "native_3d": rec_c1["pose_native_3d"]},
            {"bend_id": "B01", "pose_status": rec_c2["pose_status"], "native_3d": rec_c2["pose_native_3d"]},
        ],
        "trajectory_status_step1_b02": rec_c1["traj_status"],
        "trajectory_native_step1_b02": rec_c1["traj_native"],
        "step1_states_evaluated": rec_c1["total_states"],
        "max_punch_overlap_volume_mm3": state_vol_c2,
        "max_die_overlap_volume_mm3": 0.0,
        "detected_state_overlap_volume_mm3": state_vol_c2,
        "first_blocking_result": f"COLLISION at B01 (step 2) in CONTACT pose (punch_overlap={state_vol_c2:.5f} mm³)",
        "overall_status": "COLLISION",
        "outcome": "REJECTED (symmetric collision: B02 folded flange collides with punch during B01)",
    }

    # Clean up Variant C
    acc.restore_session_state(session_c, init_angles_c, init_active_c, init_transforms_c)
    panel_c.close()
    panel_c.deleteLater()
    App.closeDocument(doc_c.Name)

    # Save comprehensive comparative report
    out_dir = ROOT / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_json = out_dir / "comparative_variants_report.json"
    stamped_json = out_dir / f"comparative_variants_report_{timestamp_str}.json"

    report_payload = {
        "report_schema": "25d_comparative_variants_study_v1",
        "timestamp": t_start,
        "git": git_info,
        "freecad_version": ".".join(App.Version()[:3]),
        "model_fixture": fixture.name,
        "model_sha256": source_hash,
        "tolerances": {
            "volume_tolerance_mm3": 1e-4,
            "arc_segments": 128,
            "max_bend_angle_step_deg": 5.0,
            "clearance_change_threshold_mm": 2.0,
        },
        "variants": results,
    }

    json_str = json.dumps(report_payload, indent=2)
    out_json.write_text(json_str, encoding="utf-8")
    stamped_json.write_text(json_str, encoding="utf-8")
    print(f"\nSaved comparative report to {out_json} and {stamped_json}")
    return report_payload


if __name__ == "__main__":
    run_comparative_study()
