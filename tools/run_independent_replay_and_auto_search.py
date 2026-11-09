"""tools/run_independent_replay_and_auto_search.py - Independent Forward Replay and Automatic Backward Search.

Part of experiment/box-last-bend-setups.
Part: box.100out-50in-(ok).FCStd (8 bends: B01..B08)
Tooling: PUNCH_AP_1013_85°_H159,65_R0,8 + T-DIE_AT_3015_88°_V12_H80
Setup: Centered (xm=0.0), match_active_bend_length=True, strict mode (allow_end_zone_collision=False).

Modes:
- --replay-only: Run only independent forward replay for known sequence (Part 1).
- --search-only: Run automatic backward search from scratch (fixed_suffix=None),
                 followed by an independent forward verification in a fresh FreeCAD session
                 if a complete sequence is found (Part 2).
- Default (no args): Run both Part 1 and Part 2.
"""
from __future__ import annotations

import argparse
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
    PhysicalEvidence,
    check_active_25d_bend_trajectory,
    check_current_25d_pose,
)
from smbs_core.kinematics2d.sequence_search import (
    SearchStatus,
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
                "elapsed_ms": round(float(pres.elapsed_ms), 2),
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


def init_session():
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
    return doc, session, panel, panel.tooling_setup


def run_part1_independent_forward_replay(
    git_commit: str,
    git_dirty: bool,
    sequence: Tuple[str, ...] = ("B08", "B06", "B07", "B05", "B04", "B02", "B03", "B01"),
    time_limit_s: float = 3600.0,
) -> Dict[str, Any]:
    print("\n" + "=" * 70)
    print("=== PART 1: INDEPENDENT FORWARD REPLAY FROM FLAT SHEET (0°) ===")
    print("=" * 70, flush=True)

    doc, session, panel, tooling = init_session()
    config128 = AdaptiveSamplingConfig(
        early_stop_on_blocking_collision=True,
        max_phase_time_s=600.0,
        arc_segments=128,
        volume_tolerance_mm3=1e-4,
    )

    step_results = []
    overall_ok = True
    total_occt_calls = 0
    t0_total = time.perf_counter()

    for step_idx, bend_id in enumerate(sequence, start=1):
        completed_prefix = sequence[:step_idx - 1]
        remaining_after = sequence[step_idx:]
        print(f"\n--- [Step {step_idx}/{len(sequence)}] Active bend: {bend_id} ---", flush=True)
        print(f"  Prefix completed: {completed_prefix}", flush=True)
        print(f"  Remaining after:  {remaining_after}", flush=True)

        # Apply state: prefix folded, active bend at 0, remaining at 0
        _apply_prefix_to_session(
            session,
            panel.press_cycle,
            tooling,
            completed_prefix=completed_prefix,
            candidate_bend_id=bend_id,
            remaining_after_candidate=remaining_after,
            match_active_bend_length=True,
            allow_end_zone_collision=False,
            end_zone_length_mm=0.0,
        )
        bend_len = _sync_tooling_and_cycle_for_active_bend(
            session,
            panel.press_cycle,
            tooling,
            match_active_bend_length=True,
            allow_end_zone_collision=False,
            end_zone_length_mm=0.0,
        )

        angles_before = dict(session.fold_state.angles)
        target_angle = float(session.fold_state.bends[bend_id].angle_deg)

        # 1. Check initial contact pose
        pose_rep = check_current_25d_pose(
            session,
            panel.press_cycle,
            tooling,
            native_3d=True,
            desired_midpoint_xm=0.0,
            match_active_bend_length=True,
            allow_end_zone_collision=False,
            end_zone_length_mm=0.0,
            classify_end_zone_length_mm=0.0,
        )
        print(f"  Initial Pose status: {pose_rep.status.value}", flush=True)

        # 2. Check full 5-phase trajectory
        t_traj0 = time.perf_counter()
        traj_rep = check_active_25d_bend_trajectory(
            session,
            tooling,
            config=config128,
            desired_midpoint_xm=0.0,
            match_active_bend_length=True,
            allow_end_zone_collision=False,
            end_zone_length_mm=0.0,
            classify_end_zone_length_mm=0.0,
        )
        t_step = time.perf_counter() - t_traj0

        phases_dict = extract_phase_data(traj_rep.raw_report)
        step_occt = sum(p["collision_queries"] + p["actual_distance_queries"] + p["actual_common_queries"] for p in phases_dict.values())
        total_occt_calls += step_occt

        # Check unresolved intervals across all phases
        all_unresolved = []
        for ph_name, ph_info in phases_dict.items():
            for iv in ph_info["unresolved_intervals"]:
                all_unresolved.append({"phase": ph_name, "interval": iv})

        max_p_ov = max((p["max_punch_overlap_volume_mm3"] for p in phases_dict.values()), default=0.0)
        max_d_ov = max((p["max_die_overlap_volume_mm3"] for p in phases_dict.values()), default=0.0)

        # Workpiece geometry state after the bend: active bend at target angle
        angles_after = dict(angles_before)
        angles_after[bend_id] = target_angle

        is_step_pass = (
            traj_rep.status == PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS
            and len(all_unresolved) == 0
        )
        if not is_step_pass:
            overall_ok = False

        print(f"  Trajectory status:  {traj_rep.status.value}", flush=True)
        print(f"  Step wall time:     {t_step:.2f} s", flush=True)
        print(f"  Unresolved count:   {len(all_unresolved)}", flush=True)
        print(f"  Max punch overlap:  {max_p_ov:.2e} mm³", flush=True)
        print(f"  Max die overlap:    {max_d_ov:.2e} mm³", flush=True)

        step_results.append({
            "step_index": step_idx,
            "bend_id": bend_id,
            "target_angle_deg": target_angle,
            "active_bend_length_mm": bend_len,
            "effective_punch_length_mm": bend_len,
            "effective_die_length_mm": bend_len,
            "angles_before": angles_before,
            "angles_after": angles_after,
            "pose_status": pose_rep.status.value,
            "trajectory_status": traj_rep.status.value,
            "is_step_pass": is_step_pass,
            "reason": traj_rep.reason,
            "wall_time_s": round(t_step, 2),
            "occt_queries_count": step_occt,
            "max_punch_overlap_volume_mm3": max_p_ov,
            "max_die_overlap_volume_mm3": max_d_ov,
            "unresolved_intervals": all_unresolved,
            "phases": phases_dict,
        })

        if not is_step_pass:
            print(f"  [STOP] Step {step_idx} failed acceptance criterion! Aborting replay.", flush=True)
            break

        elapsed_so_far = time.perf_counter() - t0_total
        if elapsed_so_far >= time_limit_s:
            print(f"  [STOP] Time limit exceeded ({elapsed_so_far:.2f} s >= {time_limit_s:.2f} s).", flush=True)
            overall_ok = False
            break

    t_total = time.perf_counter() - t0_total
    App.closeDocument(doc.Name)

    replay_report = {
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "git_commit": git_commit,
            "git_dirty": git_dirty,
            "command": "python tools/run_independent_replay_and_auto_search.py",
            "part": "box.100out-50in-(ok).FCStd",
            "total_wall_time_s": round(t_total, 2),
            "total_occt_calls": total_occt_calls,
        },
        "tooling_setup": {
            "punch": "PUNCH_AP_1013_85°_H159,65_R0,8",
            "die": "T-DIE_AT_3015_88°_V12_H80",
            "desired_midpoint_xm": 0.0,
            "match_active_bend_length": True,
            "allow_end_zone_collision": False,
            "end_zone_length_mm": 0.0,
            "arc_segments": 128,
            "volume_tolerance_mm3": 1e-4,
        },
        "sequence": list(sequence),
        "overall_status": "SAMPLED_3D_CLEAR_NOT_CONTINUOUS" if overall_ok and len(step_results) == len(sequence) else "REJECTED_OR_UNVERIFIED",
        "all_8_operations_passed_acceptance": overall_ok and len(step_results) == len(sequence),
        "completed_steps_count": len(step_results),
        "steps": step_results,
    }
    return replay_report


