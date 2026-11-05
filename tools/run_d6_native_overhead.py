"""Measure per-call FreeCAD Part validation overhead without altering the solver.

Run in FreeCAD Python console:
    import runpy, smbs_core
    from pathlib import Path
    root = Path(smbs_core.__file__).resolve().parent.parent
    runpy.run_path(str(root / "tools" / "run_d6_native_overhead.py"), run_name="__main__")

No external dependencies; does not edit FreeCAD documents. This is a
microbenchmark, NOT a collision checker and NOT evidence of production safety.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from time import perf_counter
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import FreeCAD as App
import Part

from smbs_core.kinematics2d.mesh_collision_poc import bbox_gap_mm
from smbs_core.kinematics2d.freecad_native_collision import classify_solids


def measure(label, func, repeats=500):
    # Warm up native FreeCAD lazy initialization.
    for _ in range(5):
        func()
    samples = []
    for _ in range(repeats):
        start = perf_counter()
        func()
        samples.append((perf_counter() - start) * 1000.0)
    samples.sort()
    return {
        "name": label,
        "iterations": repeats,
        "median_ms": round(median(samples), 6),
        "p95_ms": round(samples[min(repeats - 1, int(0.95 * repeats))], 6),
        "min_ms": round(samples[0], 6),
        "max_ms": round(samples[-1], 6),
    }


def main():
    a = Part.makeBox(10, 10, 10, App.Vector(0, 0, 0))
    b = Part.makeBox(10, 10, 10, App.Vector(40, 0, 0))
    c = Part.makeBox(10, 10, 10, App.Vector(8, 0, 0))
    tasks = [
        ("aabb_only_disjoint", lambda: bbox_gap_mm(a.BoundBox, b.BoundBox)),
        ("shape_validity_only", lambda: (
            a.isNull(), b.isNull(), a.isValid(), b.isValid(),
            a.isClosed(), b.isClosed(), a.Volume, b.Volume,
        )),
        ("production_candidate_disjoint", lambda: classify_solids(a, b)),
        ("production_candidate_overlap", lambda: classify_solids(a, c)),
        ("part_common_overlap_only", lambda: a.common(c).Volume),
        ("part_distance_disjoint_only", lambda: a.distToShape(b)[0]),
    ]
    report = {
        "schema": "smbs-part-overhead-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "freecad": ".".join(map(str, App.Version()[:3])),
        "scope": "Microbenchmark static valid cubes; source shapes never change",
        "benchmarks": [],
    }
    for name, fn in tasks:
        try:
            row = measure(name, fn)
        except Exception as exc:
            row = {"name": name, "error": str(exc)}
        report["benchmarks"].append(row)
        print("SMBS Part overhead:", row)
    output = Path(os.environ.get("SMBS_PART_OVERHEAD_OUTPUT") or (
        Path.home() / "pressbrake_traces" /
        ("part_overhead_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    ))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("SMBS Part overhead report:", output)
    return report


if __name__ == "__main__":
    main()
