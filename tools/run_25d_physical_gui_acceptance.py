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

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _p in ("/Applications/FreeCAD.app/Contents/Resources/lib",):
    if os.path.exists(_p) and _p not in sys.path:
        sys.path.append(_p)


def get_git_info() -> dict:
    commit = "UNKNOWN"
    is_dirty = False
    dirty_files = []
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True, text=True, check=True)
        commit = res.stdout.strip()
        status_res = subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT), capture_output=True, text=True, check=True)
        lines = [ln for ln in status_res.stdout.splitlines() if ln.strip()]
        is_dirty = bool(lines)
        dirty_files = lines
    except Exception:
        pass
    return {"commit": commit, "is_dirty": is_dirty, "dirty_files": dirty_files}


def save_report_safely(
    data: dict,
    *,
    timestamp_str: str | None = None,
    report_filename: str | None = None,
) -> Path:
    out_dir = ROOT / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    if timestamp_str is None:
        timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    latest_file = out_dir / "25d_sequence_acceptance_report.json"
    stamped_file = out_dir / f"25d_sequence_acceptance_report_{timestamp_str}.json"
    json_text = json.dumps(data, indent=2)
    try:
        stamped_file.write_text(json_text, encoding="utf-8")
        latest_file.write_text(json_text, encoding="utf-8")
        if report_filename:
            custom_path = Path(report_filename)
            if not custom_path.is_absolute():
                custom_path = out_dir / custom_path.name
            custom_path.parent.mkdir(parents=True, exist_ok=True)
            custom_path.write_text(json_text, encoding="utf-8")
            return custom_path
    except Exception as write_err:
        print(f"ERROR: Failed to save report file: {write_err}", file=sys.stderr)
        raise RuntimeError(f"REPORT_SAVE_FAILED: {write_err}") from write_err
    return latest_file


def restore_session_state(sess, target_angles: dict, target_active_idx: int, target_transforms: dict | None = None):
    """Restore angles, transforms, and active bend index; verify match."""
    if sess is None:
        return
    if getattr(sess, "fold_state", None) is not None:
        for b_name, ang in target_angles.items():
            sess.fold_state.set_angle(b_name, ang)
    if hasattr(sess, "select_bend_index"):
        sess.select_bend_index(target_active_idx)

    # 1. Verification of angles
    if getattr(sess, "fold_state", None) is not None:
        for b_name, expected_ang in target_angles.items():
            actual_ang = sess.fold_state.angles.get(b_name)
            if actual_ang is None or abs(actual_ang - expected_ang) > 1e-6:
                raise RuntimeError(
                    f"FOLD_STATE_RESTORATION_FAILED: angle mismatch for bend '{b_name}' "
                    f"(expected {expected_ang}, got {actual_ang})"
                )

    # 2. Verification of active bend index
    if hasattr(sess, "active_bend_index"):
        actual_idx = sess.active_bend_index
        if actual_idx != target_active_idx:
            raise RuntimeError(
                f"FOLD_STATE_RESTORATION_FAILED: active_bend_index mismatch "
                f"(expected {target_active_idx}, got {actual_idx})"
            )

    # 3. Verification of panel transforms
    if target_transforms is not None and getattr(sess, "fold_state", None) is not None:
        for pid, exp_tf in target_transforms.items():
            act_tf = sess.fold_state.transforms.get(pid)
            if act_tf is None:
                raise RuntimeError(f"FOLD_STATE_RESTORATION_FAILED: transform missing for panel '{pid}'")
            for i in range(3):
                if abs(act_tf.translation[i] - exp_tf.translation[i]) > 1e-5:
                    raise RuntimeError(
                        f"FOLD_STATE_RESTORATION_FAILED: translation mismatch for panel '{pid}' "
                        f"(expected {exp_tf.translation}, got {act_tf.translation})"
                    )
            for r in range(3):
                for c in range(3):
                    if abs(act_tf.rotation[r][c] - exp_tf.rotation[r][c]) > 1e-5:
                        raise RuntimeError(
                            f"FOLD_STATE_RESTORATION_FAILED: rotation mismatch for panel '{pid}' "
                            f"(expected {exp_tf.rotation}, got {act_tf.rotation})"
                        )


def _serialize_placement(tf) -> dict | None:
    if tf is None:
        return None
    return {
        "translation_mm": [round(float(x), 6) for x in tf.translation],
        "rotation_matrix": [[round(float(val), 6) for val in row] for row in tf.rotation],
    }


def _extract_tooling_metadata(punch_path: Path, die_path: Path) -> dict:
    from smbs_core.kinematics2d.native_tool_loader import load_native_punch, load_native_die
    p_loaded = load_native_punch(punch_path, ram_z_mm=50.0)
    d_loaded = load_native_die(die_path)
    p_sidecar = punch_path.with_suffix(".tooling.json")
    d_sidecar = die_path.with_suffix(".tooling.json")
    p_json = json.loads(p_sidecar.read_text(encoding="utf-8")) if p_sidecar.exists() else {}
    d_json = json.loads(d_sidecar.read_text(encoding="utf-8")) if d_sidecar.exists() else {}
    return {
        "punch": {
            "tool_id": p_loaded.tool_id,
            "file": punch_path.name,
            "path": str(punch_path),
            "sidecar_path": str(p_sidecar) if p_sidecar.exists() else None,
            "tip_radius_mm": float(p_loaded.tip_radius_mm),
            "included_angle_deg": float(p_loaded.included_angle_deg),
            "height_mm": float(p_loaded.height_mm),
            "length_mm": float(p_loaded.length_mm),
            "span_xm_mm": [float(p_loaded.span_xm[0]), float(p_loaded.span_xm[1])],
            "sections_xm_mm": [[float(s.span_xm[0]), float(s.span_xm[1])] for s in p_loaded.sections],
            "_sections_objs": p_loaded.sections,
            "datums": p_json.get("datums", p_json.get("cad_datum")),
            "metadata": p_json.get("metadata"),
            "is_valid_solid": bool(p_loaded.machine_shape.isValid()),
            "volume_mm3": round(float(p_loaded.machine_shape.Volume), 6),
        },
        "die": {
            "tool_id": d_loaded.tool_id,
            "file": die_path.name,
            "path": str(die_path),
            "sidecar_path": str(d_sidecar) if d_sidecar.exists() else None,
            "v_opening_mm": float(d_loaded.v_opening_mm),
            "effective_v_opening_mm": float(getattr(d_loaded, "effective_v_opening_mm", d_loaded.v_opening_mm) or d_loaded.v_opening_mm),
            "shoulder_radius_mm": float(d_loaded.shoulder_radius_mm),
            "included_angle_deg": float(d_loaded.included_angle_deg),
            "v_depth_mm": float(d_loaded.v_depth_mm),
            "height_mm": float(d_loaded.height_mm),
            "length_mm": float(d_loaded.length_mm),
            "span_xm_mm": [float(d_loaded.span_xm[0]), float(d_loaded.span_xm[1])],
            "sections_xm_mm": [[float(s.span_xm[0]), float(s.span_xm[1])] for s in d_loaded.sections],
            "_sections_objs": d_loaded.sections,
            "datums": d_json.get("datums", d_json.get("cad_datum")),
            "metadata": d_json.get("metadata"),
            "is_valid_solid": bool(d_loaded.machine_shape.isValid()),
            "volume_mm3": round(float(d_loaded.machine_shape.Volume), 6),
        },
    }


