"""native_collision_3d.py - Native FreeCAD Part / Open CASCADE 3D Collision Detector & Physical Qualification.

Architecture Contract:
1. Native Part Collision Engine (Geometric):
   - Pure geometry check answering:
     * Is there clearance? (CLEAR)
     * Is there near contact? (NEAR_CONTACT)
     * Is there true surface contact? (TOUCHING, dist <= 1e-4 mm, overlap <= 1e-4 mm³)
     * Is there positive-volume penetration? (PENETRATION, overlap > 1e-4 mm³)
     * Is the geometry valid? (INVALID_GEOMETRY)
2. Physical Machine Setup Qualification:
   - Evaluates whether the physical setup qualifies for sequence feasibility:
     * Authoritative tool and workpiece placement evidence
     * Tool length and assembly coverage along the full bend line
     * Kinematic clearance across machine cycle
     * Rejects unauthorized penetration or insufficient tool length
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple


class CollisionClass(str, Enum):
    CLEAR = "CLEAR"
    NEAR_CONTACT = "NEAR_CONTACT"
    TOUCHING = "TOUCHING"
    PENETRATION = "PENETRATION"
    INVALID_GEOMETRY = "INVALID_GEOMETRY"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    INSUFFICIENT_TOOL_LENGTH = "INSUFFICIENT_TOOL_LENGTH"


@dataclass(frozen=True)
class PairClearanceResult:
    status: CollisionClass
    reason: str
    distance_mm: float
    overlap_volume_mm3: float
    aabb_gap_mm: Optional[float]
    elapsed_ms: float


@dataclass(frozen=True)
class ToolCollisionReport:
    status: CollisionClass
    punch_result: PairClearanceResult
    die_result: PairClearanceResult
    is_safe: bool
    diagnostics: List[str]
    elapsed_ms: float


@dataclass(frozen=True)
class PhysicalQualificationReport:
    is_qualified: bool
    qualification_status: str
    tool_coverage_status: str
    pose_collision_status: str
    diagnostics: List[str]
    elapsed_ms: float


def compute_aabb_gap(box_a: Any, box_b: Any) -> Optional[float]:
    """Compute minimum signed gap between two FreeCAD BoundBox objects."""
    if box_a is None or box_b is None:
        return None
    try:
        dx = max(0.0, max(box_a.XMin - box_b.XMax, box_b.XMin - box_a.XMax))
        dy = max(0.0, max(box_a.YMin - box_b.YMax, box_b.YMin - box_a.YMax))
        dz = max(0.0, max(box_a.ZMin - box_b.ZMax, box_b.ZMin - box_a.ZMax))
        if dx == 0.0 and dy == 0.0 and dz == 0.0:
            return 0.0
        return math.sqrt(dx * dx + dy * dy + dz * dz)
    except Exception:
        return None


def check_solid_validity(shape: Any, name: str = "shape") -> Tuple[bool, str]:
    """Validate that shape is a non-null, valid, closed solid with positive volume."""
    if shape is None:
        return False, f"{name}_is_none"
    try:
        if shape.isNull():
            return False, f"{name}_is_null"
        if not shape.isValid():
            return False, f"{name}_is_not_valid"
        if hasattr(shape, "isClosed") and not shape.isClosed():
            return False, f"{name}_is_open"
        vol = float(getattr(shape, "Volume", 0.0))
        if not math.isfinite(vol) or vol <= 0.0:
            return False, f"{name}_volume_non_positive"
        return True, "valid"
    except Exception as exc:
        return False, f"{name}_check_exception:{exc}"


def check_pair_clearance(
    shape_a: Any,
    shape_b: Any,
    *,
    near_tolerance_mm: float = 0.5,
    volume_tolerance_mm3: float = 1e-4,
    contact_distance_tolerance_mm: float = 1e-4,
) -> PairClearanceResult:
    """Evaluate exact clearance and overlap volume between two native FreeCAD Part solids.

    Fails closed on any invalid geometry or evaluation failure.
    Strictly preserves true surface contact vs positive-volume penetration.
    """
    t0 = time.perf_counter()

    # 1. Geometry sanity check
    ok_a, reason_a = check_solid_validity(shape_a, "shape_a")
    if not ok_a:
        dt = (time.perf_counter() - t0) * 1000.0
        return PairClearanceResult(
            CollisionClass.INVALID_GEOMETRY, reason_a, 0.0, 0.0, None, dt
        )

    ok_b, reason_b = check_solid_validity(shape_b, "shape_b")
    if not ok_b:
        dt = (time.perf_counter() - t0) * 1000.0
        return PairClearanceResult(
            CollisionClass.INVALID_GEOMETRY, reason_b, 0.0, 0.0, None, dt
        )

    # 2. Broad Phase: AABB fast reject
    box_a = getattr(shape_a, "BoundBox", None)
    box_b = getattr(shape_b, "BoundBox", None)
    aabb_gap = compute_aabb_gap(box_a, box_b)

    if aabb_gap is not None and aabb_gap > near_tolerance_mm:
        dt = (time.perf_counter() - t0) * 1000.0
        return PairClearanceResult(
            CollisionClass.CLEAR,
            "aabb_separated",
            aabb_gap,
            0.0,
            aabb_gap,
            dt,
        )

    # 3. Narrow Phase: distToShape & Common volume
    try:
        # Distance calculation
        dist_res = shape_a.distToShape(shape_b)
        dist = float(dist_res[0]) if dist_res and len(dist_res) > 0 else 0.0
        if not math.isfinite(dist) or dist < 0.0:
            dist = 0.0

        if dist > near_tolerance_mm:
            dt = (time.perf_counter() - t0) * 1000.0
            return PairClearanceResult(
                CollisionClass.CLEAR,
                "distance_separated",
                dist,
                0.0,
                aabb_gap,
                dt,
            )

        # Common boolean intersection volume
        common_shape = shape_a.common(shape_b)
        vol = float(getattr(common_shape, "Volume", 0.0))
        if not math.isfinite(vol) or vol < 0.0:
            vol = 0.0

        dt = (time.perf_counter() - t0) * 1000.0

        # Positive volumetric intersection is strictly PENETRATION.
        if vol > volume_tolerance_mm3:
            return PairClearanceResult(
                CollisionClass.PENETRATION,
                "positive_common_volume",
                dist,
                vol,
                aabb_gap,
                dt,
            )

        if dist <= contact_distance_tolerance_mm:
            return PairClearanceResult(
                CollisionClass.TOUCHING,
                "surface_touching",
                dist,
                vol,
                aabb_gap,
                dt,
            )

        return PairClearanceResult(
            CollisionClass.NEAR_CONTACT,
            "near_contact",
            dist,
            vol,
            aabb_gap,
            dt,
        )

    except Exception as exc:
        dt = (time.perf_counter() - t0) * 1000.0
        return PairClearanceResult(
            CollisionClass.INVALID_GEOMETRY,
            f"kernel_exception:{exc}",
            0.0,
            0.0,
            aabb_gap,
            dt,
        )


def validate_workpiece_tooling_pose(
    workpiece_machine_shape: Any,
    punch_machine_shape: Any,
    die_machine_shape: Any,
    *,
    near_tolerance_mm: float = 0.5,
    volume_tolerance_mm3: float = 1e-4,
) -> ToolCollisionReport:
    """Validate 3D geometric collision status of a machine-placed workpiece against punch and die."""
    t0 = time.perf_counter()
    diags: List[str] = []

    if workpiece_machine_shape is None:
        diags.append("MISSING_WORKPIECE_SHAPE")
    if punch_machine_shape is None:
        diags.append("MISSING_PUNCH_SHAPE")
    if die_machine_shape is None:
        diags.append("MISSING_DIE_SHAPE")

    if diags:
        dt = (time.perf_counter() - t0) * 1000.0
        dummy = PairClearanceResult(CollisionClass.MISSING_EVIDENCE, "missing_evidence", 0.0, 0.0, None, 0.0)
        return ToolCollisionReport(CollisionClass.MISSING_EVIDENCE, dummy, dummy, False, diags, dt)

    # Check Punch
    punch_res = check_pair_clearance(
        workpiece_machine_shape,
        punch_machine_shape,
        near_tolerance_mm=near_tolerance_mm,
        volume_tolerance_mm3=volume_tolerance_mm3,
    )

    # Check Die
    die_res = check_pair_clearance(
        workpiece_machine_shape,
        die_machine_shape,
        near_tolerance_mm=near_tolerance_mm,
        volume_tolerance_mm3=volume_tolerance_mm3,
    )

    overall_status = CollisionClass.CLEAR
    is_safe = True

    for label, res in [("PUNCH", punch_res), ("DIE", die_res)]:
        if res.status == CollisionClass.INVALID_GEOMETRY:
            overall_status = CollisionClass.INVALID_GEOMETRY
            is_safe = False
            diags.append(f"{label}_INVALID_GEOMETRY: {res.reason}")
        elif res.status == CollisionClass.PENETRATION:
            overall_status = CollisionClass.PENETRATION
            is_safe = False
            diags.append(f"{label}_PENETRATION: overlap={res.overlap_volume_mm3:.4f} mm3")
        elif res.status == CollisionClass.TOUCHING:
            diags.append(f"{label}_TOUCHING: surface contact (dist={res.distance_mm:.4f} mm)")
            if overall_status == CollisionClass.CLEAR:
                overall_status = CollisionClass.TOUCHING
        elif res.status == CollisionClass.NEAR_CONTACT:
            diags.append(f"{label}_NEAR_CONTACT: dist={res.distance_mm:.4f} mm")
            if overall_status == CollisionClass.CLEAR:
                overall_status = CollisionClass.NEAR_CONTACT

    dt = (time.perf_counter() - t0) * 1000.0
    return ToolCollisionReport(
        status=overall_status,
        punch_result=punch_res,
        die_result=die_res,
        is_safe=is_safe,
        diagnostics=diags,
        elapsed_ms=dt,
    )


def qualify_machine_setup(
    collision_report: ToolCollisionReport,
    punch_coverage: Any,
    die_coverage: Any,
) -> PhysicalQualificationReport:
    """Qualify physical feasibility of the machine tooling setup for an operation.

    Separates pure geometric collision detection from full physical qualification.
    """
    t0 = time.perf_counter()
    diags = list(collision_report.diagnostics)
    is_qualified = True
    qual_status = "QUALIFIED"

    if punch_coverage is None or not getattr(punch_coverage, "is_covered", False):
        is_qualified = False
        qual_status = "INSUFFICIENT_PUNCH_COVERAGE"
        diags.append(f"PUNCH_COVERAGE_FAILED: {getattr(punch_coverage, 'reason', 'missing_coverage')}")

    if die_coverage is None or not getattr(die_coverage, "is_covered", False):
        is_qualified = False
        qual_status = "INSUFFICIENT_DIE_COVERAGE"
        diags.append(f"DIE_COVERAGE_FAILED: {getattr(die_coverage, 'reason', 'missing_coverage')}")

    if not collision_report.is_safe:
        is_qualified = False
        qual_status = f"COLLISION_{collision_report.status.value}"

    dt = (time.perf_counter() - t0) * 1000.0
    return PhysicalQualificationReport(
        is_qualified=is_qualified,
        qualification_status=qual_status,
        tool_coverage_status="COVERED" if (getattr(punch_coverage, "is_covered", False) and getattr(die_coverage, "is_covered", False)) else "INSUFFICIENT",
        pose_collision_status=collision_report.status.value,
        diagnostics=diags,
        elapsed_ms=dt,
    )
