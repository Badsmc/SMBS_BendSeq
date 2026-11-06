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
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple


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


@dataclass(frozen=True)
class ToolCollisionReport:
    status: CollisionClass
    punch_result: PairClearanceResult
    die_result: PairClearanceResult
    is_safe: bool
    diagnostics: List[str]
    elapsed_ms: float


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


def _compute_exact_distance_with_pruning(
    shape_a: Any,
    shape_b: Any,
    box_a: Any = None,
    box_b: Any = None,
) -> float:
    """Compute exact Euclidean distance between two native FreeCAD Part solids with conservative face pruning.

    Mathematical Proof of Exactness:
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
    Therefore, d is mathematically guaranteed to be the exact global minimum distance between
    shape_a and shape_b.
    If d > margin or any exception occurs, it falls back to full OCC shape_a.distToShape(shape_b),
    preserving 100% exact OCC truth in all circumstances.
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
            res = shape_a.distToShape(shape_b)
            d = float(res[0]) if res and len(res) > 0 else 0.0
            return d if math.isfinite(d) and d >= 0.0 else 0.0

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
            res = comp.distToShape(s_other)
            d = float(res[0]) if res and len(res) > 0 else 0.0
            if math.isfinite(d) and d >= 0.0 and d <= margin:
                return d
    except Exception:
        pass

    res = shape_a.distToShape(shape_b)
    d = float(res[0]) if res and len(res) > 0 else 0.0
    return d if math.isfinite(d) and d >= 0.0 else 0.0


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

    # 2. Broad Phase: AABB fast reject with certified distance lower bound
    box_a = getattr(shape_a, "BoundBox", None)
    box_b = getattr(shape_b, "BoundBox", None)
    aabb_gap = compute_aabb_gap(box_a, box_b)

    # Conservative AABB early reject:
    # Mathematical proof: For any bounded shapes S_a subset AABB_a, S_b subset AABB_b,
    # Euclidean distance dist(S_a, S_b) >= dist(AABB_a, AABB_b) = aabb_gap.
    # If aabb_gap > max(5.0, near_tolerance_mm * 2.0), the shapes are strictly separated
    # with distance >= aabb_gap > near_tolerance_mm, volume == 0.0, and neither contact
    # nor penetration can occur.
    if aabb_gap is not None and aabb_gap > max(5.0, near_tolerance_mm * 2.0):
        dt = (time.perf_counter() - t0) * 1000.0
        return PairClearanceResult(
            CollisionClass.CLEAR,
            "aabb_separated",
            float(aabb_gap),
            0.0,
            aabb_gap,
            dt,
            is_lower_bound=True,
        )

    # 3. Narrow Phase: distToShape & Common volume
    try:
        dist = _compute_exact_distance_with_pruning(shape_a, shape_b, box_a, box_b)

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

        common_shape = shape_a.common(shape_b)
        vol = float(getattr(common_shape, "Volume", 0.0))
        if not math.isfinite(vol) or vol < 0.0:
            vol = 0.0

        dt = (time.perf_counter() - t0) * 1000.0

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
    contact_distance_tolerance_mm: float = 1e-4,
    cached_die_result: Optional[PairClearanceResult] = None,
) -> ToolCollisionReport:
    """Validate 3D geometric collision status of a machine-placed workpiece against punch and die."""
    t0 = time.perf_counter()
    diags: List[str] = []

    if workpiece_machine_shape is None:
        diags.append("MISSING_WORKPIECE_SHAPE")
    if punch_machine_shape is None:
        diags.append("MISSING_PUNCH_SHAPE")
    if die_machine_shape is None and cached_die_result is None:
        diags.append("MISSING_DIE_SHAPE")

    if diags:
        dt = (time.perf_counter() - t0) * 1000.0
        dummy = PairClearanceResult(CollisionClass.MISSING_EVIDENCE, "missing_evidence", 0.0, 0.0, None, 0.0)
        return ToolCollisionReport(CollisionClass.MISSING_EVIDENCE, dummy, dummy, False, diags, dt)

    punch_res = check_pair_clearance(
        workpiece_machine_shape,
        punch_machine_shape,
        near_tolerance_mm=near_tolerance_mm,
        volume_tolerance_mm3=volume_tolerance_mm3,
        contact_distance_tolerance_mm=contact_distance_tolerance_mm,
    )

    if cached_die_result is not None:
        die_res = cached_die_result
    else:
        die_res = check_pair_clearance(
            workpiece_machine_shape,
            die_machine_shape,
            near_tolerance_mm=near_tolerance_mm,
            volume_tolerance_mm3=volume_tolerance_mm3,
            contact_distance_tolerance_mm=contact_distance_tolerance_mm,
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