def _compute_coverage_details(
    bend,
    shift_mm: float,
    punch_meta: dict,
    die_meta: dict,
    match_active_bend_length: bool = False,
) -> dict:
    from smbs_core.kinematics2d.native_tool_loader import check_tool_coverage
    axis = getattr(bend, "axis_xyz", None)
    if axis is not None:
        p0, p1 = axis
        bend_len = math.dist(p0, p1)
    else:
        p0, p1 = (0.0, 0.0, 0.0), (100.0, 0.0, 0.0)
        bend_len = 100.0
    unshifted_span = (-0.5 * bend_len, 0.5 * bend_len)
    shifted_span = (unshifted_span[0] + shift_mm, unshifted_span[1] + shift_mm)

    if match_active_bend_length and bend_len > 0.0:
        p_span = (-0.5 * bend_len, 0.5 * bend_len)
        d_span = (-0.5 * bend_len, 0.5 * bend_len)
        p_secs = None
        d_secs = None
    else:
        p_span = tuple(punch_meta.get("span_xm_mm", (-50.0, 50.0)))
        d_span = tuple(die_meta.get("span_xm_mm", (-50.0, 50.0)))
        p_secs = punch_meta.get("_sections_objs")
        d_secs = die_meta.get("_sections_objs")

    pcov = check_tool_coverage(shifted_span, p_span, p_secs)
    dcov = check_tool_coverage(shifted_span, d_span, d_secs)

    return {
        "bend_axis_cad_endpoints_mm": [[round(float(c), 6) for c in p0], [round(float(c), 6) for c in p1]],
        "bend_length_mm": round(float(bend_len), 6),
        "unshifted_bend_span_xm_mm": [round(float(unshifted_span[0]), 6), round(float(unshifted_span[1]), 6)],
        "longitudinal_shift_xm_mm": round(float(shift_mm), 6),
        "shifted_bend_span_xm_mm": [round(float(shifted_span[0]), 6), round(float(shifted_span[1]), 6)],
        "is_virtual_tool_geometry": bool(match_active_bend_length),
        "source_punch_length_mm": float(punch_meta.get("length_mm", 100.0)),
        "virtual_punch_length_mm": round(float(bend_len), 6) if match_active_bend_length else None,
        "source_die_length_mm": float(die_meta.get("length_mm", 100.0)),
        "virtual_die_length_mm": round(float(bend_len), 6) if match_active_bend_length else None,
        "punch_span_xm_mm": [round(float(p_span[0]), 6), round(float(p_span[1]), 6)],
        "die_span_xm_mm": [round(float(d_span[0]), 6), round(float(d_span[1]), 6)],
        "punch_is_covered": bool(pcov.is_covered),
        "punch_uncovered_left_mm": round(float(pcov.uncovered_left_mm), 6),
        "punch_uncovered_right_mm": round(float(pcov.uncovered_right_mm), 6),
        "punch_reason": pcov.reason,
        "die_is_covered": bool(dcov.is_covered),
        "die_uncovered_left_mm": round(float(dcov.uncovered_left_mm), 6),
        "die_uncovered_right_mm": round(float(dcov.uncovered_right_mm), 6),
        "die_reason": dcov.reason,
    }