def run_part2_automatic_search_and_verify(
    git_commit: str,
    git_dirty: bool,
    search_budget_s: float = 3600.0,
    verify_budget_s: float = 3600.0,
    max_states: int = 100,
    max_checks: int = 200,
) -> Dict[str, Any]:
    print("\n" + "=" * 70)
    print("=== PART 2: AUTOMATIC BACKWARD SEARCH & INDEPENDENT VERIFICATION ===")
    print("=" * 70, flush=True)
    print(f"Configured Search Budget: {search_budget_s:.1f} s, Max States: {max_states}, Max Checks: {max_checks}")
    print(f"Configured Verification Budget: {verify_budget_s:.1f} s")
    print("Settings: AP1013 + AT3015, centered, match length, strict mode (allow_end_zone=False)")
    print("Sampling: 128 arc segments, 1e-4 mm3 volume tolerance, 600.0 s per phase\n", flush=True)

    # 1. Fresh session for backward DFS search
    doc, session, panel, tooling = init_session()

    search_cfg = AdaptiveSamplingConfig(
        early_stop_on_blocking_collision=True,
        max_phase_time_s=600.0,
        arc_segments=128,
        volume_tolerance_mm3=1e-4,
    )

    t0_search = time.perf_counter()
    print("Launching search_bend_sequence_backward_25d (fixed_suffix=None)...", flush=True)
    search_res = search_bend_sequence_backward_25d(
        session=session,
        press_cycle=panel.press_cycle,
        tooling=tooling,
        weights=OperationWeights(),
        config=search_cfg,
        match_active_bend_length=True,
        allow_end_zone_collision=False,
        end_zone_length_mm=0.0,
        fixed_suffix=None,
        max_search_time_s=search_budget_s,
        max_states=max_states,
        max_checks=max_checks,
    )
    t_search = time.perf_counter() - t0_search
    App.closeDocument(doc.Name)

    print(f"\nAutomatic search finished in {t_search:.2f} s (allocated budget: {search_budget_s:.1f} s)", flush=True)
    print(f"  Search Status:          {search_res.status.value}", flush=True)
    print(f"  Overall Evidence:       {search_res.overall_evidence.value}", flush=True)
    print(f"  Max Depth Reached:       {search_res.max_depth_reached} / {search_res.total_bends}", flush=True)
    print(f"  States Visited:          {search_res.states_visited}", flush=True)
    print(f"  Step Evaluations:        {search_res.step_evaluations}", flush=True)
    print(f"  Found Sequence:          {search_res.found_sequence}", flush=True)
    print(f"  Best Partial Sequence:   {search_res.best_partial_sequence}", flush=True)
    print(f"  Limit Reason:            {search_res.search_limit_reason}", flush=True)
    print(f"  Time to 1st Candidate:   {search_res.time_to_first_full_candidate_s}", flush=True)
    print(f"  Time to 1st Plan:        {search_res.time_to_first_verified_plan_s}", flush=True)

    passed_root = search_res.max_depth_reached > 0
    found_all_8 = search_res.found_sequence is not None and len(search_res.found_sequence) == search_res.total_bends

    # 2. Independent Final Verification in a completely fresh FreeCAD session
    independent_verification_report: Optional[Dict[str, Any]] = None
    verification_passed = False

    if found_all_8 and search_res.found_sequence is not None:
        print("\n" + "=" * 70)
        print("=== FINAL INDEPENDENT VERIFICATION OF FOUND SEQUENCE ===")
        print("=" * 70, flush=True)
        print(f"Verifying found order: {' -> '.join(search_res.found_sequence)}", flush=True)
        independent_verification_report = run_part1_independent_forward_replay(
            git_commit=git_commit,
            git_dirty=git_dirty,
            sequence=search_res.found_sequence,
            time_limit_s=verify_budget_s,
        )
        verification_passed = bool(independent_verification_report.get("all_8_operations_passed_acceptance", False))
        print(f"\nFinal independent verification result: {'PASS' if verification_passed else 'FAIL'}", flush=True)
    else:
        print("\nNo complete 8-bend sequence found by auto-search; skipping independent verification.", flush=True)

    # Classify candidate rejections and transitions
    classified_rejections = {}
    for cand, reasons in search_res.rejection_summary.items():
        cand_list = []
        for r in reasons:
            kind = "UNKNOWN"
            if "COLLISION" in r:
                kind = "COLLISION"
            elif "UNVERIFIED" in r:
                kind = "UNVERIFIED"
            elif "LIMIT" in r or "BUDGET" in r:
                kind = "BUDGET_EXHAUSTED"
            cand_list.append({"kind": kind, "detail": r})
        classified_rejections[cand] = cand_list

    auto_search_report = {
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "git_commit": git_commit,
            "git_dirty": git_dirty,
            "command": "python tools/run_independent_replay_and_auto_search.py --search-only",
            "part": "box.100out-50in-(ok).FCStd",
            "search_wall_time_s": round(t_search, 2),
            "search_budget_allocated_s": search_budget_s,
            "verification_budget_allocated_s": verify_budget_s,
        },
        "search_parameters": {
            "tooling_punch": "PUNCH_AP_1013_85°_H159,65_R0,8",
            "tooling_die": "T-DIE_AT_3015_88°_V12_H80",
            "desired_midpoint_xm": 0.0,
            "match_active_bend_length": True,
            "allow_end_zone_collision": False,
            "end_zone_length_mm": 0.0,
            "fixed_suffix": None,
            "max_search_time_s": search_budget_s,
            "max_states": max_states,
            "max_checks": max_checks,
            "arc_segments": 128,
            "volume_tolerance_mm3": 1e-4,
            "max_phase_time_s": 600.0,
        },
        "search_summary": {
            "passed_root": passed_root,
            "found_all_8_bends": found_all_8,
            "verification_passed": verification_passed,
            "status": search_res.status.value,
            "overall_evidence": search_res.overall_evidence.value,
            "max_depth_reached": search_res.max_depth_reached,
            "total_bends": search_res.total_bends,
            "deepest_confirmed_suffix": list(search_res.best_partial_sequence),
            "found_sequence": list(search_res.found_sequence) if search_res.found_sequence else None,
            "states_visited": search_res.states_visited,
            "step_evaluations": search_res.step_evaluations,
            "cache_hits": search_res.cache_hits,
            "dead_end_hits": search_res.dead_end_hits,
            "backtracks_count": search_res.backtracks_count,
            "time_to_first_full_candidate_s": search_res.time_to_first_full_candidate_s,
            "time_to_first_verified_plan_s": search_res.time_to_first_verified_plan_s,
            "search_limit_reason": search_res.search_limit_reason,
            "message_ru": search_res.message_ru,
        },
        "classified_rejections": classified_rejections,
        "winning_steps": [s.to_dict() for s in getattr(search_res, "winning_steps", [])],
        "independent_final_verification": independent_verification_report,
    }
    return auto_search_report


