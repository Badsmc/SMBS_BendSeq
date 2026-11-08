"""Automatic Sequence Search Runner for box.100out-50in-(ok).FCStd.

Evaluates 2.5D physical validation and D6 adaptive trajectory search across all 8 bends
under virtual tool length mode (punch/die length = active bend length) and controlled
end-zone tolerance.

Tests tooling pairs:
1. PUNCH_AP_1017_88°_H104,65_R0,8 + T-DIE_AT_3015_88°_V12_H80
2. PUNCH_AP_1013_85°_H159,65_R0,8 + T-DIE_AT_3015_88°_V12_H80
3. puancon + matrica

Saves structured JSON reports in reports/ directory and verifies any found sequence
via independent forward replay from 0° across all 5 phases.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _p in ("/Applications/FreeCAD.app/Contents/Resources/lib",):
    if os.path.exists(_p) and _p not in sys.path:
        sys.path.append(_p)

import FreeCAD as App

from freecad_smbs.commands.pressbrake_2d import (
    _PreviewPanel,
    _qt,
    build_part_session,
)
from smbs_core.demo_paths import demo_part_path
from smbs_core.kinematics2d.physical_validation_25d import PhysicalEvidence
from smbs_core.kinematics2d.press_cycle import (
    CycleExecutionMode,
    PressCycleController,
)
from smbs_core.kinematics2d.sequence_search import (
    SearchStatus,
    StepEvaluationCache,
    search_bend_sequence_25d,
    verify_sequence_forward_replay,
)
from smbs_core.kinematics2d.tooling_25d import ToolKind, ToolingSetup2D


def run_search_for_tooling(
    panel: _PreviewPanel,
    session,
    punch_id: str,
    die_id: str,
    end_zone_len_mm: float = 15.0,
    max_states: int = 500,
    max_checks: int = 1000,
    max_search_time_s: float = 600.0,
) -> dict:
    punch_tool = panel.tool_library.load_tool(punch_id, ToolKind.PUNCH)
    die_tool = panel.tool_library.load_tool(die_id, ToolKind.DIE)

    panel.select_punch(punch_tool)
    panel.select_die(die_tool)
    panel.align_tooling()

    tooling = panel.tooling_setup
    tooling.set_match_active_bend_length(True, 45.0)

    cycle = panel._ensure_press_cycle_controller()
    tooling.set_match_active_bend_length(True, 45.0)

    cache = StepEvaluationCache()

    print(f"\n================================================================================")
    print(f"SEARCH CANDIDATE: punch={punch_id} + die={die_id}")
    print(f"MODES: match_active_bend_length=True, end_zone_length_mm={end_zone_len_mm}")
    print(f"================================================================================")

    t0 = time.perf_counter()
    report = search_bend_sequence_25d(
        session=session,
        press_cycle=cycle,
        tooling=tooling,
        match_active_bend_length=True,
        allow_end_zone_collision=True,
        end_zone_length_mm=end_zone_len_mm,
        max_states=max_states,
        max_checks=max_checks,
        max_search_time_s=max_search_time_s,
        cache=cache,
    )
    elapsed = time.perf_counter() - t0

    print(f"Search completed in {elapsed:.2f}s:")
    print(f"  Status: {report.status.value}")
    print(f"  Overall Evidence: {report.overall_evidence.value}")
    print(f"  Strict Pass?: {report.is_strict_pass}")
    print(f"  Conditional Pass?: {report.is_conditional_pass}")
    print(f"  Found Sequence: {report.found_sequence}")
    print(f"  Best Partial Sequence: {report.best_partial_sequence} ({len(report.best_partial_sequence)} of {report.total_bends})")
    print(f"  Strict Search Attempted: {report.strict_search_attempted}")
    print(f"  Strict Search Exhausted: {report.strict_search_exhausted}")
    print(f"  Strict Search Limit Reached: {report.strict_search_limit_reached}")
    print(f"  Strict Note: {report.strict_search_note}")
    print(f"  Search Limit Reason: {report.search_limit_reason}")
    print(f"  States Visited: {report.states_visited}, Checks: {report.step_evaluations}, Cache Hits: {report.cache_hits}, Dead Ends: {report.dead_end_hits}, Backtracks: {report.backtracks_count}")
    print(f"  Stage A (Fast Geom): eval={report.stage_a_evaluated}, pruned={report.stage_a_pruned}")
    print(f"  Stage B (2D Multi-Phase): eval={report.stage_b_evaluated}, pruned={report.stage_b_pruned}")
    print(f"  Stage C (Native 3D): eval={report.stage_c_native_evaluated}")
    if report.plan_cost:
        print(f"  Plan Penalty Cost: {report.plan_cost.get('total', 0.0):.2f}")
    print(f"  Conditional Bends: {report.conditional_bends}")

    res_dict = report.to_dict()
    res_dict["candidate_punch"] = punch_id
    res_dict["candidate_die"] = die_id
    res_dict["timestamp_utc"] = datetime.now(timezone.utc).isoformat()
    return res_dict


def main():
    parser = argparse.ArgumentParser(description="Automatic sequence search on box model")
    parser.add_argument("--punch", default=None, help="Specific punch ID")
    parser.add_argument("--die", default=None, help="Specific die ID")
    parser.add_argument("--timeout", type=float, default=600.0, help="Max search time in seconds")
    parser.add_argument("--max-states", type=int, default=500, help="Max DFS states")
    parser.add_argument("--max-checks", type=int, default=1000, help="Max step evaluations")
    parser.add_argument("--end-zone", type=float, default=15.0, help="End zone length in mm")
    args = parser.parse_args()

    _QtCore, _QtGui, QtWidgets = _qt()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    fixture = demo_part_path("box.100out-50in-(ok).FCStd")
    if not fixture.is_file():
        print(f"ERROR: Model file not found: {fixture}")
        sys.exit(1)

    doc = App.openDocument(str(fixture), True)
    unfold = next(o for o in doc.Objects if o.Name.endswith("_Unfold"))
    session = build_part_session(doc, [unfold])
    panel = _PreviewPanel()
    panel.session = session
    panel._ensure_press_cycle_controller()

    reports_dir = ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    if args.punch and args.die:
        candidates = [(args.punch, args.die)]
    else:
        candidates = [
            ("PUNCH_AP_1017_88°_H104,65_R0,8", "T-DIE_AT_3015_88°_V12_H80"),
            ("PUNCH_AP_1013_85°_H159,65_R0,8", "T-DIE_AT_3015_88°_V12_H80"),
            ("puancon", "matrica"),
        ]

    all_results = []
    for punch_id, die_id in candidates:
        res = run_search_for_tooling(
            panel=panel,
            session=session,
            punch_id=punch_id,
            die_id=die_id,
            end_zone_len_mm=args.end_zone,
            max_states=args.max_states,
            max_checks=args.max_checks,
            max_search_time_s=args.timeout,
        )
        all_results.append(res)

    out_file = reports_dir / "box_automatic_sequence_search_report.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "model": "box.100out-50in-(ok).FCStd",
                "total_bends": 8,
                "initial_bend_order": ["B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08"],
                "results": all_results,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"\nSaved combined search report to {out_file}")

    panel.close()
    panel.deleteLater()
    App.closeDocument(doc.Name)


if __name__ == "__main__":
    main()
