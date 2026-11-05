"""native_collision_3d.py - Native FreeCAD Part / Open CASCADE 3D Collision Detector.

Architecture & Physical Collision Contract:
- Uses native FreeCAD Part.Shape (Open CASCADE BREP) directly.
- Strict physical separation of surface contact vs volumetric penetration.
- NO blanket volume exceptions: arbitrary volume tolerances or "forming bypasses"
  that treat positive volumetric intersections as harmless contact are strictly forbidden.
- Clear classification statuses:
  * CLEAR: Clearance > near_tolerance_mm (e.g. > 0.5 mm). No collision.
  * NEAR_CONTACT: 0.0 < Clearance <= near_tolerance_mm. Approaching contact.
  * TOUCHING: Clearance <= contact_distance_tolerance_mm (e.g. 1e-4 mm) AND
              common overlap volume <= volume_tolerance_mm3 (e.g. 1e-4 mm³ OCC facet boundary noise).
  * PENETRATION: Common overlap volume > volume_tolerance_mm3. Physical clash / interference.
  * INVALID_GEOMETRY: Non-solid, open, null, or zero-volume input.
  * MISSING_EVIDENCE: Tool, workpiece, or required placement evidence is missing (fails closed).
  * INSUFFICIENT_TOOL_LENGTH: Active bend line extends beyond physical tooling coverage.

Contact Policy:
- A pose is only verified safe if there is NO volumetric penetration.
- If forming geometry and punch placement are physically inconsistent, the pose is
  classified as PENETRATION (unsafe), requiring correction of kinematics/placement,
  never arbitrary tolerance relaxation.
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
    coverage_result: Optional[Any] = None,
) -> ToolCollisionReport:
    """Validate 3D collision status of a machine-placed workpiece against punch and die.

    Parameters:
    - workpiece_machine_shape: Full 3D solid of the sheet in Machine Space.
    - punch_machine_shape: 3D solid of the punch in Machine Space.
    - die_machine_shape: 3D solid of the die in Machine Space.
    - coverage_result: Optional ToolCoverageResult verifying tool length sufficiency.

    Contract:
    - Never permits volumetric penetration.
    - Fails closed if any required input or evidence is missing.
    - Fails closed if tool coverage is insufficient.
    """
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

    if coverage_result is not None and not coverage_result.is_covered:
        diags.append(f"INSUFFICIENT_TOOL_LENGTH: {coverage_result.reason}")
        dt = (time.perf_counter() - t0) * 1000.0
        dummy = PairClearanceResult(CollisionClass.INSUFFICIENT_TOOL_LENGTH, "coverage_failed", 0.0, 0.0, None, 0.0)
        return ToolCollisionReport(CollisionClass.INSUFFICIENT_TOOL_LENGTH, dummy, dummy, False, diags, dt)

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

    # Evaluate Overall Status
    # Any PENETRATION is strictly unsafe.
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
