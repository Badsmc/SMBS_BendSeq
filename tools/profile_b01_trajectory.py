"""Fine-grained profiling for B01 trajectory check on box.100out-50in-(ok).FCStd.

Measures and breaks down exact time spent in:
- Solid part rebuilding (build_multi_bend_sheet_solid)
- Placement computation & transformShape
- Broad phase (AABB checks)
- Punch distance & common() boolean queries
- Die distance & common() boolean queries
- End-zone classification
- Cache lookup overhead vs cache hits
- Unexplained residual time
- Redundant / duplicate computations on identical bodies or poses
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

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
            "broad_phase_calls": 0,
            "broad_phase_time_s": 0.0,
            "punch_dist_calls": 0,
            "punch_dist_time_s": 0.0,
            "punch_common_calls": 0,
            "punch_common_time_s": 0.0,
            "die_dist_calls": 0,
            "die_dist_time_s": 0.0,
            "die_common_calls": 0,
            "die_common_time_s": 0.0,
            "end_zone_calls": 0,
            "end_zone_time_s": 0.0,
            "solid_cache_lookups": 0,
            "solid_cache_lookup_time_s": 0.0,
            "solid_cache_hits": 0,
            "die_cache_lookups": 0,
            "die_cache_lookup_time_s": 0.0,
            "die_cache_hits": 0,
        }
        self.solid_rebuild_keys = defaultdict(int)
        self.punch_eval_keys = defaultdict(int)
        self.die_eval_keys = defaultdict(int)
        self.end_zone_keys = defaultdict(int)

        self._orig_build_solid = nbs.build_multi_bend_sheet_solid
        self._orig_compute_placement = wp_mod.WorkpiecePlacement3D.compute_placement
        self._orig_check_pair = nc.check_pair_clearance
        self._orig_validate_pose = nc.validate_workpiece_tooling_pose
        self._orig_exact_dist = nc._compute_exact_distance_with_pruning
        self._orig_common_vol = nc._evaluate_common_volume
        self._orig_classify_ez = nc.classify_punch_overlap_zones

    def install(self):
        profiler = self

        def patched_build_multi_bend_sheet_solid(*args, **kwargs):
            t0 = time.perf_counter()
            res = profiler._orig_build_solid(*args, **kwargs)
            dt = time.perf_counter() - t0
            profiler.stats["solid_rebuild_calls"] += 1
            profiler.stats["solid_rebuild_time_s"] += dt
            # Record fold state key
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
            # Track broad phase AABB checks
            t_bp0 = time.perf_counter()
            box_a = getattr(shape_a, "BoundBox", None)
            box_b = getattr(shape_b, "BoundBox", None)
            gap = nc.compute_aabb_gap(box_a, box_b)
            dt_bp = time.perf_counter() - t_bp0
            profiler.stats["broad_phase_calls"] += 1
            profiler.stats["broad_phase_time_s"] += dt_bp

            old_tool = getattr(profiler, "_current_tool", None)
            if hasattr(profiler, "_punch_shape") and shape_b is profiler._punch_shape:
                profiler._current_tool = "punch"
            elif hasattr(profiler, "_die_shape") and shape_b is profiler._die_shape:
                profiler._current_tool = "die"
            try:
                return profiler._orig_check_pair(shape_a, shape_b, *args, **kwargs)
            finally:
                profiler._current_tool = old_tool

        def patched_exact_dist(shape_a, shape_b, box_a, box_b, *args, **kwargs):
            t0 = time.perf_counter()
            res = profiler._orig_exact_dist(shape_a, shape_b, box_a, box_b, *args, **kwargs)
            dt = time.perf_counter() - t0
            if getattr(profiler, "_current_tool", None) == "punch":
                profiler.stats["punch_dist_calls"] += 1
                profiler.stats["punch_dist_time_s"] += dt
            else:
                profiler.stats["die_dist_calls"] += 1
                profiler.stats["die_dist_time_s"] += dt
            return res

        def patched_common_vol(shape_a, shape_b):
            t0 = time.perf_counter()
            res = profiler._orig_common_vol(shape_a, shape_b)
            dt = time.perf_counter() - t0
            if getattr(profiler, "_current_tool", None) == "punch":
                profiler.stats["punch_common_calls"] += 1
                profiler.stats["punch_common_time_s"] += dt
            else:
                profiler.stats["die_common_calls"] += 1
                profiler.stats["die_common_time_s"] += dt
            return res

        def patched_classify_ez(*args, **kwargs):
            t0 = time.perf_counter()
            res = profiler._orig_classify_ez(*args, **kwargs)
            dt = time.perf_counter() - t0
            profiler.stats["end_zone_calls"] += 1
            profiler.stats["end_zone_time_s"] += dt
            return res

        def patched_validate_pose(wp_shape, punch_shape, die_shape, *args, **kwargs):
            # In validate_workpiece_tooling_pose, punch is checked first, die second.
            profiler._call_index = 0
            return orig_validate_pose(wp_shape, punch_shape, die_shape, *args, **kwargs)

        def patched_check_pair_clearance(shape_a, shape_b, *args, **kwargs):
            # Track broad phase AABB checks
            t_bp0 = time.perf_counter()
            box_a = getattr(shape_a, "BoundBox", None)
            box_b = getattr(shape_b, "BoundBox", None)
            gap = nc.compute_aabb_gap(box_a, box_b)
            dt_bp = time.perf_counter() - t_bp0
            profiler.stats["broad_phase_calls"] += 1
            profiler.stats["broad_phase_time_s"] += dt_bp

            old_tool = getattr(profiler, "_current_tool", None)
            idx = getattr(profiler, "_call_index", 0)
            if idx == 0:
                profiler._current_tool = "punch"
                profiler._call_index = 1
            else:
                profiler._current_tool = "die"
                profiler._call_index = 0
            try:
                return profiler._orig_check_pair(shape_a, shape_b, *args, **kwargs)
            finally:
                profiler._current_tool = old_tool

        nbs.build_multi_bend_sheet_solid = patched_build_multi_bend_sheet_solid
        mt_mod.build_multi_bend_sheet_solid = patched_build_multi_bend_sheet_solid
        wp_mod.WorkpiecePlacement3D.compute_placement = patched_compute_placement
        nc.compute_exact_distance_with_pruning = patched_exact_dist
        nc._compute_exact_distance_with_pruning = patched_exact_dist
        nc._evaluate_common_volume = patched_common_vol
        nc.classify_punch_overlap_zones = patched_classify_ez
        nc.check_pair_clearance = patched_check_pair_clearance
        nc.validate_workpiece_tooling_pose = patched_validate_pose

    def uninstall(self):
        nbs.build_multi_bend_sheet_solid = self._orig_build_solid
        mt_mod.build_multi_bend_sheet_solid = self._orig_build_solid
        wp_mod.WorkpiecePlacement3D.compute_placement = self._orig_compute_placement
        nc._compute_exact_distance_with_pruning = self._orig_exact_dist
        nc._evaluate_common_volume = self._orig_common_vol
        nc.classify_punch_overlap_zones = self._orig_classify_ez
        nc.check_pair_clearance = self._orig_check_pair
        nc.validate_workpiece_tooling_pose = self._orig_validate_pose


def run_profiling(max_phase_time_s: float = 15.0):
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
    rep = check_active_25d_bend_trajectory(session, tooling, config=cfg)
    total_time_s = time.perf_counter() - t_total0

    profiler.uninstall()

    raw_rep = rep.raw_report
    phase_summaries = []
    if raw_rep and raw_rep.phase_results:
        for pr in raw_rep.phase_results:
            phase_summaries.append({
                "phase": pr.phase_name,
                "status": pr.evidence_status.value,
                "elapsed_s": round(pr.elapsed_ms / 1000.0, 3),
                "states": len(pr.states),
                "solid_hits": pr.solid_cache_hits,
                "solid_misses": pr.solid_cache_misses,
                "die_hits": pr.die_cache_hits,
                "die_misses": pr.die_cache_misses,
                "diagnostics": pr.diagnostics,
            })

    # Calculate accounted time
    accounted_time_s = (
        profiler.stats["solid_rebuild_time_s"]
        + profiler.stats["placement_calc_time_s"]
        + profiler.stats["broad_phase_time_s"]
        + profiler.stats["punch_dist_time_s"]
        + profiler.stats["punch_common_time_s"]
        + profiler.stats["die_dist_time_s"]
        + profiler.stats["die_common_time_s"]
        + profiler.stats["end_zone_time_s"]
    )
    residual_time_s = total_time_s - accounted_time_s

    # Cache hit metrics
    total_solid_rebuilds = sum(p["solid_misses"] for p in phase_summaries)
    total_solid_hits = sum(p["solid_hits"] for p in phase_summaries)
    total_die_misses = sum(p["die_misses"] for p in phase_summaries)
    total_die_hits = sum(p["die_hits"] for p in phase_summaries)

    result = {
        "benchmark": "B01 trajectory profiling on box.100out-50in-(ok).FCStd",
        "total_wall_time_s": round(total_time_s, 4),
        "phases": phase_summaries,
        "time_breakdown_s": {
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
            "broad_phase_aabb": {
                "calls": profiler.stats["broad_phase_calls"],
                "total_s": round(profiler.stats["broad_phase_time_s"], 4),
                "pct_total": round(100.0 * profiler.stats["broad_phase_time_s"] / max(total_time_s, 1e-6), 2),
            },
            "punch_distance_exact": {
                "calls": profiler.stats["punch_dist_calls"],
                "total_s": round(profiler.stats["punch_dist_time_s"], 4),
                "pct_total": round(100.0 * profiler.stats["punch_dist_time_s"] / max(total_time_s, 1e-6), 2),
            },
            "punch_common_boolean": {
                "calls": profiler.stats["punch_common_calls"],
                "total_s": round(profiler.stats["punch_common_time_s"], 4),
                "pct_total": round(100.0 * profiler.stats["punch_common_time_s"] / max(total_time_s, 1e-6), 2),
            },
            "die_distance_exact": {
                "calls": profiler.stats["die_dist_calls"],
                "total_s": round(profiler.stats["die_dist_time_s"], 4),
                "pct_total": round(100.0 * profiler.stats["die_dist_time_s"] / max(total_time_s, 1e-6), 2),
            },
            "die_common_boolean": {
                "calls": profiler.stats["die_common_calls"],
                "total_s": round(profiler.stats["die_common_time_s"], 4),
                "pct_total": round(100.0 * profiler.stats["die_common_time_s"] / max(total_time_s, 1e-6), 2),
            },
            "end_zone_classification": {
                "calls": profiler.stats["end_zone_calls"],
                "total_s": round(profiler.stats["end_zone_time_s"], 4),
                "pct_total": round(100.0 * profiler.stats["end_zone_time_s"] / max(total_time_s, 1e-6), 2),
            },
            "unexplained_residual": {
                "total_s": round(residual_time_s, 4),
                "pct_total": round(100.0 * residual_time_s / max(total_time_s, 1e-6), 2),
            },
        },
        "cache_metrics": {
            "solid_cache": {
                "hits": total_solid_hits,
                "misses": total_solid_rebuilds,
                "hit_rate_pct": round(100.0 * total_solid_hits / max(total_solid_hits + total_solid_rebuilds, 1), 2),
            },
            "die_clearance_cache": {
                "hits": total_die_hits,
                "misses": total_die_misses,
                "hit_rate_pct": round(100.0 * total_die_hits / max(total_die_hits + total_die_misses, 1), 2),
            },
        },
        "redundant_rebuilds": {
            k: count for k, count in profiler.solid_rebuild_keys.items() if count > 1
        },
    }

    panel.close()
    panel.deleteLater()
    App.closeDocument(doc.Name)

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


if __name__ == "__main__":
    run_profiling(max_phase_time_s=15.0)
