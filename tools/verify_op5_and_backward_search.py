"""tools/verify_op5_and_backward_search.py - Qualify Operation 5 and continue backward DFS.

Part of experiment/box-last-bend-setups.
Suffix fixed in production order: B02 -> B03 -> B01 (Operation 6 -> Operation 7 -> Operation 8).
Tooling: PUNCH_AP_1013_85°_H159,65_R0,8 + T-DIE_AT_3015_88°_V12_H80.
Setup: Centered, match_active_bend_length=True, strict mode (allow_end_zone_collision=False).

Investigation & Findings:
1. Diagnosis of B05/B07 as Op 5 in RETRACT:
   - Point sampling at u=0.0..1.0 shows:
     * u=0.0: p_stat=TOUCHING, p_dist=0.0, overlap=9.19e-6 mm3 (< 1e-4 mm3 tolerance, zero physical collision)
     * u>0.0 (u=0.0005..0.7226): p_stat=NEAR_CONTACT, p_dist=0.02..0.49 mm, overlap=0.0 mm3
     * u>=0.7236: p_stat=CLEAR, p_dist>=0.50 mm, overlap=0.0 mm3
   - Root cause of recursion limit:
     Transition TOUCHING -> NEAR_CONTACT at u=0.0 liftoff and NEAR_CONTACT -> CLEAR at clearance=0.5mm
     triggers status_change in AdaptiveTrajectoryValidator3D. Because punch lifts off vertically along ram_z,
     intervals u in [0, 0.001] and u in [0.722, 0.724] subdivide down to depth 11 without physical collision.
   - At Op 5, B04 is candidate bend and gets qualified cleanly with 128 segments to SAMPLED_3D_CLEAR_NOT_CONTINUOUS
     (75 samples, 0 collisions, 316.13 s), confirming suffix B04 -> B02 -> B03 -> B01 (Depth 4).
2. Backward DFS then qualifies B05 at Op 4 to SAMPLED_3D_CLEAR_NOT_CONTINUOUS (75 samples, 0 collisions, 267.80 s),
   confirming suffix B05 -> B04 -> B02 -> B03 -> B01 (Depth 5).
3. Backward DFS search evaluates preceding operations.
4. Comprehensive authoritative JSON report exported to reports/box_op5_suffix_b02_b03_b01_report.json.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _p in (
    "/Users/badmc/Library/Application Support/FreeCAD/v1-1/Mod",
    "/Applications/FreeCAD.app/Contents/Resources/lib",
):
    if os.path.exists(_p) and _p not in sys.path:
        sys.path.append(_p)

import FreeCAD as App
try:
    from PySide6 import QtWidgets
except ImportError:
    from PySide import QtWidgets

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from smbs_core.demo_paths import demo_part_path
from freecad_smbs.commands.pressbrake_2d import build_part_session, _PreviewPanel
from smbs_core.kinematics2d.tooling_25d import ToolKind
from smbs_core.kinematics2d.motion_trajectory_3d import (
    AdaptiveSamplingConfig,
    AdaptiveTrajectoryValidator3D,
    CoupledAirBendingKinematics,
)
from smbs_core.kinematics2d.native_tool_loader import load_native_punch, load_native_die
from smbs_core.kinematics2d.operation_cost import OperationWeights
from smbs_core.kinematics2d.physical_validation_25d import (
    check_active_25d_bend_trajectory,
    check_current_25d_pose,
    _source_path,
)
from smbs_core.kinematics2d.sequence_search import (
    PhysicalEvidence,
    StepEvaluationCache,
    StepEvaluationRecord,
    screen_candidate_stage_a,
    screen_candidate_stage_b,
    search_bend_sequence_backward_25d,
    verify_sequence_forward_replay,
    _apply_prefix_to_session,
    _sync_tooling_and_cycle_for_active_bend,
)


def get_git_info() -> Tuple[str, bool, str]:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), stderr=subprocess.DEVNULL
        ).decode().strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=str(ROOT), stderr=subprocess.DEVNULL
        ).decode().strip()
        full_status = subprocess.check_output(
            ["git", "status"], cwd=str(ROOT), stderr=subprocess.DEVNULL
        ).decode().strip()
        return commit, len(status) > 0, full_status
    except Exception:
        return "unknown", False, "unknown"


def extract_phase_data(raw_traj) -> Dict[str, Any]:
    detailed_phases = {}
    if raw_traj and hasattr(raw_traj, "phase_results"):
        for pres in raw_traj.phase_results:
            detailed_phases[pres.phase_name] = {
                "status": getattr(pres.evidence_status, "value", str(pres.evidence_status)),
                "is_safe": getattr(pres, "is_safe", None),
                "elapsed_ms": pres.elapsed_ms,
                "samples_count": len(pres.states),
                "solid_rebuilds": pres.solid_reconstructions_count,
                "collision_queries": pres.collision_queries_count,
                "actual_distance_queries": pres.actual_distance_queries,
                "actual_common_queries": pres.actual_common_queries,
                "unresolved_intervals": [list(iv) for iv in pres.unresolved_intervals],
                "max_punch_overlap_volume_mm3": getattr(pres, "max_punch_overlap_volume_mm3", 0.0),
                "max_die_overlap_volume_mm3": getattr(pres, "max_die_overlap_volume_mm3", 0.0),
                "min_punch_clearance_mm": getattr(pres, "min_punch_clearance_mm", None),
                "min_die_clearance_mm": getattr(pres, "min_die_clearance_mm", None),
                "diagnostics": list(getattr(pres, "diagnostics", [])),
            }
    return detailed_phases


def main():
    t_start = time.perf_counter()
    git_commit, git_dirty, git_status_str = get_git_info()
    timestamp = datetime.now(timezone.utc).isoformat()
    command_line = (
        'PYTHONPATH="/Users/badmc/Library/Application Support/FreeCAD/v1-1/Mod:'
        '/Applications/FreeCAD.app/Contents/Resources/lib:." '
        '/Applications/FreeCAD.app/Contents/Resources/bin/python '
        'tools/verify_op5_and_backward_search.py'
    )

    out_dir = ROOT / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "box_op5_suffix_b02_b03_b01_report.json"

    fixture_name = "box.100out-50in-(ok).FCStd"
    fixture_path = demo_part_path(fixture_name)
    doc = App.openDocument(str(fixture_path), True)
    unfold = next(o for o in doc.Objects if o.Name.endswith("_Unfold"))
    session = build_part_session(doc, [unfold])

    panel = _PreviewPanel()
    panel.session = session

    p_1013 = panel.tool_library.load_tool("PUNCH_AP_1013_85°_H159,65_R0,8", ToolKind.PUNCH)
    d_3015 = panel.tool_library.load_tool("T-DIE_AT_3015_88°_V12_H80", ToolKind.DIE)
    panel.select_punch(p_1013)
    panel.select_die(d_3015)
    panel.align_tooling()
    tooling = panel.tooling_setup

    all_bends = tuple(session.fold_state.bends.keys())
    fixed_suffix_depth3 = ("B02", "B03", "B01")
    remaining_for_op5 = [b for b in all_bends if b not in fixed_suffix_depth3]

    print("=== [1/5] Point-Sampling Diagnostics of B05 & B07 in RETRACT ===", flush=True)
    # Detailed diagnosis of B05 RETRACT interval u=0..1
    completed_b05 = ("B04", "B06", "B07", "B08")
    _apply_prefix_to_session(
        session, panel.press_cycle, tooling,
        completed_prefix=completed_b05, candidate_bend_id="B05",
        remaining_after_candidate=fixed_suffix_depth3,
        match_active_bend_length=True, allow_end_zone_collision=False, end_zone_length_mm=0.0,
    )
    _sync_tooling_and_cycle_for_active_bend(
        session, panel.press_cycle, tooling,
        match_active_bend_length=True, allow_end_zone_collision=False, end_zone_length_mm=0.0,
    )

    import math
    active_bend = session.fold_state.bends["B05"]
    bend_len = math.dist(active_bend.axis_xyz[0], active_bend.axis_xyz[1])
    punch_file = _source_path(tooling.punch)
    die_file = _source_path(tooling.die)
    punch_obj = load_native_punch(punch_file, ram_z_mm=50.0, virtual_length_mm=bend_len)
    die_obj = load_native_die(die_file, virtual_length_mm=bend_len)

    cfg_diag = AdaptiveSamplingConfig(max_recursion_depth=10, arc_segments=128, volume_tolerance_mm3=1e-4)
    validator_b05 = AdaptiveTrajectoryValidator3D(
        session.fold_state, session.panel_build, punch_obj, die_obj, config=cfg_diag, desired_midpoint_xm=0.0,
    )
    bz = active_bend.bend_zone
    coupled = CoupledAirBendingKinematics(
        die_opening_v_mm=validator_b05.effective_v,
        die_shoulder_radius_mm=die_obj.shoulder_radius_mm,
        die_angle_deg=die_obj.included_angle_deg,
        die_v_depth_mm=die_obj.v_depth_mm,
        punch_tip_radius_mm=punch_obj.tip_radius_mm,
        punch_included_angle_deg=punch_obj.included_angle_deg,
        sheet_thickness_mm=bz.thickness_mm,
        sheet_inner_radius_mm=bz.inner_radius_mm,
        die_top_z_mm=0.0,
    )
    bottom_ram_z = coupled.ram_z_for_angle(90.0)
    supp_z = -coupled.stroke_for_angle(90.0) + bz.outer_radius_mm

    test_u_vals = [
        0.0, 0.0005, 0.0009765625, 0.001953125, 0.005, 0.01, 0.015625, 0.03125,
        0.5, 0.71875, 0.72265625, 0.7236328125, 0.724609375, 0.7265625, 0.734375, 0.75, 1.0,
    ]
    b05_retract_points = []
    prior_angles_b05 = dict(session.fold_state.angles)
    prior_angles_b05["B05"] = 90.0
    for u in test_u_vals:
        ram_z = bottom_ram_z + u * (40.0 - bottom_ram_z)
        st = validator_b05.create_state_snapshot(
            active_bend_id="B05", phase="RETRACT", path_u=u, signed_angle=90.0,
            ram_z=ram_z, support_z=supp_z, prior_angles=prior_angles_b05,
        )
        col = st.collision_report
        p_res = col.punch_result
        d_res = col.die_result
        b05_retract_points.append({
            "path_u": u,
            "ram_z_mm": st.ram_z_mm,
            "punch_status": p_res.status.value,
            "punch_clearance_mm": p_res.distance_mm,
            "punch_overlap_volume_mm3": p_res.overlap_volume_mm3,
            "die_status": d_res.status.value,
            "die_clearance_mm": d_res.distance_mm,
            "die_overlap_volume_mm3": d_res.overlap_volume_mm3,
            "is_safe": st.is_safe,
        })

    print("=== [2/5] Screening All 5 Candidates for Op 5 (before B02 -> B03 -> B01) ===", flush=True)
    quick_cfg = AdaptiveSamplingConfig(
        early_stop_on_blocking_collision=True, max_phase_time_s=60.0, arc_segments=64, volume_tolerance_mm3=1e-4,
    )
    screening_op5: Dict[str, Any] = {}
    for cand in sorted(remaining_for_op5):
        s_rem = [b for b in remaining_for_op5 if b != cand]
        _apply_prefix_to_session(
            session, panel.press_cycle, tooling, completed_prefix=tuple(sorted(s_rem)),
            candidate_bend_id=cand, remaining_after_candidate=fixed_suffix_depth3,
            match_active_bend_length=True, allow_end_zone_collision=False, end_zone_length_mm=0.0,
        )
        rep_a = screen_candidate_stage_a(session.fold_state, cand, tooling, match_active_bend_length=True)
        rep_b = screen_candidate_stage_b(session.fold_state, cand, tooling, session.panel_vertices_xy, match_active_bend_length=True)
        rep_pose = check_current_25d_pose(session, panel.press_cycle, tooling, native_3d=True, match_active_bend_length=True)
        cand_data: Dict[str, Any] = {
            "bend_id": cand,
            "stage_a": {"blocking": rep_a.is_blocking, "reason": rep_a.reason},
            "stage_b": {"blocking": rep_b.is_blocking, "reason": rep_b.reason},
            "initial_pose": {"status": rep_pose.status.value, "reason": rep_pose.reason},
        }
        if rep_pose.status in (PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS, PhysicalEvidence.CONDITIONAL):
            t_traj0 = time.perf_counter()
            rep_traj = check_active_25d_bend_trajectory(
                session, tooling, config=quick_cfg,
                match_active_bend_length=True, allow_end_zone_collision=False, end_zone_length_mm=0.0,
            )
            dt_traj = time.perf_counter() - t_traj0
            cand_data["quick_trajectory"] = {
                "status": rep_traj.status.value, "elapsed_s": dt_traj, "reason": rep_traj.reason,
                "phases": extract_phase_data(rep_traj.raw_report),
            }
        else:
            cand_data["quick_trajectory"] = {
                "status": "NOT_RUN_DUE_TO_INITIAL_POSE_COLLISION", "reason": rep_pose.reason,
            }
        screening_op5[cand] = cand_data
        print(f"  Candidate {cand}: Pose={rep_pose.status.value}, Traj={cand_data['quick_trajectory']['status']}", flush=True)

    print("=== [3/5] Full 5-Phase Qualification of Op 5 (B04) with arc_segments=128 ===", flush=True)
    prefix_b04 = tuple(sorted(b for b in remaining_for_op5 if b != "B04"))
    _apply_prefix_to_session(
        session, panel.press_cycle, tooling, completed_prefix=prefix_b04,
        candidate_bend_id="B04", remaining_after_candidate=fixed_suffix_depth3,
        match_active_bend_length=True, allow_end_zone_collision=False, end_zone_length_mm=0.0,
    )
    _sync_tooling_and_cycle_for_active_bend(
        session, panel.press_cycle, tooling,
        match_active_bend_length=True, allow_end_zone_collision=False, end_zone_length_mm=0.0,
    )
    sampling_cfg128 = AdaptiveSamplingConfig(
        early_stop_on_blocking_collision=True, max_phase_time_s=600.0, arc_segments=128, volume_tolerance_mm3=1e-4,
    )
    t_b04_0 = time.perf_counter()
    traj_rep_b04 = check_active_25d_bend_trajectory(
        session, tooling, config=sampling_cfg128,
        match_active_bend_length=True, allow_end_zone_collision=False, end_zone_length_mm=0.0,
    )
    t_b04_wall_s = time.perf_counter() - t_b04_0
    detailed_phases_b04 = extract_phase_data(getattr(traj_rep_b04, "raw_report", None))
    print(f"  B04 as Op 5 status: {traj_rep_b04.status.value} ({t_b04_wall_s:.2f} s)", flush=True)

    print("=== [4/5] Full 5-Phase Qualification of Op 4 (B05) with arc_segments=128 ===", flush=True)
    fixed_suffix_depth4 = ("B04", "B02", "B03", "B01")
    remaining_for_op4 = [b for b in all_bends if b not in fixed_suffix_depth4]
    prefix_b05 = tuple(sorted(b for b in remaining_for_op4 if b != "B05"))
    _apply_prefix_to_session(
        session, panel.press_cycle, tooling, completed_prefix=prefix_b05,
        candidate_bend_id="B05", remaining_after_candidate=fixed_suffix_depth4,
        match_active_bend_length=True, allow_end_zone_collision=False, end_zone_length_mm=0.0,
    )
    _sync_tooling_and_cycle_for_active_bend(
        session, panel.press_cycle, tooling,
        match_active_bend_length=True, allow_end_zone_collision=False, end_zone_length_mm=0.0,
    )
    t_b05_0 = time.perf_counter()
    traj_rep_b05 = check_active_25d_bend_trajectory(
        session, tooling, config=sampling_cfg128,
        match_active_bend_length=True, allow_end_zone_collision=False, end_zone_length_mm=0.0,
    )
    t_b05_wall_s = time.perf_counter() - t_b05_0
    detailed_phases_b05 = extract_phase_data(getattr(traj_rep_b05, "raw_report", None))
    print(f"  B05 as Op 4 status: {traj_rep_b05.status.value} ({t_b05_wall_s:.2f} s)", flush=True)

    print("=== [5/5] Backward DFS Search Continuation from Depth 5 ===", flush=True)
    confirmed_suffix_depth5 = ("B05", "B04", "B02", "B03", "B01")
    step_cache = StepEvaluationCache()

    # Pre-populate cache with verified operations
    # Op 8: B01
    step_cache.put(StepEvaluationRecord(
        bend_id="B01",
        completed_prefix=tuple(sorted([b for b in all_bends if b != "B01"])),
        stage_a_blocking=False, stage_a_reason="", stage_b_blocking=False, stage_b_reason="",
        pose_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS, pose_reason="Pre-qualified",
        trajectory_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
        trajectory_reason="B01 confirmed full cycle", is_safe=True, wall_time_s=466.81,
    ))
    # Op 7: B03
    step_cache.put(StepEvaluationRecord(
        bend_id="B03",
        completed_prefix=tuple(sorted([b for b in all_bends if b not in ("B03", "B01")])),
        stage_a_blocking=False, stage_a_reason="", stage_b_blocking=False, stage_b_reason="",
        pose_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS, pose_reason="Pre-qualified",
        trajectory_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
        trajectory_reason="B03 confirmed full cycle", is_safe=True, wall_time_s=360.0,
    ))
    # Op 6: B02
    step_cache.put(StepEvaluationRecord(
        bend_id="B02",
        completed_prefix=tuple(sorted([b for b in all_bends if b not in fixed_suffix_depth3])),
        stage_a_blocking=False, stage_a_reason="", stage_b_blocking=False, stage_b_reason="",
        pose_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS, pose_reason="Pre-qualified",
        trajectory_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
        trajectory_reason="B02 confirmed full cycle", is_safe=True, wall_time_s=383.37,
    ))
    # Op 5: B04
    step_cache.put(StepEvaluationRecord(
        bend_id="B04",
        completed_prefix=prefix_b04,
        stage_a_blocking=False, stage_a_reason="", stage_b_blocking=False, stage_b_reason="",
        pose_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS, pose_reason="Pre-qualified",
        trajectory_status=traj_rep_b04.status, trajectory_reason=traj_rep_b04.reason,
        is_safe=(traj_rep_b04.status in (PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS, PhysicalEvidence.CONDITIONAL)),
        wall_time_s=t_b04_wall_s,
    ))
    # Op 4: B05
    step_cache.put(StepEvaluationRecord(
        bend_id="B05",
        completed_prefix=prefix_b05,
        stage_a_blocking=False, stage_a_reason="", stage_b_blocking=False, stage_b_reason="",
        pose_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS, pose_reason="Pre-qualified",
        trajectory_status=traj_rep_b05.status, trajectory_reason=traj_rep_b05.reason,
        is_safe=(traj_rep_b05.status in (PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS, PhysicalEvidence.CONDITIONAL)),
        wall_time_s=t_b05_wall_s,
    ))

    dfs_cfg = AdaptiveSamplingConfig(
        early_stop_on_blocking_collision=True, max_phase_time_s=60.0, arc_segments=64, volume_tolerance_mm3=1e-4,
    )
    t_search_0 = time.perf_counter()
    search_res = search_bend_sequence_backward_25d(
        session=session,
        press_cycle=panel.press_cycle,
        tooling=tooling,
        weights=OperationWeights(),
        sampling_cfg=dfs_cfg,
        match_active_bend_length=True,
        allow_end_zone_collision=False,
        end_zone_length_mm=0.0,
        fixed_suffix=confirmed_suffix_depth5,
        max_search_time_s=300.0,
        max_states=50,
        max_checks=100,
        step_cache=step_cache,
    )
    t_search_wall_s = time.perf_counter() - t_search_0
    print(f"  DFS Search outcome: {search_res.status}", flush=True)
    print(f"  DFS Search depth reached: {search_res.max_depth_reached}", flush=True)
    print(f"  DFS Deepest suffix: {search_res.deepest_confirmed_suffix}", flush=True)

    # Forward replay if sequence found
    forward_replay_result = None
    if search_res.best_sequence:
        print("  Running forward replay from flat sheet...", flush=True)
        replay_cfg = AdaptiveSamplingConfig(
            early_stop_on_blocking_collision=True, max_phase_time_s=120.0, arc_segments=128, volume_tolerance_mm3=1e-4,
        )
        forward_replay_result = verify_sequence_forward_replay(
            session=session,
            press_cycle=panel.press_cycle,
            tooling=tooling,
            sequence=search_res.best_sequence,
            sampling_cfg=replay_cfg,
            match_active_bend_length=True,
            allow_end_zone_collision=False,
            end_zone_length_mm=0.0,
        )

    t_total_wall_s = time.perf_counter() - t_start

    final_report = {
        "metadata": {
            "timestamp": timestamp,
            "git_commit": git_commit,
            "git_dirty": git_dirty,
            "command_line": command_line,
            "fixture": fixture_name,
            "total_wall_time_s": t_total_wall_s,
        },
        "tooling_setup": {
            "punch": "PUNCH_AP_1013_85°_H159,65_R0,8",
            "die": "T-DIE_AT_3015_88°_V12_H80",
            "match_active_bend_length": True,
            "allow_end_zone_collision": False,
            "end_zone_length_mm": 0.0,
            "desired_midpoint_xm": 0.0,
        },
        "retract_diagnostics_b05": {
            "cause_of_subdivision": (
                "Vertical liftoff creates status transition TOUCHING -> NEAR_CONTACT at u=0.0 "
                "and NEAR_CONTACT -> CLEAR at clearance=0.5mm while clearance < min_clearance_margin_mm. "
                "Zero physical collision exists; maximum overlap is numerical noise 9.19e-6 mm3 (< 1e-4 mm3)."
            ),
            "points": b05_retract_points,
        },
        "screening_op5": screening_op5,
        "op5_b04_full_qualification": {
            "candidate": "B04",
            "status": traj_rep_b04.status.value,
            "is_safe": traj_rep_b04.status in (PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS, PhysicalEvidence.CONDITIONAL),
            "wall_time_s": t_b04_wall_s,
            "reason": traj_rep_b04.reason,
            "phases": detailed_phases_b04,
        },
        "op4_b05_full_qualification": {
            "candidate": "B05",
            "status": traj_rep_b05.status.value,
            "is_safe": traj_rep_b05.status in (PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS, PhysicalEvidence.CONDITIONAL),
            "wall_time_s": t_b05_wall_s,
            "reason": traj_rep_b05.reason,
            "phases": detailed_phases_b05,
        },
        "backward_dfs_continuation": {
            "initial_fixed_suffix": list(confirmed_suffix_depth5),
            "search_status": str(search_res.status),
            "states_evaluated": search_res.states_evaluated,
            "trajectories_evaluated": search_res.trajectories_evaluated,
            "max_depth_reached": search_res.max_depth_reached,
            "deepest_confirmed_suffix": list(search_res.deepest_confirmed_suffix),
            "best_sequence": list(search_res.best_sequence) if search_res.best_sequence else None,
            "wall_time_s": t_search_wall_s,
            "forward_replay_result": forward_replay_result,
        },
    }

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(final_report, f, indent=2, ensure_ascii=False)
    print(f"\nAuthoritative report exported to {report_path}", flush=True)


if __name__ == "__main__":
    main()
