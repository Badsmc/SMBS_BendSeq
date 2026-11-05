"""D6 native FreeCAD static-solid collision adapter (no external dependency).

AABB fast reject -> FreeCAD Part.common for true-volume overlap ->
Part.distToShape for clearance. This is a *static* exact-shape verifier
and does not claim to validate swept movement or intentional tooling contact.

Module import is pure Python; real Part.Shape calls occur only in FreeCAD.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from time import perf_counter

from .mesh_collision_poc import bbox_gap_mm


class NativeStatus(str, Enum):
    CLEAR = "CLEAR"
    COLLISION = "COLLISION"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class NativeResult:
    status: NativeStatus
    reason: str
    distance_mm: float | None
    overlap_volume_mm3: float | None
    elapsed_ms: float


def classify_solids(shape_a, shape_b, *,
                    clearance_mm=0.02, volume_tolerance_mm3=1e-5):
    """Verify separation or positive-volume penetration on FreeCAD solids.

    Exactly zero clearance (intentional punch/die support) is UNVERIFIED and
    must be separately approved using an explicit support-contact model.
    """
    start=perf_counter()
    def result(status, reason, distance=None, overlap=None):
        return NativeResult(status, reason, distance, overlap,
                            (perf_counter()-start)*1000)
    if (not isfinite(clearance_mm) or clearance_mm < 0
            or not isfinite(volume_tolerance_mm3) or volume_tolerance_mm3 < 0):
        return result(NativeStatus.UNVERIFIED, "invalid_tolerance")
    try:
        if (shape_a is None or shape_b is None
                or shape_a.isNull() or shape_b.isNull()
                or not shape_a.isValid() or not shape_b.isValid()
                or not shape_a.isClosed() or not shape_b.isClosed()
                or float(shape_a.Volume) <= 0 or float(shape_b.Volume) <= 0):
            return result(NativeStatus.UNVERIFIED, "missing_or_invalid_solid")
        gap = bbox_gap_mm(shape_a.BoundBox, shape_b.BoundBox)
        if gap is None:
            return result(NativeStatus.UNVERIFIED, "invalid_bbox")
        if gap > clearance_mm:
            return result(NativeStatus.CLEAR, "aabb_separated", gap)
        common = shape_a.common(shape_b)
        vol = float(common.Volume)
        if not isfinite(vol) or vol < 0:
            return result(NativeStatus.UNVERIFIED, "invalid_common_volume")
        if vol > volume_tolerance_mm3:
            return result(NativeStatus.COLLISION, "positive_common_volume", 0.0, vol)
        dist = float(shape_a.distToShape(shape_b)[0])
        if not isfinite(dist) or dist < 0:
            return result(NativeStatus.UNVERIFIED, "invalid_distance", None, vol)
        if dist > clearance_mm:
            return result(NativeStatus.CLEAR, "part_distance_separated", dist, vol)
        return result(NativeStatus.UNVERIFIED, "near_or_touching_requires_contact_policy", dist, vol)
    except Exception as exc:
        return result(NativeStatus.UNVERIFIED, "native_kernel_error:"+str(exc))
