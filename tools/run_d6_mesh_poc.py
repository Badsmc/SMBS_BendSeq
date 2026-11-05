"""Run D6 Mesh PoC *inside* the FreeCAD Python console or FreeCADCmd.

Usage, from the FreeCAD Python console:
    import runpy
    runpy.run_path(r"C:\path\to\SMBS_BendSeq\tools\run_d6_mesh_poc.py")

Optional environment variables:
    SMBS_MESH_POC_SHEET: name of existing solid object in active document
    SMBS_MESH_POC_TOOL: existing solid tool name (optionally DocumentName:ObjectName)
    SMBS_MESH_POC_OUTPUT: destination JSON file

Never changes any FreeCAD document. Uses created in-memory Part boxes as
known regression fixtures; optionally tests two existing real CAD solids.
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

from smbs_core.kinematics2d.mesh_collision_poc import (
    MeshContact, mesh_from_shape, classify_mesh_pair, benchmark_mesh_pair,
)


def shape_stats(shape):
    return {
        "volume_mm3": round(float(shape.Volume), 7),
        "valid": bool(shape.isValid()),
        "closed": bool(shape.isClosed()),
        "bounds": [
            round(float(v), 5) for v in
            (shape.BoundBox.XMin, shape.BoundBox.YMin, shape.BoundBox.ZMin,
             shape.BoundBox.XMax, shape.BoundBox.YMax, shape.BoundBox.ZMax)
        ],
    }


def brep_trial(sheet, tool, repeats=10, epsilon_mm3=1e-5):
    """B-Rep reference, not an instruction to use OCC in production search."""
    times = []
    statuses = []
    try:
        for _ in range(repeats):
            started = perf_counter()
            common = sheet.common(tool)
            elapsed = (perf_counter() - started) * 1000.0
            volume = float(common.Volume)
            times.append(elapsed)
            statuses.append("PENETRATION" if volume > epsilon_mm3 else "NO_POSITIVE_VOLUME")
        return {
            "trials": repeats, "median_ms": round(sorted(times)[len(times)//2], 6),
            "statuses": sorted(set(statuses)),
            "warning": "Zero common volume is NOT proof of positive tool clearance.",
        }
    except Exception as exc:
        return {"error": str(exc)}


def run_case(name, sheet, tool, expected, benchmark_iterations=100,
             expected_volume_mm3=None, relative_volume_tolerance=0.01):
    start = perf_counter()
    sm = mesh_from_shape(sheet)
    tm = mesh_from_shape(tool)
    mesh_build_ms = round((perf_counter() - start) * 1000.0, 5)
    result = classify_mesh_pair(sm, tm)
    measured = result.overlap_volume_mm3
    volume_error = (
        abs(measured - expected_volume_mm3) / expected_volume_mm3
        if expected_volume_mm3 is not None and expected_volume_mm3 > 0
        and measured is not None
        else None
    )
    volume_accepted = (
        True if expected_volume_mm3 is None
        else volume_error is not None and volume_error <= relative_volume_tolerance
    )
    return {
        "case": name,
        "expected": expected,
        "expected_overlap_volume_mm3": expected_volume_mm3,
        "relative_overlap_volume_error": volume_error,
        "max_relative_overlap_error_allowed": relative_volume_tolerance,
        "volume_accuracy_pass": volume_accepted,
        "observed": result.to_dict(),
        "matches_expected": (result.status.value == expected and volume_accepted),
        "sheet": shape_stats(sheet),
        "tool": shape_stats(tool),
        "mesh_build_ms": mesh_build_ms,
        "mesh_benchmark": benchmark_mesh_pair(
            sm, tm, iterations=benchmark_iterations,
        ),
        "brep_reference": brep_trial(sheet, tool, repeats=min(10, benchmark_iterations)),
    }


def get_existing_solid(document, name):
    """Resolve ObjectName in active document or DocumentName:ObjectName."""
    if not name:
        raise ValueError("Missing FreeCAD object name")
    if ":" in name:
        doc_name, obj_name = name.split(":", 1)
        document = App.getDocument(doc_name)
        if document is None:
            raise ValueError("Document is not open: " + doc_name)
    else:
        obj_name = name
    if document is None:
        raise ValueError("No active document for object: " + name)
    obj = document.getObject(obj_name)
    if obj is None:
        raise ValueError("Missing FreeCAD object: " + name)
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull() or not shape.isValid() or not shape.isClosed() or shape.Volume <= 0:
        raise ValueError("Object is not a valid closed positive-volume CAD solid: " + name)
    return shape.copy()


def main():
    # Known facts: 10 x 10 x 10 mm axis-aligned boxes in world coordinates.
    # Touch must be UNVERIFIED, not presented as CLEAR; penetration 2 mm.
    sheet = Part.makeBox(10, 10, 10, App.Vector(0, 0, 0))
    separate = Part.makeBox(10, 10, 10, App.Vector(40, 0, 0))
    penetration = Part.makeBox(10, 10, 10, App.Vector(8, 0, 0))
    touching = Part.makeBox(10, 10, 10, App.Vector(10, 0, 0))
    report = {
        "schema": "smbs-mesh-poc-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "freecad_version": ".".join(map(str, App.Version()[:3])),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "source_branch": "d6-mesh-poc",
        "scope": "Static full-3D solids; no bend or transfer continuous collision detection",
        "cases": [],
        "known_fixture_acceptance": None,
        "real_cad_case": None,
    }
    for case in (
        ("separated_boxes", sheet, separate, MeshContact.CLEAR.value, 1000, None),
        ("overlapping_boxes", sheet, penetration, MeshContact.PENETRATION.value, 100, 200.0),
        ("touching_boxes", sheet, touching, MeshContact.UNVERIFIED.value, 100, None),
    ):
        try:
            result = run_case(*case)
        except Exception as exc:
            result = {"case": case[0], "expected": case[3],
                      "matches_expected": False, "error": str(exc)}
        report["cases"].append(result)
        print("SMBS MESH PoC:", result["case"], result.get("observed", result.get("error")),
              "ACCURACY:", "PASS" if result.get("matches_expected") else "FAIL",
              "REL_VOLUME_ERR:", result.get("relative_overlap_volume_error"))

    report["known_fixture_acceptance"] = (
        "PASS" if all(c.get("matches_expected") for c in report["cases"]) else "FAIL"
    )
    if report["known_fixture_acceptance"] != "PASS":
        print("SMBS MESH PoC WARNING: baseline mesh Boolean is not yet trustworthy.")
        print("Do not enable Mesh Boolean as the production SMBS collision backend.")

    document = App.ActiveDocument
    sheet_name = os.environ.get("SMBS_MESH_POC_SHEET")
    tool_name = os.environ.get("SMBS_MESH_POC_TOOL")
    if sheet_name or tool_name:
        try:
            if not sheet_name or not tool_name:
                raise ValueError("Both SMBS_MESH_POC_SHEET and SMBS_MESH_POC_TOOL are required")
            cad_sheet = get_existing_solid(document, sheet_name)
            cad_tool = get_existing_solid(document, tool_name)
            report["real_cad_case"] = run_case(
                "existing_freecad_solids", cad_sheet, cad_tool,
                "UNVERIFIED", 20,
            )
            # No expected outcome is known for arbitrarily placed real CAD
            # objects; matching an assumed status is not an acceptance gate.
            report["real_cad_case"].pop("matches_expected", None)
            report["real_cad_case"].pop("expected", None)
        except Exception as exc:
            report["real_cad_case"] = {"error": str(exc)}
    else:
        report["real_cad_case"] = {
            "skipped": "Select actual sheet/tool solids with environment object names; "
                       "the preview FoldState panel faces are not CAD solids."
        }

    output = Path(os.environ.get("SMBS_MESH_POC_OUTPUT") or
                  (Path.home() / "pressbrake_traces" /
                   ("mesh_poc_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SMBS MESH PoC report:", str(output))
    return report


if __name__ == "__main__" or __name__ == "<run_path>":
    main()
