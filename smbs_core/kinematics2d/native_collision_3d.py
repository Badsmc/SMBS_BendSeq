"""native_collision_3d.py - Native FreeCAD Part / Open CASCADE 3D Collision Detector & Physical Qualification.

Architecture Contract:
1. Native Part Collision Engine (Geometric):
   - Pure geometry check answering:
     * Is there clearance? (CLEAR)
     * Is there near contact? (NEAR_CONTACT)
     * Is there true surface contact? (TOUCHING, dist <= 1e-4 mm, overlap <= 1e-4 mm³)
     * Is there positive-volume penetration? (PENETRATION, overlap > 1e-4 mm³)
     * Is the geometry valid? (INVALID_GEOMETRY)
2. Tool Setup Qualification:
   - Evaluates whether the physical tooling setup meets requirements:
     * TOOL_SETUP_QUALIFIED: Full coverage, no assembly gaps.
     * INSUFFICIENT_PUNCH_COVERAGE / INSUFFICIENT_DIE_COVERAGE / TOOL_ASSEMBLY_GAP.
3. Operation Sampled-Poses Qualification:
   - Aggregates all sampled kinematic cycle phases using strict logical AND:
     * SAMPLED_POSES_QUALIFIED: Every tested phase is safe (CLEAR, NEAR_CONTACT, TOUCHING) AND tool setup is qualified.
     * SAMPLED_POSES_REJECTED: One or more phases has PENETRATION or tool setup is disqualified.
     * UNVERIFIED_TRAJECTORY: Discrete sampled poses verified, continuous swept trajectory remains unverified.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .native_kernel_metrics import measured_common, measured_distance
from .native_geometry_build_metrics import measure_native_geometry_region


try:
    import Part
except ImportError:
    Part = None


class CollisionClass(str, Enum):
    CLEAR = "CLEAR"
    NEAR_CONTACT = "NEAR_CONTACT"
    TOUCHING = "TOUCHING"
    PENETRATION = "PENETRATION"
    INVALID_GEOMETRY = "INVALID_GEOMETRY"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    INSUFFICIENT_TOOL_LENGTH = "INSUFFICIENT_TOOL_LENGTH"


class PunchCollisionZoneClassification(str, Enum):
    NONE = "NONE"
    END_ZONE_ONLY = "END_ZONE_ONLY"
    CENTRAL_ZONE = "CENTRAL_ZONE"
    MIXED_CENTRAL_AND_END_ZONE = "MIXED_CENTRAL_AND_END_ZONE"
    UNCLASSIFIED = "UNCLASSIFIED"


@dataclass(frozen=True)
class PunchOverlapComponentInfo:
    component_index: int
    volume_mm3: float
    bbox_xmin_mm: float
    bbox_xmax_mm: float
    bbox_ymin_mm: float
    bbox_ymax_mm: float
    bbox_zmin_mm: float
    bbox_zmax_mm: float
    zone: str  # "LEFT_END_ZONE", "RIGHT_END_ZONE", "CENTRAL_ZONE", "MIXED", "UNCLASSIFIED"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "component_index": self.component_index,
            "volume_mm3": round(float(self.volume_mm3), 6),
            "bbox_xmin_mm": round(float(self.bbox_xmin_mm), 6),
            "bbox_xmax_mm": round(float(self.bbox_xmax_mm), 6),
            "bbox_ymin_mm": round(float(self.bbox_ymin_mm), 6),
            "bbox_ymax_mm": round(float(self.bbox_ymax_mm), 6),
            "bbox_zmin_mm": round(float(self.bbox_zmin_mm), 6),
            "bbox_zmax_mm": round(float(self.bbox_zmax_mm), 6),
            "zone": self.zone,
        }


@dataclass(frozen=True)
class PunchEndZoneAnalysis:
    classification: PunchCollisionZoneClassification
    end_zone_length_mm: float
    punch_span_xm: Tuple[float, float]
    left_end_zone_xm: Tuple[float, float]
    central_zone_xm: Tuple[float, float]
    right_end_zone_xm: Tuple[float, float]
    total_overlap_volume_mm3: float
    end_zone_overlap_volume_mm3: float
    central_zone_overlap_volume_mm3: float
    affected_sides: Tuple[str, ...]
    components: Tuple[PunchOverlapComponentInfo, ...]
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "classification": self.classification.value,
            "end_zone_length_mm": round(float(self.end_zone_length_mm), 6),
            "punch_span_xm_mm": [round(float(self.punch_span_xm[0]), 6), round(float(self.punch_span_xm[1]), 6)],
            "left_end_zone_xm_mm": [round(float(self.left_end_zone_xm[0]), 6), round(float(self.left_end_zone_xm[1]), 6)],
            "central_zone_xm_mm": [round(float(self.central_zone_xm[0]), 6), round(float(self.central_zone_xm[1]), 6)],
            "right_end_zone_xm_mm": [round(float(self.right_end_zone_xm[0]), 6), round(float(self.right_end_zone_xm[1]), 6)],
            "total_overlap_volume_mm3": round(float(self.total_overlap_volume_mm3), 6),
            "end_zone_overlap_volume_mm3": round(float(self.end_zone_overlap_volume_mm3), 6),
            "central_zone_overlap_volume_mm3": round(float(self.central_zone_overlap_volume_mm3), 6),
            "affected_sides": list(self.affected_sides),
            "components": [c.to_dict() for c in self.components],
            "reason": self.reason,
        }


@dataclass(frozen=True)
class EndZoneCollisionPolicy:
    allow_end_zone_collision: bool = False
    end_zone_length_mm: float = 0.0

    def signature(self) -> Tuple[bool, float]:
        val = float(self.end_zone_length_mm) if math.isfinite(float(self.end_zone_length_mm)) else -1.0
        return (bool(self.allow_end_zone_collision), round(val, 6))


class SetupQualificationStatus(str, Enum):
    TOOL_SETUP_QUALIFIED = "TOOL_SETUP_QUALIFIED"
    INSUFFICIENT_PUNCH_COVERAGE = "INSUFFICIENT_PUNCH_COVERAGE"
    INSUFFICIENT_DIE_COVERAGE = "INSUFFICIENT_DIE_COVERAGE"
    TOOL_ASSEMBLY_GAP = "TOOL_ASSEMBLY_GAP"
    MISSING_TOOLING_EVIDENCE = "MISSING_TOOLING_EVIDENCE"


class OperationQualificationStatus(str, Enum):
    SAMPLED_POSES_QUALIFIED = "SAMPLED_POSES_QUALIFIED"
    SAMPLED_POSES_REJECTED = "SAMPLED_POSES_REJECTED"
    MISSING_PHASE_EVIDENCE = "MISSING_PHASE_EVIDENCE"


@dataclass(frozen=True)
class PairClearanceResult:
    status: CollisionClass
    reason: str
    distance_mm: float
    overlap_volume_mm3: float
    aabb_gap_mm: Optional[float]
    elapsed_ms: float
    is_lower_bound: bool = False
    end_zone_analysis: Optional[PunchEndZoneAnalysis] = None
    distance_queries_count: int = 0
    common_queries_count: int = 0
    broad_phase_time_ms: float = 0.0
    distance_time_ms: float = 0.0
    common_time_ms: float = 0.0
    tool_label: str = ""
    # Actual OCC intersection solid bbox in MACHINE X/Y/Z, not merely the
    # overlapping bounding boxes of the original solids. None if absent.
    overlap_bbox_xyz_mm: Optional[Dict[str, float]] = None


@dataclass(frozen=True)
class ToolCollisionReport:
    status: CollisionClass
    punch_result: PairClearanceResult
    die_result: PairClearanceResult
    is_safe: bool
    diagnostics: List[str]
    elapsed_ms: float
    is_conditional_safe: bool = False
    conditional_status: Optional[str] = None
    punch_end_zone_analysis: Optional[PunchEndZoneAnalysis] = None
    punch_cache_hit: bool = False
    die_cache_hit: bool = False


@dataclass(frozen=True)
class PhaseQualificationReport:
    phase_name: str
    bend_angle_deg: float
    ram_z_mm: float
    support_plane_zm: float
    collision_report: ToolCollisionReport
    is_phase_safe: bool


@dataclass(frozen=True)
class OperationQualificationReport:
    is_qualified: bool
    operation_status: OperationQualificationStatus
    tool_setup_status: SetupQualificationStatus
    trajectory_status: str  # Always "UNVERIFIED_TRAJECTORY" for discrete pose testing
    total_phases_tested: int
    safe_phases_count: int
    rejected_phases: List[str]
    phase_reports: List[PhaseQualificationReport]
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


def _evaluate_common_volume(
    shape_a: Any,
    shape_b: Any,
    *,
    include_bbox: bool = False,
    include_shape: bool = False,
):
    """Compute exact intersection volume between shape_a and shape_b.

    Strictly differentiates between a valid empty intersection (0.0 volume)
    and kernel failure / invalid geometry.
    Raises ValueError with a descriptive code on missing Volume, non-finite (NaN/inf),
    negative volume, or kernel evaluation error so the caller can fail closed with
    INVALID_GEOMETRY rather than falsifying CLEAR or TOUCHING.
    """
    if include_shape and not include_bbox:
        raise ValueError("include_shape_requires_include_bbox")
    try:
        common_shape = measured_common(shape_a, shape_b, label="pair_common")
    except Exception as exc:
        raise ValueError(f"KERNEL_COMMON_EVALUATION_FAILED:{exc}") from exc

    if common_shape is None:
        raise ValueError("KERNEL_COMMON_SHAPE_IS_NONE")

    # In OCC, if intersection is completely empty, it may be a null shape or an empty compound
    if hasattr(common_shape, "isNull") and common_shape.isNull():
        # Valid empty intersection shape
        return (
            (0.0, None, common_shape) if include_shape else (0.0, None)
        ) if include_bbox else 0.0

    if hasattr(common_shape, "isValid") and not common_shape.isValid():
        raise ValueError("KERNEL_COMMON_SHAPE_INVALID")

    if not hasattr(common_shape, "Volume"):
        raise ValueError("KERNEL_COMMON_MISSING_VOLUME_ATTR")

    try:
        vol = float(common_shape.Volume)
    except Exception as exc:
        raise ValueError(f"KERNEL_COMMON_VOLUME_READ_ERROR:{exc}") from exc

    if not math.isfinite(vol):
        raise ValueError(f"KERNEL_COMMON_NON_FINITE_VOLUME:{vol}")

    if vol < 0.0:
        raise ValueError(f"KERNEL_COMMON_NEGATIVE_VOLUME:{vol}")

    if not include_bbox:
        return vol

    def with_bbox(value, bound):
        # Kept exclusively inside this single clearance evaluation. Never
        # store a potentially huge Part.Shape in persistent PairClearanceResult
        # or the trajectory's multi-state cache.
        return (value, bound, common_shape) if include_shape else (value, bound)

    # Capture the EXACT common-solid location for developer diagnostics at
    # zero extra Common/OCC booleans. If the kernel cannot provide its bbox,
    # preserve the collision verdict but explicitly expose no location.
    bb = getattr(common_shape, "BoundBox", None)
    if bb is None or vol <= 0.0:
        return with_bbox(vol, None)
    try:
        coords = {
            "xmin": float(bb.XMin), "xmax": float(bb.XMax),
            "ymin": float(bb.YMin), "ymax": float(bb.YMax),
            "zmin": float(bb.ZMin), "zmax": float(bb.ZMax),
        }
        if not all(math.isfinite(v) for v in coords.values()):
            return with_bbox(vol, None)
        if (coords["xmin"] > coords["xmax"]
                or coords["ymin"] > coords["ymax"]
                or coords["zmin"] > coords["zmax"]):
            return with_bbox(vol, None)
    except (AttributeError, TypeError, ValueError):
        return with_bbox(vol, None)
    return with_bbox(vol, coords)


def _compute_exact_distance_with_pruning(
    shape_a: Any,
    shape_b: Any,
    box_a: Any = None,
    box_b: Any = None,
) -> float:
    """Compute exact Euclidean distance between two native FreeCAD Part solids with conservative face pruning.

    Mathematical Proof of Exactness for Boundary Distances:
    For any face f of shape S_many, the Euclidean distance dist(f, S_other) is bounded below by
    the distance between their axis-aligned bounding boxes:
        dist(f, S_other) >= compute_aabb_gap(f.BoundBox, S_other.BoundBox).
    Let g_min = min_{f in S_many.Faces} compute_aabb_gap(f.BoundBox, S_other.BoundBox).
    For any chosen margin >= 5.0 mm (e.g. margin = max(5.0, g_min + 5.0)),
    any excluded face f_ex satisfies:
        dist(f_ex, S_other) >= compute_aabb_gap(f_ex.BoundBox, S_other.BoundBox) > margin.
    If the compound of candidate faces C = Compound({f | gap(f, S_other) <= margin}) yields:
        d = dist(C, S_other) <= margin,
    then no excluded face can possibly have a distance smaller than d (since dist(f_ex) > margin >= d).
    Therefore, d is mathematically guaranteed to be the exact global minimum distance from
    the boundary faces of S_many to S_other.
    If d > margin or any exception occurs, it falls back to full OCC shape_a.distToShape(shape_b).

    Note on Volume Enclosure:
    Boundary distance alone does not rule out total volume enclosure (e.g. a small solid floating
    wholly inside a large solid without touching its boundary). Volume enclosure is verified in
    check_pair_clearance before returning CLEAR.
    """
    try:
        faces_a = getattr(shape_a, "Faces", ())
        faces_b = getattr(shape_b, "Faces", ())
        len_a = len(faces_a) if faces_a else 0
        len_b = len(faces_b) if faces_b else 0

        # Choose the shape with more faces to prune, provided it has > 12 faces
        if len_a >= len_b and len_a > 12 and box_b is not None and Part is not None:
            s_many, s_other, b_other, faces_many = shape_a, shape_b, box_b, faces_a
        elif len_b > len_a and len_b > 12 and box_a is not None and Part is not None:
            s_many, s_other, b_other, faces_many = shape_b, shape_a, box_a, faces_b
        else:
            res = measured_distance(shape_a, shape_b, label="full_shape_distance")
            if not res or len(res) == 0:
                raise ValueError("KERNEL_EMPTY_DISTANCE_RESULT")
            d = float(res[0])
            if not math.isfinite(d) or d < 0.0:
                raise ValueError(f"KERNEL_NON_FINITE_DISTANCE:{d}")
            return d

        face_gaps = []
        min_g = 1e9
        for f in faces_many:
            fb = getattr(f, "BoundBox", None)
            g = compute_aabb_gap(fb, b_other)
            if g is None:
                g = 0.0
            face_gaps.append((f, g))
            if g < min_g:
                min_g = g

        margin = max(5.0, min_g + 5.0)
        cand_faces = [f for f, g in face_gaps if g <= margin]

        if cand_faces and len(cand_faces) < len(faces_many):
            comp = Part.Compound(cand_faces)
            res = measured_distance(comp, s_other, label="pruned_faces_distance")
            if not res or len(res) == 0:
                raise ValueError("KERNEL_EMPTY_DISTANCE_RESULT")
            d = float(res[0])
            if not math.isfinite(d) or d < 0.0:
                raise ValueError(f"KERNEL_NON_FINITE_DISTANCE:{d}")
            if d <= margin:
                return d
    except Exception as exc:
        if isinstance(exc, ValueError) and "KERNEL_" in str(exc):
            raise

    res = measured_distance(shape_a, shape_b, label="full_shape_distance")
    if not res or len(res) == 0:
        raise ValueError("KERNEL_EMPTY_DISTANCE_RESULT")
    d = float(res[0])
    if not math.isfinite(d) or d < 0.0:
        raise ValueError(f"KERNEL_NON_FINITE_DISTANCE:{d}")
    return d


def check_pair_clearance(
    shape_a: Any,
    shape_b: Any,
    *,
    near_tolerance_mm: float = 0.5,
    volume_tolerance_mm3: float = 1e-4,
    contact_distance_tolerance_mm: float = 1e-4,
    tool_label: str = "",
    classify_end_zone_length_mm: Optional[float] = None,
    prefer_overlap_first: bool = False,
    _shared_shape_a_check: Optional[Tuple[Any, Tuple[bool, str]]] = None,
) -> PairClearanceResult:
    """Evaluate exact clearance and overlap volume between two native FreeCAD Part solids.

    Fails closed on any invalid geometry or evaluation failure.
    Strictly preserves true surface contact vs positive-volume penetration.
    Guarantees that boundary distance measurements never miss internal volume enclosure.

    When prefer_overlap_first=True, performs the SAME exact OCC Common first
    (only for overlapping AABBs), and may skip costly distToShape ONLY if the
    Common has already confirmed a positive-volume penetration. Otherwise
    falls back to the original exact distance classification; no contact or
    unknown is silently treated as clear. Intended only as a performance
    hint from a previous sampled penetrating punch state.
    """
    t0 = time.perf_counter()

    # 1. Geometry sanity check
    # The two tools of the SAME pose see the exact same workpiece object.
    # Reuse its completed validity result only for this object identity;
    # unknown/mismatched evidence must invoke the original strict check.
    shared_is_current = (
        _shared_shape_a_check is not None
        and isinstance(_shared_shape_a_check, tuple)
        and len(_shared_shape_a_check) == 2
        and _shared_shape_a_check[0] is shape_a
    )
    if shared_is_current:
        ok_a, reason_a = _shared_shape_a_check[1]
    else:
        with measure_native_geometry_region("pair_geometry_sanity"):
            ok_a, reason_a = check_solid_validity(shape_a, "shape_a")
    if not ok_a:
        dt = (time.perf_counter() - t0) * 1000.0
        return PairClearanceResult(
            CollisionClass.INVALID_GEOMETRY, reason_a, 0.0, 0.0, None, dt,
            tool_label=tool_label,
        )

    with measure_native_geometry_region("pair_geometry_sanity"):
        ok_b, reason_b = check_solid_validity(shape_b, "shape_b")
    if not ok_b:
        dt = (time.perf_counter() - t0) * 1000.0
        return PairClearanceResult(
            CollisionClass.INVALID_GEOMETRY, reason_b, 0.0, 0.0, None, dt,
            tool_label=tool_label,
        )

    # 2. Broad Phase: AABB fast reject with certified distance lower bound
    t_bp0 = time.perf_counter()
    box_a = getattr(shape_a, "BoundBox", None)
    box_b = getattr(shape_b, "BoundBox", None)
    aabb_gap = compute_aabb_gap(box_a, box_b)
    t_bp_ms = (time.perf_counter() - t_bp0) * 1000.0

    # Conservative AABB early reject:
    # AABB distance is a mathematically conservative lower bound of the
    # exact separation. If the gap exceeds BOTH thresholds, neither contact
    # nor penetration is possible. The former arbitrary 5 mm margin made
    # numerous proven-clear poses pay for a costly exact distance call.
    if (aabb_gap is not None and aabb_gap >
            max(0.0, near_tolerance_mm, contact_distance_tolerance_mm) + 1e-8):
        dt = (time.perf_counter() - t0) * 1000.0
        return PairClearanceResult(
            CollisionClass.CLEAR,
            "aabb_separated",
            float(aabb_gap),
            0.0,
            aabb_gap,
            dt,
            is_lower_bound=True,
            distance_queries_count=0,
            common_queries_count=0,
            broad_phase_time_ms=t_bp_ms,
            distance_time_ms=0.0,
            common_time_ms=0.0,
            tool_label=tool_label,
        )

    # 3. Narrow Phase: distToShape & Common volume.
    # When selected punch end-zone classification is requested, consume
    # the SAME OCC Common solid once, then discard it. Previously this path
    # ran the expensive Common boolean twice at every penetrating pose.
    def intersect_once_with_zone():
        if classify_end_zone_length_mm is None or tool_label != "PUNCH":
            volume, bound = _evaluate_common_volume(
                shape_a, shape_b, include_bbox=True,
            )
            return volume, bound, None
        volume, bound, common = _evaluate_common_volume(
            shape_a, shape_b, include_bbox=True, include_shape=True,
        )
        analysis = (
            classify_punch_overlap_zones(
                shape_a, shape_b, classify_end_zone_length_mm,
                volume_tolerance_mm3=volume_tolerance_mm3,
                _precomputed_common=common,
            )
            if volume > volume_tolerance_mm3 else None
        )
        return volume, bound, analysis

    # A positive OCC Common establishes penetration exactly, without an
    # additional exact boundary-distance operation. In all other situations,
    # the previous distance-first oracle is preserved. The hint changes only
    # evaluation ORDER, never physical collision thresholds or geometry.
    _common_value = None
    _common_time_ms = 0.0

    def evaluate_common_once():
        nonlocal _common_value, _common_time_ms
        if _common_value is None:
            started = time.perf_counter()
            _common_value = intersect_once_with_zone()
            _common_time_ms = (time.perf_counter() - started) * 1000.0
        return _common_value

    try:
        if (prefer_overlap_first and aabb_gap is not None
                and aabb_gap <= 1e-8):
            vol, overlap_bbox, ez_from_same_common = evaluate_common_once()
            if vol > volume_tolerance_mm3:
                dt = (time.perf_counter() - t0) * 1000.0
                return PairClearanceResult(
                    CollisionClass.PENETRATION,
                    "positive_common_volume",
                    0.0,  # Positive-volume overlap has zero solid distance.
                    vol,
                    aabb_gap,
                    dt,
                    distance_queries_count=0,
                    common_queries_count=1,
                    broad_phase_time_ms=t_bp_ms,
                    distance_time_ms=0.0,
                    common_time_ms=_common_time_ms,
                    tool_label=tool_label,
                    overlap_bbox_xyz_mm=overlap_bbox,
                    end_zone_analysis=ez_from_same_common,
                )

        t_dist0 = time.perf_counter()
        dist = _compute_exact_distance_with_pruning(shape_a, shape_b, box_a, box_b)
        t_dist_ms = (time.perf_counter() - t_dist0) * 1000.0

        if not math.isfinite(dist) or dist < 0.0:
            dt = (time.perf_counter() - t0) * 1000.0
            return PairClearanceResult(
                CollisionClass.INVALID_GEOMETRY,
                f"invalid_distance_value:{dist}",
                0.0,
                0.0,
                aabb_gap,
                dt,
                distance_queries_count=1,
                common_queries_count=0,
                broad_phase_time_ms=t_bp_ms,
                distance_time_ms=t_dist_ms,
                common_time_ms=0.0,
                tool_label=tool_label,
            )

        # A strictly positive AABB gap mathematically excludes ANY shared
        # volume or enclosure. If the gap is smaller than the near-contact
        # threshold, we still need one exact distance query to distinguish
        # NEAR_CONTACT from TOUCHING, but no expensive OCC Common is needed.
        # A tiny 1e-8 mm guard protects against bbox numerical roundoff.
        if aabb_gap is not None and aabb_gap > 1e-8:
            if dist > near_tolerance_mm:
                status, reason = CollisionClass.CLEAR, "distance_separated"
            elif dist <= contact_distance_tolerance_mm:
                status, reason = CollisionClass.TOUCHING, "surface_touching"
            else:
                status, reason = CollisionClass.NEAR_CONTACT, "near_contact"
            dt = (time.perf_counter() - t0) * 1000.0
            return PairClearanceResult(
                status, reason, dist, 0.0, aabb_gap, dt,
                distance_queries_count=1,
                common_queries_count=0,
                broad_phase_time_ms=t_bp_ms,
                distance_time_ms=t_dist_ms,
                common_time_ms=0.0,
                tool_label=tool_label,
            )

        if dist > near_tolerance_mm:
            # Check for potential total volume enclosure:
            b_in_a = (box_a.XMin <= box_b.XMin and box_a.XMax >= box_b.XMax and
                      box_a.YMin <= box_b.YMin and box_a.YMax >= box_b.YMax and
                      box_a.ZMin <= box_b.ZMin and box_a.ZMax >= box_b.ZMax)
            a_in_b = (box_b.XMin <= box_a.XMin and box_b.XMax >= box_a.XMax and
                      box_b.YMin <= box_a.YMin and box_b.YMax >= box_a.YMax and
                      box_b.ZMin <= box_a.ZMin and box_b.ZMax >= box_a.ZMax)
            if not (b_in_a or a_in_b):
                dt = (time.perf_counter() - t0) * 1000.0
                return PairClearanceResult(
                    CollisionClass.CLEAR,
                    "distance_separated",
                    dist,
                    0.0,
                    aabb_gap,
                    dt,
                    distance_queries_count=1,
                    common_queries_count=0,
                    broad_phase_time_ms=t_bp_ms,
                    distance_time_ms=t_dist_ms,
                    common_time_ms=0.0,
                    tool_label=tool_label,
                )

            # Potential enclosure: verify common volume explicitly.
            vol, overlap_bbox, ez_from_same_common = (
                evaluate_common_once()
            )
            t_comm_ms = _common_time_ms
            dt = (time.perf_counter() - t0) * 1000.0
            if vol > volume_tolerance_mm3:
                return PairClearanceResult(
                    CollisionClass.PENETRATION,
                    "enclosed_positive_common_volume",
                    0.0,
                    vol,
                    aabb_gap,
                    dt,
                    distance_queries_count=1,
                    common_queries_count=1,
                    broad_phase_time_ms=t_bp_ms,
                    distance_time_ms=t_dist_ms,
                    common_time_ms=t_comm_ms,
                    tool_label=tool_label,
                    overlap_bbox_xyz_mm=overlap_bbox,
                    end_zone_analysis=ez_from_same_common,
                )
            return PairClearanceResult(
                CollisionClass.CLEAR,
                "distance_separated_enclosure_checked",
                dist,
                0.0,
                aabb_gap,
                dt,
                distance_queries_count=1,
                common_queries_count=1,
                broad_phase_time_ms=t_bp_ms,
                distance_time_ms=t_dist_ms,
                common_time_ms=t_comm_ms,
                tool_label=tool_label,
            )

        vol, overlap_bbox, ez_from_same_common = (
            evaluate_common_once()
        )
        t_comm_ms = _common_time_ms
        dt = (time.perf_counter() - t0) * 1000.0

        if vol > volume_tolerance_mm3:
            return PairClearanceResult(
                CollisionClass.PENETRATION,
                "positive_common_volume",
                dist,
                vol,
                aabb_gap,
                dt,
                distance_queries_count=1,
                common_queries_count=1,
                broad_phase_time_ms=t_bp_ms,
                distance_time_ms=t_dist_ms,
                common_time_ms=t_comm_ms,
                tool_label=tool_label,
                overlap_bbox_xyz_mm=overlap_bbox,
                end_zone_analysis=ez_from_same_common,
            )

        if dist <= contact_distance_tolerance_mm:
            return PairClearanceResult(
                CollisionClass.TOUCHING,
                "surface_touching",
                dist,
                vol,
                aabb_gap,
                dt,
                distance_queries_count=1,
                common_queries_count=1,
                broad_phase_time_ms=t_bp_ms,
                distance_time_ms=t_dist_ms,
                common_time_ms=t_comm_ms,
                tool_label=tool_label,
            )

        return PairClearanceResult(
            CollisionClass.NEAR_CONTACT,
            "near_contact",
            dist,
            vol,
            aabb_gap,
            dt,
            distance_queries_count=1,
            common_queries_count=1,
            broad_phase_time_ms=t_bp_ms,
            distance_time_ms=t_dist_ms,
            common_time_ms=t_comm_ms,
            tool_label=tool_label,
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
            distance_queries_count=0,
            common_queries_count=0,
            broad_phase_time_ms=t_bp_ms,
            distance_time_ms=0.0,
            common_time_ms=0.0,
            tool_label=tool_label,
        )


def classify_punch_overlap_zones(
    workpiece_machine_shape: Any,
    punch_machine_shape: Any,
    end_zone_length_mm: float,
    *,
    volume_tolerance_mm3: float = 1e-4,
    _precomputed_common: Any = None,
) -> PunchEndZoneAnalysis:
    """Classify every positive-volume intersection solid between workpiece and punch against configured end-zones.

    Rules:
    - Evaluates the full 3D bounding box `[XMin, XMax]` of every intersection solid in `workpiece.common(punch).Solids`.
    - Never classifies by center of mass alone and never ignores small-volume central collisions.
    - Fails closed with `UNCLASSIFIED` if `end_zone_length_mm <= 0`, non-finite, or `2 * end_zone_length_mm >= punch_length`,
      or if intersection geometry is invalid / lacks solid components.
    """
    def _unclassified(reason: str, p_span: Tuple[float, float] = (0.0, 0.0), ez_len: float = 0.0, vol: float = 0.0) -> PunchEndZoneAnalysis:
        return PunchEndZoneAnalysis(
            classification=PunchCollisionZoneClassification.UNCLASSIFIED,
            end_zone_length_mm=ez_len,
            punch_span_xm=p_span,
            left_end_zone_xm=(p_span[0], p_span[0]),
            central_zone_xm=p_span,
            right_end_zone_xm=(p_span[1], p_span[1]),
            total_overlap_volume_mm3=vol,
            end_zone_overlap_volume_mm3=0.0,
            central_zone_overlap_volume_mm3=vol,
            affected_sides=(),
            components=(),
            reason=reason,
        )

    ok_w, r_w = check_solid_validity(workpiece_machine_shape, "workpiece")
    if not ok_w:
        return _unclassified(f"invalid_workpiece:{r_w}")
    ok_p, r_p = check_solid_validity(punch_machine_shape, "punch")
    if not ok_p:
        return _unclassified(f"invalid_punch:{r_p}")

    try:
        ez_len = float(end_zone_length_mm)
    except (TypeError, ValueError):
        return _unclassified(f"non_numeric_end_zone_length:{end_zone_length_mm!r}")

    p_bb = getattr(punch_machine_shape, "BoundBox", None)
    if p_bb is None:
        return _unclassified("missing_punch_boundbox", ez_len=ez_len if math.isfinite(ez_len) else 0.0)

    try:
        p_xmin = float(p_bb.XMin)
        p_xmax = float(p_bb.XMax)
    except Exception as exc:
        return _unclassified(f"punch_boundbox_read_error:{exc}", ez_len=ez_len if math.isfinite(ez_len) else 0.0)

    punch_len = p_xmax - p_xmin
    if not (math.isfinite(p_xmin) and math.isfinite(p_xmax) and punch_len > 1e-3):
        return _unclassified(f"invalid_punch_span:[{p_xmin},{p_xmax}]", ez_len=ez_len if math.isfinite(ez_len) else 0.0)

    if not math.isfinite(ez_len) or ez_len <= 0.0:
        return _unclassified(f"end_zone_length_must_be_positive:{ez_len}", p_span=(p_xmin, p_xmax), ez_len=0.0)

    if 2.0 * ez_len >= punch_len - 1e-6:
        return _unclassified(
            f"end_zones_cover_or_overlap_entire_punch:2*{ez_len:.3f}>={punch_len:.3f}",
            p_span=(p_xmin, p_xmax),
            ez_len=ez_len,
        )

    left_zone = (p_xmin, p_xmin + ez_len)
    central_zone = (p_xmin + ez_len, p_xmax - ez_len)
    right_zone = (p_xmax - ez_len, p_xmax)

    try:
        # _precomputed_common is only passed from the SAME local native
        # punch/part pair check. Standalone callers continue to run OCC.
        common_shape = (
            _precomputed_common if _precomputed_common is not None
            else measured_common(
                workpiece_machine_shape, punch_machine_shape,
                label="punch_end_zone_common",
            )
        )
    except Exception as exc:
        return _unclassified(f"common_evaluation_failed:{exc}", p_span=(p_xmin, p_xmax), ez_len=ez_len)

    if common_shape is None:
        return _unclassified("common_shape_is_none", p_span=(p_xmin, p_xmax), ez_len=ez_len)

    if hasattr(common_shape, "isNull") and common_shape.isNull():
        return PunchEndZoneAnalysis(
            classification=PunchCollisionZoneClassification.NONE,
            end_zone_length_mm=ez_len,
            punch_span_xm=(p_xmin, p_xmax),
            left_end_zone_xm=left_zone,
            central_zone_xm=central_zone,
            right_end_zone_xm=right_zone,
            total_overlap_volume_mm3=0.0,
            end_zone_overlap_volume_mm3=0.0,
            central_zone_overlap_volume_mm3=0.0,
            affected_sides=(),
            components=(),
            reason="empty_intersection",
        )

    if hasattr(common_shape, "isValid") and not common_shape.isValid():
        return _unclassified("common_shape_invalid", p_span=(p_xmin, p_xmax), ez_len=ez_len)

    try:
        total_vol = float(common_shape.Volume)
    except Exception as exc:
        return _unclassified(f"common_volume_read_error:{exc}", p_span=(p_xmin, p_xmax), ez_len=ez_len)

    if not math.isfinite(total_vol) or total_vol < 0.0:
        return _unclassified(f"common_volume_invalid:{total_vol}", p_span=(p_xmin, p_xmax), ez_len=ez_len)

    if total_vol <= volume_tolerance_mm3:
        return PunchEndZoneAnalysis(
            classification=PunchCollisionZoneClassification.NONE,
            end_zone_length_mm=ez_len,
            punch_span_xm=(p_xmin, p_xmax),
            left_end_zone_xm=left_zone,
            central_zone_xm=central_zone,
            right_end_zone_xm=right_zone,
            total_overlap_volume_mm3=total_vol,
            end_zone_overlap_volume_mm3=0.0,
            central_zone_overlap_volume_mm3=0.0,
            affected_sides=(),
            components=(),
            reason="overlap_within_volume_tolerance",
        )

    solids = list(getattr(common_shape, "Solids", ()))
    if not solids:
        return _unclassified("positive_volume_without_solids", p_span=(p_xmin, p_xmax), ez_len=ez_len, vol=total_vol)

    comp_infos: List[PunchOverlapComponentInfo] = []
    end_zone_vol = 0.0
    central_vol = 0.0
    sides_set = set()
    has_end_zone_comp = False
    has_central_comp = False
    mixed_fragment_volume_mm3 = 0.0
    sum_comp_vol = 0.0
    boundary_eps = 1e-5

    for idx, sol in enumerate(solids):
        if sol is None or (hasattr(sol, "isNull") and sol.isNull()) or (hasattr(sol, "isValid") and not sol.isValid()):
            return _unclassified(f"component_{idx}_invalid", p_span=(p_xmin, p_xmax), ez_len=ez_len, vol=total_vol)
        try:
            svol = float(sol.Volume)
            sbb = sol.BoundBox
            xmin, xmax = float(sbb.XMin), float(sbb.XMax)
            ymin, ymax = float(sbb.YMin), float(sbb.YMax)
            zmin, zmax = float(sbb.ZMin), float(sbb.ZMax)
        except Exception as exc:
            return _unclassified(f"component_{idx}_read_error:{exc}", p_span=(p_xmin, p_xmax), ez_len=ez_len, vol=total_vol)

        if not (
            math.isfinite(svol) and svol >= 0.0
            and math.isfinite(xmin) and math.isfinite(xmax) and xmax >= xmin - 1e-7
            and math.isfinite(ymin) and math.isfinite(ymax)
            and math.isfinite(zmin) and math.isfinite(zmax)
        ):
            return _unclassified(f"component_{idx}_non_finite_metrics", p_span=(p_xmin, p_xmax), ez_len=ez_len, vol=total_vol)

        sum_comp_vol += svol
        if svol <= 1e-9:
            continue

        in_left = (xmin >= p_xmin - 1e-2) and (xmax <= left_zone[1] + boundary_eps)
        in_right = (xmin >= right_zone[0] - boundary_eps) and (xmax <= p_xmax + 1e-2)
        in_central = (xmin > left_zone[1] - boundary_eps) and (xmax < right_zone[0] + boundary_eps) and (not in_left) and (not in_right)

        if in_left and not in_right:
            zone_label = "LEFT_END_ZONE"
            end_zone_vol += svol
            sides_set.add("LEFT")
            has_end_zone_comp = True
        elif in_right and not in_left:
            zone_label = "RIGHT_END_ZONE"
            end_zone_vol += svol
            sides_set.add("RIGHT")
            has_end_zone_comp = True
        elif in_central:
            zone_label = "CENTRAL_ZONE"
            central_vol += svol
            sides_set.add("CENTRAL")
            has_central_comp = True
        else:
            zone_label = "MIXED"
            central_vol += svol
            if xmin < left_zone[1]:
                sides_set.add("LEFT")
            if xmax > right_zone[0]:
                sides_set.add("RIGHT")
            sides_set.add("CENTRAL")
            mixed_fragment_volume_mm3 += svol

        comp_infos.append(
            PunchOverlapComponentInfo(
                component_index=idx,
                volume_mm3=svol,
                bbox_xmin_mm=xmin,
                bbox_xmax_mm=xmax,
                bbox_ymin_mm=ymin,
                bbox_ymax_mm=ymax,
                bbox_zmin_mm=zmin,
                bbox_zmax_mm=zmax,
                zone=zone_label,
            )
        )

    if not comp_infos or abs(sum_comp_vol - total_vol) > max(1e-3, 1e-2 * total_vol):
        return _unclassified(
            f"component_volume_mismatch:sum={sum_comp_vol:.6f}_vs_total={total_vol:.6f}",
            p_span=(p_xmin, p_xmax),
            ez_len=ez_len,
            vol=total_vol,
        )

    # Note: final affected_sides follows significance filtering below.
    # Do not report CENTER as physically blocking for numerical slivers.

    # Classify based on cumulative volumes outside and inside allowed end-zones.
    # A set of small central bodies whose cumulative volume exceeds volume_tolerance_mm3
    # must never be lost or ignored.
    has_significant_central = central_vol > volume_tolerance_mm3
    has_significant_end_zone = end_zone_vol > volume_tolerance_mm3

    # Tiny OCC Common slivers may have an X bounding box across the full
    # punch span, despite a COMBINED volume smaller than the existing
    # geometric collision tolerance. A boolean "has any mixed solid"
    # incorrectly rejects the entire end-zone allowance in that case.
    # Aggregate ALL central/mixed component volumes before applying the
    # SAME volume tolerance as the authoritative OCC collision oracle.
    # Never discard significant central material, even when each separate
    # sliver is individually below tolerance.
    if not has_significant_central:
        sides_set.discard("CENTRAL")
    if has_significant_central and has_significant_end_zone:
        cls_res = PunchCollisionZoneClassification.MIXED_CENTRAL_AND_END_ZONE
        reason = "significant_overlap_spans_both_central_and_end_zones"
    elif has_significant_central:
        cls_res = PunchCollisionZoneClassification.CENTRAL_ZONE
        reason = "overlap_in_central_working_zone"
    elif has_significant_end_zone:
        cls_res = PunchCollisionZoneClassification.END_ZONE_ONLY
        reason = (
            "end_zone_only_with_central_volume_within_tolerance:"
            "central={:.9f}mm3,threshold={:.9f}mm3,"
            "mixed_fragment={:.9f}mm3"
            .format(central_vol, volume_tolerance_mm3,
                    mixed_fragment_volume_mm3)
            if central_vol > 0.0
            else "all_overlap_components_strictly_within_end_zones"
        )
    else:
        cls_res = PunchCollisionZoneClassification.NONE
        reason = "overlap_within_volume_tolerance"

    ordered_sides = tuple(
        side for side in ("LEFT", "CENTRAL", "RIGHT")
        if side in sides_set
    )
    return PunchEndZoneAnalysis(
        classification=cls_res,
        end_zone_length_mm=ez_len,
        punch_span_xm=(p_xmin, p_xmax),
        left_end_zone_xm=left_zone,
        central_zone_xm=central_zone,
        right_end_zone_xm=right_zone,
        total_overlap_volume_mm3=total_vol,
        end_zone_overlap_volume_mm3=end_zone_vol,
        central_zone_overlap_volume_mm3=central_vol,
        affected_sides=ordered_sides,
        components=tuple(comp_infos),
        reason=reason,
    )


def validate_workpiece_tooling_pose(
    workpiece_machine_shape: Any,
    punch_machine_shape: Any,
    die_machine_shape: Any,
    *,
    near_tolerance_mm: float = 0.5,
    volume_tolerance_mm3: float = 1e-4,
    contact_distance_tolerance_mm: float = 1e-4,
    cached_die_result: Optional[PairClearanceResult] = None,
    cached_punch_result: Optional[PairClearanceResult] = None,
    end_zone_policy: Optional[EndZoneCollisionPolicy] = None,
    classify_end_zone_length_mm: Optional[float] = None,
    prefer_punch_overlap_first: bool = False,
    _validated_workpiece_shape: Optional[Tuple[Any, Tuple[bool, str]]] = None,
) -> ToolCollisionReport:
    """Validate 3D geometric collision status of a machine-placed workpiece against punch and die."""
    t0 = time.perf_counter()
    diags: List[str] = []

    if workpiece_machine_shape is None and (cached_punch_result is None or cached_die_result is None):
        diags.append("MISSING_WORKPIECE_SHAPE")
    if punch_machine_shape is None and cached_punch_result is None:
        diags.append("MISSING_PUNCH_SHAPE")
    if die_machine_shape is None and cached_die_result is None:
        diags.append("MISSING_DIE_SHAPE")

    if diags:
        dt = (time.perf_counter() - t0) * 1000.0
        dummy = PairClearanceResult(CollisionClass.MISSING_EVIDENCE, "missing_evidence", 0.0, 0.0, None, 0.0)
        return ToolCollisionReport(CollisionClass.MISSING_EVIDENCE, dummy, dummy, False, diags, dt)

    punch_cache_hit = cached_punch_result is not None
    die_cache_hit = cached_die_result is not None

    # Select the exact end-zone length BEFORE the first punch Common, so
    # the zone classifier can reuse its native OCC result. The cache key in
    # AdaptiveTrajectoryValidator3D already includes this policy and length.
    punch_ez_len = None
    if (end_zone_policy is not None and
            (end_zone_policy.allow_end_zone_collision or
             end_zone_policy.end_zone_length_mm != 0.0)):
        punch_ez_len = end_zone_policy.end_zone_length_mm
    elif (classify_end_zone_length_mm is not None
          and classify_end_zone_length_mm != 0.0):
        punch_ez_len = float(classify_end_zone_length_mm)

    # Per-pose memo: when BOTH tool pairs are uncached, check the native
    # machine-placed sheet ONCE, then share that exact verdict with the punch
    # and die query. No result survives this ToolCollisionReport call, and
    # tools are still checked independently by check_pair_clearance.
    shared_workpiece_check = None
    # Optional same-object proof from the validator's ONE retained native
    # machine shape. Refuse a different object or malformed tuple; never
    # trust a validity result based only on equal coordinates/geometry keys.
    if (_validated_workpiece_shape is not None
            and isinstance(_validated_workpiece_shape, tuple)
            and len(_validated_workpiece_shape) == 2
            and _validated_workpiece_shape[0] is workpiece_machine_shape
            and isinstance(_validated_workpiece_shape[1], tuple)
            and len(_validated_workpiece_shape[1]) == 2
            and isinstance(_validated_workpiece_shape[1][0], bool)):
        shared_workpiece_check = _validated_workpiece_shape
    elif not punch_cache_hit and not die_cache_hit:
        with measure_native_geometry_region("pair_geometry_sanity"):
            shared_workpiece_check = (
                workpiece_machine_shape,
                check_solid_validity(workpiece_machine_shape, "shape_a"),
            )

    if punch_cache_hit:
        punch_res = cached_punch_result
    else:
        punch_res = check_pair_clearance(
            workpiece_machine_shape,
            punch_machine_shape,
            near_tolerance_mm=near_tolerance_mm,
            volume_tolerance_mm3=volume_tolerance_mm3,
            contact_distance_tolerance_mm=contact_distance_tolerance_mm,
            tool_label="PUNCH",
            classify_end_zone_length_mm=punch_ez_len,
            prefer_overlap_first=bool(prefer_punch_overlap_first),
            _shared_shape_a_check=shared_workpiece_check,
        )

    if die_cache_hit:
        die_res = cached_die_result
    else:
        die_res = check_pair_clearance(
            workpiece_machine_shape,
            die_machine_shape,
            near_tolerance_mm=near_tolerance_mm,
            volume_tolerance_mm3=volume_tolerance_mm3,
            contact_distance_tolerance_mm=contact_distance_tolerance_mm,
            tool_label="DIE",
            _shared_shape_a_check=shared_workpiece_check,
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

    is_conditional_safe = False
    conditional_status: Optional[str] = None
    ez_analysis: Optional[PunchEndZoneAnalysis] = None

    # Classify punch overlap zones when punch penetration occurs and either end_zone_policy or classify_end_zone_length_mm is given
    should_classify_ez = (
        punch_res.status == CollisionClass.PENETRATION
        and (
            (end_zone_policy is not None and (end_zone_policy.allow_end_zone_collision or end_zone_policy.end_zone_length_mm != 0.0))
            or (classify_end_zone_length_mm is not None and classify_end_zone_length_mm != 0.0)
        )
    )
    if should_classify_ez:
        ez_len_to_use = (
            end_zone_policy.end_zone_length_mm
            if end_zone_policy is not None and (end_zone_policy.allow_end_zone_collision or end_zone_policy.end_zone_length_mm != 0.0)
            else float(classify_end_zone_length_mm or 0.0)
        )
        cached_ez = getattr(punch_res, "end_zone_analysis", None)
        if (
            cached_ez is not None
            # A properly computed UNCLASSIFIED is authoritative missing
            # evidence too; do not retry the same failing Common needlessly.
            and math.isclose(cached_ez.end_zone_length_mm, ez_len_to_use, abs_tol=1e-5)
        ):
            ez_analysis = cached_ez
        else:
            if workpiece_machine_shape is None or punch_machine_shape is None:
                p_span = getattr(cached_ez, "punch_span_xm", (0.0, 0.0)) if cached_ez else (0.0, 0.0)
                ez_analysis = PunchEndZoneAnalysis(
                    classification=PunchCollisionZoneClassification.UNCLASSIFIED,
                    end_zone_length_mm=ez_len_to_use,
                    punch_span_xm=p_span,
                    left_end_zone_xm=(p_span[0], p_span[0]),
                    central_zone_xm=p_span,
                    right_end_zone_xm=(p_span[1], p_span[1]),
                    total_overlap_volume_mm3=punch_res.overlap_volume_mm3,
                    end_zone_overlap_volume_mm3=0.0,
                    central_zone_overlap_volume_mm3=punch_res.overlap_volume_mm3,
                    affected_sides=(),
                    components=(),
                    reason="missing_shapes_for_end_zone_classification",
                )
            else:
                ez_analysis = classify_punch_overlap_zones(
                    workpiece_machine_shape,
                    punch_machine_shape,
                    ez_len_to_use,
                    volume_tolerance_mm3=volume_tolerance_mm3,
                )
        punch_res = replace(punch_res, end_zone_analysis=ez_analysis)
        diags.append(
            f"PUNCH_ZONE_CLASSIFICATION: {ez_analysis.classification.value} "
            f"(sides={list(ez_analysis.affected_sides)}, end_vol={ez_analysis.end_zone_overlap_volume_mm3:.4f}mm3, "
            f"central_vol={ez_analysis.central_zone_overlap_volume_mm3:.4f}mm3, reason={ez_analysis.reason})"
        )

        if end_zone_policy is not None and end_zone_policy.allow_end_zone_collision:
            if ez_analysis.classification == PunchCollisionZoneClassification.UNCLASSIFIED:
                # Fail closed: failed zone classification when policy is active must report UNVERIFIED / INVALID_GEOMETRY
                overall_status = CollisionClass.INVALID_GEOMETRY
                is_safe = False
                is_conditional_safe = False
                conditional_status = "UNVERIFIED_END_ZONE_CLASSIFICATION"
                diags.append(f"END_ZONE_CLASSIFICATION_FAILED: {ez_analysis.reason}")
            elif (
                ez_analysis.classification == PunchCollisionZoneClassification.END_ZONE_ONLY
                and die_res.status in (CollisionClass.CLEAR, CollisionClass.NEAR_CONTACT, CollisionClass.TOUCHING)
            ):
                is_safe = False
                is_conditional_safe = True
                conditional_status = "CONDITIONAL"
                diags.append(
                    "PUNCH_END_ZONE_CONDITIONAL_ALLOWED: collision strictly in punch end-zone(s); "
                    "requires relief-cut punch verification."
                )
            else:
                is_conditional_safe = False
                conditional_status = "REJECTED_CENTRAL_OR_DIE_COLLISION"

    dt = (time.perf_counter() - t0) * 1000.0
    return ToolCollisionReport(
        status=overall_status,
        punch_result=punch_res,
        die_result=die_res,
        is_safe=is_safe,
        diagnostics=diags,
        elapsed_ms=dt,
        is_conditional_safe=is_conditional_safe,
        conditional_status=conditional_status,
        punch_end_zone_analysis=ez_analysis,
        punch_cache_hit=punch_cache_hit,
        die_cache_hit=die_cache_hit,
    )


def qualify_tool_setup(
    punch_coverage: Any,
    die_coverage: Any,
) -> Tuple[bool, SetupQualificationStatus, List[str]]:
    """Qualify physical tooling coverage and assembly continuity."""
    diags: List[str] = []
    if punch_coverage is None:
        diags.append("MISSING_PUNCH_COVERAGE_EVIDENCE")
        return False, SetupQualificationStatus.MISSING_TOOLING_EVIDENCE, diags

    if die_coverage is None:
        diags.append("MISSING_DIE_COVERAGE_EVIDENCE")
        return False, SetupQualificationStatus.MISSING_TOOLING_EVIDENCE, diags

    if getattr(punch_coverage, "status", "") == "TOOL_ASSEMBLY_GAP":
        diags.append(f"PUNCH_GAP: {punch_coverage.reason}")
        return False, SetupQualificationStatus.TOOL_ASSEMBLY_GAP, diags

    if getattr(die_coverage, "status", "") == "TOOL_ASSEMBLY_GAP":
        diags.append(f"DIE_GAP: {die_coverage.reason}")
        return False, SetupQualificationStatus.TOOL_ASSEMBLY_GAP, diags

    if not getattr(punch_coverage, "is_covered", False):
        diags.append(f"PUNCH_COVERAGE_INSUFFICIENT: {punch_coverage.reason}")
        return False, SetupQualificationStatus.INSUFFICIENT_PUNCH_COVERAGE, diags

    if not getattr(die_coverage, "is_covered", False):
        diags.append(f"DIE_COVERAGE_INSUFFICIENT: {die_coverage.reason}")
        return False, SetupQualificationStatus.INSUFFICIENT_DIE_COVERAGE, diags

    diags.append("Tooling setup verified: full punch and die coverage without gaps.")
    return True, SetupQualificationStatus.TOOL_SETUP_QUALIFIED, diags


def qualify_operation_sampled_poses(
    phase_reports: Sequence[PhaseQualificationReport],
    punch_coverage: Any,
    die_coverage: Any,
) -> OperationQualificationReport:
    """Aggregate physical qualification for an entire sampled bend operation.

    Strict logical AND contract:
    - Every sampled phase must be safe (CLEAR, NEAR_CONTACT, TOUCHING).
    - If ANY phase has PENETRATION or INVALID_GEOMETRY, operation is SAMPLED_POSES_REJECTED.
    - If tooling coverage fails, operation is SAMPLED_POSES_REJECTED.
    - If no phases are evaluated, operation is MISSING_PHASE_EVIDENCE.
    - Preserves UNVERIFIED_TRAJECTORY distinction.
    """
    t0 = time.perf_counter()
    tool_ok, tool_status, diags = qualify_tool_setup(punch_coverage, die_coverage)

    if not phase_reports:
        dt = (time.perf_counter() - t0) * 1000.0
        diags.append("No cycle phases were provided for evaluation.")
        return OperationQualificationReport(
            is_qualified=False,
            operation_status=OperationQualificationStatus.MISSING_PHASE_EVIDENCE,
            tool_setup_status=tool_status,
            trajectory_status="UNVERIFIED_TRAJECTORY",
            total_phases_tested=0,
            safe_phases_count=0,
            rejected_phases=[],
            phase_reports=[],
            diagnostics=diags,
            elapsed_ms=dt,
        )

    safe_count = 0
    rejected_phases: List[str] = []

    for pr in phase_reports:
        if pr.is_phase_safe:
            safe_count += 1
        else:
            rejected_phases.append(pr.phase_name)
            diags.append(f"Phase {pr.phase_name} REJECTED: {pr.collision_report.status.value}")

    all_phases_safe = (safe_count == len(phase_reports))
    is_qualified = tool_ok and all_phases_safe

    if is_qualified:
        op_status = OperationQualificationStatus.SAMPLED_POSES_QUALIFIED
        diags.append(f"All {safe_count}/{len(phase_reports)} sampled poses qualified.")
    else:
        op_status = OperationQualificationStatus.SAMPLED_POSES_REJECTED
        if not tool_ok:
            diags.append(f"Operation rejected due to tool setup: {tool_status.value}")
        if rejected_phases:
            diags.append(f"Operation rejected due to collision in phases: {', '.join(rejected_phases)}")

    dt = (time.perf_counter() - t0) * 1000.0
    return OperationQualificationReport(
        is_qualified=is_qualified,
        operation_status=op_status,
        tool_setup_status=tool_status,
        trajectory_status="UNVERIFIED_TRAJECTORY",
        total_phases_tested=len(phase_reports),
        safe_phases_count=safe_count,
        rejected_phases=rejected_phases,
        phase_reports=list(phase_reports),
        diagnostics=diags,
        elapsed_ms=dt,
    )
