"""Reproducible 1-second FAST-BFM-lite benchmark using the installed solver.

Execute: python tools/run_fast_bfm_beam_benchmark.py
Only measures pure-Python matrix + bitmask Beam Search. Does not load actual
FCStd files or run 2D/3D collisions, punch, die, clamps, rear gauge or GUI.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smbs_core.kinematics2d.fast_bfm_beam_search import search_fast_candidates
from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.staged_bends import StagedBendSequenceController


def sample(count):
    panels = tuple("P{:02d}".format(i) for i in range(count + 1))
    edges = tuple(
        GraphEdge(
            "B{:02d}".format(i), panels[i-1], panels[i], 90.0,
            (
                (20.0 * (i // 2), 20.0 * i, 0.0),
                (20.0 * (i // 2) + (40.0 if i % 2 else 0.0),
                 20.0 * i + (0.0 if i % 2 else 40.0), 0.0),
            ),
        )
        for i in range(1, count + 1)
    )
    return FoldState(PanelGraph(panels, edges), panels[0])


def run():
    rows = []
    for count, staged in ((8, False), (10, False), (15, False), (10, True), (15, True)):
        fold = sample(count)
        controller = StagedBendSequenceController(fold)
        if staged:
            controller.add_prebend("B02", 35.0)
            controller.add_prebend("B{:02d}".format(count-1), 45.0)
        report = search_fast_candidates(
            fold, controller.operation_steps,
            limit_seconds=1.0, beam_width=32, max_candidates=5,
        )
        rows.append({
            "physical_bends": count,
            "operations": len(controller.operation_steps),
            "staged": staged,
            "elapsed_s": round(report.elapsed_seconds, 6),
            "status": report.status,
            "candidates": len(report.candidates),
            "explored": report.explored_states,
            "expanded": report.expanded_transitions,
            "duplicates_pruned": report.pruned_duplicate_states,
            "geometry_pairs": report.geometric_pair_count,
            "evidence": "UNVERIFIED_3D_NOT_CHECKED",
        })
    print(json.dumps(
        {"benchmark": "synthetic_geometry_and_search_only", "results": rows},
        ensure_ascii=False, indent=2,
    ))
    if not all(row["candidates"] and row["elapsed_s"] < 1.0 for row in rows):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
