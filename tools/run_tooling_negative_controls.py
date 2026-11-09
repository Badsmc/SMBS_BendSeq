"""Read-only negative-control checks against the actual SMBS tool catalog.

Synthetic sheet/obstacle shapes use exact *selected tool profile outlines*
as deterministic overlap witnesses. These are diagnostic geometry controls,
NOT proof that an actual FreeCAD part is extractable from the press brake.
Does not modify the FreeCAD document or tool library.
"""
from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter

from smbs_core.kinematics2d.contact_oracle_2d import (
    ContactStatus, classify_polygon_contact,
)
from smbs_core.kinematics2d.tooling_25d import (
    ToolKind, ToolLibrary25D, ToolingSetup2D,
)

DEFAULT_PUNCH = "PUNCH_AP_1017_88°_H104,65_R0,8"
DEFAULT_DIE = "T-DIE_AT_3015_88°_V12_H80"
TOLERANCE_MM = 0.02


def _translate(poly, dx: float, dz: float = 0.0):
    return tuple((float(x) + dx, float(z) + dz) for x, z in poly)


def _width(poly):
    xs = tuple(p[0] for p in poly)
    return max(xs) - min(xs)


def _case(name, expected, sheet, outer, holes=()):
    actual, reason = classify_polygon_contact(
        sheet, outer, holes, tolerance_mm=TOLERANCE_MM,
    )
    return {
        "name": name,
        "expected": expected.value,
        "actual": actual.value,
        "reason": reason,
        "passed": actual is expected,
    }


def run_negative_controls(
    *,
    punch_id: str = DEFAULT_PUNCH,
    die_id: str = DEFAULT_DIE,
    output_path: str | None = None,
) -> dict:
    """Check free space, overlap, and withdrawal-crossing on real catalog tool profiles.

    The synthetic moved shape is a copy of the REAL punch/die 2D outline.
    Overlap and clearance are exact positive/negative controls for the
    classifier. The withdrawal example is only a sampled 2D obstruction;
    it does not test actual part transfer or certify a 3D blocked path.
    """
    started = perf_counter()
    library = ToolLibrary25D()
    setup = ToolingSetup2D()
    setup.select_punch(library.load_tool(punch_id, ToolKind.PUNCH))
    setup.select_die(library.load_tool(die_id, ToolKind.DIE))
    # Test machine-space contours using an explicit machine-frame setup.
    # No implicit placement against a model; origin is the catalog datum.
    punch = setup.punch_polygons_machine(arc_segments=24)
    die = setup.die_polygons_machine(arc_segments=24)
    if punch is None or die is None:
        raise RuntimeError("Both tool geometries are required")
    cases = []
    for kind, geometry in (("PUNCH", punch), ("DIE", die)):
        outer, holes = geometry
        if len(outer) < 3:
            raise RuntimeError(kind + ": invalid or missing tool contour")
        shift = 2.0 * _width(outer) + 50.0
        cases.append(_case(
            kind + ": clearance from real profile", ContactStatus.CLEAR,
            _translate(outer, shift), outer, holes,
        ))
        cases.append(_case(
            kind + ": exact-material overlap", ContactStatus.INTERFERENCE,
            outer, outer, holes,
        ))
    # Two individually clear endpoints can have a colliding intermediate
    # translation: an endpoint-only transfer check would be unsafe.
    outer, holes = punch
    shift = 2.0 * _width(outer) + 50.0
    samples = []
    for index in range(21):
        t = index / 20.0
        offset = shift * (1.0 - 2.0 * t)
        result = _case(
            "TRANSFER_SAMPLE_%02d" % index, ContactStatus.CLEAR,
            _translate(outer, offset), outer, holes,
        )
        samples.append({
            "fraction": t,
            "offset_mm": offset,
            "status": result["actual"],
            "reason": result["reason"],
        })
    clear_endpoints = (
        samples[0]["status"] == ContactStatus.CLEAR.value
        and samples[-1]["status"] == ContactStatus.CLEAR.value
    )
    observed_collision = any(
        sample["status"] == ContactStatus.INTERFERENCE.value
        for sample in samples[1:-1]
    )
    transfer_control = {
        "name": "SAMPLED_2D_TRANSFER_BLOCKED_BETWEEN_CLEAR_ENDPOINTS",
        "expected": "CLEAR endpoints and INTERFERENCE along sweep",
        "actual": (
            "CLEAR endpoints and INTERFERENCE along sweep"
            if clear_endpoints and observed_collision else
            "control did not detect the required intermediate obstruction"
        ),
        "passed": clear_endpoints and observed_collision,
        "samples": samples,
        "scope": "synthetic 2D profile translation, NOT real Z part extraction",
    }
    cases.append({k: v for k, v in transfer_control.items() if k != "samples"})
    payload = {
        "test_suite": "SMBS_TOOL_LIBRARY_NEGATIVE_CONTROLS",
        "punch": punch_id,
        "die": die_id,
        "tolerance_mm": TOLERANCE_MM,
        "source_geometry": "actual tool-library machine-space polygons",
        "test_geometry": "synthetic translated copies of selected tool outlines",
        "scope": "discrete planar contact oracle, not physical 3D machine validation",
        "cases": cases,
        "transfer_control": transfer_control,
        "passed": sum(c["passed"] for c in cases),
        "total": len(cases),
        "success": all(c["passed"] for c in cases),
        "elapsed_s": perf_counter() - started,
    }
    if output_path is not None:
        dest = Path(output_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return payload


def print_negative_controls(payload: dict):
    print("SMBS NEGATIVE CONTROLS:", payload["passed"], "/", payload["total"])
    for case in payload["cases"]:
        print(" ", "PASS" if case["passed"] else "FAIL", case["name"],
              "|", case["actual"])
    print("TIME:", round(payload["elapsed_s"], 3), "s")
    print("LIMIT: Synthetic 2D controls only; actual part transfer NOT checked")


if __name__ == "__main__":
    report = run_negative_controls()
    print_negative_controls(report)
    if not report["success"]:
        raise SystemExit(1)
