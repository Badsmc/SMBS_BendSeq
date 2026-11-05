"""FreeCAD Mesh/MeshPart experimental narrow-phase adapter (D6 Mesh PoC).

No FreeCAD import at module scope: pure-Python CI can verify fail-closed
decisions. Construct real meshes only in FreeCAD using MeshPart.meshFromShape.

This backend is NOT a swept-volume CCD or a production collision oracle.
Exact boundary-only contacts and unreliable booleans remain UNVERIFIED.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from time import perf_counter
from typing import Any, Callable


class MeshContact(str, Enum):
    CLEAR = "CLEAR"
    PENETRATION = "PENETRATION"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class MeshContactResult:
    status: MeshContact
    reason: str
    overlap_volume_mm3: float | None
    elapsed_ms: float
    sheet_facets: int
    tool_facets: int

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "reason": self.reason,
            "overlap_volume_mm3": self.overlap_volume_mm3,
            "elapsed_ms": round(self.elapsed_ms, 5),
            "sheet_facets": self.sheet_facets,
            "tool_facets": self.tool_facets,
        }


def bbox_gap_mm(a: Any, b: Any) -> float | None:
    """Positive separation for disjoint world-coordinate AABBs; 0 if touching.

    Never use overlap of boxes as evidence that solids intersect.
    """
    try:
        vals = [(float(getattr(box, key))) for box in (a, b)
                for key in ("XMin", "XMax", "YMin", "YMax", "ZMin", "ZMax")]
        if any(not isfinite(v) for v in vals):
            return None
        x0, x1, y0, y1, z0, z1, xx0, xx1, yy0, yy1, zz0, zz1 = vals
        if x1 < x0 or y1 < y0 or z1 < z0 or xx1 < xx0 or yy1 < yy0 or zz1 < zz0:
            return None
        dx = max(0.0, x0 - xx1, xx0 - x1)
        dy = max(0.0, y0 - yy1, yy0 - y1)
        dz = max(0.0, z0 - zz1, zz0 - z1)
        return (dx * dx + dy * dy + dz * dz) ** 0.5
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def bbox_overlap_volume_mm3(a: Any, b: Any) -> float | None:
    """Strict upper bound on the volume common to two bounded solids.

    A valid Boolean intersection cannot exceed the intersection of the
    world-space axis-aligned bounds. This guards against nonsensical
    triangulated Boolean output (observed 260 mm3 for 200 mm3 box overlap).
    """
    try:
        lengths = []
        for low, high in (("XMin", "XMax"), ("YMin", "YMax"), ("ZMin", "ZMax")):
            a0, a1 = float(getattr(a, low)), float(getattr(a, high))
            b0, b1 = float(getattr(b, low)), float(getattr(b, high))
            if not all(isfinite(v) for v in (a0, a1, b0, b1)):
                return None
            if a1 < a0 or b1 < b0:
                return None
            lengths.append(max(0.0, min(a1, b1) - max(a0, b0)))
        return lengths[0] * lengths[1] * lengths[2]
    except (AttributeError, ValueError, TypeError, OverflowError):
        return None


def mesh_from_shape(shape: Any, *, linear_deflection_mm: float = 0.1,
                    angular_deflection_rad: float = 0.25) -> Any:
    """One-time tessellation of an existing CAD solid in the FreeCAD process."""
    if (not isfinite(linear_deflection_mm) or linear_deflection_mm <= 0
            or not isfinite(angular_deflection_rad) or angular_deflection_rad <= 0):
        raise ValueError("Positive finite tessellation tolerances required")
    if shape is None or shape.isNull() or not shape.isValid():
        raise ValueError("A valid non-null Part.Shape is required")
    # A face/shell mesh is *not* a closed collision solid.
    if not shape.isClosed() or float(shape.Volume) <= 0:
        raise ValueError("Only closed positive-volume Part.Shape is supported")
    import MeshPart
    result = MeshPart.meshFromShape(
        Shape=shape, LinearDeflection=linear_deflection_mm,
        AngularDeflection=angular_deflection_rad, Relative=False,
    )
    if result is None or int(result.CountFacets) == 0 or not result.isSolid():
        raise ValueError("FreeCAD tessellation did not produce a solid mesh")
    return result


def classify_mesh_pair(sheet: Any, tool: Any,
                       *, min_volume_mm3: float = 1e-5,
                       narrow_phase: Callable[[Any, Any], Any] | None = None,
                       strict_clearance_mm: float = 0.02) -> MeshContactResult:
    """Conservatively classify a static 3D pose.

    AABBs separated by more than strict_clearance_mm are demonstrably CLEAR
    *for this pose only*. Positive intersection volume is PENETRATION.
    Empty/invalid mesh booleans with overlapping bounds cannot prove clearance
    or distinguish boundary touch -> UNVERIFIED, never false CLEAR.
    """
    t0 = perf_counter()
    nf, nt = 0, 0
    def report(status, reason, volume=None):
        return MeshContactResult(status, reason, volume,
                                 (perf_counter() - t0) * 1000.0, nf, nt)
    try:
        if (not isfinite(min_volume_mm3) or min_volume_mm3 < 0
                or not isfinite(strict_clearance_mm) or strict_clearance_mm < 0):
            return report(MeshContact.UNVERIFIED, "invalid_tolerance")
        nf, nt = int(sheet.CountFacets), int(tool.CountFacets)
        if nf <= 0 or nt <= 0 or not sheet.isSolid() or not tool.isSolid():
            return report(MeshContact.UNVERIFIED, "not_closed_solid_mesh")
        if sheet.hasSelfIntersections() or tool.hasSelfIntersections():
            return report(MeshContact.UNVERIFIED, "self_intersecting_input_mesh")
        gap = bbox_gap_mm(sheet.BoundBox, tool.BoundBox)
        if gap is None:
            return report(MeshContact.UNVERIFIED, "invalid_mesh_bbox")
        if gap > strict_clearance_mm:
            return report(MeshContact.CLEAR, "separated_3d_bounding_boxes")
        intersect = narrow_phase or (lambda a, b: a.intersect(b))
        overlap = intersect(sheet, tool)
        if overlap is None or int(overlap.CountFacets) == 0:
            return report(MeshContact.UNVERIFIED, "empty_boolean_near_tools_or_touch")
        volume = float(overlap.Volume)
        if not isfinite(volume) or volume < 0:
            return report(MeshContact.UNVERIFIED, "invalid_overlap_volume")
        bound_volume = bbox_overlap_volume_mm3(sheet.BoundBox, tool.BoundBox)
        if bound_volume is None:
            return report(MeshContact.UNVERIFIED, "invalid_overlap_bounds", volume)
        # True solid intersection is a subset of both AABBs. A result outside
        # their overlap volume is provably inconsistent, even with a coarse
        # triangle mesh. Do not approve a false positive as trusted geometry.
        numerical_slack = max(1e-5, 1e-6 * bound_volume)
        if volume > bound_volume + numerical_slack:
            return report(MeshContact.UNVERIFIED,
                          "impossible_boolean_volume_exceeds_bbox_intersection", volume)
        if volume > min_volume_mm3:
            return report(MeshContact.PENETRATION, "positive_mesh_boolean_volume", volume)
        return report(MeshContact.UNVERIFIED, "touch_or_overlap_below_resolution", volume)
    except Exception as exc:
        return report(MeshContact.UNVERIFIED, "mesh_backend_exception:" + str(exc))


def assess_repeated_checks(checks: list[MeshContactResult], *,
                           expected: MeshContact,
                           expected_volume_mm3: float | None = None,
                           relative_volume_tolerance: float = 0.01) -> dict:
    """Pass known fixtures only when EVERY result is repeatable and accurate.

    A status-only one-shot PASS is not a reliable mesh backend qualification.
    For known positive-volume intersection all repeated positive volumes
    must match the geometric reference within tolerance.
    """
    if not checks or not isinstance(expected, MeshContact):
        raise ValueError("Non-empty checks and expected MeshContact required")
    if not isfinite(relative_volume_tolerance) or relative_volume_tolerance < 0:
        raise ValueError("Invalid volume tolerance")
    if expected_volume_mm3 is not None and (
        not isfinite(expected_volume_mm3) or expected_volume_mm3 <= 0
    ):
        raise ValueError("Expected positive overlap volume required")
    errors = []
    failed = 0
    for check in checks:
        if check.status is not expected:
            failed += 1
            continue
        if expected_volume_mm3 is not None:
            vol = check.overlap_volume_mm3
            if vol is None or not isfinite(vol):
                failed += 1
                continue
            deviation = abs(vol - expected_volume_mm3) / expected_volume_mm3
            errors.append(deviation)
            if deviation > relative_volume_tolerance:
                failed += 1
    return {
        "pass": failed == 0,
        "failed_iterations": failed,
        "total_iterations": len(checks),
        "max_relative_volume_error": max(errors) if errors else None,
        "expected_status": expected.value,
        "expected_volume_mm3": expected_volume_mm3,
    }


def benchmark_mesh_pair(sheet: Any, tool: Any, *, iterations: int = 1000,
                        strict_clearance_mm: float = 0.02,
                        min_volume_mm3: float = 1e-5,
                        expected_status: MeshContact | None = None,
                        expected_volume_mm3: float | None = None,
                        relative_volume_tolerance: float = 0.01) -> dict:
    """Repeat static-pose checks; reports all statuses, never labels it CCD."""
    if not isinstance(iterations, int) or not 1 <= iterations <= 100_000:
        raise ValueError("iterations must be between 1 and 100000")
    checks = []
    started = perf_counter()
    for _ in range(iterations):
        checks.append(classify_mesh_pair(
            sheet, tool, strict_clearance_mm=strict_clearance_mm,
            min_volume_mm3=min_volume_mm3,
        ))
    elapsed = perf_counter() - started
    timings = sorted(c.elapsed_ms for c in checks)
    statuses = {status.value: sum(c.status == status for c in checks)
                for status in MeshContact}
    reasons = dict(sorted(Counter(c.reason for c in checks).items()))
    observed_volumes = [
        round(c.overlap_volume_mm3, 5)
        for c in checks if c.overlap_volume_mm3 is not None
    ]
    volume_histogram = dict(sorted(Counter(observed_volumes).items()))
    qualification = (
        assess_repeated_checks(
            checks, expected=expected_status,
            expected_volume_mm3=expected_volume_mm3,
            relative_volume_tolerance=relative_volume_tolerance,
        ) if expected_status is not None else None
    )
    return {
        "iterations": iterations,
        "total_seconds": round(elapsed, 6),
        "checks_per_second": round(iterations / elapsed, 2) if elapsed > 0 else None,
        "median_check_ms": round(timings[len(timings)//2], 5),
        "p95_check_ms": round(timings[min(len(timings)-1, int(iterations * 0.95))], 5),
        "statuses": statuses,
        "repeatability_acceptance": qualification,
        "reasons": reasons,
        "overlap_volumes_mm3_histogram": {
            str(volume): count for volume, count in volume_histogram.items()
        },
        "observed_min_volume_mm3": min(observed_volumes) if observed_volumes else None,
        "observed_max_volume_mm3": max(observed_volumes) if observed_volumes else None,
        "first_check": checks[0].to_dict(),
        "note": "Static 3D positions only. Not CCD, not proof of a bend sequence.",
    }
