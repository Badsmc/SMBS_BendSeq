"""tools/compare_forward_backward_search_z_part.py - Comparative benchmark on Z-(ok).FCStd.

Runs forward and backward sequence search with physical 5-phase validation
under identical budgets, cold caches, and strict collision rules.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict

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

from tools.benchmark_forward_backward_base import (
    build_comparison_summary,
    format_search_record,
    get_git_info,
    run_single_search_in_clean_session,
)
from smbs_core.kinematics2d.motion_trajectory_3d import AdaptiveSamplingConfig
from smbs_core.kinematics2d.operation_cost import OperationWeights


def run_z_benchmark():
    git_commit, git_dirty = get_git_info()
    fixture_name = "Z-(ok).FCStd"
    punch_name = "PUNCH_AP_1017_88°_H104,65_R0,8"
    die_name = "T-DIE_AT_3015_88°_V12_H80"

    sampling_cfg = AdaptiveSamplingConfig(
        early_stop_on_blocking_collision=True,
        max_phase_time_s=180.0,
        arc_segments=128,
        near_tolerance_mm=0.5,
        volume_tolerance_mm3=1e-4,
        contact_distance_tolerance_mm=1e-4,
    )
    weights = OperationWeights(flip=20.0, turn_per_90_deg=10.0, shift_fixed=5.0)

    max_search_time_s = 600.0
    max_states = 20
    max_checks = 50

    command_line = (
        'PYTHONPATH="/Users/badmc/Library/Application Support/FreeCAD/v1-1/Mod:'
        '/Applications/FreeCAD.app/Contents/Resources/lib:." '
        '/Applications/FreeCAD.app/Contents/Resources/bin/python '
        'tools/compare_forward_backward_search_z_part.py'
    )

    print("=== [1/2] Running Forward Search on Z-(ok).FCStd ===", flush=True)
    fwd_rep, fwd_cache, fwd_wall_s, all_bends = run_single_search_in_clean_session(
        direction="FORWARD",
        fixture_name=fixture_name,
        punch_name=punch_name,
        die_name=die_name,
        sampling_cfg=sampling_cfg,
        weights=weights,
        max_search_time_s=max_search_time_s,
        max_states=max_states,
        max_checks=max_checks,
        match_active_bend_length=True,
        allow_end_zone_collision=False,
        end_zone_length_mm=0.0,
        optimize_cost=False,
    )
    fwd_record = format_search_record("FORWARD", fwd_rep, fwd_cache, fwd_wall_s)
    print(f"Forward done: status={fwd_rep.status.value}, seq={fwd_rep.found_sequence}, wall={fwd_wall_s:.2f}s", flush=True)

    print("\n=== [2/2] Running Backward Search on Z-(ok).FCStd ===", flush=True)
    bwd_rep, bwd_cache, bwd_wall_s, _ = run_single_search_in_clean_session(
        direction="BACKWARD",
        fixture_name=fixture_name,
        punch_name=punch_name,
        die_name=die_name,
        sampling_cfg=sampling_cfg,
        weights=weights,
        max_search_time_s=max_search_time_s,
        max_states=max_states,
        max_checks=max_checks,
        match_active_bend_length=True,
        allow_end_zone_collision=False,
        end_zone_length_mm=0.0,
        optimize_cost=False,
    )
    bwd_record = format_search_record("BACKWARD", bwd_rep, bwd_cache, bwd_wall_s)
    print(f"Backward done: status={bwd_rep.status.value}, seq={bwd_rep.found_sequence}, wall={bwd_wall_s:.2f}s", flush=True)

    summary = build_comparison_summary(fwd_record, bwd_record)

    results: Dict[str, Any] = {
        "metadata": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "git_commit": git_commit,
            "git_dirty": git_dirty,
            "command_line": command_line,
            "freecad_version": "1.1.3",
            "occt_version": "7.8.1",
            "fixture": fixture_name,
            "total_bends": len(all_bends),
            "bends": all_bends,
            "tool_punch": punch_name,
            "tool_die": die_name,
            "match_active_bend_length": True,
            "allow_end_zone_collision": False,
            "end_zone_length_mm": 0.0,
            "sampling_config": {
                "max_bend_angle_step_deg": sampling_cfg.max_bend_angle_step_deg,
                "max_phase_time_s": sampling_cfg.max_phase_time_s,
                "early_stop_on_blocking_collision": sampling_cfg.early_stop_on_blocking_collision,
                "min_clearance_margin_mm": sampling_cfg.min_clearance_margin_mm,
                "volume_tolerance_mm3": sampling_cfg.volume_tolerance_mm3,
                "contact_distance_tolerance_mm": sampling_cfg.contact_distance_tolerance_mm,
            },
            "weights": {
                "flip": weights.flip,
                "turn_per_90_deg": weights.turn_per_90_deg,
                "shift_fixed": weights.shift_fixed,
            },
            "budgets": {
                "max_search_time_s": max_search_time_s,
                "max_states": max_states,
                "max_checks": max_checks,
                "max_phase_time_s": sampling_cfg.max_phase_time_s,
            },
            "timing_accounting_rule": (
                "search_time_s records DFS tree exploration before plan discovery; "
                "replay_time_s records independent forward replay from flat blank (0°); "
                "total_wall_time_s covers complete execution from clean session launch."
            ),
        },
        "forward_search": fwd_record,
        "backward_search": bwd_record,
        "comparison_summary": summary,
    }

    out_file = ROOT / "reports" / "compare_forward_backward_z_part_report.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nAuthoritative report written to: {out_file}", flush=True)
    return results


if __name__ == "__main__":
    run_z_benchmark()
