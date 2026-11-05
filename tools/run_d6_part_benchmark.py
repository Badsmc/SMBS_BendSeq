"""Run native FreeCAD Part collision regression and benchmark (no pip deps).

Usage inside FreeCAD Python console:
    from pathlib import Path
from smbs_core.report_paths import report_file
    import smbs_core, runpy
    root = Path(smbs_core.__file__).resolve().parent.parent
    runpy.run_path(str(root / "tools" / "run_d6_part_benchmark.py"), run_name="__main__")

This script does not modify documents. It qualifies static full 3D solids
only; continuous bending/repositioning is not yet tested.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
import json
import os
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import FreeCAD as App
import Part
from smbs_core.kinematics2d.freecad_native_collision import (
    NativeStatus, classify_solids,
)


def _stats(times):
    ordered = sorted(times)
    return {
        "median_ms": round(statistics.median(ordered), 6),
        "p95_ms": round(ordered[min(len(ordered)-1, int(len(ordered)*0.95))], 6),
        "min_ms": round(ordered[0], 6),
        "max_ms": round(ordered[-1], 6),
    }


def _run_fixture(name, first, second, expected, expected_volume, iterations):
    results = [classify_solids(first, second) for _ in range(iterations)]
    counts = dict(Counter(x.status.value for x in results))
    reasons = dict(Counter(x.reason for x in results))
    errors = []
    for x in results:
        if x.status != expected:
            errors.append("wrong_status:" + x.status.value)
        if expected_volume is not None:
            if x.overlap_volume_mm3 is None:
                errors.append("missing_volume")
            elif abs(x.overlap_volume_mm3 - expected_volume) > max(1e-4, expected_volume*0.01):
                errors.append("wrong_volume:" + str(x.overlap_volume_mm3))
    return {
        "case": name, "expected_status": expected.value,
        "expected_overlap_volume_mm3": expected_volume,
        "iterations": iterations, "pass": not errors,
        "failed_checks": len(errors), "example_errors": errors[:10],
        "statuses": counts, "reasons": reasons,
        "times": _stats([x.elapsed_ms for x in results]),
        "first": {
            "status": results[0].status.value,
            "reason": results[0].reason,
            "distance_mm": results[0].distance_mm,
            "overlap_volume_mm3": results[0].overlap_volume_mm3,
        },
    }


def main():
    sheet = Part.makeBox(10, 10, 10, App.Vector(0, 0, 0))
    fixtures = [
        ("separated", Part.makeBox(10, 10, 10, App.Vector(40, 0, 0)), NativeStatus.CLEAR, None, 1000),
        ("overlap_coplanar", Part.makeBox(10, 10, 10, App.Vector(8, 0, 0)), NativeStatus.COLLISION, 200.0, 100),
        ("overlap_staggered", Part.makeBox(10, 10, 10, App.Vector(8, 1, 1)), NativeStatus.COLLISION, 162.0, 100),
        ("face_touch", Part.makeBox(10, 10, 10, App.Vector(10, 0, 0)), NativeStatus.UNVERIFIED, None, 100),
        ("small_clearance", Part.makeBox(10, 10, 10, App.Vector(10.01, 0, 0)), NativeStatus.UNVERIFIED, None, 100),
        ("clearance_above_threshold", Part.makeBox(10, 10, 10, App.Vector(10.1, 0, 0)), NativeStatus.CLEAR, None, 100),
    ]
    report = {
        "schema": "smbs-native-part-benchmark-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "freecad": ".".join(map(str, App.Version()[:3])),
        "python": sys.version.split()[0],
        "external_dependencies": False,
        "backend": "FreeCAD Part.Shape.common + distToShape + bounding box",
        "scope": "static boxes, not full motion validation",
        "fixtures": [],
    }
    for name, other, status, volume, iterations in fixtures:
        try:
            row = _run_fixture(name, sheet, other, status, volume, iterations)
        except Exception as exc:
            row = {"case": name, "pass": False, "error": str(exc)}
        report["fixtures"].append(row)
        print("SMBS native Part:", name, "PASS" if row.get("pass") else "FAIL",
              row.get("statuses"), row.get("times"))
    report["acceptance"] = (
        "PASS_STATIC_FIXTURES" if all(x.get("pass") for x in report["fixtures"])
        else "FAIL_STATIC_FIXTURES"
    )
    output = report_file(
        "part_benchmark_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json",
        legacy_override_env="SMBS_PART_BENCH_OUTPUT",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("SMBS native Part:", report["acceptance"], "report:", str(output))
    return report


if __name__ == "__main__":
    main()
