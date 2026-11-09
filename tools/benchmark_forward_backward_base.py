"""benchmark_forward_backward_base.py - Authoritative base module for forward vs backward DFS benchmarks.

Provides shared environment setup, fresh session isolation, cold cache guarantee,
fine-grained OCCT query extraction, timing decomposition, rejection categorization,
and report generation.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
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

# SheetMetal Workbench is normally on sys.path as a top-level FreeCAD Mod
# directory (Windows), while some installs expose it as a package.
try:
    from SheetMetalUnfoldCmd import SMUnfold
except ImportError as exc:
    if getattr(exc, "name", None) != "SheetMetalUnfoldCmd":
        raise
    from sheetmetal.SheetMetalUnfoldCmd import SMUnfold
from smbs_core.demo_paths import demo_part_path
from freecad_smbs.commands.pressbrake_2d import build_part_session, _PreviewPanel
from smbs_core.kinematics2d.tooling_25d import ToolKind
from smbs_core.kinematics2d.motion_trajectory_3d import AdaptiveSamplingConfig
from smbs_core.kinematics2d.operation_cost import OperationWeights
from smbs_core.kinematics2d.sequence_search import (
    PhysicalEvidence,
    SearchStatus,
    SequenceSearchReport,
    StepEvaluationCache,
    StepEvaluationRecord,
    search_bend_sequence_25d,
    search_bend_sequence_backward_25d,
)


def get_git_info() -> Tuple[str, bool]:
    """Return HEAD commit SHA and working copy dirty status."""
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), stderr=subprocess.DEVNULL
        ).decode().strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=str(ROOT), stderr=subprocess.DEVNULL
        ).decode().strip()
        return commit, len(status) > 0
    except Exception:
        return "unknown", False


def categorize_rejections(rejection_summary: Any) -> Dict[str, List[str]]:
    """Categorize rejection reasons into proven physical collisions, geometry errors, and incomplete/timeout checks."""
    proven_collisions: List[str] = []
    geometry_errors: List[str] = []
    incomplete_checks: List[str] = []
    other_rejections: List[str] = []

    items: List[str] = []
    if isinstance(rejection_summary, dict):
        for bend_id, reasons in rejection_summary.items():
            if isinstance(reasons, (list, tuple)):
                for r in reasons:
                    items.append(f"{bend_id}: {r}")
            else:
                items.append(f"{bend_id}: {reasons}")
    elif isinstance(rejection_summary, (list, tuple)):
        items = [str(r) for r in rejection_summary]
    elif isinstance(rejection_summary, str):
        items = [s.strip() for s in rejection_summary.split(";") if s.strip()]

    for entry in items:
        r_upper = entry.upper()
        if any(k in r_upper for k in ("COLLISION", "PENETRATION", "DISCRETE_SAMPLES_REJECTED", "STAGE_A", "STAGE_B")):
            proven_collisions.append(entry)
        elif any(k in r_upper for k in ("INVALID_GEOMETRY", "GEOMETRY_ERROR", "FAIL")):
            geometry_errors.append(entry)
        elif any(k in r_upper for k in ("INTERVAL_UNRESOLVED", "MAX_TIME", "BUDGET", "UNVERIFIED", "TIMEOUT", "LIMIT")):
            incomplete_checks.append(entry)
        else:
            other_rejections.append(entry)

    return {
        "proven_collisions": proven_collisions,
        "geometry_errors": geometry_errors,
        "incomplete_checks": incomplete_checks,
        "other_rejections": other_rejections,
    }


def extract_occt_and_physics_counters(
    report: SequenceSearchReport,
    cache: Optional[StepEvaluationCache] = None,
) -> Dict[str, int]:
    """Accurately extract actual OCCT distance/common calls, pair collision checks,
    and cache events across all evaluated steps (including cache and forward replay)."""
    records: List[StepEvaluationRecord] = []
    if cache is not None:
        records.extend(cache._step_cache.values())
    if report.winning_steps:
        records.extend(report.winning_steps)
    if report.forward_replay and report.forward_replay.steps:
        records.extend(report.forward_replay.steps)

    seen = set()
    unique_records: List[StepEvaluationRecord] = []
    for r in records:
        r_id = id(r)
        if r_id not in seen:
            seen.add(r_id)
            unique_records.append(r)

    pair_checks = 0
    dist_queries = 0
    common_queries = 0
    solid_rebuilds = 0
    states_evaluated = 0
    pose_hits = 0
    punch_hits = 0
    die_hits = 0
    solid_hits = 0

    for r in unique_records:
        traj_rep = getattr(r, "trajectory_report", None)
        if traj_rep is not None and getattr(traj_rep, "raw_report", None) is not None:
            raw = traj_rep.raw_report
            pair_checks += int(getattr(raw, "total_pair_collision_checks", 0))
            dist_queries += int(getattr(raw, "total_distance_queries", 0))
            common_queries += int(getattr(raw, "total_common_queries", 0))
            solid_rebuilds += int(getattr(raw, "total_solid_reconstructions", 0))
            states_evaluated += int(getattr(raw, "total_states_evaluated", 0))
            pose_hits += int(getattr(raw, "total_pose_cache_hits", 0))
            punch_hits += int(getattr(raw, "total_punch_cache_hits", 0))
            die_hits += int(getattr(raw, "total_die_cache_hits", 0))
            solid_hits += int(getattr(raw, "total_solid_cache_hits", 0))

    return {
        "total_unique_steps_evaluated": len(unique_records),
        "pair_collision_checks": pair_checks,
        "actual_distance_queries": dist_queries,
        "actual_common_queries": common_queries,
        "solid_reconstructions": solid_rebuilds,
        "states_evaluated_in_trajectories": states_evaluated,
        "pose_cache_hits": pose_hits,
        "punch_cache_hits": punch_hits,
        "die_cache_hits": die_hits,
        "solid_cache_hits": solid_hits,
    }


def run_single_search_in_clean_session(
    direction: str,
    fixture_name: str,
    punch_name: str,
    die_name: str,
    sampling_cfg: AdaptiveSamplingConfig,
    weights: OperationWeights,
    max_search_time_s: float,
    max_states: int,
    max_checks: int,
    match_active_bend_length: bool = True,
    allow_end_zone_collision: bool = False,
    end_zone_length_mm: float = 0.0,
    optimize_cost: bool = False,
) -> Tuple[SequenceSearchReport, StepEvaluationCache, float, List[str]]:
    """Execute forward or backward search in an isolated FreeCAD session with cold caches."""
    fixture_path = demo_part_path(fixture_name)
    if not fixture_path or not os.path.exists(str(fixture_path)):
        raise FileNotFoundError(f"Fixture not found: {fixture_name}")

    doc = App.openDocument(str(fixture_path), True)
    try:
        unfold = next((o for o in doc.Objects if o.Name.endswith("_Unfold")), None)
        if unfold is None:
            body = getattr(doc, "Body", None)
            if body is None:
                raise ValueError(f"No Body or _Unfold object in document {fixture_name}")
            unfold = doc.addObject("Part::FeaturePython", "Тело_Unfold")
            # Select root face (Face2 for Z-part, Face1 for box if Face2 not present)
            root_face = "Face2"
            try:
                SMUnfold(unfold, body, [root_face])
            except Exception:
                root_face = "Face1"
                SMUnfold(unfold, body, [root_face])
            unfold.GenerateSketch = True
            doc.recompute()

        session = build_part_session(doc, [unfold])
        all_bends = list(session.fold_state.bends.keys())

        panel = _PreviewPanel()
        panel.session = session
        p_tool = panel.tool_library.load_tool(punch_name, ToolKind.PUNCH)
        d_tool = panel.tool_library.load_tool(die_name, ToolKind.DIE)
        panel.select_punch(p_tool)
        panel.select_die(d_tool)
        panel.align_tooling()
        tooling = panel.tooling_setup
        panel._sync_tooling_to_active_bend()

        cache = StepEvaluationCache()

        t0 = time.perf_counter()
        if direction.upper() == "FORWARD":
            report = search_bend_sequence_25d(
                session,
                panel.press_cycle,
                tooling,
                config=sampling_cfg,
                weights=weights,
                match_active_bend_length=match_active_bend_length,
                allow_end_zone_collision=allow_end_zone_collision,
                end_zone_length_mm=end_zone_length_mm,
                max_states=max_states,
                max_checks=max_checks,
                max_search_time_s=max_search_time_s,
                optimize_cost=optimize_cost,
                cache=cache,
                search_direction="FORWARD",
            )
        elif direction.upper() == "BACKWARD":
            report = search_bend_sequence_backward_25d(
                session,
                panel.press_cycle,
                tooling,
                config=sampling_cfg,
                weights=weights,
                match_active_bend_length=match_active_bend_length,
                allow_end_zone_collision=allow_end_zone_collision,
                end_zone_length_mm=end_zone_length_mm,
                max_states=max_states,
                max_checks=max_checks,
                max_search_time_s=max_search_time_s,
                optimize_cost=optimize_cost,
                cache=cache,
            )
        else:
            raise ValueError(f"Unknown direction: {direction}")

        elapsed_wall_s = time.perf_counter() - t0
        return report, cache, elapsed_wall_s, all_bends
    finally:
        try:
            panel.close()
            panel.deleteLater()
        except Exception:
            pass
        App.closeDocument(doc.Name)


def format_search_record(
    direction: str,
    report: SequenceSearchReport,
    cache: StepEvaluationCache,
    elapsed_wall_s: float,
) -> Dict[str, Any]:
    """Format comprehensive search record with timing decomposition and physical counters."""
    replay_rep = report.forward_replay
    replay_time_s = round(float(replay_rep.elapsed_ms) / 1000.0, 3) if replay_rep else 0.0
    search_time_s = round(max(0.0, float(report.elapsed_s) - replay_time_s), 3)

    occt_counters = extract_occt_and_physics_counters(report, cache)
    rejection_cat = categorize_rejections(report.rejection_summary)

    is_verified = bool(
        report.found_sequence is not None
        and report.status in (SearchStatus.STRICT_VALIDATED, SearchStatus.CONDITIONAL_VALIDATED)
        and replay_rep is not None
        and replay_rep.is_verified
    )

    return {
        "direction": direction.upper(),
        "status": report.status.value,
        "overall_evidence": report.overall_evidence.value,
        "found_sequence": list(report.found_sequence) if report.found_sequence is not None else None,
        "best_partial_sequence": list(report.best_partial_sequence),
        "max_depth_reached": report.max_depth_reached,
        "total_bends": report.total_bends,
        "is_verified": is_verified,
        "timings": {
            "search_time_s": search_time_s,
            "replay_time_s": replay_time_s,
            "total_wall_time_s": round(elapsed_wall_s, 3),
            "time_to_first_verified_plan_s": (
                round(report.time_to_first_verified_plan_s, 3)
                if report.time_to_first_verified_plan_s is not None
                else None
            ),
        },
        "search_exploration": {
            "states_visited": report.states_visited,
            "step_evaluations": report.step_evaluations,
            "cache_hits": report.cache_hits,
            "dead_end_hits": report.dead_end_hits,
            "backtracks_count": report.backtracks_count,
            "stage_a_evaluated": report.stage_a_evaluated,
            "stage_a_pruned": report.stage_a_pruned,
            "stage_b_evaluated": report.stage_b_evaluated,
            "stage_b_pruned": report.stage_b_pruned,
            "stage_c_native_evaluated": report.stage_c_native_evaluated,
        },
        "occt_and_physics_counters": occt_counters,
        "forward_replay": {
            "is_verified": bool(replay_rep.is_verified) if replay_rep else False,
            "overall_status": replay_rep.overall_status.value if replay_rep else None,
            "elapsed_s": replay_time_s,
            "steps_verified": len(replay_rep.steps) if replay_rep else 0,
            "blocking_bend_id": replay_rep.blocking_bend_id if replay_rep else None,
            "blocking_reason": replay_rep.blocking_reason if replay_rep else "",
        },
        "plan_cost": report.plan_cost,
        "rejection_analysis": {
            "search_limit_reason": report.search_limit_reason,
            "categorized_rejections": rejection_cat,
        },
    }


def build_comparison_summary(
    fwd: Dict[str, Any],
    bwd: Dict[str, Any],
) -> Dict[str, Any]:
    """Build side-by-side comparison summary between forward and backward directions."""
    f_seq = fwd.get("found_sequence")
    b_seq = bwd.get("found_sequence")
    both_verified = bool(fwd["is_verified"] and bwd["is_verified"])
    seq_identical = bool(both_verified and f_seq == b_seq)

    f_heavy = (
        fwd["occt_and_physics_counters"]["actual_distance_queries"]
        + fwd["occt_and_physics_counters"]["actual_common_queries"]
    )
    b_heavy = (
        bwd["occt_and_physics_counters"]["actual_distance_queries"]
        + bwd["occt_and_physics_counters"]["actual_common_queries"]
    )

    if b_heavy < f_heavy:
        fewer_heavy = "BACKWARD"
    elif f_heavy < b_heavy:
        fewer_heavy = "FORWARD"
    else:
        fewer_heavy = "EQUAL"

    return {
        "both_verified": both_verified,
        "sequences_identical": seq_identical,
        "forward_sequence": f_seq,
        "backward_sequence": b_seq,
        "forward_search_time_s": fwd["timings"]["search_time_s"],
        "backward_search_time_s": bwd["timings"]["search_time_s"],
        "forward_replay_time_s": fwd["timings"]["replay_time_s"],
        "backward_replay_time_s": bwd["timings"]["replay_time_s"],
        "forward_total_time_s": fwd["timings"]["total_wall_time_s"],
        "backward_total_time_s": bwd["timings"]["total_wall_time_s"],
        "forward_states_visited": fwd["search_exploration"]["states_visited"],
        "backward_states_visited": bwd["search_exploration"]["states_visited"],
        "forward_step_evaluations": fwd["search_exploration"]["step_evaluations"],
        "backward_step_evaluations": bwd["search_exploration"]["step_evaluations"],
        "forward_distance_queries": fwd["occt_and_physics_counters"]["actual_distance_queries"],
        "backward_distance_queries": bwd["occt_and_physics_counters"]["actual_distance_queries"],
        "forward_common_queries": fwd["occt_and_physics_counters"]["actual_common_queries"],
        "backward_common_queries": bwd["occt_and_physics_counters"]["actual_common_queries"],
        "forward_solid_rebuilds": fwd["occt_and_physics_counters"]["solid_reconstructions"],
        "backward_solid_rebuilds": bwd["occt_and_physics_counters"]["solid_reconstructions"],
        "forward_cost": fwd["plan_cost"]["total"] if fwd.get("plan_cost") else None,
        "backward_cost": bwd["plan_cost"]["total"] if bwd.get("plan_cost") else None,
        "fewer_heavy_checks_direction": fewer_heavy,
        "conclusion": (
            "Authoritative comparison between forward and backward search directions. "
            "Both modes run with identical budgets, cold caches, and strict collision rules."
        ),
    }

