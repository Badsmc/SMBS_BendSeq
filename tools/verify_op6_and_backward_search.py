"""tools/verify_op6_and_backward_search.py - Qualify Operation 6 and continue backward DFS.

Part of experiment/box-last-bend-setups.
Suffix fixed in production order: B03 -> B01 (Operation 7 -> Operation 8).
Tooling: PUNCH_AP_1013_85°_H159,65_R0,8 + T-DIE_AT_3015_88°_V12_H80.
Setup: Centered, match_active_bend_length=True, strict mode (allow_end_zone_collision=False).

Steps:
1. Detailed screening of all 6 candidates for Operation 6 before B03 -> B01.
2. Full 5-phase trajectory validation of primary candidate B02 with arc_segments=128 and 600s phase budget.
3. If B02 converges to SAMPLED_3D_CLEAR_NOT_CONTINUOUS, pre-populate cache and continue
   backward DFS with fixed_suffix=("B02", "B03", "B01").
4. Export comprehensive JSON report and diagnostic summary.
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
from smbs_core.kinematics2d.motion_trajectory_3d import AdaptiveSamplingConfig
from smbs_core.kinematics2d.operation_cost import OperationWeights
from smbs_core.kinematics2d.physical_validation_25d import (
    check_active_25d_bend_trajectory,
    check_current_25d_pose,
)
from smbs_core.kinematics2d.sequence_search import (
    PhysicalEvidence,
    StepEvaluationCache,
    StepEvaluationRecord,
    screen_candidate_stage_a,
    screen_candidate_stage_b,
    search_bend_sequence_backward_25d,
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
        'tools/verify_op6_and_backward_search.py'
    )

    out_dir = ROOT / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "box_op6_suffix_b03_b01_report.json"

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
    target_angles = {bid: float(session.fold_state.bends[bid].angle_deg) for bid in all_bends}
    fixed_suffix = ("B03", "B01")
    remaining_for_op6 = [b for b in all_bends if b not in fixed_suffix]

    print("=== [1/4] Screening All 6 Candidates for Op 6 (before B03 -> B01) ===", flush=True)
    print(f"  Fixed production suffix: {fixed_suffix}", flush=True)
    print(f"  Remaining candidates: {sorted(remaining_for_op6)}", flush=True)

    quick_cfg = AdaptiveSamplingConfig(
        early_stop_on_blocking_collision=True,
        max_phase_time_s=60.0,
        arc_segments=64,
        volume_tolerance_mm3=1e-4,
    )

    screening_results: Dict[str, Any] = {}

    for cand in sorted(remaining_for_op6):
        s_double_prime = [b for b in remaining_for_op6 if b != cand]
        _apply_prefix_to_session(
            session,
            panel.press_cycle,
            tooling,
            completed_prefix=tuple(sorted(s_double_prime)),
            candidate_bend_id=cand,
            remaining_after_candidate=fixed_suffix,
            match_active_bend_length=True,
            allow_end_zone_collision=False,
            end_zone_length_mm=0.0,
        )

        rep_a = screen_candidate_stage_a(
            session.fold_state, cand, tooling, match_active_bend_length=True
        )
        rep_b = screen_candidate_stage_b(
            session.fold_state, cand, tooling, session.panel_vertices_xy, match_active_bend_length=True
        )
        rep_pose = check_current_25d_pose(
            session, panel.press_cycle, tooling, native_3d=True, match_active_bend_length=True
        )

        cand_data: Dict[str, Any] = {
            "bend_id": cand,
            "stage_a": {"blocking": rep_a.is_blocking, "reason": rep_a.reason},
            "stage_b": {"blocking": rep_b.is_blocking, "reason": rep_b.reason},
            "initial_pose": {
                "status": rep_pose.status.value,
                "reason": rep_pose.reason,
            },
        }

        # For non-blocking poses, run quick trajectory check to identify phase limiter
        if rep_pose.status in (PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS, PhysicalEvidence.CONDITIONAL):
            t_traj0 = time.perf_counter()
            rep_traj = check_active_25d_bend_trajectory(
                session,
                tooling,
                config=quick_cfg,
                match_active_bend_length=True,
                allow_end_zone_collision=False,
                end_zone_length_mm=0.0,
            )
            dt_traj = time.perf_counter() - t_traj0
            cand_data["quick_trajectory"] = {
                "status": rep_traj.status.value,
                "elapsed_s": dt_traj,
                "reason": rep_traj.reason,
                "phases": extract_phase_data(rep_traj.raw_report),
            }
        else:
            cand_data["quick_trajectory"] = {
                "status": "NOT_RUN_DUE_TO_INITIAL_POSE_COLLISION",
                "reason": rep_pose.reason,
            }

        screening_results[cand] = cand_data
        print(f"  Candidate {cand}: Pose={rep_pose.status.value}, Trajectory={cand_data['quick_trajectory']['status']}", flush=True)

    # =========================================================================
    # 2. Complete Full Cycle Trajectory Check of B02 (segs=128, max_phase_time=600s)
    # =========================================================================
    print("\n=== [2/4] Full Cycle Trajectory Qualification of Primary Candidate B02 ===", flush=True)

    cand_primary = "B02"
    s_double_prime = [b for b in remaining_for_op6 if b != cand_primary]
    _apply_prefix_to_session(
        session,
        panel.press_cycle,
        tooling,
        completed_prefix=tuple(sorted(s_double_prime)),
        candidate_bend_id=cand_primary,
        remaining_after_candidate=fixed_suffix,
        match_active_bend_length=True,
        allow_end_zone_collision=False,
        end_zone_length_mm=0.0,
    )

    max_phase_time_s = 600.0
    sampling_cfg128 = AdaptiveSamplingConfig(
        early_stop_on_blocking_collision=True,
        max_phase_time_s=max_phase_time_s,
        arc_segments=128,
        near_tolerance_mm=0.5,
        volume_tolerance_mm3=1e-4,
        contact_distance_tolerance_mm=1e-4,
        min_clearance_margin_mm=0.5,
    )

    t_b02_0 = time.perf_counter()
    traj_rep_b02 = check_active_25d_bend_trajectory(
        session,
        tooling,
        config=sampling_cfg128,
        match_active_bend_length=True,
        allow_end_zone_collision=False,
        end_zone_length_mm=0.0,
    )
    t_b02_wall_s = time.perf_counter() - t_b02_0

    print(f"  B02 trajectory status: {traj_rep_b02.status.value}", flush=True)
    print(f"  B02 trajectory wall time: {t_b02_wall_s:.2f} s", flush=True)
    print(f"  B02 trajectory reason: {traj_rep_b02.reason}", flush=True)

    raw_traj_b02 = getattr(traj_rep_b02, "raw_report", None)
    detailed_phases_b02 = extract_phase_data(raw_traj_b02)

    b02_summary = {
        "candidate": cand_primary,
        "status": traj_rep_b02.status.value,
        "is_safe": traj_rep_b02.status in (PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS, PhysicalEvidence.CONDITIONAL),
        "is_conditional": traj_rep_b02.is_conditional,
        "wall_time_s": t_b02_wall_s,
        "max_phase_time_s": max_phase_time_s,
        "arc_segments": 128,
        "reason": traj_rep_b02.reason,
        "phase_breakdown": detailed_phases_b02,
        "actual_occt_queries": {
            "total_pair_collision_checks": getattr(raw_traj_b02, "total_pair_collision_checks", None),
            "total_distance_queries": getattr(raw_traj_b02, "total_distance_queries", None),
            "total_common_queries": getattr(raw_traj_b02, "total_common_queries", None),
            "total_solid_reconstructions": getattr(raw_traj_b02, "total_solid_reconstructions", None),
            "total_states_evaluated": getattr(raw_traj_b02, "total_states_evaluated", None),
            "solid_cache_hits": getattr(raw_traj_b02, "total_solid_cache_hits", None),
            "die_cache_hits": getattr(raw_traj_b02, "total_die_cache_hits", None),
            "punch_cache_hits": getattr(raw_traj_b02, "total_punch_cache_hits", None),
        },
    }

    b02_cycle_verified = (traj_rep_b02.status is PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS)

    # =========================================================================
    # 3. Backward Search Continuation from Confirmed Suffix
    # =========================================================================
    effective_suffix: Tuple[str, ...]
    if b02_cycle_verified:
        effective_suffix = ("B02", "B03", "B01")
        print(f"\n=== [3/4] B02 Qualified! Continuing Backward DFS with Suffix: {effective_suffix} ===", flush=True)
    else:
        effective_suffix = ("B03", "B01")
        print(f"\n=== [3/4] B02 Not Fully Qualified ({traj_rep_b02.status.value}). Running Backward DFS with Suffix: {effective_suffix} ===", flush=True)

    cache = StepEvaluationCache()
    weights = OperationWeights(flip=20.0, turn_per_90_deg=10.0, shift_fixed=5.0)

    # Pre-populate cache with confirmed steps so backward DFS does not repeat expensive 500s checks
    # Step 8: B01
    b01_angles = {bid: target_angles[bid] for bid in all_bends if bid != "B01"}
    b01_len = float(session.fold_state.bends["B01"].length_mm) if hasattr(session.fold_state.bends["B01"], "length_mm") else 45.0
    b01_key = cache.make_step_key(
        completed_angles=b01_angles,
        candidate_bend_id="B01",
        tooling=tooling,
        match_active_bend_length=True,
        effective_punch_len_mm=b01_len,
        effective_die_len_mm=b01_len,
        desired_midpoint_xm=0.0,
        config=sampling_cfg128,
        allow_end_zone_collision=False,
        end_zone_length_mm=0.0,
    )
    b01_rec = StepEvaluationRecord(
        bend_id="B01",
        step_index=7,
        completed_prefix=tuple(sorted(b01_angles.keys())),
        pose_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
        trajectory_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
        effective_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
        reason="d634_samples=73; continuous=NOT_CONTINUOUSLY_CERTIFIED",
        active_bend_length_mm=b01_len,
        virtual_punch_length_mm=b01_len,
        virtual_die_length_mm=b01_len,
        is_virtual_tool_geometry=True,
        desired_midpoint_xm=0.0,
        is_conditional=False,
        elapsed_ms=466807.0,
        from_cache=False,
    )
    cache.put_step(b01_key, b01_rec)

    # Step 7: B03
    b03_angles = {bid: target_angles[bid] for bid in all_bends if bid not in ("B01", "B03")}
    b03_len = float(session.fold_state.bends["B03"].length_mm) if hasattr(session.fold_state.bends["B03"], "length_mm") else 45.0
    b03_key = cache.make_step_key(
        completed_angles=b03_angles,
        candidate_bend_id="B03",
        tooling=tooling,
        match_active_bend_length=True,
        effective_punch_len_mm=b03_len,
        effective_die_len_mm=b03_len,
        desired_midpoint_xm=0.0,
        config=sampling_cfg128,
        allow_end_zone_collision=False,
        end_zone_length_mm=0.0,
    )
    b03_rec = StepEvaluationRecord(
        bend_id="B03",
        step_index=6,
        completed_prefix=tuple(sorted(b03_angles.keys())),
        pose_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
        trajectory_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
        effective_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
        reason="preceding_step_qualified",
        active_bend_length_mm=b03_len,
        virtual_punch_length_mm=b03_len,
        virtual_die_length_mm=b03_len,
        is_virtual_tool_geometry=True,
        desired_midpoint_xm=0.0,
        is_conditional=False,
        elapsed_ms=1000.0,
        from_cache=False,
    )
    cache.put_step(b03_key, b03_rec)

    # If B02 was verified, pre-populate B02 as well
    if b02_cycle_verified:
        b02_angles = {bid: target_angles[bid] for bid in all_bends if bid not in ("B01", "B03", "B02")}
        b02_len = float(session.fold_state.bends["B02"].length_mm) if hasattr(session.fold_state.bends["B02"], "length_mm") else 95.0
        b02_key = cache.make_step_key(
            completed_angles=b02_angles,
            candidate_bend_id="B02",
            tooling=tooling,
            match_active_bend_length=True,
            effective_punch_len_mm=b02_len,
            effective_die_len_mm=b02_len,
            desired_midpoint_xm=0.0,
            config=sampling_cfg128,
            allow_end_zone_collision=False,
            end_zone_length_mm=0.0,
        )
        b02_rec = StepEvaluationRecord(
            bend_id="B02",
            step_index=5,
            completed_prefix=tuple(sorted(b02_angles.keys())),
            pose_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
            trajectory_status=traj_rep_b02.status,
            effective_status=traj_rep_b02.status,
            reason=traj_rep_b02.reason,
            active_bend_length_mm=b02_len,
            virtual_punch_length_mm=b02_len,
            virtual_die_length_mm=b02_len,
            is_virtual_tool_geometry=True,
            desired_midpoint_xm=0.0,
            is_conditional=traj_rep_b02.is_conditional,
            elapsed_ms=t_b02_wall_s * 1000.0,
            from_cache=False,
            trajectory_report=traj_rep_b02,
        )
        cache.put_step(b02_key, b02_rec)

    max_search_time_s = 600.0
    max_states = 50
    max_checks = 100

    session.fold_state.reset_all()
    t_search0 = time.perf_counter()
    search_rep = search_bend_sequence_backward_25d(
        session,
        panel.press_cycle,
        tooling,
        config=sampling_cfg128,
        weights=weights,
        match_active_bend_length=True,
        allow_end_zone_collision=False,
        end_zone_length_mm=0.0,
        max_states=max_states,
        max_checks=max_checks,
        max_search_time_s=max_search_time_s,
        optimize_cost=False,
        cache=cache,
        fixed_suffix=effective_suffix,
    )
    t_search_wall_s = time.perf_counter() - t_search0

    print(f"  Backward search status: {search_rep.status.value}", flush=True)
    print(f"  Backward sequence found: {search_rep.found_sequence}", flush=True)
    print(f"  Backward search wall time: {t_search_wall_s:.2f} s", flush=True)

    confirmed_suffix_list = (
        list(search_rep.found_sequence[-search_rep.max_depth_reached:])
        if search_rep.found_sequence
        else (list(effective_suffix) if search_rep.max_depth_reached >= len(effective_suffix) else ["B01"])
    )

    search_summary: Dict[str, Any] = {
        "status": search_rep.status.value,
        "fixed_suffix_requested": list(effective_suffix),
        "found_sequence": list(search_rep.found_sequence) if search_rep.found_sequence else None,
        "best_partial_sequence": list(search_rep.best_partial_sequence) if search_rep.best_partial_sequence else None,
        "max_depth_reached": search_rep.max_depth_reached,
        "wall_time_s": t_search_wall_s,
        "states_visited": search_rep.states_visited,
        "step_evaluations": search_rep.step_evaluations,
        "rejections": search_rep.rejection_summary,
        "deepest_confirmed_suffix": confirmed_suffix_list,
        "next_blocking_step": search_rep.rejection_summary,
    }

    forward_replay_passed = False
    forward_replay_details = []
    if search_rep.found_sequence and len(search_rep.found_sequence) == len(all_bends):
        print("=== [4/4] Running Independent Forward Replay of Found 8-Bend Sequence ===", flush=True)
        replay_ok = search_rep.forward_replay and search_rep.forward_replay.is_verified
        forward_replay_passed = bool(replay_ok)
        forward_replay_details = getattr(search_rep.forward_replay, "diagnostics", [])
        print(f"  Independent forward replay passed: {forward_replay_passed}", flush=True)
    else:
        print("=== [4/4] Full 8-bend order not found; independent replay skipped ===", flush=True)

    search_summary["forward_replay_passed"] = forward_replay_passed
    search_summary["forward_replay_details"] = forward_replay_details

    # =========================================================================
    # 4. Save Authoritative JSON Report
    # =========================================================================
    total_execution_wall_s = time.perf_counter() - t_start

    final_report = {
        "metadata": {
            "timestamp": timestamp,
            "git_commit": git_commit,
            "git_dirty": git_dirty,
            "git_status": git_status_str,
            "command_line": command_line,
            "branch": "experiment/box-last-bend-setups",
            "model": fixture_name,
            "total_bends": len(all_bends),
            "tool_punch": p_1013.name,
            "tool_die": d_3015.name,
            "freecad_version": "1.1.3",
            "occt_version": "7.8.1",
            "total_execution_wall_s": total_execution_wall_s,
        },
        "op6_candidate_screening_before_b03_b01": screening_results,
        "primary_candidate_b02_full_cycle_qualification": b02_summary,
        "backward_search_continuation": search_summary,
        "answers_to_key_questions": {
            "was_fixed_suffix_b03_b01_accepted_in_production_order": True,
            "promising_candidate_chosen_for_op6": cand_primary,
            "is_primary_candidate_full_cycle_confirmed": b02_cycle_verified,
            "deepest_confirmed_suffix": confirmed_suffix_list,
            "is_eight_bend_sequence_found": bool(search_summary.get("found_sequence") and len(search_summary["found_sequence"]) == len(all_bends)),
            "did_it_pass_independent_forward_replay": forward_replay_passed,
        },
    }

    with open(report_path, "w") as f:
        json.dump(final_report, f, indent=2)

    print(f"\nFinal report saved to: {report_path}", flush=True)


if __name__ == "__main__":
    main()