def run(
    *,
    diagnostic_only: bool = False,
    max_bends: int | None = None,
    pose_only: bool = False,
    bend_order: tuple[str, ...] | list[str] | None = None,
    punch_id: str | None = None,
    die_id: str | None = None,
    bend_shifts: dict[str, float] | None = None,
    report_filename: str | None = None,
    max_phase_time_s: float = 120.0,
    force_trajectory_on_pose_failure: bool = False,
    match_active_bend_length: bool = False,
    allow_end_zone_collision: bool = False,
    end_zone_length_mm: float = 0.0,
    search_sequence: bool = False,
    max_search_states: int = 500,
    max_search_checks: int = 1000,
    max_search_time_s: float = 600.0,
):
    import FreeCAD as App
    try:
        from PySide6 import QtWidgets
    except ImportError:
        from PySide import QtWidgets
    from smbs_core.demo_paths import demo_part_path
    import smbs_core.kinematics2d.physical_validation_25d as pv25d
    import smbs_core.kinematics2d.motion_trajectory_3d as mt3d
    import smbs_core.kinematics2d.native_collision_3d as nc3d
    from smbs_core.kinematics2d.physical_validation_25d import (
        check_current_25d_pose, check_active_25d_bend_trajectory,
        PhysicalEvidence,
    )
    from smbs_core.kinematics2d.motion_trajectory_3d import AdaptiveSamplingConfig
    from smbs_core.kinematics2d.tooling_25d import ToolKind
    from freecad_smbs.commands.pressbrake_2d import (
        _PreviewPanel, build_part_session,
    )

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    t_run_start = datetime.now(timezone.utc).isoformat()
    run_timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
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
    freecad_ver = ".".join(App.Version()[:3])
    module_paths = {
        "FreeCAD": getattr(App, "__file__", "built-in"),
        "physical_validation_25d": str(Path(pv25d.__file__).resolve()),
        "motion_trajectory_3d": str(Path(mt3d.__file__).resolve()),
        "native_collision_3d": str(Path(nc3d.__file__).resolve()),
        "runner_script": str(Path(__file__).resolve()),
    }
    print("PHYSICAL_RUNNER_ENVIRONMENT:", env_name)
    print("FREECAD_VERSION:", freecad_ver)

    fixture = demo_part_path("box.100out-50in-(ok).FCStd")
    source_hash = hashlib.sha256(fixture.read_bytes()).hexdigest()
    names_before = set(App.listDocuments())
    previously_active = getattr(getattr(App, "ActiveDocument", None), "Name", None)
    doc = None
    panel = None
    session = None
    initial_angles = {}
    initial_active_idx = 0
    initial_transforms = {}
    sequence_records = []

    report_data = {}
    report_saved = False

    shifts_map = dict(bend_shifts) if bend_shifts else {}
    sampling_cfg = AdaptiveSamplingConfig(max_phase_time_s=float(max_phase_time_s))
    validator_config_dict = {
        "max_bend_angle_step_deg": sampling_cfg.max_bend_angle_step_deg,
        "max_ram_displacement_step_mm": sampling_cfg.max_ram_displacement_step_mm,
        "min_clearance_margin_mm": sampling_cfg.min_clearance_margin_mm,
        "clearance_change_threshold_mm": sampling_cfg.clearance_change_threshold_mm,
        "max_recursion_depth": sampling_cfg.max_recursion_depth,
        "max_evaluated_states_per_phase": sampling_cfg.max_evaluated_states_per_phase,
        "max_phase_time_s": sampling_cfg.max_phase_time_s,
        "arc_segments": sampling_cfg.arc_segments,
        "near_tolerance_mm": sampling_cfg.near_tolerance_mm,
        "volume_tolerance_mm3": sampling_cfg.volume_tolerance_mm3,
        "contact_distance_tolerance_mm": sampling_cfg.contact_distance_tolerance_mm,
        "position_continuity_tolerance_mm": sampling_cfg.position_continuity_tolerance_mm,
        "rotation_continuity_tolerance_deg": sampling_cfg.rotation_continuity_tolerance_deg,
        "angle_continuity_tolerance_deg": sampling_cfg.angle_continuity_tolerance_deg,
    }

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

        if punch_id is not None:
            clean_p = punch_id[:-6] if punch_id.lower().endswith(".fcstd") else punch_id
            prof_p = panel.tool_library.load_tool(clean_p, ToolKind.PUNCH)
            panel.select_punch(prof_p)
        if die_id is not None:
            clean_d = die_id[:-6] if die_id.lower().endswith(".fcstd") else die_id
            prof_d = panel.tool_library.load_tool(clean_d, ToolKind.DIE)
            panel.select_die(prof_d)

        panel.align_tooling()
        if not panel.tooling_setup.aligned:
            raise RuntimeError("TOOLING_NOT_ALIGNED:" + panel.status.text())

        if hasattr(panel.tooling_setup, "set_match_active_bend_length"):
            panel.tooling_setup.set_match_active_bend_length(bool(match_active_bend_length))
        else:
            panel.tooling_setup.match_active_bend_length = bool(match_active_bend_length)
        panel.tooling_setup.allow_end_zone_collision = bool(allow_end_zone_collision)
        panel.tooling_setup.end_zone_length_mm = float(end_zone_length_mm)

        # If custom bend order is requested, apply through official session API
        if bend_order is not None:
            session.set_bend_order(bend_order)

        # Ensure flat starting state before recording initial snapshot
        if hasattr(session.fold_state, "reset_all"):
            session.fold_state.reset_all()
        if hasattr(session, "select_bend_index"):
            session.select_bend_index(0)

        initial_angles = dict(session.fold_state.angles)
        initial_active_idx = getattr(session, "active_bend_index", 0)
        initial_transforms = (
            copy.deepcopy(session.fold_state.transforms)
            if isinstance(getattr(session.fold_state, "transforms", None), dict)
            else {}
        )

        search_report_obj = None
        if search_sequence:
            from smbs_core.kinematics2d.sequence_search import search_bend_sequence_25d

            print(
                f"SEQUENCE_SEARCH_START: match_active_bend_length={match_active_bend_length}, "
                f"allow_end_zone_collision={allow_end_zone_collision}, end_zone_length_mm={end_zone_length_mm}"
            )
            search_report_obj = search_bend_sequence_25d(
                session,
                panel.press_cycle,
                panel.tooling_setup,
                config=sampling_cfg,
                bend_shifts=shifts_map,
                match_active_bend_length=match_active_bend_length,
                allow_end_zone_collision=allow_end_zone_collision,
                end_zone_length_mm=end_zone_length_mm,
                max_states=max_search_states,
                max_checks=max_search_checks,
                max_search_time_s=max_search_time_s,
            )
            print(
                f"SEQUENCE_SEARCH_RESULT: status={search_report_obj.status.value}, "
                f"overall={search_report_obj.overall_evidence.value}, "
                f"found={search_report_obj.found_sequence}, "
                f"states={search_report_obj.states_visited}, "
                f"checks={search_report_obj.step_evaluations}, "
                f"elapsed={search_report_obj.elapsed_s:.2f}s"
            )
            print(f"SEQUENCE_SEARCH_MESSAGE_RU: {search_report_obj.message_ru}")
            if search_report_obj.found_sequence is not None:
                session.set_bend_order(list(search_report_obj.found_sequence))
                session.fold_state.reset_all()
                session.select_bend_index(0)

        raw_order = list(getattr(session, "bend_order", ()) or ())
        full_expected_order = raw_order if raw_order else list(session.fold_state.angles.keys())
        total_expected_count = len(full_expected_order)

        requested_range = (
            full_expected_order[:max_bends]
            if max_bends is not None
            else list(full_expected_order)
        )

        punch_obj = getattr(panel.tooling_setup, "punch", None)
        die_obj = getattr(panel.tooling_setup, "die", None)
        if punch_obj is not None and die_obj is not None and getattr(punch_obj, "source_file", None):
            punch_path = Path(punch_obj.source_file)
            die_path = Path(die_obj.source_file)
            punch_hash = hashlib.sha256(punch_path.read_bytes()).hexdigest()
            die_hash = hashlib.sha256(die_path.read_bytes()).hexdigest()
            tooling_meta = _extract_tooling_metadata(punch_path, die_path)
            punch_file_name = punch_path.name
            die_file_name = die_path.name
        else:
            punch_file_name = "mock_punch.FCStd"
            die_file_name = "mock_die.FCStd"
            punch_hash = "mock"
            die_hash = "mock"
            tooling_meta = {
                "punch": {"tool_id": "mock_punch", "length_mm": 100.0, "span_xm_mm": [-50.0, 50.0]},
                "die": {"tool_id": "mock_die", "length_mm": 100.0, "span_xm_mm": [-50.0, 50.0]},
            }

        print(f"SEQUENCE_VALIDATION_START: expected={total_expected_count} bends {full_expected_order}")
        print(f"REQUESTED_SCOPE: {len(requested_range)} bends {requested_range} (diagnostic_only={diagnostic_only}, pose_only={pose_only})")
        print(f"TOOLING_INFO: punch={punch_file_name} ({punch_hash[:16]}), die={die_file_name} ({die_hash[:16]})")
        print(
            f"DEVELOPMENT_MODES: match_active_bend_length={match_active_bend_length}, "
            f"allow_end_zone_collision={allow_end_zone_collision}, end_zone_length_mm={end_zone_length_mm}"
        )
        if shifts_map:
            print(f"CONFIGURED_SHIFTS_MM: {shifts_map} (all unspecified bends explicitly use 0.0 mm)")

        first_collision_info = None
        has_any_collision = False
        has_any_unverified = False
        has_any_conditional_end_zone = False
        first_blocking_bend = None
        first_blocking_status = None
        stop_reason = "COMPLETED_REQUESTED_SCOPE"

        executed_checks = set()
        shift_changes_detected = False
        prev_shift_mm = None

        def _build_current_report_payload(cur_stop_reason: str) -> dict:
            evaluated_bends = [r["bend_id"] for r in sequence_records]
            skipped_bends = [b for b in full_expected_order if b not in evaluated_bends]
            is_full_sequence_evaluated = (len(evaluated_bends) == total_expected_count)

            is_legitimate_pass = (
                total_expected_count > 0
                and is_full_sequence_evaluated
                and not pose_only
                and not has_any_collision
                and not has_any_unverified
                and not has_any_conditional_end_zone
                and all(r["trajectory_status"] == "SAMPLED_3D_CLEAR_NOT_CONTINUOUS" for r in sequence_records)
                and all(r["pose_status"] == "SAMPLED_3D_CLEAR_NOT_CONTINUOUS" for r in sequence_records)
            )
            is_conditional_plan = (
                total_expected_count > 0
                and is_full_sequence_evaluated
                and not pose_only
                and not has_any_collision
                and not has_any_unverified
                and has_any_conditional_end_zone
                and all(
                    r["trajectory_status"] in ("SAMPLED_3D_CLEAR_NOT_CONTINUOUS", "CONDITIONAL")
                    for r in sequence_records
                )
                and all(
                    r["pose_status"] in ("SAMPLED_3D_CLEAR_NOT_CONTINUOUS", "CONDITIONAL")
                    for r in sequence_records
                )
            )

            overall_status = (
                "COLLISION"
                if has_any_collision
                else (
                    "SAMPLED_3D_CLEAR_NOT_CONTINUOUS"
                    if is_legitimate_pass
                    else ("CONDITIONAL" if is_conditional_plan else "INCOMPLETE/UNRESOLVED")
                )
            )

            checked_scope = sorted(list(executed_checks)) if executed_checks else ["2D_SECTION"]

            unverified_scope = [
                "SWEPT_TRANSFER_MOTION",
                "BACKGAUGE_FINGERS",
                "HYDRAULIC_CLAMPS",
                "MACHINE_FRAME_UPRIGHTS_AND_THROAT",
                "SHEET_FRONT_SUPPORTS",
                "CONTINUOUS_SWEPT_VOLUME",
                "SPRINGBACK_AND_TONNAGE",
            ]
            if match_active_bend_length:
                unverified_scope.append("VIRTUAL_TOOL_LENGTH_EQUAL_TO_ACTIVE_BEND_LENGTH_DEV_MODE")
            if has_any_conditional_end_zone:
                unverified_scope.append("PUNCH_END_ZONE_RELIEF_CUT_GEOMETRY_NOT_VERIFIED")
            if shift_changes_detected:
                unverified_scope.append("LONGITUDINAL_SHIFT_REPOSITIONING_TRANSFER_UNVERIFIED")
            if pose_only:
                unverified_scope.append("ALL_BEND_TRAJECTORIES (pose_only mode)")
            if skipped_bends:
                unverified_scope.append(f"SKIPPED_BENDS: {skipped_bends}")

            return {
                "report_schema": "25d_sequence_acceptance_v3",
                "timestamp": t_run_start,
                "git": git_info,
                "environment": env_name,
                "freecad_version": freecad_ver,
                "module_paths": module_paths,
                "model_fixture": fixture.name,
                "model_sha256": source_hash,
                "punch_file": punch_file_name,
                "punch_sha256": punch_hash,
                "die_file": die_file_name,
                "die_sha256": die_hash,
                "tooling_metadata": {
                    "punch": {k: v for k, v in tooling_meta["punch"].items() if not k.startswith("_")},
                    "die": {k: v for k, v in tooling_meta["die"].items() if not k.startswith("_")},
                },
                "development_policy": {
                    "match_active_bend_length": bool(match_active_bend_length),
                    "is_virtual_tool_geometry": bool(match_active_bend_length),
                    "allow_end_zone_collision": bool(allow_end_zone_collision),
                    "end_zone_length_mm": float(end_zone_length_mm),
                },
                "configured_bend_shifts_mm": {b: float(shifts_map.get(b, 0.0)) for b in full_expected_order},
                "tolerances": {
                    "volume_tolerance_mm3": sampling_cfg.volume_tolerance_mm3,
                    "arc_segments": sampling_cfg.arc_segments,
                    "max_bend_angle_step_deg": sampling_cfg.max_bend_angle_step_deg,
                    "clearance_change_threshold_mm": sampling_cfg.clearance_change_threshold_mm,
                },
                "validator_config": validator_config_dict,
                "scopes": {
                    "checked_scope": checked_scope,
                    "unverified_scope": unverified_scope,
                },
                "sequence_scope": {
                    "full_expected_order": full_expected_order,
                    "total_expected_count": total_expected_count,
                    "requested_range": requested_range,
                    "evaluated_bends": evaluated_bends,
                    "skipped_bends": skipped_bends,
                    "is_full_sequence_evaluated": is_full_sequence_evaluated,
                    "stop_reason": cur_stop_reason,
                },
                "sequence_search": search_report_obj.to_dict() if search_report_obj is not None else None,
                "overall_status": overall_status,
                "is_legitimate_pass": is_legitimate_pass,
                "is_conditional_plan": is_conditional_plan,
                "first_collision": first_collision_info,
                "sequence": sequence_records,
            }

        for idx, bend_id in enumerate(requested_range):
            if hasattr(session, "select_bend_index"):
                session.select_bend_index(idx)
            if hasattr(panel, "_sync_tooling_to_active_bend"):
                panel._sync_tooling_to_active_bend()
            bend = session.fold_state.bends[bend_id]
            target_ang = float(getattr(bend, "angle_deg", 90.0))
            prior_angles_record = {b: float(session.fold_state.angles.get(b, 0.0)) for b in full_expected_order[:idx]}

            # Explicit per-bend longitudinal shift (defaults strictly to 0.0 mm if not specified)
            shift_mm = float(shifts_map.get(bend_id, 0.0))
            if prev_shift_mm is not None and abs(shift_mm - prev_shift_mm) > 1e-6:
                shift_changes_detected = True
            prev_shift_mm = shift_mm

            coverage_info = _compute_coverage_details(
                bend,
                shift_mm,
                tooling_meta["punch"],
                tooling_meta["die"],
                match_active_bend_length=match_active_bend_length,
            )

            is_conditional = (first_blocking_bend is not None)
            cond_reason = f"preceded_by_{first_blocking_status}_at_{first_blocking_bend}" if is_conditional else None
            cond_label = f" [CONDITIONAL: {cond_reason}]" if is_conditional else ""
            print(
                f"\n--- VALIDATING BEND {idx + 1}/{total_expected_count}: {bend_id} "
                f"(target {target_ang:+.1f} deg, shift_xm={shift_mm:+.2f} mm){cond_label} ---"
            )
            print(f"Prior completed bends: {prior_angles_record}")
            print(
                f"  Coverage: shifted_span={coverage_info['shifted_bend_span_xm_mm']} vs "
                f"punch={coverage_info['punch_span_xm_mm']} ({coverage_info['punch_reason']}), "
                f"die={coverage_info['die_span_xm_mm']} ({coverage_info['die_reason']})"
            )

            t_bend0 = time.perf_counter()

            # 1. Pose check at stationary initial contact
            if match_active_bend_length or allow_end_zone_collision or end_zone_length_mm > 0.0:
                pose_result = check_current_25d_pose(
                    session,
                    panel.press_cycle,
                    panel.tooling_setup,
                    native_3d=True,
                    desired_midpoint_xm=shift_mm,
                    match_active_bend_length=match_active_bend_length,
                    allow_end_zone_collision=allow_end_zone_collision,
                    end_zone_length_mm=end_zone_length_mm,
                    classify_end_zone_length_mm=end_zone_length_mm,
                )
            else:
                pose_result = check_current_25d_pose(
                    session,
                    panel.press_cycle,
                    panel.tooling_setup,
                    native_3d=True,
                    desired_midpoint_xm=shift_mm,
                )
            pose_punch_vol = None
            pose_die_vol = None
            pose_punch_dist = None
            pose_die_dist = None
            if getattr(pose_result, "raw_report", None) is not None:
                cr_pose = pose_result.raw_report
                if getattr(cr_pose, "punch_result", None) is not None and cr_pose.punch_result.overlap_volume_mm3 is not None:
                    pose_punch_vol = float(cr_pose.punch_result.overlap_volume_mm3)
                    pose_punch_dist = float(cr_pose.punch_result.distance_mm)
                if getattr(cr_pose, "die_result", None) is not None and cr_pose.die_result.overlap_volume_mm3 is not None:
                    pose_die_vol = float(cr_pose.die_result.overlap_volume_mm3)
                    pose_die_dist = float(cr_pose.die_result.distance_mm)

            pose_placement = None
            try:
                pose_placement = _serialize_placement(
                    panel.press_cycle.current_workpiece_placement_3d(desired_midpoint_xm=shift_mm)
                )
            except TypeError:
                try:
                    pose_placement = _serialize_placement(
                        panel.press_cycle.current_workpiece_placement_3d()
                    )
                except Exception:
                    pose_placement = None
            except Exception:
                pose_placement = None

            print(
                f"  Pose check: status={pose_result.status.value}, 2D={pose_result.section_status}, "
                f"3D={pose_result.native_status}, p_vol={pose_punch_vol}, d_vol={pose_die_vol}"
            )
            if hasattr(pose_result, "checked_scope"):
                for sc in pose_result.checked_scope:
                    executed_checks.add(sc)

            pose_is_blocking = (
                pose_result.status in (
                    PhysicalEvidence.COLLISION,
                    PhysicalEvidence.UNVERIFIED,
                    PhysicalEvidence.SECTION_CLEAR_3D_UNVERIFIED,
                )
            )

            traj_result = None
            raw = None
            total_states = 0
            total_unresolved = 0
            traj_max_punch_vol = None
            traj_max_die_vol = None
            phase_records = []

            # Early stopping: do not evaluate trajectory if stationary initial pose is blocking,
            # unless explicitly requested via diagnostic flag force_trajectory_on_pose_failure.
            skip_traj_due_to_pose = pose_is_blocking and not force_trajectory_on_pose_failure

            if skip_traj_due_to_pose:
                print(
                    f"  Early stop on bend {bend_id}: pose check failed with status={pose_result.status.value} "
                    f"({pose_result.native_status}). Skipping trajectory calculation (NOT_EVALUATED)."
                )

            if not pose_only and not skip_traj_due_to_pose:
                if match_active_bend_length or allow_end_zone_collision or end_zone_length_mm > 0.0:
                    traj_result = check_active_25d_bend_trajectory(
                        session,
                        panel.tooling_setup,
                        config=sampling_cfg,
                        desired_midpoint_xm=shift_mm,
                        match_active_bend_length=match_active_bend_length,
                        allow_end_zone_collision=allow_end_zone_collision,
                        end_zone_length_mm=end_zone_length_mm,
                        classify_end_zone_length_mm=end_zone_length_mm,
                    )
                else:
                    traj_result = check_active_25d_bend_trajectory(
                        session,
                        panel.tooling_setup,
                        config=sampling_cfg,
                        desired_midpoint_xm=shift_mm,
                    )
                raw = traj_result.raw_report
                print(f"  Trajectory check: status={traj_result.status.value}, native={traj_result.native_status}")
                if hasattr(traj_result, "checked_scope"):
                    for sc in traj_result.checked_scope:
                        executed_checks.add(sc)
                else:
                    executed_checks.add("3D_PUNCH_DIE_SAMPLED_BEND")

                if raw and hasattr(raw, "phase_results"):
                    total_states = raw.total_states_evaluated
                    if raw.phase_results:
                        traj_max_punch_vol = 0.0
                        traj_max_die_vol = 0.0
                    for pr in raw.phase_results:
                        unres_cnt = len(pr.unresolved_intervals)
                        total_unresolved += unres_cnt
                        p_phase_vol = float(pr.max_punch_overlap_volume_mm3)
                        d_phase_vol = float(pr.max_die_overlap_volume_mm3)
                        traj_max_punch_vol = max(traj_max_punch_vol, p_phase_vol)
                        traj_max_die_vol = max(traj_max_die_vol, d_phase_vol)

                        state_measurements = []
                        for st in pr.states:
                            st_p_vol = None
                            st_d_vol = None
                            st_p_dist = None
                            st_d_dist = None
                            st_col_status = None
                            if st.collision_report is not None:
                                st_col_status = st.collision_report.status.value
                                if st.collision_report.punch_result is not None:
                                    st_p_vol = float(st.collision_report.punch_result.overlap_volume_mm3)
                                    st_p_dist = float(st.collision_report.punch_result.distance_mm)
                                if st.collision_report.die_result is not None:
                                    st_d_vol = float(st.collision_report.die_result.overlap_volume_mm3)
                                    st_d_dist = float(st.collision_report.die_result.distance_mm)
                            state_measurements.append({
                                "phase": st.phase,
                                "path_u": round(float(st.path_u), 6),
                                "signed_angle_deg": round(float(st.signed_angle_deg), 6),
                                "ram_z_mm": round(float(st.ram_z_mm), 6),
                                "support_plane_zm": round(float(st.support_plane_zm), 6),
                                "placement": _serialize_placement(st.workpiece_placement),
                                "collision_status": st_col_status,
                                "is_safe": bool(st.is_safe),
                                "is_conditional_safe": bool(getattr(st, "is_conditional_safe", False)),
                                "punch_overlap_volume_mm3": st_p_vol,
                                "die_overlap_volume_mm3": st_d_vol,
                                "punch_distance_mm": st_p_dist,
                                "die_distance_mm": st_d_dist,
                            })

                        print(
                            f"    Phase {pr.phase_name:<10}: status={pr.evidence_status.value}, "
                            f"states={len(pr.states)}, unres={unres_cnt}, elapsed={pr.elapsed_ms:.1f}ms, "
                            f"p_max={p_phase_vol:.6f}mm3, d_max={d_phase_vol:.6f}mm3"
                        )
                        if pr.unresolved_intervals:
                            print(f"      Unresolved intervals: {pr.unresolved_intervals} | diags={pr.diagnostics}")

                        phase_records.append({
                            "phase": pr.phase_name,
                            "status": pr.evidence_status.value,
                            "continuous_certification_status": pr.continuous_certification_status.value,
                            "is_safe": bool(pr.is_safe),
                            "is_conditional": bool(getattr(pr, "is_conditional", False)),
                            "states_evaluated": len(pr.states),
                            "subdivisions_count": int(pr.subdivisions_count),
                            "peak_recursion_depth": int(pr.peak_recursion_depth),
                            "unresolved_intervals_count": unres_cnt,
                            "unresolved_intervals": [[float(iv[0]), float(iv[1])] for iv in pr.unresolved_intervals],
                            "discontinuities": list(pr.discontinuities),
                            "solid_reconstructions_count": int(pr.solid_reconstructions_count),
                            "collision_queries_count": int(pr.collision_queries_count),
                            "min_punch_clearance_mm": float(pr.min_punch_clearance_mm),
                            "min_die_clearance_mm": float(pr.min_die_clearance_mm),
                            "punch_overlap_volume_mm3": p_phase_vol,
                            "die_overlap_volume_mm3": d_phase_vol,
                            "elapsed_ms": float(pr.elapsed_ms),
                            "diagnostics": list(pr.diagnostics),
                            "states": state_measurements,
                        })

            bend_elapsed_ms = (time.perf_counter() - t_bend0) * 1000.0

            # Compute overall max overlap volume across evaluated checks for this bend
            # Never substitute 0.0 when no 3D overlap measurement was obtained!
            measured_p_vols = [v for v in (pose_punch_vol, traj_max_punch_vol) if v is not None]
            measured_d_vols = [v for v in (pose_die_vol, traj_max_die_vol) if v is not None]
            bend_max_punch_vol = max(measured_p_vols) if measured_p_vols else None
            bend_max_die_vol = max(measured_d_vols) if measured_d_vols else None

            bend_has_collision = (
                pose_result.status == PhysicalEvidence.COLLISION
                or (traj_result is not None and traj_result.status == PhysicalEvidence.COLLISION)
            )
            bend_is_conditional_end_zone = (
                not bend_has_collision
                and (
                    pose_result.status == PhysicalEvidence.CONDITIONAL
                    or (traj_result is not None and traj_result.status == PhysicalEvidence.CONDITIONAL)
                )
            )
            if bend_is_conditional_end_zone:
                has_any_conditional_end_zone = True

            bend_has_unverified = (
                pose_result.status in (PhysicalEvidence.UNVERIFIED, PhysicalEvidence.SECTION_CLEAR_3D_UNVERIFIED)
                or (traj_result is not None and traj_result.status == PhysicalEvidence.UNVERIFIED)
            )

            # Record exact state details for first collision
            specific_state_info = None
            if bend_has_collision and first_collision_info is None:
                first_col_st = getattr(raw, "first_collision_state", None)
                state_punch_overlap = None
                state_die_overlap = None
                state_placement = pose_placement

                if first_col_st and getattr(first_col_st, "collision_report", None):
                    cr = first_col_st.collision_report
                    if cr.punch_result and cr.punch_result.overlap_volume_mm3 is not None:
                        state_punch_overlap = float(cr.punch_result.overlap_volume_mm3)
                    if cr.die_result and cr.die_result.overlap_volume_mm3 is not None:
                        state_die_overlap = float(cr.die_result.overlap_volume_mm3)
                    state_placement = _serialize_placement(first_col_st.workpiece_placement)
                elif pose_result.status == PhysicalEvidence.COLLISION:
                    state_punch_overlap = pose_punch_vol
                    state_die_overlap = pose_die_vol

                specific_state_info = {
                    "bend_id": bend_id,
                    "bend_index": idx,
                    "longitudinal_shift_xm_mm": shift_mm,
                    "first_detected_in": "TRAJECTORY" if (traj_result and traj_result.status == PhysicalEvidence.COLLISION) else "POSE",
                    "pose_native_status": pose_result.native_status,
                    "trajectory_native_status": traj_result.native_status if traj_result else "NOT_EVALUATED",
                    "phase": first_col_st.phase if first_col_st else "CONTACT",
                    "path_u": float(first_col_st.path_u) if (first_col_st and first_col_st.path_u is not None) else 0.0,
                    "signed_angle_deg": float(first_col_st.signed_angle_deg) if (first_col_st and first_col_st.signed_angle_deg is not None) else 0.0,
                    "ram_z_mm": float(first_col_st.ram_z_mm) if (first_col_st and first_col_st.ram_z_mm is not None) else float(panel.press_cycle.ram_z_mm),
                    "placement": state_placement,
                    "state_punch_overlap_volume_mm3": state_punch_overlap,
                    "state_die_overlap_volume_mm3": state_die_overlap,
                    "trajectory_max_punch_overlap_volume_mm3": traj_max_punch_vol,
                    "trajectory_max_die_overlap_volume_mm3": traj_max_die_vol,
                    "max_punch_overlap_volume_mm3": bend_max_punch_vol,
                    "max_die_overlap_volume_mm3": bend_max_die_vol,
                    "note": "first_detected_collision_state_by_search_not_guaranteed_initial_impact_instant",
                }
                first_collision_info = specific_state_info
                has_any_collision = True
                print(f"  >>> FIRST COLLISION RECORDED: {first_collision_info}")

            if bend_has_collision:
                has_any_collision = True
                if first_blocking_bend is None:
                    first_blocking_bend = bend_id
                    first_blocking_status = "COLLISION"
            elif bend_has_unverified:
                has_any_unverified = True
                if first_blocking_bend is None:
                    first_blocking_bend = bend_id
                    first_blocking_status = "UNVERIFIED"

            ez_rep = (
                (getattr(traj_result, "end_zone_analysis", None) if traj_result else None)
                or getattr(pose_result, "end_zone_analysis", None)
            )
            tool_len_rep = (
                (getattr(traj_result, "tool_length_info", None) if traj_result else None)
                or getattr(pose_result, "tool_length_info", None)
            )

            bend_summary = {
                "bend_index": idx,
                "bend_id": bend_id,
                "target_angle_deg": target_ang,
                "longitudinal_shift_xm_mm": shift_mm,
                "prior_completed_angles": prior_angles_record,
                "coverage": coverage_info,
                "tool_length_info": tool_len_rep,
                "is_conditional_end_zone": bend_is_conditional_end_zone,
                "end_zone_analysis": ez_rep.to_dict() if hasattr(ez_rep, "to_dict") else None,
                "is_conditional_after_rejected_bend": is_conditional,
                "conditional_reason": cond_reason,
                "pose_status": pose_result.status.value,
                "pose_section_status": pose_result.section_status,
                "pose_native_status": pose_result.native_status,
                "pose_placement": pose_placement,
                "pose_punch_overlap_volume_mm3": pose_punch_vol,
                "pose_die_overlap_volume_mm3": pose_die_vol,
                "pose_punch_distance_mm": pose_punch_dist,
                "pose_die_distance_mm": pose_die_dist,
                "trajectory_status": traj_result.status.value if traj_result else "NOT_EVALUATED",
                "trajectory_native_status": traj_result.native_status if traj_result else "NOT_EVALUATED",
                "trajectory_max_punch_overlap_volume_mm3": traj_max_punch_vol,
                "trajectory_max_die_overlap_volume_mm3": traj_max_die_vol,
                "total_states_evaluated": total_states,
                "total_unresolved_intervals": total_unresolved,
                "max_punch_overlap_volume_mm3": bend_max_punch_vol,
                "max_die_overlap_volume_mm3": bend_max_die_vol,
                "elapsed_ms": round(float(bend_elapsed_ms), 2),
                "detected_collision_state": specific_state_info,
                "phases": phase_records,
                "reason": traj_result.reason if traj_result else pose_result.reason,
            }
            sequence_records.append(bend_summary)

            # Strict acceptance stops immediately on first collision or unverified issue
            if not diagnostic_only and (bend_has_collision or bend_has_unverified):
                stop_reason = f"STOPPED_AT_FIRST_FAILURE:{bend_id}:{pose_result.status.value}/{traj_result.status.value if traj_result else 'NONE'}"
                print(f"STRICT_ACCEPTANCE_HALTED: {stop_reason}")
                # Save incremental report immediately before breaking
                report_data = _build_current_report_payload(stop_reason)
                save_report_safely(report_data, timestamp_str=run_timestamp_str, report_filename=report_filename)
                break

            # Save incremental report after each completed bend
            report_data = _build_current_report_payload("IN_PROGRESS")
            save_report_safely(report_data, timestamp_str=run_timestamp_str, report_filename=report_filename)

            # Progress fold state to target angle from model (only when continuing or in diagnostic mode)
            if hasattr(session.fold_state, "set_angle"):
                session.fold_state.set_angle(bend_id, target_ang)
            else:
                session.fold_state.angles[bend_id] = target_ang

        report_data = _build_current_report_payload(stop_reason)
        evaluated_bends = report_data["sequence_scope"]["evaluated_bends"]
        skipped_bends = report_data["sequence_scope"]["skipped_bends"]
        is_legitimate_pass = report_data["is_legitimate_pass"]
        is_conditional_plan = report_data["is_conditional_plan"]

        # Step A: Restore and verify session state (including transforms) BEFORE saving final report / printing PASS
        restore_session_state(session, initial_angles, initial_active_idx, initial_transforms)

        # Step B: Save final report safely (raises if write fails)
        saved_path = save_report_safely(report_data, timestamp_str=run_timestamp_str, report_filename=report_filename)
        report_saved = True
        print(f"\nSaved sequence report to {saved_path}")

        # Backward-compatibility keys in returned dict for single-bend diagnostic unit tests
        if len(sequence_records) == 1:
            report_data["pose"] = pose_result
            report_data["trajectory"] = traj_result if not diagnostic_only else None

        # Summary table
        print("\n=== SUMMARY TABLE OF EVALUATED BENDS ===")
        print("| Step | Bend | Target Ang | Shift (mm) | Cond? | Pose (3D) | Trajectory Status | States | Unresolved | Punch Vol | Die Vol |")
        print("|---|---|---:|---:|:---:|:---:|:---:|---:|---:|---:|---:|")
        for rec in sequence_records:
            cond_str = (
                "END_ZONE"
                if rec.get("is_conditional_end_zone")
                else ("YES" if rec.get("is_conditional_after_rejected_bend") else "NO")
            )
            p_vol_str = f"{rec['max_punch_overlap_volume_mm3']:.6f}" if rec["max_punch_overlap_volume_mm3"] is not None else "null"
            d_vol_str = f"{rec['max_die_overlap_volume_mm3']:.6f}" if rec["max_die_overlap_volume_mm3"] is not None else "null"
            print(
                f"| {rec['bend_index']+1} | {rec['bend_id']} | {rec['target_angle_deg']:+.1f}° | "
                f"{rec['longitudinal_shift_xm_mm']:+.2f} | {cond_str} | {rec['pose_native_status']} | "
                f"{rec['trajectory_native_status']} | {rec['total_states_evaluated']} | "
                f"{rec['total_unresolved_intervals']} | {p_vol_str} | {d_vol_str} |"
            )

        if skipped_bends:
            print(f"\nUNVERIFIED / SKIPPED BENDS: {skipped_bends}")

        if diagnostic_only:
            print("\nPHYSICAL_FRAME_DIAGNOSTIC_ONLY: completed diagnostic run without acceptance claim")
            return report_data

        if has_any_collision:
            fc = first_collision_info
            ram_str = f"{fc['ram_z_mm']:.2f}mm" if (fc and fc.get("ram_z_mm") is not None) else "N/A"
            vol_val = fc.get("state_punch_overlap_volume_mm3") if fc else None
            vol_str = f"{vol_val:.6f} mm³" if (vol_val is not None) else "null"
            max_vol_val = fc.get("max_punch_overlap_volume_mm3") if fc else None
            max_vol_str = f"{max_vol_val:.6f} mm³" if (max_vol_val is not None) else "null"
            print("\nPHYSICAL_STAGE2_RESULT: COLLISION_DETECTED")
            print(
                f"First detected collision: bend={fc['bend_id']} (step {fc['bend_index']+1}), "
                f"phase={fc['phase']}, ram_z={ram_str}, state_punch_overlap={vol_str}, max_punch_overlap={max_vol_str}"
            )
            raise RuntimeError(f"PHYSICAL_SEQUENCE_COLLISION_DETECTED: first collision at {fc['bend_id']} in phase {fc['phase']}")

        if is_conditional_plan:
            print(
                f"\nPHYSICAL_STAGE2_RESULT: CONDITIONAL_END_ZONE_PLAN "
                f"(all {total_expected_count} bends evaluated; requires relief-cut punch; NOT_STRICT_PASS)"
            )
            return report_data

        if not is_legitimate_pass:
            print(f"\nPHYSICAL_STAGE2_RESULT: INCOMPLETE/UNRESOLVED (evaluated {len(evaluated_bends)} of {total_expected_count})")
            raise RuntimeError(
                f"PHYSICAL_SEQUENCE_INCOMPLETE / PHYSICAL_ACCEPTANCE_FAILED_INCOMPLETE_TRAJECTORY: "
                f"only {len(evaluated_bends)} of {total_expected_count} "
                f"bends evaluated or unverified issue present ({stop_reason})"
            )

        pass_marker = (
            "PHYSICAL_STAGE2_FREECAD_GUI_ACCEPTANCE_PASS"
            if is_live_gui
            else "PHYSICAL_STAGE2_HEADLESS_SAMPLED_PASS (NOT_GUI_VERIFIED)"
        )
        print(f"\n{pass_marker}: all {total_expected_count} bends SAMPLED_3D_CLEAR_NOT_CONTINUOUS | delta=(0.0, 0.0, 0.0)")
        return report_data

    except Exception as exc:
        if not report_saved and session is not None:
            partial_data = {
                "report_schema": "25d_sequence_acceptance_partial_failure",
                "timestamp": t_run_start,
                "git": git_info,
                "environment": env_name,
                "error": str(exc),
                "sequence": sequence_records,
                "overall_status": "ERROR_DURING_EXECUTION",
            }
            try:
                save_report_safely(partial_data, timestamp_str=run_timestamp_str, report_filename=report_filename)
            except Exception:
                pass
        raise

    finally:
        if session is not None:
            try:
                restore_session_state(session, initial_angles, initial_active_idx, initial_transforms)
            except Exception as restore_err:
                print(f"WARNING: Exception during fold state restoration in finally: {restore_err}", file=sys.stderr)

        if panel is not None:
            try:
                if hasattr(panel, "timer"):
                    panel.timer.stop()
                panel.close()
                panel.deleteLater()
            except Exception:
                pass
        if doc is not None and getattr(doc, "Name", None) not in names_before:
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


