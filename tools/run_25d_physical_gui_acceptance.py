"""Native FreeCAD acceptance for the integrated 2.5D + D6 collision check.

In FreeCAD Python Console, after installing feature/25d-physical-validation
and the original 17 demo models:
    from tools.run_25d_physical_gui_acceptance import run
    run()

Modes:
- Strict acceptance (default, diagnostic_only=False):
  Stops immediately at the first blocking issue (COLLISION or UNVERIFIED)
  and saves partial report without claiming full sequence PASS.
- Full diagnostic run (diagnostic_only=True):
  Continues past first collision to explore all remaining operations, but explicitly
  marks subsequent operations as CONDITIONAL (dependent on rejected earlier bends).
- Fast single-pose check (pose_only=True):
  Only performs 2D + 3D check on the current stationary machine pose without running
  the intensive adaptive trajectory validator.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def get_git_info() -> dict:
    commit = "UNKNOWN"
    is_dirty = False
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True, text=True, check=True)
        commit = res.stdout.strip()
        status_res = subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT), capture_output=True, text=True, check=True)
        is_dirty = bool(status_res.stdout.strip())
    except Exception:
        pass
    return {"commit": commit, "is_dirty": is_dirty}


def run(*, diagnostic_only=False, max_bends=None, pose_only=False):
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

    t_run_start = datetime.now(timezone.utc).isoformat()
    git_info = get_git_info()

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

    env_name = "LIVE_FREECAD_GUI" if is_live_gui else "HEADLESS_OFFSCREEN (no FreeCADGui main window)"
    print("PHYSICAL_RUNNER_ENVIRONMENT:", env_name)

    fixture = demo_part_path("box.100out-50in-(ok).FCStd")
    source_hash = hashlib.sha256(fixture.read_bytes()).hexdigest()
    names_before = set(App.listDocuments())
    previously_active = getattr(getattr(App, "ActiveDocument", None), "Name", None)
    doc = None
    panel = None
    session = None
    initial_angles = {}
    initial_active_idx = 0

    report_data = {}
    report_saved = False

    def save_report_safely(data: dict) -> Path:
        out_dir = ROOT / "reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        latest_file = out_dir / "25d_sequence_acceptance_report.json"
        stamped_file = out_dir / f"25d_sequence_acceptance_report_{timestamp_str}.json"
        json_text = json.dumps(data, indent=2)
        try:
            latest_file.write_text(json_text, encoding="utf-8")
            stamped_file.write_text(json_text, encoding="utf-8")
        except Exception as write_err:
            print(f"ERROR: Failed to save report file: {write_err}", file=sys.stderr)
        return latest_file

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

        initial_angles = dict(session.fold_state.angles)
        initial_active_idx = int(session.active_bend_index)

        full_expected_order = list(session.bend_order)
        total_expected_count = len(full_expected_order)

        # Separate range requested for diagnostic vs full expected order
        requested_range = (
            full_expected_order[:max_bends]
            if max_bends is not None
            else list(full_expected_order)
        )

        punch_path = Path(panel.tooling_setup.punch.source_file)
        die_path = Path(panel.tooling_setup.die.source_file)
        punch_hash = hashlib.sha256(punch_path.read_bytes()).hexdigest()
        die_hash = hashlib.sha256(die_path.read_bytes()).hexdigest()

        print(f"SEQUENCE_VALIDATION_START: expected={total_expected_count} bends {full_expected_order}")
        print(f"REQUESTED_SCOPE: {len(requested_range)} bends {requested_range} (diagnostic_only={diagnostic_only}, pose_only={pose_only})")
        print(f"TOOLING_INFO: punch={punch_path.name} ({punch_hash[:16]}), die={die_path.name} ({die_hash[:16]})")

        sequence_records = []
        first_collision_info = None
        has_any_collision = False
        has_any_unverified = False
        stop_reason = "COMPLETED_REQUESTED_SCOPE"

        for idx, bend_id in enumerate(requested_range):
            session.select_bend_index(idx)
            bend = session.fold_state.bends[bend_id]
            target_ang = float(bend.angle_deg)
            prior_angles_record = {b: float(session.fold_state.angles[b]) for b in full_expected_order[:idx]}

            is_conditional = has_any_collision
            cond_label = " [CONDITIONAL_AFTER_REJECTED_BEND]" if is_conditional else ""
            print(f"\n--- VALIDATING BEND {idx + 1}/{total_expected_count}: {bend_id} (target {target_ang:+.1f} deg){cond_label} ---")
            print(f"Prior completed bends: {prior_angles_record}")

            # 1. Pose check at stationary initial contact
            pose_result = check_current_25d_pose(
                session, panel.press_cycle, panel.tooling_setup, native_3d=True,
            )
            print(f"  Pose check: status={pose_result.status.value}, 2D={pose_result.section_status}, 3D={pose_result.native_status}")

            traj_result = None
            raw = None
            total_states = 0
            total_unresolved = 0
            max_punch_vol = 0.0
            max_die_vol = 0.0
            phase_records = []

            if not pose_only:
                traj_result = check_active_25d_bend_trajectory(
                    session, panel.tooling_setup,
                )
                raw = traj_result.raw_report
                print(f"  Trajectory check: status={traj_result.status.value}, native={traj_result.native_status}")

                if raw and hasattr(raw, "phase_results"):
                    total_states = raw.total_states_evaluated
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

            # Check for collision in pose or trajectory
            bend_has_collision = (
                pose_result.status == PhysicalEvidence.COLLISION
                or (traj_result is not None and traj_result.status == PhysicalEvidence.COLLISION)
            )

            # Check for unverified in pose or trajectory
            bend_has_unverified = (
                pose_result.status in (PhysicalEvidence.UNVERIFIED, PhysicalEvidence.SECTION_CLEAR_3D_UNVERIFIED)
                or (traj_result is not None and traj_result.status == PhysicalEvidence.UNVERIFIED)
            )

            # Record exact state details for first collision
            specific_state_info = None
            if bend_has_collision and first_collision_info is None:
                first_col_st = getattr(raw, "first_collision_state", None)
                first_punch_overlap = 0.0
                first_die_overlap = 0.0
                if first_col_st and getattr(first_col_st, "collision_report", None):
                    cr = first_col_st.collision_report
                    first_punch_overlap = float(cr.punch_result.overlap_volume_mm3) if cr.punch_result else 0.0
                    first_die_overlap = float(cr.die_result.overlap_volume_mm3) if cr.die_result else 0.0
                elif pose_result.status == PhysicalEvidence.COLLISION and getattr(pose_result, "raw_report", None):
                    cr = pose_result.raw_report
                    first_punch_overlap = float(cr.punch_result.overlap_volume_mm3) if getattr(cr, "punch_result", None) else 0.0
                    first_die_overlap = float(cr.die_result.overlap_volume_mm3) if getattr(cr, "die_result", None) else 0.0
                elif pose_result.status == PhysicalEvidence.COLLISION:
                    first_punch_overlap = max_punch_vol
                    first_die_overlap = max_die_vol

                specific_state_info = {
                    "bend_id": bend_id,
                    "bend_index": idx,
                    "first_detected_in": "TRAJECTORY" if (traj_result and traj_result.status == PhysicalEvidence.COLLISION) else "POSE",
                    "pose_native_status": pose_result.native_status,
                    "trajectory_native_status": traj_result.native_status if traj_result else "NOT_EVALUATED",
                    "phase": first_col_st.phase if first_col_st else "CONTACT",
                    "path_u": float(first_col_st.path_u) if (first_col_st and first_col_st.path_u is not None) else 0.0,
                    "signed_angle_deg": float(first_col_st.signed_angle_deg) if (first_col_st and first_col_st.signed_angle_deg is not None) else 0.0,
                    "ram_z_mm": float(first_col_st.ram_z_mm) if (first_col_st and first_col_st.ram_z_mm is not None) else float(panel.press_cycle.ram_z_mm),
                    "state_punch_overlap_volume_mm3": first_punch_overlap,
                    "state_die_overlap_volume_mm3": first_die_overlap,
                    "max_punch_overlap_volume_mm3": max_punch_vol,
                    "max_die_overlap_volume_mm3": max_die_vol,
                    "note": "first_detected_collision_state_by_search_not_guaranteed_initial_impact_instant",
                }
                first_collision_info = specific_state_info
                has_any_collision = True
                print(f"  >>> FIRST COLLISION RECORDED: {first_collision_info}")

            if bend_has_collision:
                has_any_collision = True
            if bend_has_unverified:
                has_any_unverified = True

            bend_summary = {
                "bend_index": idx,
                "bend_id": bend_id,
                "target_angle_deg": target_ang,
                "prior_completed_angles": prior_angles_record,
                "is_conditional_after_rejected_bend": is_conditional,
                "pose_status": pose_result.status.value,
                "pose_native_status": pose_result.native_status,
                "trajectory_status": traj_result.status.value if traj_result else "NOT_EVALUATED",
                "trajectory_native_status": traj_result.native_status if traj_result else "NOT_EVALUATED",
                "total_states_evaluated": total_states,
                "total_unresolved_intervals": total_unresolved,
                "max_punch_overlap_volume_mm3": max_punch_vol,
                "max_die_overlap_volume_mm3": max_die_vol,
                "detected_collision_state": specific_state_info,
                "phases": phase_records,
                "reason": traj_result.reason if traj_result else pose_result.reason,
            }
            sequence_records.append(bend_summary)

            # Strict acceptance stops immediately on first collision or unverified issue
            if not diagnostic_only and (bend_has_collision or bend_has_unverified):
                stop_reason = f"STOPPED_AT_FIRST_FAILURE:{bend_id}:{pose_result.status.value}/{traj_result.status.value if traj_result else 'NONE'}"
                print(f"STRICT_ACCEPTANCE_HALTED: {stop_reason}")
                break

            # Progress fold state to target angle for subsequent bends
            session.fold_state.set_angle(bend_id, target_ang)

        evaluated_bends = [r["bend_id"] for r in sequence_records]
        skipped_bends = [b for b in full_expected_order if b not in evaluated_bends]
        is_full_sequence_evaluated = (len(evaluated_bends) == total_expected_count)

        # Legitimate PASS requires:
        # 1. Entire full sequence evaluated (no partial or skipped bends)
        # 2. Not pose_only
        # 3. Every bend trajectory status is SAMPLED_3D_CLEAR_NOT_CONTINUOUS
        # 4. Every bend pose status is SAMPLED_3D_CLEAR_NOT_CONTINUOUS
        # 5. Zero collision and zero unverified
        is_legitimate_pass = (
            is_full_sequence_evaluated
            and not pose_only
            and not has_any_collision
            and not has_any_unverified
            and all(r["trajectory_status"] == "SAMPLED_3D_CLEAR_NOT_CONTINUOUS" for r in sequence_records)
            and all(r["pose_status"] == "SAMPLED_3D_CLEAR_NOT_CONTINUOUS" for r in sequence_records)
        )

        overall_status = "COLLISION" if has_any_collision else (
            "SAMPLED_3D_CLEAR_NOT_CONTINUOUS" if is_legitimate_pass else "INCOMPLETE/UNRESOLVED"
        )

        report_data = {
            "report_schema": "25d_sequence_acceptance_v2",
            "timestamp": t_run_start,
            "git": git_info,
            "environment": env_name,
            "model_fixture": fixture.name,
            "model_sha256": source_hash,
            "punch_file": punch_path.name,
            "punch_sha256": punch_hash,
            "die_file": die_path.name,
            "die_sha256": die_hash,
            "tolerances": {
                "volume_tolerance_mm3": 1e-4,
                "arc_segments": 128,
                "max_bend_angle_step_deg": 5.0,
                "clearance_change_threshold_mm": 2.0,
            },
            "scopes": {
                "checked_scope": ["3D_PUNCH_DIE_SAMPLED_BEND", "3D_TOOL_LENGTH", "2D_SECTION"],
                "unverified_scope": [
                    "SWEPT_TRANSFER_MOTION",
                    "BACKGAUGE_FINGERS",
                    "HYDRAULIC_CLAMPS",
                    "MACHINE_FRAME_UPRIGHTS_AND_THROAT",
                    "SHEET_FRONT_SUPPORTS",
                    "CONTINUOUS_SWEPT_VOLUME",
                    "SPRINGBACK_AND_TONNAGE",
                ],
            },
            "sequence_scope": {
                "full_expected_order": full_expected_order,
                "total_expected_count": total_expected_count,
                "requested_range": requested_range,
                "evaluated_bends": evaluated_bends,
                "skipped_bends": skipped_bends,
                "is_full_sequence_evaluated": is_full_sequence_evaluated,
                "stop_reason": stop_reason,
            },
            "overall_status": overall_status,
            "first_collision": first_collision_info,
            "sequence": sequence_records,
        }

        saved_path = save_report_safely(report_data)
        report_saved = True
        print(f"\nSaved sequence report to {saved_path}")

        # Summary table
        print("\n=== SUMMARY TABLE OF EVALUATED BENDS ===")
        print("| Step | Bend | Target Ang | Cond? | Pose (3D) | Trajectory Status | States | Unresolved | Max Punch Vol (mm³) | Max Die Vol (mm³) |")
        print("|---|---|---:|:---:|:---:|:---:|---:|---:|---:|---:|")
        for rec in sequence_records:
            cond_str = "YES" if rec.get("is_conditional_after_rejected_bend") else "NO"
            print(f"| {rec['bend_index']+1} | {rec['bend_id']} | {rec['target_angle_deg']:+.1f}° | {cond_str} | {rec['pose_native_status']} | {rec['trajectory_native_status']} | {rec['total_states_evaluated']} | {rec['total_unresolved_intervals']} | {rec['max_punch_overlap_volume_mm3']:.4f} | {rec['max_die_overlap_volume_mm3']:.4f} |")

        if skipped_bends:
            print(f"\nUNVERIFIED / SKIPPED BENDS: {skipped_bends}")

        if has_any_collision:
            fc = first_collision_info
            ram_str = f"{fc['ram_z_mm']:.2f}mm" if (fc and fc.get('ram_z_mm') is not None) else "N/A"
            vol_str = f"{fc['max_punch_overlap_volume_mm3']:.4f} mm³" if fc else "N/A"
            print(f"\nPHYSICAL_STAGE2_RESULT: COLLISION_DETECTED")
            print(f"First detected collision: bend={fc['bend_id']} (step {fc['bend_index']+1}), phase={fc['phase']}, ram_z={ram_str}, punch_overlap={vol_str}")
            if not diagnostic_only:
                raise RuntimeError(f"PHYSICAL_SEQUENCE_COLLISION_DETECTED: first collision at {fc['bend_id']} in phase {fc['phase']}")
            return report_data

        if not is_full_sequence_evaluated or has_any_unverified:
            print(f"\nPHYSICAL_STAGE2_RESULT: INCOMPLETE/UNRESOLVED (evaluated {len(evaluated_bends)} of {total_expected_count})")
            if not diagnostic_only:
                raise RuntimeError(f"PHYSICAL_SEQUENCE_INCOMPLETE: only {len(evaluated_bends)} of {total_expected_count} bends evaluated")
            return report_data

        pass_marker = (
            "PHYSICAL_STAGE2_FREECAD_GUI_ACCEPTANCE_PASS"
            if is_live_gui
            else "PHYSICAL_STAGE2_HEADLESS_SAMPLED_PASS (NOT_GUI_VERIFIED)"
        )
        print(f"\n{pass_marker}: all {total_expected_count} bends SAMPLED_3D_CLEAR_NOT_CONTINUOUS")
        return report_data

    except Exception as exc:
        if not report_saved and session is not None:
            # Save partial report on unexpected crash
            partial_data = {
                "report_schema": "25d_sequence_acceptance_partial_failure",
                "timestamp": t_run_start,
                "git": git_info,
                "environment": env_name,
                "error": str(exc),
                "sequence": sequence_records,
                "overall_status": "ERROR_DURING_EXECUTION",
            }
            save_report_safely(partial_data)
        raise

    finally:
        # Guarantee full restoration of fold state angles and active operation
        if session is not None and getattr(session, "fold_state", None) is not None:
            try:
                for b_name, ang in initial_angles.items():
                    session.fold_state.set_angle(b_name, ang)
                if hasattr(session, "select_bend_index"):
                    session.select_bend_index(initial_active_idx)
            except Exception as restore_err:
                print(f"WARNING: Exception during fold state restoration in finally: {restore_err}", file=sys.stderr)

        if panel is not None:
            try:
                panel.timer.stop()
                panel.close()
                panel.deleteLater()
            except Exception:
                pass
        if doc is not None and doc.Name not in names_before:
            try:
                App.closeDocument(doc.Name)
            except Exception:
                pass
        if previously_active is not None and App.getDocument(previously_active) is not None:
            try:
                App.setActiveDocument(previously_active)
            except Exception:
                pass
        if hashlib.sha256(fixture.read_bytes()).hexdigest() != source_hash:
            raise RuntimeError("ORIGINAL_FCSTD_MODIFIED")


if __name__ == "__main__":
    diag = "--diagnostic-only" in sys.argv
    p_only = "--pose-only" in sys.argv
    run(diagnostic_only=diag, pose_only=p_only)
