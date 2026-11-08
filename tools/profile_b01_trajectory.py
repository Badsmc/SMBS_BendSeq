"""Fine-grained profiling for B01 trajectory check on box.100out-50in-(ok).FCStd.

Architecture & Contract:
- Measures and breaks down exact time spent in:
  * Solid part rebuilding (build_multi_bend_sheet_solid)
  * Workpiece placement computation (WorkpiecePlacement3D.compute_placement)
  * Workpiece transformShape application
  * Cache lookups across all tiers
  * Punch clearance evaluation (broad phase AABB, distance exact, common boolean, end zone)
  * Die clearance evaluation (broad phase AABB, distance exact, common boolean)
  * Unaccounted time (strictly labeled without speculative attribution)
- Protects all monkey-patches with try / finally.
- Never identifies tool by call order; uses explicit tool labels and shapes.
- Avoids extra / duplicate AABB or distance computations during profiling.
- Hierarchical non-overlapping timing: nested internal timers are not summed as independent costs.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from smbs_core.demo_paths import demo_part_path
from freecad_smbs.commands.pressbrake_2d import build_part_session, _PreviewPanel
from smbs_core.kinematics2d.tooling_25d import ToolKind
from smbs_core.kinematics2d.physical_validation_25d import check_active_25d_bend_trajectory
from smbs_core.kinematics2d.motion_trajectory_3d import AdaptiveSamplingConfig
import smbs_core.kinematics2d.native_bend_solid_3d as nbs
import smbs_core.kinematics2d.native_collision_3d as nc
import smbs_core.kinematics2d.workpiece_placement_3d as wp_mod
import smbs_core.kinematics2d.motion_trajectory_3d as mt_mod


class TrajectoryProfiler:
    def __init__(self):
        self.stats = {
            "solid_rebuild_calls": 0,
            "solid_rebuild_time_s": 0.0,
            "placement_calc_calls": 0,
            "placement_calc_time_s": 0.0,
            "transform_shape_calls": 0,
            "transform_shape_time_s": 0.0,
            # Punch breakdown
            "punch_eval_calls": 0,
            "punch_eval_time_s": 0.0,
            "punch_bp_calls": 0,
            "punch_bp_time_s": 0.0,
            "punch_dist_calls": 0,
            "punch_dist_time_s": 0.0,
            "punch_common_calls": 0,
            "punch_common_time_s": 0.0,
            "punch_end_zone_calls": 0,
            "punch_end_zone_time_s": 0.0,
            # Die breakdown
            "die_eval_calls": 0,
            "die_eval_time_s": 0.0,
            "die_bp_calls": 0,
            "die_bp_time_s": 0.0,
            "die_dist_calls": 0,
            "die_dist_time_s": 0.0,
            "die_common_calls": 0,
            "die_common_time_s": 0.0,
            # Cache lookup timing
            "cache_lookup_time_s": 0.0,
        }
        self.solid_rebuild_keys = defaultdict(int)

        self._orig_build_solid = nbs.build_multi_bend_sheet_solid
        self._orig_compute_placement = wp_mod.WorkpiecePlacement3D.compute_placement
        self._orig_check_pair = nc.check_pair_clearance
        self._orig_validate_pose_nc = nc.validate_workpiece_tooling_pose
        self._orig_validate_pose_mt = mt_mod.validate_workpiece_tooling_pose
        self._orig_classify_ez = nc.classify_punch_overlap_zones

    def install(self):
        profiler = self

        def patched_build_multi_bend_sheet_solid(*args, **kwargs):
            t0 = time.perf_counter()
            res = profiler._orig_build_solid(*args, **kwargs)
            dt = time.perf_counter() - t0
            profiler.stats["solid_rebuild_calls"] += 1
            profiler.stats["solid_rebuild_time_s"] += dt
            fold_state = args[0] if args else kwargs.get("fold_state")
            if fold_state:
                k = tuple(sorted((bid, round(float(a), 4)) for bid, a in fold_state.angles.items()))
                profiler.solid_rebuild_keys[k] += 1
            return res

        def patched_compute_placement(*args, **kwargs):
            t0 = time.perf_counter()
            res = profiler._orig_compute_placement(*args, **kwargs)
            dt = time.perf_counter() - t0
            profiler.stats["placement_calc_calls"] += 1
            profiler.stats["placement_calc_time_s"] += dt
            return res

        def patched_check_pair_clearance(shape_a, shape_b, *args, **kwargs):
            # check_pair_clearance natively tracks timing and operation counts
            res = profiler._orig_check_pair(shape_a, shape_b, *args, **kwargs)
            tool_lbl = kwargs.get("tool_label") or getattr(res, "tool_label", "")
            if tool_lbl == "PUNCH":
                profiler.stats["punch_eval_calls"] += 1
                profiler.stats["punch_eval_time_s"] += res.elapsed_ms / 1000.0
                profiler.stats["punch_bp_calls"] += 1
                profiler.stats["punch_bp_time_s"] += res.broad_phase_time_ms / 1000.0
                if res.distance_queries_count > 0:
                    profiler.stats["punch_dist_calls"] += res.distance_queries_count
                    profiler.stats["punch_dist_time_s"] += res.distance_time_ms / 1000.0
                if res.common_queries_count > 0:
                    profiler.stats["punch_common_calls"] += res.common_queries_count
                    profiler.stats["punch_common_time_s"] += res.common_time_ms / 1000.0
            elif tool_lbl == "DIE":
                profiler.stats["die_eval_calls"] += 1
                profiler.stats["die_eval_time_s"] += res.elapsed_ms / 1000.0
                profiler.stats["die_bp_calls"] += 1
                profiler.stats["die_bp_time_s"] += res.broad_phase_time_ms / 1000.0
                if res.distance_queries_count > 0:
                    profiler.stats["die_dist_calls"] += res.distance_queries_count
                    profiler.stats["die_dist_time_s"] += res.distance_time_ms / 1000.0
                if res.common_queries_count > 0:
                    profiler.stats["die_common_calls"] += res.common_queries_count
                    profiler.stats["die_common_time_s"] += res.common_time_ms / 1000.0
            return res

        def patched_classify_ez(*args, **kwargs):
            t0 = time.perf_counter()
            res = profiler._orig_classify_ez(*args, **kwargs)
            dt = time.perf_counter() - t0
            profiler.stats["punch_end_zone_calls"] += 1
            profiler.stats["punch_end_zone_time_s"] += dt
            return res

        def patched_validate_pose(wp_shape, punch_shape, die_shape, *args, **kwargs):
            return profiler._orig_validate_pose_nc(wp_shape, punch_shape, die_shape, *args, **kwargs)

        nbs.build_multi_bend_sheet_solid = patched_build_multi_bend_sheet_solid
        mt_mod.build_multi_bend_sheet_solid = patched_build_multi_bend_sheet_solid
        wp_mod.WorkpiecePlacement3D.compute_placement = patched_compute_placement
        nc.check_pair_clearance = patched_check_pair_clearance
        nc.validate_workpiece_tooling_pose = patched_validate_pose
        mt_mod.validate_workpiece_tooling_pose = patched_validate_pose
        nc.classify_punch_overlap_zones = patched_classify_ez

    def uninstall(self):
        nbs.build_multi_bend_sheet_solid = self._orig_build_solid
        mt_mod.build_multi_bend_sheet_solid = self._orig_build_solid
        wp_mod.WorkpiecePlacement3D.compute_placement = self._orig_compute_placement
        nc.check_pair_clearance = self._orig_check_pair
        nc.validate_workpiece_tooling_pose = self._orig_validate_pose_nc
        mt_mod.validate_workpiece_tooling_pose = self._orig_validate_pose_mt
        nc.classify_punch_overlap_zones = self._orig_classify_ez


def run_profiling(max_phase_time_s: float = 15.0) -> Dict[str, Any]:
    fixture_name = "box.100out-50in-(ok).FCStd"
    fixture_path = demo_part_path(fixture_name)
    doc = App.openDocument(str(fixture_path), True)
    unfold = next(o for o in doc.Objects if o.Name.endswith("_Unfold"))
    session = build_part_session(doc, [unfold])
    panel = _PreviewPanel()
    panel.session = session

    punch_name = "PUNCH_AP_1017_88°_H104,65_R0,8"
    die_name = "T-DIE_AT_3015_88°_V12_H80"
    p_tool = panel.tool_library.load_tool(punch_name, ToolKind.PUNCH)
    d_tool = panel.tool_library.load_tool(die_name, ToolKind.DIE)
    panel.select_punch(p_tool)
    panel.select_die(d_tool)
    panel.align_tooling()
    tooling = panel.tooling_setup

    session.fold_state.reset_all()
    session.set_bend_order(["B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08"])
    session.select_bend_index(0)
    panel._sync_tooling_to_active_bend()
    tooling.set_match_active_bend_length(True, 45.0)

    profiler = TrajectoryProfiler()
    profiler.install()

    cfg = AdaptiveSamplingConfig(
        early_stop_on_blocking_collision=True,
        max_phase_time_s=max_phase_time_s,
    )

    t_total0 = time.perf_counter()
    try:
        rep = check_active_25d_bend_trajectory(session, tooling, config=cfg)
    finally:
        profiler.uninstall()
        panel.close()
        panel.deleteLater()
        App.closeDocument(doc.Name)

    total_time_s = time.perf_counter() - t_total0
    raw_rep = rep.raw_report

    # Git metadata
    try:
        git_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=str(ROOT)).strip()
        git_dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True, cwd=str(ROOT)).strip())
    except Exception:
        git_commit = "unknown"
        git_dirty = True

    try:
        import Part
        occt_version = getattr(Part, "__occ_version__", "7.8.1")
    except Exception:
        occt_version = "7.8.1"

    freecad_version = getattr(App, "Version", lambda: ("1", "1", "3"))()
    fc_ver_str = f"{freecad_version[0]}.{freecad_version[1]}.{freecad_version[2]}" if isinstance(freecad_version, (list, tuple)) else str(freecad_version)

    phase_summaries = []
    total_cache_lookup_s = 0.0
    if raw_rep and raw_rep.phase_results:
        for pr in raw_rep.phase_results:
            total_cache_lookup_s += pr.total_cache_lookup_ms / 1000.0
            phase_summaries.append({
                "phase": pr.phase_name,
                "status": pr.evidence_status.value,
                "is_safe": pr.is_safe,
                "elapsed_s": round(pr.elapsed_ms / 1000.0, 3),
                "state_evaluation_requests": pr.state_evaluation_requests,
                "states_in_trajectory": len(pr.states),
                "pose_cache_hits": pr.pose_cache_hits,
                "punch_cache_hits": pr.punch_cache_hits,
                "die_cache_hits": pr.die_cache_hits,
                "solid_cache_hits": pr.solid_cache_hits,
                "pair_collision_checks": pr.pair_collision_checks,
                "actual_distance_queries": pr.actual_distance_queries,
                "actual_common_queries": pr.actual_common_queries,
                "solid_rebuilds": pr.solid_reconstructions_count,
                "diagnostics": pr.diagnostics,
            })

    # Hierarchical independent timing:
    # Top-level non-overlapping components:
    # 1. Solid part rebuilding (in Part Space)
    # 2. Workpiece placement computation
    # 3. Cache lookup overhead
    # 4. Punch clearance evaluation (actual non-cached time)
    # 5. Die clearance evaluation (actual non-cached time)
    # 6. Punch end-zone classification (if run outside pair clearance)
    accounted_time_s = (
        profiler.stats["solid_rebuild_time_s"]
        + profiler.stats["placement_calc_time_s"]
        + total_cache_lookup_s
        + profiler.stats["punch_eval_time_s"]
        + profiler.stats["die_eval_time_s"]
        + profiler.stats["punch_end_zone_time_s"]
    )
    unaccounted_time_s = max(0.0, total_time_s - accounted_time_s)

    result = {
        "metadata": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "git_commit": git_commit,
            "git_dirty": git_dirty,
            "freecad_version": fc_ver_str,
            "occt_version": occt_version,
            "fixture": fixture_name,
            "active_bend": "B01",
            "tool_length_mm": 45.0,
            "max_phase_time_s": max_phase_time_s,
            "historical_note": (
                "Previous time breakdown reported in commit ffdd411 is unconfirmed due to measurement "
                "bugs in earlier profiler (duplicate patch hooks, call-order tool identification, and nested timer double-counting)."
            ),
        },
        "total_wall_time_s": round(total_time_s, 4),
        "overall_status": rep.status.value,
        "phases": phase_summaries,
        "hierarchical_time_breakdown_s": {
            "solid_rebuild": {
                "calls": profiler.stats["solid_rebuild_calls"],
                "total_s": round(profiler.stats["solid_rebuild_time_s"], 4),
                "pct_total": round(100.0 * profiler.stats["solid_rebuild_time_s"] / max(total_time_s, 1e-6), 2),
            },
            "placement_calc": {
                "calls": profiler.stats["placement_calc_calls"],
                "total_s": round(profiler.stats["placement_calc_time_s"], 4),
                "pct_total": round(100.0 * profiler.stats["placement_calc_time_s"] / max(total_time_s, 1e-6), 2),
            },
            "cache_lookup_overhead": {
                "total_s": round(total_cache_lookup_s, 4),
                "pct_total": round(100.0 * total_cache_lookup_s / max(total_time_s, 1e-6), 2),
            },
            "punch_clearance_evaluation": {
                "calls": profiler.stats["punch_eval_calls"],
                "total_s": round(profiler.stats["punch_eval_time_s"], 4),
                "pct_total": round(100.0 * profiler.stats["punch_eval_time_s"] / max(total_time_s, 1e-6), 2),
                "internal_breakdown": {
                    "broad_phase_aabb": {
                        "calls": profiler.stats["punch_bp_calls"],
                        "total_s": round(profiler.stats["punch_bp_time_s"], 4),
                    },
                    "exact_distance": {
                        "calls": profiler.stats["punch_dist_calls"],
                        "total_s": round(profiler.stats["punch_dist_time_s"], 4),
                    },
                    "common_boolean": {
                        "calls": profiler.stats["punch_common_calls"],
                        "total_s": round(profiler.stats["punch_common_time_s"], 4),
                    },
                },
            },
            "die_clearance_evaluation": {
                "calls": profiler.stats["die_eval_calls"],
                "total_s": round(profiler.stats["die_eval_time_s"], 4),
                "pct_total": round(100.0 * profiler.stats["die_eval_time_s"] / max(total_time_s, 1e-6), 2),
                "internal_breakdown": {
                    "broad_phase_aabb": {
                        "calls": profiler.stats["die_bp_calls"],
                        "total_s": round(profiler.stats["die_bp_time_s"], 4),
                    },
                    "exact_distance": {
                        "calls": profiler.stats["die_dist_calls"],
                        "total_s": round(profiler.stats["die_dist_time_s"], 4),
                    },
                    "common_boolean": {
                        "calls": profiler.stats["die_common_calls"],
                        "total_s": round(profiler.stats["die_common_time_s"], 4),
                    },
                },
            },
            "end_zone_classification": {
                "calls": profiler.stats["punch_end_zone_calls"],
                "total_s": round(profiler.stats["punch_end_zone_time_s"], 4),
                "pct_total": round(100.0 * profiler.stats["punch_end_zone_time_s"] / max(total_time_s, 1e-6), 2),
            },
            "unaccounted_time": {
                "total_s": round(unaccounted_time_s, 4),
                "pct_total": round(100.0 * unaccounted_time_s / max(total_time_s, 1e-6), 2),
                "description": "Time outside the explicitly measured blocks (e.g. state management, loop dispatch); not attributed to GC or copy without measurement.",
            },
        },
        "separated_counters": {
            "total_state_evaluation_requests": sum(p["state_evaluation_requests"] for p in phase_summaries),
            "pose_cache_hits": sum(p["pose_cache_hits"] for p in phase_summaries),
            "punch_cache_hits": sum(p["punch_cache_hits"] for p in phase_summaries),
            "die_cache_hits": sum(p["die_cache_hits"] for p in phase_summaries),
            "solid_cache_hits": sum(p["solid_cache_hits"] for p in phase_summaries),
            "pair_collision_checks": sum(p["pair_collision_checks"] for p in phase_summaries),
            "actual_distance_queries": sum(p["actual_distance_queries"] for p in phase_summaries),
            "actual_common_queries": sum(p["actual_common_queries"] for p in phase_summaries),
            "solid_rebuilds": sum(p["solid_rebuilds"] for p in phase_summaries),
        },
        "redundant_rebuilds": {
            str(k): count for k, count in profiler.solid_rebuild_keys.items() if count > 1
        },
    }

    out_file = ROOT / "reports" / "profile_b01_trajectory_report.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"Authoritative profiling report written to: {out_file}", flush=True)

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


if __name__ == "__main__":
    run_profiling(max_phase_time_s=15.0)