def _parse_shifts(raw_str: str | None) -> dict[str, float] | None:
    if not raw_str:
        return None
    out: dict[str, float] = {}
    for item in raw_str.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"Invalid shift specification '{item}', expected BEND_ID=VALUE_MM")
        k, v = item.split("=", 1)
        out[k.strip()] = float(v.strip())
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run 2.5D + D6 physical sequence acceptance.")
    parser.add_argument("--diagnostic-only", action="store_true", help="Continue after failures without raising RuntimeError")
    parser.add_argument("--pose-only", action="store_true", help="Check stationary initial contact pose only")
    parser.add_argument("--max-bends", type=int, default=None, help="Limit number of bends evaluated")
    parser.add_argument("--punch", type=str, default=None, help="Punch tool ID or filename")
    parser.add_argument("--die", type=str, default=None, help="Die tool ID or filename")
    parser.add_argument("--shifts", type=str, default=None, help="Comma-separated per-bend shifts, e.g. B01=0.0,B02=2.4")
    parser.add_argument("--bend-order", type=str, default=None, help="Comma-separated bend order")
    parser.add_argument("--report-name", type=str, default=None, help="Custom report filename in reports/")
    parser.add_argument("--max-phase-time", type=float, default=120.0, help="Max time budget per trajectory phase in seconds (default 120.0)")
    parser.add_argument("--force-trajectory-on-pose-failure", action="store_true", help="Explicit diagnostic mode: evaluate trajectory even if stationary initial pose failed")
    parser.add_argument("--match-active-bend-length", action="store_true", help="Development mode: virtual punch & die lengths equal active bend length")
    parser.add_argument("--allow-end-zone-collision", action="store_true", help="Allow conditional continuation (CONDITIONAL) on END_ZONE_ONLY punch collisions")
    parser.add_argument("--end-zone-length-mm", type=float, default=0.0, help="Punch end-zone length in mm from each end (default 0.0)")
    parser.add_argument("--search-sequence", action="store_true", help="Run backtracking sequence search + forward replay verification")
    parser.add_argument("--max-search-states", type=int, default=500, help="Max states visited during sequence search")
    parser.add_argument("--max-search-checks", type=int, default=1000, help="Max step evaluations during sequence search")
    parser.add_argument("--max-search-time-s", type=float, default=600.0, help="Max time budget in seconds for sequence search")
    args = parser.parse_args()

    order_arg = [b.strip() for b in args.bend_order.split(",") if b.strip()] if args.bend_order else None
    shifts_arg = _parse_shifts(args.shifts)
    run(
        diagnostic_only=args.diagnostic_only,
        pose_only=args.pose_only,
        max_bends=args.max_bends,
        bend_order=order_arg,
        punch_id=args.punch,
        die_id=args.die,
        bend_shifts=shifts_arg,
        report_filename=args.report_name,
        max_phase_time_s=args.max_phase_time,
        force_trajectory_on_pose_failure=args.force_trajectory_on_pose_failure,
        match_active_bend_length=args.match_active_bend_length,
        allow_end_zone_collision=args.allow_end_zone_collision,
        end_zone_length_mm=args.end_zone_length_mm,
        search_sequence=args.search_sequence,
        max_search_states=args.max_search_states,
        max_search_checks=args.max_search_checks,
        max_search_time_s=args.max_search_time_s,
    )
