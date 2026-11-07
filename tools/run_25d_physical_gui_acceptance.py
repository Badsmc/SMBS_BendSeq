"""Native FreeCAD acceptance for the integrated 2.5D + D6 collision check.

In FreeCAD Python Console, after installing feature/25d-physical-validation
and the original 17 demo models:
    from tools.run_25d_physical_gui_acceptance import run
    run()

Validates the full bend sequence (B01-B08 for box) preserving prior completed
angles with real CAD tool bodies, writes JSON report, and outputs summary table.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(*, diagnostic_only=False, max_bends=None):
    import FreeCAD as App
    from PySide6 import QtWidgets
    from smbs_core.demo_paths import demo_part_path
    from smbs_core.kinematics2d.physical_validation_25d import (
        check_current_25d_pose, check_active_25d_bend_trajectory,
        PhysicalEvidence,
    )
    from smbs_core.kinematics2d.workpiece_placement_3d import WorkpiecePlacement3D
    from freecad_smbs.commands.pressbrake_2d import (
        _PreviewPanel, build_part_session,
    )

    is_live_gui = False
    try:
        import FreeCADGui as Gui
        is_live_gui = bool(
            getattr(App, "GuiUp", False)
            and Gui.getMainWindow() is not None
            and Gui.getMainWindow().isVisible()
        )
    except Exception:
        is_live_gui = False

    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv if sys.argv else [])
        app._smbs_headless = True
    print(
        "PHYSICAL_RUNNER_ENVIRONMENT:",
        "LIVE_FREECAD_GUI" if is_live_gui else "HEADLESS_OFFSCREEN (no FreeCADGui main window)",
    )
    fixture = demo_part_path("box.100out-50in-(ok).FCStd")
    source_hash = hashlib.sha256(fixture.read_bytes()).hexdigest()
    names_before = set(App.listDocuments())
    previously_active = getattr(getattr(App, "ActiveDocument", None), "Name", None)
    doc = None
    panel = None
    try:
        doc = App.openDocument(str(fixture), True)
        unfold = next(
            (o for o in doc.Objects if o.Name.endswith("_Unfold")),
            None,
        )
        if unfold is None:
            raise RuntimeError("ORIGINAL_UNFOLD_OBJECT_MISSING")
        session = build_part_session(doc, [unfold])
        panel = _PreviewPanel()
        panel.session = session
        panel._ensure_press_cycle_controller()
        panel.align_tooling()
        if not panel.tooling_setup.aligned:
            raise RuntimeError("TOOLING_NOT_ALIGNED:" + panel.status.text())
        initial = dict(session.fold_state.angles)

        # Dynamic bend sequence from session (not hardcoded)
        bend_order = list(session.bend_order)
        if max_bends is not None:
            bend_order = bend_order[:max_bends]
        total_bends_count = len(bend_order)

        punch_path = Path(panel.tooling_setup.punch.source_file)
        die_path = Path(panel.tooling_setup.die.source_file)
        punch_hash = hashlib.sha256(punch_path.read_bytes()).hexdigest()
        die_hash = hashlib.sha256(die_path.read_bytes()).hexdigest()

        print(f"SEQUENCE_VALIDATION_START: {total_bends_count} bends in order: {bend_order}")
        print(f"TOOLING_INFO: punch={punch_path.name} ({punch_hash[:16]}), die={die_path.name} ({die_hash[:16]})")

        sequence_records = []
        first_collision_info = None
        has_any_collision = False
        has_any_incomplete = False

        for idx, bend_id in enumerate(bend_order):
            session.select_bend_index(idx)
            bend = session.fold_state.bends[bend_id]
            target_ang = float(bend.angle_deg)
            prior_angles_record = {b: float(session.fold_state.angles[b]) for b in bend_order[:idx]}

            print(f"\n--- VALIDATING BEND {idx + 1}/{total_bends_count}: {bend_id} (target {target_ang:+.1f} deg) ---")
            print(f"Prior completed bends: {prior_angles_record}")

            # 1. Pose check at stationary initial contact
            pose_result = check_current_25d_pose(
                session, panel.press_cycle, panel.tooling_setup, native_3d=True,
            )
            print(f"  Pose check: status={pose_result.status.value}, 2D={pose_result.section_status}, 3D={pose_result.native_status}")

            # 2. Adaptive trajectory check across APPROACH, CONTACT, BENDING, BOTTOM, RETRACT
            traj_result = check_active_25d_bend_trajectory(
                session, panel.tooling_setup,
            )
            raw = traj_result.raw_report
            print(f"  Trajectory check: status={traj_result.status.value}, native={traj_result.native_status}")

            phase_records = []
            total_states = raw.total_states_evaluated if raw else 0
            total_unresolved = 0
            max_punch_vol = 0.0
            max_die_vol = 0.0

            if raw and hasattr(raw, "phase_results"):
                for pr in raw.phase_results:
                    unres_cnt = len(pr.unresolved_intervals)
                    total_unresolved += unres_cnt
                    max_punch_vol = max(max_punch_vol, pr.max_punch_overlap_volume_mm3)
                    max_die_vol = max(max_die_vol, pr.max_die_overlap_volume_mm3)
                    phase_records.append({
                        "phase": pr.phase_name,
                        "status": pr.evidence_status.value,
                        "states_evaluated": len(pr.states),
                        "unresolved_intervals_count": unres_cnt,
                        "elapsed_ms": pr.elapsed_ms,
                        "punch_overlap_volume_mm3": pr.max_punch_overlap_volume_mm3,
                        "die_overlap_volume_mm3": pr.max_die_overlap_volume_mm3,
                    })

            bend_summary = {
                "bend_index": idx,
                "bend_id": bend_id,
                "target_angle_deg": target_ang,
                "prior_completed_angles": prior_angles_record,
                "pose_status": pose_result.status.value,
                "pose_native_status": pose_result.native_status,
                "trajectory_status": traj_result.status.value,
                "trajectory_native_status": traj_result.native_status,
                "total_states_evaluated": total_states,
                "total_unresolved_intervals": total_unresolved,
                "max_punch_overlap_volume_mm3": max_punch_vol,
                "max_die_overlap_volume_mm3": max_die_vol,
                "phases": phase_records,
                "reason": traj_result.reason,
            }
            sequence_records.append(bend_summary)

            if traj_result.status == PhysicalEvidence.COLLISION or pose_result.status == PhysicalEvidence.COLLISION:
                has_any_collision = True
                if first_collision_info is None:
                    first_col_st = getattr(raw, "first_collision_state", None)
                    first_collision_info = {
                        "bend_id": bend_id,
                        "bend_index": idx,
                        "pose_native_status": pose_result.native_status,
                        "trajectory_native_status": traj_result.native_status,
                        "phase": first_col_st.phase if first_col_st else "UNKNOWN",
                        "path_u": first_col_st.path_u if first_col_st else None,
                        "angle_deg": first_col_st.signed_angle_deg if first_col_st else None,
                        "ram_z_mm": first_col_st.ram_z_mm if first_col_st else None,
                        "max_punch_overlap_volume_mm3": max_punch_vol,
                        "max_die_overlap_volume_mm3": max_die_vol,
                    }
                    print(f"  >>> FIRST COLLISION RECORDED: {first_collision_info}")

            if traj_result.status == PhysicalEvidence.UNVERIFIED:
                has_any_incomplete = True

            # Progress workpiece fold state to target angle for subsequent bends
            session.fold_state.set_angle(bend_id, target_ang)

        # Restore initial angles before concluding
        for b, ang in initial.items():
            session.fold_state.set_angle(b, ang)

        report_data = {
            "model_fixture": fixture.name,
            "model_sha256": source_hash,
            "punch_file": punch_path.name,
            "punch_sha256": punch_hash,
            "die_file": die_path.name,
            "die_sha256": die_hash,
            "bends_evaluated_count": len(sequence_records),
            "total_bends_count": total_bends_count,
            "overall_status": (
                "COLLISION" if has_any_collision
                else ("INCOMPLETE/UNRESOLVED" if has_any_incomplete
                      else "SAMPLED_3D_CLEAR_NOT_CONTINUOUS")
            ),
            "first_collision": first_collision_info,
            "sequence": sequence_records,
        }

        # Write JSON report to disk
        report_path = Path("reports/25d_sequence_acceptance_report.json")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report_data, indent=2), encoding="utf-8")
        print(f"\nSaved sequence report to {report_path}")

        # Print Markdown summary table
        print("\n=== SUMMARY TABLE OF ALL BENDS EVALUATED ===")
        print("| Step | Bend | Target Ang | Pose (3D) | Trajectory Status | States | Unresolved | Max Punch Vol (mm³) | Max Die Vol (mm³) |")
        print("|---|---|---:|:---:|:---:|---:|---:|---:|---:|")
        for rec in sequence_records:
            print(f"| {rec['bend_index']+1} | {rec['bend_id']} | {rec['target_angle_deg']:+.1f}° | {rec['pose_native_status']} | {rec['trajectory_native_status']} | {rec['total_states_evaluated']} | {rec['total_unresolved_intervals']} | {rec['max_punch_overlap_volume_mm3']:.4f} | {rec['max_die_overlap_volume_mm3']:.4f} |")

        if has_any_collision:
            fc = first_collision_info
            print(f"\nPHYSICAL_STAGE2_RESULT: COLLISION_DETECTED")
            print(f"First collision at bend {fc['bend_id']} (step {fc['bend_index']+1}), phase={fc['phase']}, ram_z={fc['ram_z_mm']:.2f}mm, punch_overlap={fc['max_punch_overlap_volume_mm3']:.4f} mm³")
            if not diagnostic_only:
                raise RuntimeError(f"PHYSICAL_SEQUENCE_COLLISION_DETECTED: first collision at {fc['bend_id']} in phase {fc['phase']}")
            return report_data

        if has_any_incomplete:
            print("\nPHYSICAL_STAGE2_RESULT: INCOMPLETE/UNRESOLVED")
            if not diagnostic_only:
                raise RuntimeError("PHYSICAL_SEQUENCE_INCOMPLETE_OR_UNRESOLVED")
            return report_data

        pass_marker = (
            "PHYSICAL_STAGE2_FREECAD_GUI_ACCEPTANCE_PASS"
            if is_live_gui
            else "PHYSICAL_STAGE2_HEADLESS_SAMPLED_PASS (NOT_GUI_VERIFIED)"
        )
        print(f"\n{pass_marker}: all {total_bends_count} bends SAMPLED_3D_CLEAR_NOT_CONTINUOUS")
        return report_data

    finally:
        if panel is not None:
            panel.timer.stop()
            panel.close()
            panel.deleteLater()
        if doc is not None and doc.Name not in names_before:
            App.closeDocument(doc.Name)
        if previously_active is not None and App.getDocument(previously_active) is not None:
            App.setActiveDocument(previously_active)
        if hashlib.sha256(fixture.read_bytes()).hexdigest() != source_hash:
            raise RuntimeError("ORIGINAL_FCSTD_MODIFIED")


if __name__ == "__main__":
    diag = "--diagnostic-only" in sys.argv
    run(diagnostic_only=diag)
