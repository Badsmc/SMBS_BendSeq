"""Run D6 FCL evaluation inside FreeCAD 1.1.3 console.

Example:
    import runpy
    runpy.run_path(str(script), run_name="__main__")

No installed python-fcl is presumed; the script reports an actionable
dependency error without modifying FreeCAD's Python installation.
"""
from __future__ import annotations

import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import FreeCAD as App
import Part

from smbs_core.kinematics2d.fcl_collision_poc import (
    FCLStatus, _require_runtime, build_fcl_box,
    benchmark_fcl_pair, fcl_from_shape,
)
from smbs_core.kinematics2d.mesh_collision_poc import mesh_from_shape


def _case(label, a, b, expected, repeats):
    report = benchmark_fcl_pair(a, b, expected=expected, iterations=repeats)
    print("SMBS FCL:", label, "PASS" if report["pass"] else "FAIL",
          report["statuses"], "median_ms:", round(report["median_ms"], 5))
    return {"case": label, "result": report}


def main():
    report = {
        "schema": "smbs-fcl-poc-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "freecad_version": ".".join(map(str, App.Version()[:3])),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "branch": "d6-fcl-poc",
        "backend": "python-fcl",
        "dependency_status": None,
        "cases": [],
        "acceptance": "UNVERIFIED",
        "limitations": [
            "Static 3D collision/distance only; no transfer swept-volume test",
            "No test yet for full finite-thickness FoldState across B01-B08",
            "FCL contact is conservatively a blocker, no support/contact allowance",
        ],
    }
    try:
        np, fcl = _require_runtime()
        report["dependency_status"] = {
            "available": True,
            "fcl_version": str(getattr(fcl, "__version__", "unknown")),
            "numpy_version": str(np.__version__),
        }
        # Actual MeshPart bridge: create 10 mm solids in memory, import via
        # installed FreeCAD MeshPart and build FCL BVH collision objects.
        first = Part.makeBox(10, 10, 10, App.Vector(0, 0, 0))
        separated = Part.makeBox(10, 10, 10, App.Vector(40, 0, 0))
        penetrating = Part.makeBox(10, 10, 10, App.Vector(8, 0, 0))
        touching = Part.makeBox(10, 10, 10, App.Vector(10, 0, 0))
        a = fcl_from_shape(first)
        b = fcl_from_shape(separated)
        c = fcl_from_shape(penetrating)
        d = fcl_from_shape(touching)
        report["cases"].extend((
            _case("mesh_separated", a, b, FCLStatus.CLEAR, 1000),
            _case("mesh_penetrating", a, c, FCLStatus.COLLISION, 1000),
        ))
        # FCL's mesh contact semantics at exact co-planar contact depend on
        # tolerance and solver. Record without requiring a specific status.
        touching_check = benchmark_fcl_pair(
            a, d, expected=FCLStatus.COLLISION, iterations=100,
        )
        report["cases"].append({
            "case": "mesh_touching_diagnostic",
            "result": touching_check,
            "acceptance_required": False,
        })
        # Primitive cross-check isolates FCL core from MeshPart conversion.
        box_a = build_fcl_box((10,10,10), (5,5,5))
        box_b = build_fcl_box((10,10,10), (45,5,5))
        box_c = build_fcl_box((10,10,10), (13,5,5))
        report["cases"].extend((
            _case("primitive_separated", box_a, box_b, FCLStatus.CLEAR, 1000),
            _case("primitive_penetrating", box_a, box_c, FCLStatus.COLLISION, 1000),
        ))
        report["acceptance"] = (
            "PASS_STATIC_FIXTURES"
            if all(row["result"]["pass"] for row in report["cases"]
                   if row.get("acceptance_required", True))
            else "FAIL_STATIC_FIXTURES"
        )
    except Exception as exc:
        report["dependency_status"] = {
            "available": False,
            "reason": str(exc),
        }
        report["acceptance"] = "NOT_RUN"
        print("SMBS FCL unavailable:", str(exc))
    output = Path(os.environ.get("SMBS_FCL_POC_OUTPUT") or
                  (Path.home() / "pressbrake_traces" /
                   ("fcl_poc_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SMBS FCL PoC:", report["acceptance"], "report:", str(output))
    return report


if __name__ == "__main__":
    main()
