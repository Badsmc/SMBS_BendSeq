"""Reproducible pure-Python FAST search benchmark (no FreeCAD/OCC).

Command: python tools/run_fast_bfm_beam_benchmark.py
Times describe only the cheap candidate SEARCH on this machine. They are not
3D collision-verification or whole GUI startup benchmarks.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smbs_core.kinematics2d.fast_bfm_beam import (
    FastProblem, FastStep, search_fast_beam,
)


def synthetic_problem(bend_count: int, *, staged: bool = False) -> FastProblem:
    steps = []
    precedences = []
    for i in range(bend_count):
        bend = "B{:02d}".format(i + 1)
        axis = "axis:0" if i % 3 else "axis:90"
        length = 100.0 + 25.0 * (i % 4)
        if staged and i in (1, bend_count - 2):
            a, b = bend + ".1", bend + ".2"
            steps.append(FastStep(a, bend, "PREBEND", 35.0, axis, 0.0, length))
            steps.append(FastStep(b, bend, "FINISH", 90.0, axis, 0.0, length))
            precedences.append((a, b))
        else:
            steps.append(FastStep(bend, bend, "SINGLE", 90.0, axis, 0.0, length))
    return FastProblem(tuple(steps), tuple(precedences))


def run():
    rows = []
    for bends, staged in ((8, False), (10, False), (15, False), (10, True), (15, True)):
        problem = synthetic_problem(bends, staged=staged)
        outcome = search_fast_beam(problem, time_budget_s=1.0, beam_width=32)
        rows.append({
            "bends": bends,
            "operations": len(problem.steps),
            "staged": staged,
            "elapsed_s": round(outcome.elapsed_s, 6),
            "status": outcome.status,
            "candidates": len(outcome.candidates),
            "expanded": outcome.expanded,
            "generated": outcome.generated,
            "cache_hits": outcome.cache_hits,
            "deduplicated_states": outcome.deduplicated_states,
            "evidence": "UNVERIFIED_FAST_TOPOLOGY",
        })
    print(json.dumps({"mode": "synthetic_search_only", "results": rows}, indent=2))
    return 0 if all(x["candidates"] and x["elapsed_s"] < 1.0 for x in rows) else 1


if __name__ == "__main__":
    raise SystemExit(run())
