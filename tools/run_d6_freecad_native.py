"""D6 FreeCAD-native collision diagnostics. Requires NO additional packages.

Run inside FreeCAD Python console:
    import runpy
    runpy.run_path(str(script), run_name="__main__")

Investigates why Mesh.intersect yields nondeterministic volumes for identical
10 mm CAD cubes. Compares reused meshes, copied meshes, re-tessellated meshes,
operand order, and non-coplanar overlap; measures Part.common and distToShape.

No documents are edited; no external dependencies. Report is always JSON.
"""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
import json
import math
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import FreeCAD as App
import Part
from smbs_core.kinematics2d.mesh_collision_poc import (
    mesh_from_shape, bbox_overlap_volume_mm3,
)


def _mesh_status(a, b, expected_volume):
    start = perf_counter()
    try:
        result = a.intersect(b)
        facets = int(result.CountFacets)
        volume = float(result.Volume) if facets else None
        upper = bbox_overlap_volume_mm3(a.BoundBox, b.BoundBox)
        valid = (volume is not None and math.isfinite(volume)
                 and bool(result.isSolid()) and not result.hasSelfIntersections())
        if expected_volume == 0:
            ok = not facets or (volume is not None and abs(volume) < 1e-5)
        else:
            ok = valid and abs(volume - expected_volume) <= max(1e-4, expected_volume * 0.01)
        return {
            "facets": facets,
            "volume_mm3": volume,
            "is_solid": bool(result.isSolid()) if facets else False,
            "self_intersections": bool(result.hasSelfIntersections()) if facets else False,
            "aabb_volume_limit_mm3": upper,
            "correct": bool(ok),
            "elapsed_ms": round((perf_counter()-start)*1000, 5),
        }
    except Exception as exc:
        return {"error": str(exc), "correct": False,
                "elapsed_ms": round((perf_counter()-start)*1000, 5)}


def _part_status(a, b, expected_volume):
    start = perf_counter()
    out = {}
    try:
        common = a.common(b)
        volume = float(common.Volume)
        out["common_volume_mm3"] = volume
        out["common_correct"] = abs(volume-expected_volume) <= max(1e-4, expected_volume*0.01)
        out["common_ms"] = round((perf_counter()-start)*1000, 5)
    except Exception as exc:
        out["common_error"] = str(exc)
    start = perf_counter()
    try:
        out["distToShape_mm"] = float(a.distToShape(b)[0])
        out["distance_ms"] = round((perf_counter()-start)*1000, 5)
    except Exception as exc:
        out["distance_error"] = str(exc)
    return out


def _case(name, s1, s2, expected, count=40):
    mesh1 = mesh_from_shape(s1)
    mesh2 = mesh_from_shape(s2)
    variants = {}
    for mode in ("reuse", "copies", "reverse_order", "retessellate"):
        outcomes = []
        for _ in range(count):
            if mode == "copies":
                a, b = mesh1.copy(), mesh2.copy()
            elif mode == "retessellate":
                a, b = mesh_from_shape(s1), mesh_from_shape(s2)
            elif mode == "reverse_order":
                a, b = mesh2, mesh1
            else:
                a, b = mesh1, mesh2
            outcomes.append(_mesh_status(a, b, expected))
        volumes = Counter(str(o.get("volume_mm3")) for o in outcomes)
        reasons = Counter(o.get("error", "OK") for o in outcomes)
        times = sorted(x["elapsed_ms"] for x in outcomes)
        variants[mode] = {
            "runs": count,
            "correct": sum(bool(x["correct"]) for x in outcomes),
            "pass": all(x["correct"] for x in outcomes),
            "volumes_mm3_histogram": dict(sorted(volumes.items())),
            "errors": dict(sorted(reasons.items())),
            "median_ms": times[len(times)//2],
            "p95_ms": times[min(len(times)-1, int(count*0.95))],
            "first": outcomes[0],
        }
    ref = [_part_status(s1, s2, expected) for _ in range(min(count, 10))]
    print("SMBS native:", name,
          {mode: v["correct"] for mode, v in variants.items()},
          "out of", count)
    return {
        "name": name,
        "expected_volume_mm3": expected,
        "source_facet_counts": [int(mesh1.CountFacets), int(mesh2.CountFacets)],
        "variants": variants,
        "part_reference_first": ref[0],
        "part_reference_all_correct": all(x.get("common_correct", False) for x in ref),
    }


def main():
    a = Part.makeBox(10, 10, 10, App.Vector(0, 0, 0))
    cases = [
        ("coplanar_faces_overlap_2mm",
         Part.makeBox(10,10,10,App.Vector(8,0,0)), 200.0),
        ("staggered_overlap_xyz",
         Part.makeBox(10,10,10,App.Vector(8,1,1)), 162.0),
        ("separated",
         Part.makeBox(10,10,10,App.Vector(40,0,0)), 0.0),
        ("face_touch",
         Part.makeBox(10,10,10,App.Vector(10,0,0)), 0.0),
    ]
    report = {
        "schema": "smbs-freecad-native-diagnostic-v1",
        "date_utc": datetime.now(timezone.utc).isoformat(),
        "freecad": ".".join(map(str, App.Version()[:3])),
        "python": sys.version.split()[0],
        "external_dependencies": False,
        "description": "Mesh boolean repeatability vs FreeCAD Part in real native runtime.",
        "results": [],
    }
    for name, b, expected in cases:
        try:
            report["results"].append(_case(name, a, b, expected))
        except Exception as exc:
            report["results"].append({"name": name, "error": str(exc)})
    report["mesh_qualified"] = all(
        case.get("part_reference_all_correct", False)
        and all(v["pass"] for v in case.get("variants", {}).values())
        for case in report["results"]
    )
    output = Path(os.environ.get("SMBS_NATIVE_DIAG_OUTPUT") or (
        Path.home() / "pressbrake_traces"
        / ("native_d6_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    ))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("SMBS native diagnostic:", "PASS" if report["mesh_qualified"] else "FAIL")
    print("Report:", output)
    return report


if __name__ == "__main__":
    main()
