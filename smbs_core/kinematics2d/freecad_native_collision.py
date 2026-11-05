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



@dataclass(frozen=True)
class PreparedSolid:
    """A validated per-state snapshot. Treat `shape` as read-only.

    The Part.Shape copy belongs to the prepared state; do NOT transform it or
    share it with moving geometry. Reprepare after any bend or placement change.
    Cached bbox is scalar data, unaffected by FreeCAD BoundBox wrapper lifetime.
    """
    shape: object
    bounds: object


@dataclass(frozen=True)
class _Bounds:
    XMin: float
    XMax: float
    YMin: float
    YMax: float
    ZMin: float
    ZMax: float


def prepare_solid(shape):
    """Validate and copy geometry once per immutable physical search state.

    Reject missing, invalid, open and zero-volume solids. Never assume that
    callers can mutate a PreparedSolid.shape; changing pose requires preparing
    a fresh state. This is a trust boundary, not an identity-based global cache.
    """
    if shape is None:
        raise ValueError("missing_shape")
    try:
        isolated = shape.copy()
        if (isolated.isNull() or not isolated.isValid() or
                not isolated.isClosed() or float(isolated.Volume) <= 0):
            raise ValueError("missing_or_invalid_solid")
        source_bbox = isolated.BoundBox
        bounds = _Bounds(*(float(getattr(source_bbox, key)) for key in (
            "XMin", "XMax", "YMin", "YMax", "ZMin", "ZMax"
        )))
        if bbox_gap_mm(bounds, bounds) is None:
            raise ValueError("invalid_bbox")
        return PreparedSolid(isolated, bounds)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("prepare_solid_failed:" + str(exc)) from exc


def classify_prepared(a: PreparedSolid, b: PreparedSolid, *,
                      clearance_mm=0.02, volume_tolerance_mm3=1e-5):
    """Fast repeated static check with input validation amortized per state.

    Prepared shapes are *read-only*. Geometry or pose mutation invalidates the
    preparation; never return a prior result for a changed FoldState.
    """
    started = perf_counter()
    def result(status, reason, distance=None, overlap=None):
        return NativeResult(
            status, reason, distance, overlap,
            (perf_counter()-started)*1000,
        )
    if (not isfinite(clearance_mm) or clearance_mm < 0 or
            not isfinite(volume_tolerance_mm3) or volume_tolerance_mm3 < 0):
        return result(NativeStatus.UNVERIFIED, "invalid_tolerance")
    if not isinstance(a, PreparedSolid) or not isinstance(b, PreparedSolid):
        return result(NativeStatus.UNVERIFIED, "unprepared_solid")
    try:
        gap = bbox_gap_mm(a.bounds, b.bounds)
        if gap is None:
            return result(NativeStatus.UNVERIFIED, "invalid_cached_bbox")
        if gap > clearance_mm:
            return result(NativeStatus.CLEAR, "aabb_separated", gap)
        intersection = a.shape.common(b.shape)
        volume = float(intersection.Volume)
        if not isfinite(volume) or volume < 0:
            return result(NativeStatus.UNVERIFIED, "invalid_common_volume")
        if volume > volume_tolerance_mm3:
            return result(NativeStatus.COLLISION, "positive_common_volume", 0.0, volume)
        distance = float(a.shape.distToShape(b.shape)[0])
        if not isfinite(distance) or distance < 0:
            return result(NativeStatus.UNVERIFIED, "invalid_distance", None, volume)
        if distance > clearance_mm:
            return result(NativeStatus.CLEAR, "part_distance_separated", distance, volume)
        return result(
            NativeStatus.UNVERIFIED,
            "near_or_touching_requires_contact_policy", distance, volume
        )
    except Exception as exc:
        return result(NativeStatus.UNVERIFIED, "native_kernel_error:" + str(exc))



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