def main():
    parser = argparse.ArgumentParser(description="Box bend sequence runner")
    parser.add_argument("--replay-only", action="store_true", help="Run only Part 1 (independent replay)")
    parser.add_argument("--search-only", action="store_true", help="Run only Part 2 (auto search + verification)")
    parser.add_argument("--search-budget", type=float, default=3600.0, help="Search budget in seconds (default: 3600.0)")
    parser.add_argument("--verify-budget", type=float, default=3600.0, help="Verification budget in seconds (default: 3600.0)")
    parser.add_argument("--max-states", type=int, default=100, help="Maximum search states (default: 100)")
    parser.add_argument("--max-checks", type=int, default=200, help="Maximum step evaluations (default: 200)")
    args = parser.parse_args()

    git_commit, git_dirty, _ = get_git_info()
    out_dir = ROOT / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.replay_only:
        replay_report = run_part1_independent_forward_replay(git_commit, git_dirty, time_limit_s=args.verify_budget)
        replay_path = out_dir / "box_independent_forward_replay_report.json"
        with open(replay_path, "w", encoding="utf-8") as f:
            json.dump(replay_report, f, indent=2, ensure_ascii=False)
        print(f"\n[OK] Independent forward replay report saved to {replay_path}")
    elif args.search_only:
        auto_report = run_part2_automatic_search_and_verify(
            git_commit=git_commit,
            git_dirty=git_dirty,
            search_budget_s=args.search_budget,
            verify_budget_s=args.verify_budget,
            max_states=args.max_states,
            max_checks=args.max_checks,
        )
        auto_path = out_dir / "box_automatic_search_qualification_report.json"
        with open(auto_path, "w", encoding="utf-8") as f:
            json.dump(auto_report, f, indent=2, ensure_ascii=False)
        print(f"\n[OK] Automatic search qualification report saved to {auto_path}")
    else:
        # Default: run both
        replay_report = run_part1_independent_forward_replay(git_commit, git_dirty, time_limit_s=args.verify_budget)
        replay_path = out_dir / "box_independent_forward_replay_report.json"
        with open(replay_path, "w", encoding="utf-8") as f:
            json.dump(replay_report, f, indent=2, ensure_ascii=False)
        print(f"\n[OK] Independent forward replay report saved to {replay_path}")

        auto_report = run_part2_automatic_search_and_verify(
            git_commit=git_commit,
            git_dirty=git_dirty,
            search_budget_s=args.search_budget,
            verify_budget_s=args.verify_budget,
            max_states=args.max_states,
            max_checks=args.max_checks,
        )
        auto_path = out_dir / "box_automatic_search_qualification_report.json"
        with open(auto_path, "w", encoding="utf-8") as f:
            json.dump(auto_report, f, indent=2, ensure_ascii=False)
        print(f"\n[OK] Automatic search qualification report saved to {auto_path}")


if __name__ == "__main__":
    main()
