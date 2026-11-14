"""motion_trajectory_3d.py - Continuous 3D Motion & Adaptive Collision Validation (D6.3.4).

Architecture & Contracts:
1. Continuous Motion vs Discrete Samples:
   - Evaluates motion trajectories across complete cycle phases:
     APPROACH, CONTACT, BENDING, BOTTOM, RETRACT, and optionally TRANSFER.
   - Status vocabulary strictly preserves:
     * DISCRETE_SAMPLES_SAFE
     * DISCRETE_SAMPLES_REJECTED
     * SAMPLED_INTERVAL_CONVERGED
     * NOT_CONTINUOUSLY_CERTIFIED
     * ADAPTIVE_SAMPLING_CONVERGED
     * INTERVAL_UNRESOLVED
     * CONTINUOUS_COLLISION_CERTIFIED
     * MISSING_EVIDENCE
     * INVALID_GEOMETRY
     * TRANSFER_PATH_UNDEFINED
   - Adaptive sampling alone DOES NOT certify continuous collision freedom.
     CONTINUOUS_COLLISION_CERTIFIED is strictly withheld unless conservative
     analytical clearance bounds / swept envelopes are mathematically proven.
     When adaptive sampling converges without such analytical proof, the trajectory
     status explicitly records SAMPLED_INTERVAL_CONVERGED and NOT_CONTINUOUSLY_CERTIFIED.
2. Continuity Checking:
   - Checks continuity of workpiece rigid placement (both translation and SO(3) rotation),
     ram position, active bend angle, all non-active bend angles, and panel transforms.
   - Compares boundary poses across phases: end pose of phase N must match start pose of phase N+1.
3. Adaptive Interval Sampling & Mandatory Midpoint Evaluation:
   - Always evaluates the midpoint of an interval before accepting it.
   - Recursively bisects intervals when clearance drops, clearance changes rapidly,
     ram/angle steps exceed limits, contact changes, or endpoints/midpoints clash.
   - Strictly reports INTERVAL_UNRESOLVED if max recursion depth, state budget,
     or time limit is reached.
4. FoldState Deterministic Isolation:
   - Snapshots the complete incoming FoldState before evaluation and restores it
     atomically via try...finally.
   - Restores the exact angles and panel transforms even on exceptions.
   - Evaluates bends sequentially with clean state snapshots:
     Bend B02 evaluates with prior completed bends (e.g. B01 at folded angle).
   - Handles signed bend angles (+45, -45, +90, -90) with proper punch/die kinematics and orientation.
5. Sample Budget and Accounting:
   - Enforces limits against actual solid reconstructions and collision evaluations.
   - Prevents recursive calls after budget is exhausted.
   - Caches/deduplicates identical sample parameters (path_u) within each phase.
   - Records solid reconstruction count and collision evaluation count accurately.
"""
from __future__ import annotations

import copy
import math
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from smbs_core.kinematics2d.coupled_kinematics_2d import CoupledAirBendingKinematics
from smbs_core.kinematics2d.ram_cycle_geometry import ram_cycle_positions
from smbs_core.kinematics2d.tooling_25d import DEFAULT_INITIAL_PUNCH_CLEARANCE_MM
from smbs_core.kinematics2d.native_bend_solid_3d import (
    build_multi_bend_sheet_solid, build_multi_bend_sheet_components,
    is_rigid_transform_matrix,
)
from smbs_core.kinematics2d.component_collision_oracle import (
    screen_machine_component_overlap, record_component_shadow_probe,
    record_component_shadow_rebuild,
    prove_machine_component_blocker, ComponentBlockerFound,
)
from smbs_core.kinematics2d.native_geometry_build_metrics import (
    measure_native_geometry_region, record_native_geometry_cache_event,
)
from smbs_core.kinematics2d.native_collision_3d import (
    CollisionClass,
    EndZoneCollisionPolicy,
    PairClearanceResult,
    PunchCollisionZoneClassification,
    PunchEndZoneAnalysis,
    ToolCollisionReport,
    validate_workpiece_tooling_pose,
    check_solid_validity,
    qualify_tool_setup,
    SetupQualificationStatus,
)
from smbs_core.kinematics2d.native_tool_loader import (
    LoadedTool,
    check_tool_coverage,
    set_punch_ram_z,
)
from smbs_core.kinematics2d.workpiece_placement_3d import (
    PlacementTransform3D,
    WorkpiecePlacement3D,
    Matrix3x3,
    Point3D,
    _mat_mult,
    _mat_vec,
)
from smbs_core.kinematics2d.workpiece_transfer_3d import Transfer3D, build_transfer_3d


class TrajectoryEvidenceStatus(str, Enum):
    DISCRETE_SAMPLES_SAFE = "DISCRETE_SAMPLES_SAFE"
    DISCRETE_SAMPLES_REJECTED = "DISCRETE_SAMPLES_REJECTED"
    SAMPLED_INTERVAL_CONVERGED = "SAMPLED_INTERVAL_CONVERGED"
    SAMPLED_INTERVAL_CONDITIONAL_END_ZONE = "SAMPLED_INTERVAL_CONDITIONAL_END_ZONE"
    NOT_CONTINUOUSLY_CERTIFIED = "NOT_CONTINUOUSLY_CERTIFIED"
    ADAPTIVE_SAMPLING_CONVERGED = "ADAPTIVE_SAMPLING_CONVERGED"
    INTERVAL_UNRESOLVED = "INTERVAL_UNRESOLVED"
    CONTINUOUS_COLLISION_CERTIFIED = "CONTINUOUS_COLLISION_CERTIFIED"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    INVALID_GEOMETRY = "INVALID_GEOMETRY"
    TRANSFER_PATH_UNDEFINED = "TRANSFER_PATH_UNDEFINED"


@dataclass(frozen=True)
class MotionState3D:
    """Rigorous snapshot of a single point along the 3D press-brake motion trajectory."""
    phase: str
    active_bend_id: str
    signed_angle_deg: float
    path_u: float  # Normalized phase parameter [0.0, 1.0]
    ram_z_mm: float
    support_plane_zm: float
    workpiece_placement: PlacementTransform3D
    completed_angles: Dict[str, float]
    # Geometric and collision evaluations
    collision_report: Optional[ToolCollisionReport] = None
    is_safe: bool = True
    discontinuity_detected: bool = False
    discontinuity_reason: str = ""
    punch_actual_eval_ms: float = 0.0
    die_actual_eval_ms: float = 0.0
    cache_lookup_ms: float = 0.0
    die_cache_hit: bool = False
    solid_cache_hit: bool = False
    punch_cache_hit: bool = False
    punch_eval_ms: float = 0.0
    die_eval_ms: float = 0.0
    is_conditional_safe: bool = False
    conditional_status: Optional[str] = None
    punch_end_zone_analysis: Optional[PunchEndZoneAnalysis] = None


@dataclass
class AdaptiveSamplingConfig:
    """Configurable conservative limits for adaptive trajectory subdivision."""
    max_bend_angle_step_deg: float = 5.0
    max_ram_displacement_step_mm: float = 5.0
    min_clearance_margin_mm: float = 0.5
    clearance_change_threshold_mm: float = 2.0
    max_recursion_depth: int = 6
    max_evaluated_states_per_phase: int = 48
    max_phase_time_s: float = 120.0
    arc_segments: int = 128
    near_tolerance_mm: float = 0.5
    volume_tolerance_mm3: float = 1e-4
    contact_distance_tolerance_mm: float = 1e-4
    position_continuity_tolerance_mm: float = 1e-3
    rotation_continuity_tolerance_deg: float = 1e-3
    angle_continuity_tolerance_deg: float = 1e-3
    allow_end_zone_collision: bool = False
    end_zone_length_mm: float = 0.0
    early_stop_on_blocking_collision: bool = True
    # Use the same *relative to contact* upper travel as PressCycleController.
    # Do not hard-code absolute +40/+50 mm in sampled D6 trajectories.
    initial_clearance_mm: float = DEFAULT_INITIAL_PUNCH_CLEARANCE_MM
    # Only after a proven endpoint collision: bound its onset with adaptive
    # native OCC checks, without claiming uninspected intervals are safe.
    onset_refinement_max_samples: int = 6
    onset_refinement_max_s: float = 30.0
    onset_refinement_min_angle_deg: float = 1.0
    # Experimental read-only component screen. Zero by default to avoid
    # extra OCC calls in ordinary native validation. For manual Windows
    # testing, set SMBS_COMPONENT_SHADOW_SAMPLES=2 before starting a check.
    component_shadow_samples_per_bend: int = 0
    # Opt-in reject-only experiment; disabled for independent full validation.
    component_reject_enabled: bool = False
    component_reject_max_candidates: int = 12
    component_reject_max_s: float = 2.0


@dataclass
class TrajectoryPhaseResult:
    phase_name: str
    active_bend_id: str
    states: List[MotionState3D]
    evidence_status: TrajectoryEvidenceStatus
    continuous_certification_status: TrajectoryEvidenceStatus
    is_safe: bool
    subdivisions_count: int
    peak_recursion_depth: int
    unresolved_intervals: List[Tuple[float, float]]
    discontinuities: List[str]
    solid_reconstructions_count: int
    collision_queries_count: int
    min_punch_clearance_mm: float
    min_die_clearance_mm: float
    max_punch_overlap_volume_mm3: float
    max_die_overlap_volume_mm3: float
    elapsed_ms: float
    diagnostics: List[str]
    total_punch_eval_ms: float = 0.0
    total_die_eval_ms: float = 0.0
    total_cache_lookup_ms: float = 0.0
    solid_cache_hits: int = 0
    solid_cache_misses: int = 0
    die_cache_hits: int = 0
    die_cache_misses: int = 0
    punch_cache_hits: int = 0
    punch_cache_misses: int = 0
    phase_solid_reconstruction_ms: float = 0.0
    is_conditional: bool = False
    max_end_zone_overlap_volume_mm3: float = 0.0
    uninspected_intervals: List[Tuple[float, float]] = field(default_factory=list)
    pose_cache_hits: int = 0
    pose_cache_misses: int = 0
    pair_collision_checks: int = 0
    actual_distance_queries: int = 0
    actual_common_queries: int = 0
    state_evaluation_requests: int = 0
    first_collision_bracket: Optional[Dict[str, Any]] = None


@dataclass
class BendTrajectoryResult:
    bend_id: str
    signed_target_angle_deg: float
    tool_setup_status: SetupQualificationStatus
    evidence_status: TrajectoryEvidenceStatus
    continuous_certification_status: TrajectoryEvidenceStatus
    is_qualified: bool
    phase_results: List[TrajectoryPhaseResult]
    total_states_evaluated: int
    total_subdivisions: int
    total_solid_reconstructions: int
    total_collision_queries: int
    first_collision_state: Optional[MotionState3D]
    fold_state_restored: bool
    elapsed_ms: float
    diagnostics: List[str]
    is_conditional: bool = False
    end_zone_collision_states: List[MotionState3D] = field(default_factory=list)
    max_end_zone_overlap_volume_mm3: float = 0.0
    total_pose_cache_hits: int = 0
    total_punch_cache_hits: int = 0
    total_die_cache_hits: int = 0
    total_solid_cache_hits: int = 0
    total_pair_collision_checks: int = 0
    total_distance_queries: int = 0
    total_common_queries: int = 0
    total_state_evaluation_requests: int = 0
    ram_cycle_positions_mm: Dict[str, float] = field(default_factory=dict)
    total_machine_pose_reuse_hits: int = 0
    total_machine_pose_reuse_misses: int = 0


class FoldStateRestorationError(RuntimeError):
    """The shared FoldState could not be restored; further reuse is unsafe."""


def restore_fold_state_snapshot(fold_state: Any, angles: Dict[str, float], transforms: Dict[str, Any]) -> None:
    """Restore immutable transform snapshots; fail closed if assignment or verification fails.

    RigidTransform is a frozen dataclass. Dictionaries are copied so later pose
    evaluations cannot mutate the snapshot mappings.
    """
    try:
        fold_state.angles = dict(angles)
        fold_state.transforms = dict(transforms)
        if fold_state.angles != angles or fold_state.transforms != transforms:
            raise ValueError("restored FoldState differs from saved snapshot")
    except Exception as exc:
        raise FoldStateRestorationError("FOLD_STATE_RESTORATION_FAILED: shared state cannot be reused") from exc


def compute_rotation_distance_deg(r1: Matrix3x3, r2: Matrix3x3) -> float:
    """Geodesic distance in SO(3) between two 3x3 orthonormal rotation matrices."""
    # tr(R1^T * R2) = sum_{i,j} r1[i][j] * r2[i][j]
    tr = sum(r1[i][j] * r2[i][j] for i in range(3) for j in range(3))
    cos_val = 0.5 * (tr - 1.0)
    cos_val = max(-1.0, min(1.0, cos_val))
    return math.degrees(math.acos(cos_val))


def check_motion_continuity(
    prev_state: MotionState3D,
    curr_state: MotionState3D,
    config: AdaptiveSamplingConfig,
) -> Tuple[bool, str]:
    """Verify rigid placement (translation & rotation), ram, active & non-active angles."""
    # 1. Translation jump
    dx = curr_state.workpiece_placement.translation[0] - prev_state.workpiece_placement.translation[0]
    dy = curr_state.workpiece_placement.translation[1] - prev_state.workpiece_placement.translation[1]
    dz = curr_state.workpiece_placement.translation[2] - prev_state.workpiece_placement.translation[2]
    trans_dist = math.sqrt(dx * dx + dy * dy + dz * dz)

    # 2. Rotation jump
    rot_dist_deg = compute_rotation_distance_deg(
        prev_state.workpiece_placement.rotation,
        curr_state.workpiece_placement.rotation,
    )

    # 3. Ram jump
    d_ram = abs(curr_state.ram_z_mm - prev_state.ram_z_mm)

    # 4. Active bend angle jump
    d_active_angle = abs(curr_state.signed_angle_deg - prev_state.signed_angle_deg)

    # 5. Non-active completed angles jump
    non_active_jump = 0.0
    all_bends = set(prev_state.completed_angles.keys()) | set(curr_state.completed_angles.keys())
    for b in all_bends:
        a_prev = prev_state.completed_angles.get(b, 0.0)
        a_curr = curr_state.completed_angles.get(b, 0.0)
        non_active_jump = max(non_active_jump, abs(a_curr - a_prev))

    # Phase boundary transition: poses and angles must match within tight numerical tolerance
    if prev_state.phase != curr_state.phase:
        if trans_dist > config.position_continuity_tolerance_mm:
            return False, f"PHASE_BOUNDARY_TRANSLATION_JUMP: {prev_state.phase}->{curr_state.phase} jump={trans_dist:.6f}mm > {config.position_continuity_tolerance_mm}mm"
        if rot_dist_deg > config.rotation_continuity_tolerance_deg:
            return False, f"PHASE_BOUNDARY_ROTATION_JUMP: {prev_state.phase}->{curr_state.phase} rot_jump={rot_dist_deg:.6f}deg > {config.rotation_continuity_tolerance_deg}deg"
        if d_ram > config.position_continuity_tolerance_mm:
            return False, f"PHASE_BOUNDARY_RAM_JUMP: {prev_state.phase}->{curr_state.phase} ram_jump={d_ram:.6f}mm > {config.position_continuity_tolerance_mm}mm"
        if d_active_angle > config.angle_continuity_tolerance_deg:
            return False, f"PHASE_BOUNDARY_ACTIVE_ANGLE_JUMP: {prev_state.phase}->{curr_state.phase} angle_jump={d_active_angle:.6f}deg > {config.angle_continuity_tolerance_deg}deg"
        if non_active_jump > config.angle_continuity_tolerance_deg:
            return False, f"PHASE_BOUNDARY_NON_ACTIVE_ANGLE_JUMP: {prev_state.phase}->{curr_state.phase} non_active_jump={non_active_jump:.6f}deg > {config.angle_continuity_tolerance_deg}deg"

    return True, "CONTINUOUS"


def _panel_build_geometry_signature(panel_build: Any) -> Tuple[Any, ...]:
    """Deterministic hashable signature of panel geometry in panel_build.
    
    Includes generation tokens, geom versions, face vertex coordinates,
    center of mass, and surface area. Distinguishes shapes even when bounding box,
    vertex count, and surface area are identical.
    """
    if panel_build is None:
        return ()
    gen_token = getattr(panel_build, "generation_token", getattr(panel_build, "geom_version", None))
    panels = getattr(panel_build, "panels", None)
    faces = getattr(panel_build, "panel_faces", None)
    if not panels or not faces:
        return (gen_token, id(panel_build))
    sig = [gen_token]
    for p, f in zip(panels, faces):
        p_id = getattr(p, "panel_id", str(p))
        v_coords = []
        if hasattr(f, "Vertexes"):
            for v in f.Vertexes:
                v_coords.append((round(float(v.X), 5), round(float(v.Y), 5), round(float(v.Z), 5)))
        v_coords_tuple = tuple(sorted(v_coords))
        com = getattr(f, "CenterOfMass", None)
        com_tuple = (round(float(com.x), 5), round(float(com.y), 5), round(float(com.z), 5)) if com is not None else ()
        area = round(float(getattr(f, "Area", 0.0)), 6)
        sig.append((p_id, len(v_coords), area, com_tuple, v_coords_tuple))
    return tuple(sig)


def _tool_shape_geometry_discriminator(shape: Any) -> Tuple[Any, ...]:
    """Capture full geometry properties beyond volume & CoM to guarantee distinct signatures."""
    if shape is None:
        return ()
    try:
        vol = round(float(getattr(shape, "Volume", 0.0)), 6)
        area = round(float(getattr(shape, "Area", 0.0)), 6)
        bb = getattr(shape, "BoundBox", None)
        bb_tuple = (
            round(float(bb.XMin), 5), round(float(bb.XMax), 5),
            round(float(bb.YMin), 5), round(float(bb.YMax), 5),
            round(float(bb.ZMin), 5), round(float(bb.ZMax), 5),
        ) if bb is not None else ()
        com = getattr(shape, "CenterOfMass", None)
        com_tuple = (round(float(com.x), 5), round(float(com.y), 5), round(float(com.z), 5)) if com is not None else ()
        v_count = len(getattr(shape, "Vertexes", ()))
        f_count = len(getattr(shape, "Faces", ()))
        e_count = len(getattr(shape, "Edges", ()))
        return (vol, area, bb_tuple, com_tuple, v_count, f_count, e_count)
    except Exception:
        return ()


def _die_geometry_signature(die: LoadedTool) -> Tuple[Any, ...]:
    """Deterministic hashable signature of die tool geometry and placement."""
    if die is None:
        return ()
    die_id = getattr(die, "tool_id", getattr(die, "name", ""))
    gen_token = getattr(die, "generation_token", getattr(die, "geom_version", None))
    v_open = float(getattr(die, "v_opening_mm", 0.0))
    eff_v = float(getattr(die, "effective_v_opening_mm", v_open))
    radius = float(getattr(die, "shoulder_radius_mm", 0.0))
    height = float(getattr(die, "height_mm", 0.0))
    length = float(getattr(die, "length_mm", 0.0))
    is_virt = bool(getattr(die, "is_virtual_length", False))
    src_len = float(getattr(die, "source_length_mm", length))
    span_xm = tuple(float(x) for x in getattr(die, "span_xm", (-0.5 * length, 0.5 * length)))
    m_shape = getattr(die, "machine_shape", None)
    shape_disc = _tool_shape_geometry_discriminator(m_shape)
    return (die_id, gen_token, v_open, eff_v, radius, height, length, is_virt, src_len, span_xm, shape_disc)


def _punch_geometry_signature(punch: LoadedTool) -> Tuple[Any, ...]:
    """Deterministic hashable signature of punch tool geometry and length."""
    if punch is None:
        return ()
    p_id = getattr(punch, "tool_id", getattr(punch, "name", ""))
    gen_token = getattr(punch, "generation_token", getattr(punch, "geom_version", None))
    tip_radius = float(getattr(punch, "tip_radius_mm", 0.0))
    inc_angle = float(getattr(punch, "included_angle_deg", 0.0))
    height = float(getattr(punch, "height_mm", 0.0))
    length = float(getattr(punch, "length_mm", 0.0))
    is_virt = bool(getattr(punch, "is_virtual_length", False))
    src_len = float(getattr(punch, "source_length_mm", length))
    span_xm = tuple(float(x) for x in getattr(punch, "span_xm", (-0.5 * length, 0.5 * length)))
    m_shape = getattr(punch, "machine_shape", None)
    shape_disc = _tool_shape_geometry_discriminator(m_shape)
    return (p_id, gen_token, tip_radius, inc_angle, height, length, is_virt, src_len, span_xm, shape_disc)


def is_blocking_collision(st: Optional[MotionState3D]) -> bool:
    """Return True iff state has a confirmed forbidden physical collision.

    Strict invariants:
    - Safe or conditionally safe states are NOT blocking collisions.
    - CONDITIONAL (e.g. allowed end-zone) does NOT stop checking other states.
    - UNKNOWN, UNVERIFIED, INVALID_GEOMETRY are NOT substituted by collision.
    - Only confirmed PENETRATION with positive volume in central punch zone or die.
    """
    if st is None:
        return False
    if getattr(st, "is_safe", True):
        return False
    if getattr(st, "is_conditional_safe", False):
        return False
    rep = getattr(st, "collision_report", None)
    if rep is None:
        return False
    rep_status = getattr(rep, "status", None)
    rep_status_val = getattr(rep_status, "value", str(rep_status))
    if rep_status_val in ("INVALID_GEOMETRY", "UNVERIFIED", "UNKNOWN", "MISSING_EVIDENCE"):
        return False
    if rep_status in (CollisionClass.INVALID_GEOMETRY, getattr(CollisionClass, "UNVERIFIED", None)):
        return False

    p_res = getattr(rep, "punch_result", None)
    if p_res is not None:
        p_status = getattr(p_res, "status", None)
        p_status_val = getattr(p_status, "value", str(p_status))
        if p_status_val == "PENETRATION" or p_status == CollisionClass.PENETRATION:
            p_vol = float(getattr(p_res, "overlap_volume_mm3", 0.0) or 0.0)
            if p_vol > 0.0:
                ez = getattr(st, "punch_end_zone_analysis", None)
                if ez is not None:
                    ez_class = getattr(getattr(ez, "classification", None), "value", None)
                    if ez_class == "END_ZONE_ONLY" and getattr(st, "is_conditional_safe", False):
                        return False
                return True

    d_res = getattr(rep, "die_result", None)
    if d_res is not None:
        d_status = getattr(d_res, "status", None)
        d_status_val = getattr(d_status, "value", str(d_status))
        if d_status_val == "PENETRATION" or d_status == CollisionClass.PENETRATION:
            d_vol = float(getattr(d_res, "overlap_volume_mm3", 0.0) or 0.0)
            if d_vol > 0.0:
                return True

    return False


class AdaptiveIntervalOutcome(int):
    """Subclass of int returned by validate_adaptive_interval.

    Carries the peak recursion depth (as an int) along with the early stop flag
    and the first blocking collision state encountered during recursion.
    """
    stopped_on_collision: bool
    first_blocking_state: Optional[MotionState3D]

    def __new__(
        cls,
        depth: int,
        stopped_on_collision: bool = False,
        first_blocking_state: Optional[MotionState3D] = None,
    ):
        obj = super().__new__(cls, depth)
        obj.stopped_on_collision = bool(stopped_on_collision)
        obj.first_blocking_state = first_blocking_state
        return obj


def _bounded_full_sheet_cache_put(
    cache: OrderedDict, key: tuple, value: Any, limit: int,
) -> None:
    """Bound only RAM retention; eviction changes no collision verdict."""
    if limit < 1:
        raise ValueError("full-sheet cache limit must be positive")
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > limit:
        cache.popitem(last=False)


class AdaptiveTrajectoryValidator3D:
    """Adaptive 3D motion collision validator for press brake cycles."""

    def __init__(
        self,
        fold_state: Any,
        panel_build: Any,
        punch_base: LoadedTool,
        die: LoadedTool,
        config: Optional[AdaptiveSamplingConfig] = None,
        desired_midpoint_xm: float = 0.0,
        reverse_axis: bool = False,
        end_zone_policy: Optional[EndZoneCollisionPolicy] = None,
        classify_end_zone_length_mm: Optional[float] = None,
        sequence_geometry_cache: Any = None,
    ):
        self.fold_state = fold_state
        self.panel_build = panel_build
        self.punch_base = punch_base
        self.die = die
        self.config = config or AdaptiveSamplingConfig()
        self.desired_midpoint_xm = float(desired_midpoint_xm)
        self.reverse_axis = bool(reverse_axis)
        self.end_zone_policy = end_zone_policy or EndZoneCollisionPolicy(
            allow_end_zone_collision=bool(getattr(self.config, "allow_end_zone_collision", False)),
            end_zone_length_mm=float(getattr(self.config, "end_zone_length_mm", 0.0)),
        )
        self.classify_end_zone_length_mm = (
            float(classify_end_zone_length_mm)
            if classify_end_zone_length_mm is not None
            else (
                self.end_zone_policy.end_zone_length_mm
                if self.end_zone_policy.end_zone_length_mm > 0.0
                else None
            )
        )

        self.effective_v = getattr(die, "effective_v_opening_mm", die.v_opening_mm) or die.v_opening_mm

        # Performance & accounting counters
        self.solid_reconstruction_time_ms: float = 0.0
        self.placement_calc_time_ms: float = 0.0
        self.broad_phase_time_ms: float = 0.0
        self.narrow_phase_time_ms: float = 0.0
        self.state_evaluation_requests: int = 0
        self.total_states_evaluated: int = 0
        self.total_subdivisions: int = 0
        self.total_solid_reconstructions: int = 0
        self.total_collision_queries: int = 0
        self.solid_cache_hits: int = 0
        self.solid_cache_misses: int = 0
        self.die_cache_hits: int = 0
        self.die_cache_misses: int = 0
        self.punch_cache_hits: int = 0
        self.punch_cache_misses: int = 0
        self.pose_cache_hits: int = 0
        self.pose_cache_misses: int = 0
        self.pair_collision_checks: int = 0
        self.actual_distance_queries: int = 0
        self.actual_common_queries: int = 0
        self.total_cache_lookup_ms: float = 0.0
        # Keep expensive joined Part.Shapes available for reused poses
        # without retaining an unbounded set of large OCC solids in RAM.
        # The native punch clearance oracle may start with exact Common
        # after a preceding sampled pose had actual punch penetration.
        # Positive Common proves the next state penetrating without the
        # expensive redundant boundary distToShape. A non-positive Common
        # falls back to the previous exact-distance oracle.
        self._prefer_punch_overlap_next: bool = False
        # This flag changes diagnostics only, not any collision verdict.
        try:
            requested_shadow = int(os.environ.get(
                "SMBS_COMPONENT_SHADOW_SAMPLES",
                str(getattr(self.config, "component_shadow_samples_per_bend", 0)),
            ))
        except (TypeError, ValueError):
            requested_shadow = 0
        self._component_shadow_limit = max(0, min(8, requested_shadow))
        self._component_shadow_bending_seen = 0
        self._component_shadow_blocker_seen = 0

        # Explicit per-Manual-run exact-BRep reuse; never global. The
        # default independent validator retains its previous local caches.
        # Tool-dependent collision results remain strictly per-validator.
        self._component_reject_cache = OrderedDict()
        # Retain only ONE machine-frame sheet whose exact source BRep object
        # and rigid placement are unchanged across ram-only poses. Disabled
        # by default until independently measured with native OCC.
        self._machine_pose_cache = None
        self.machine_pose_reuse_hits = 0
        self.machine_pose_reuse_misses = 0
        self._sequence_geometry_cache = sequence_geometry_cache
        if sequence_geometry_cache is None:
            self._solid_cache: OrderedDict[Tuple[Any, ...], Any] = OrderedDict()
            self._max_cached_full_sheets: int = 64
            self._panel_prism_cache: Dict[str, Any] = {}
            self._zone_shape_cache: Dict[Tuple[Any, ...], Any] = {}
            self._panel_prism_cache_identity: Any = None
        else:
            self._solid_cache = sequence_geometry_cache.full_sheets
            self._max_cached_full_sheets = sequence_geometry_cache.max_full_sheets
            self._panel_prism_cache = sequence_geometry_cache.panel_prisms
            self._zone_shape_cache = sequence_geometry_cache.bend_zones
            self._panel_prism_cache_identity = sequence_geometry_cache.source_identity
        self._die_clearance_cache: Dict[Tuple[Any, ...], Any] = {}
        self._punch_clearance_cache: Dict[Tuple[Any, ...], Any] = {}
        self._pose_collision_cache: Dict[Tuple[Any, ...], Any] = {}

    def clear_caches(self) -> None:
        """Clear Part Space solid cache, clearance caches, and pose collision cache."""
        if self._sequence_geometry_cache is not None:
            self._sequence_geometry_cache.clear()
        else:
            self._solid_cache.clear()
            self._panel_prism_cache.clear()
            self._zone_shape_cache.clear()
        self._panel_prism_cache_identity = None
        self._component_reject_cache.clear()
        self._machine_pose_cache = None
        self._component_shadow_bending_seen = 0
        self._component_shadow_blocker_seen = 0
        self._die_clearance_cache.clear()
        self._punch_clearance_cache.clear()
        self._pose_collision_cache.clear()

    def clear_solid_cache(self) -> None:
        """Clear caches (backwards compatibility alias)."""
        self.clear_caches()

    def set_die(self, die: LoadedTool) -> None:
        """Update die and invalidate caches."""
        self.die = die
        self.effective_v = getattr(die, "effective_v_opening_mm", die.v_opening_mm) or die.v_opening_mm
        self.clear_caches()

    def set_punch(self, punch: LoadedTool) -> None:
        """Update punch and invalidate caches."""
        self.punch_base = punch
        self.clear_caches()

    def set_config(self, config: AdaptiveSamplingConfig) -> None:
        """Update sampling config and invalidate caches."""
        self.config = config
        try:
            requested_shadow = int(os.environ.get(
                "SMBS_COMPONENT_SHADOW_SAMPLES",
                str(getattr(config, "component_shadow_samples_per_bend", 0)),
            ))
        except (TypeError, ValueError):
            requested_shadow = 0
        self._component_shadow_limit = max(0, min(8, requested_shadow))
        self.end_zone_policy = EndZoneCollisionPolicy(
            allow_end_zone_collision=bool(getattr(config, "allow_end_zone_collision", False)),
            end_zone_length_mm=float(getattr(config, "end_zone_length_mm", 0.0)),
        )
        self.clear_caches()

    def set_end_zone_policy(
        self,
        policy: EndZoneCollisionPolicy,
        classify_end_zone_length_mm: Optional[float] = None,
    ) -> None:
        """Update end-zone collision policy and invalidate caches."""
        self.end_zone_policy = policy
        if classify_end_zone_length_mm is not None:
            self.classify_end_zone_length_mm = float(classify_end_zone_length_mm)
        elif policy.end_zone_length_mm > 0.0:
            self.classify_end_zone_length_mm = float(policy.end_zone_length_mm)
        self.clear_caches()

    def create_state_snapshot(
        self,
        active_bend_id: str,
        phase: str,
        path_u: float,
        signed_angle: float,
        ram_z: float,
        support_z: float,
        prior_angles: Dict[str, float],
    ) -> MotionState3D:
        """Create a MotionState3D with rigid placement and native FreeCAD solid evaluation."""
        import FreeCAD as App

        t_p0 = time.perf_counter()

        # Isolate state: set all completed prior angles plus active angle
        for b_id, ang in prior_angles.items():
            self.fold_state.set_angle(b_id, ang)
        self.fold_state.set_angle(active_bend_id, signed_angle)

        tf = WorkpiecePlacement3D.compute_placement(
            self.fold_state,
            active_bend_id,
            support_plane_zm=support_z,
            align_bisector=True,
            align_bend_arc_center=True,
            desired_midpoint_xm=self.desired_midpoint_xm,
            reverse_axis=self.reverse_axis,
        )
        self.placement_calc_time_ms += (time.perf_counter() - t_p0) * 1000.0

        # Build signatures for cache keys
        angles_tuple = tuple(sorted((b_id, float(a)) for b_id, a in self.fold_state.angles.items()))
        wp_geom_sig = _panel_build_geometry_signature(self.panel_build)
        first_zone = self.fold_state.ordered_bends[0].bend_zone
        panel_prism_identity = (
            id(self.panel_build), wp_geom_sig,
            float(first_zone.thickness_mm) if first_zone is not None else None,
            tuple((id(f), id(p)) for p, f in zip(
                getattr(self.panel_build, "panels", ()),
                getattr(self.panel_build, "panel_faces", ()),
            )),
        )
        if panel_prism_identity != self._panel_prism_cache_identity:
            self._component_reject_cache.clear()
            self._machine_pose_cache = None
            if self._sequence_geometry_cache is not None:
                # A changed source must invalidate every cached BRep,
                # never silently borrow an old model's "clear" geometry.
                self._sequence_geometry_cache.bind_source(
                    panel_prism_identity
                )
            else:
                self._panel_prism_cache.clear()
                self._zone_shape_cache.clear()
            self._panel_prism_cache_identity = panel_prism_identity
        die_geom_sig = _die_geometry_signature(self.die)
        punch_geom_sig = _punch_geometry_signature(self.punch_base)
        tolerances_sig = (
            float(self.config.near_tolerance_mm),
            float(self.config.volume_tolerance_mm3),
            float(self.config.contact_distance_tolerance_mm),
        )
        ez_policy_sig = (
            bool(self.end_zone_policy.allow_end_zone_collision) if self.end_zone_policy else False,
            float(self.end_zone_policy.end_zone_length_mm) if self.end_zone_policy else 0.0,
        )
        cls_ez_len = float(self.classify_end_zone_length_mm) if self.classify_end_zone_length_mm is not None else 0.0

        reject_enabled = os.environ.get(
            "SMBS_COMPONENT_REJECT", "1" if self.config.component_reject_enabled else "0"
        ) == "1"

        # Exact complete pose cache key
        pose_cache_key = (
            reject_enabled,
            self.config.arc_segments,
            active_bend_id,
            float(ram_z),
            float(support_z),
            float(self.desired_midpoint_xm),
            self.reverse_axis,
            angles_tuple,
            punch_geom_sig,
            die_geom_sig,
            wp_geom_sig,
            tolerances_sig,
            cls_ez_len,
            ez_policy_sig,
        )

        t_lookup0 = time.perf_counter()
        cached_pose_res = self._pose_collision_cache.get(pose_cache_key)
        pose_lookup_ms = (time.perf_counter() - t_lookup0) * 1000.0
        if cached_pose_res is not None:
            cache_lookup_ms = pose_lookup_ms
            self.total_cache_lookup_ms += cache_lookup_ms
            self.state_evaluation_requests += 1
            self.total_states_evaluated += 1
            self.total_collision_queries += 1
            self.pose_cache_hits += 1
            col_res = cached_pose_res
            return MotionState3D(
                phase=phase,
                active_bend_id=active_bend_id,
                signed_angle_deg=signed_angle,
                path_u=path_u,
                ram_z_mm=ram_z,
                support_plane_zm=support_z,
                workpiece_placement=tf,
                completed_angles=dict(prior_angles),
                collision_report=col_res,
                is_safe=col_res.is_safe,
                punch_actual_eval_ms=0.0,
                die_actual_eval_ms=0.0,
                cache_lookup_ms=cache_lookup_ms,
                die_cache_hit=True,
                solid_cache_hit=True,
                punch_cache_hit=True,
                punch_eval_ms=0.0,
                die_eval_ms=0.0,
                is_conditional_safe=bool(getattr(col_res, "is_conditional_safe", False)),
                conditional_status=str(getattr(col_res, "conditional_status", col_res.status.value)),
                punch_end_zone_analysis=getattr(col_res, "punch_end_zone_analysis", None),
            )

        self.state_evaluation_requests += 1
        self.total_states_evaluated += 1
        self.total_collision_queries += 1
        self.pose_cache_misses += 1

        # Check solid cache lookup (strictly timed as pure dict lookup)
        solid_cache_key = (self.config.arc_segments, wp_geom_sig, angles_tuple)
        t_solid_lookup0 = time.perf_counter()
        in_solid_cache = solid_cache_key in self._solid_cache
        if in_solid_cache:
            solid = self._solid_cache[solid_cache_key]
            self._solid_cache.move_to_end(solid_cache_key)
        solid_lookup_ms = (time.perf_counter() - t_solid_lookup0) * 1000.0

        # The two-probe experiment prioritizes a representative BENDING
        # sample and the *first real blocking RETRACT* pose. The latter can
        # reuse a cached full sheet and therefore requires a diagnostic-only
        # rebuild of its native components (NOT another multiFuse).
        shadow_results = None

        def run_component_shadow(components):
            nonlocal shadow_results
            from smbs_core.kinematics2d.component_collision_oracle import (
                ComponentVerdict, MachineComponentScreen,
            )
            started = time.perf_counter()
            try:
                matrix = App.Matrix(
                    tf.rotation[0][0], tf.rotation[0][1], tf.rotation[0][2], tf.translation[0],
                    tf.rotation[1][0], tf.rotation[1][1], tf.rotation[1][2], tf.translation[1],
                    tf.rotation[2][0], tf.rotation[2][1], tf.rotation[2][2], tf.translation[2],
                    0.0, 0.0, 0.0, 1.0,
                )
                # Original selected FreeCAD tooling at this exact ram Z.
                preview_punch = set_punch_ram_z(self.punch_base, ram_z).machine_shape
                actual_die = self.die.machine_shape
                named = components.named_shapes()
                shadow_results = {
                    "PUNCH": screen_machine_component_overlap(
                        named, tf, matrix, "PUNCH", preview_punch,
                        volume_tolerance_mm3=self.config.volume_tolerance_mm3,
                        max_exact_candidates=12,
                    ),
                    "DIE": screen_machine_component_overlap(
                        named, tf, matrix, "DIE", actual_die,
                        volume_tolerance_mm3=self.config.volume_tolerance_mm3,
                        max_exact_candidates=12,
                    ),
                }
            except Exception as exc:
                # Only advisory evidence is missing. Never alter old D6.
                error = MachineComponentScreen(
                    ComponentVerdict.FALLBACK_REQUIRED,
                    "observer_exception:" + type(exc).__name__,
                    "PUNCH", 0, 0, 0, 0, 0,
                    (time.perf_counter() - started) * 1000.0,
                )
                shadow_results = {"PUNCH": error}

        def reject_component_blocker(components):
            # Bound BRep retention separately from the joined-sheet cache.
            self._component_reject_cache[solid_cache_key] = components
            self._component_reject_cache.move_to_end(solid_cache_key)
            while len(self._component_reject_cache) > 8:
                self._component_reject_cache.popitem(last=False)
            try:
                matrix = App.Matrix(
                    tf.rotation[0][0], tf.rotation[0][1], tf.rotation[0][2], tf.translation[0],
                    tf.rotation[1][0], tf.rotation[1][1], tf.rotation[1][2], tf.translation[1],
                    tf.rotation[2][0], tf.rotation[2][1], tf.rotation[2][2], tf.translation[2],
                    0.0, 0.0, 0.0, 1.0,
                )
                screen = prove_machine_component_blocker(
                    components.named_shapes(), tf, matrix,
                    set_punch_ram_z(self.punch_base, ram_z).machine_shape,
                    self.die.machine_shape,
                    volume_tolerance_mm3=self.config.volume_tolerance_mm3,
                    end_zone_policy=self.end_zone_policy,
                    max_exact_candidates=self.config.component_reject_max_candidates,
                    max_screen_s=self.config.component_reject_max_s,
                )
            except Exception:
                # Errors in the optional path must preserve the full oracle.
                return
            self.actual_common_queries += screen.evidence["common_calls"]
            self.pair_collision_checks += screen.evidence["common_calls"]
            try:
                from .developer_debug_log import debug_event
                debug_event("NATIVE_COMPONENT_REJECT_SCREEN", bend_id=active_bend_id,
                            phase=phase, path_u=path_u, **screen.evidence)
            except Exception:
                pass
            if screen.report is not None:
                raise ComponentBlockerFound(screen)

        component_observer = None
        bending_budget = (
            max(1, self._component_shadow_limit - 1)
            if self._component_shadow_limit > 0 else 0
        )
        if (
            not reject_enabled
            and not in_solid_cache
            and phase == "BENDING"
            and self._component_shadow_bending_seen < bending_budget
        ):
            self._component_shadow_bending_seen += 1
            component_observer = run_component_shadow

        try:
            if reject_enabled:
                component_observer = reject_component_blocker
                if in_solid_cache:
                    components = self._component_reject_cache.get(solid_cache_key)
                    if components is None:
                        try:
                            components = build_multi_bend_sheet_components(
                                self.fold_state, self.panel_build, segments=self.config.arc_segments,
                                panel_prism_cache=self._panel_prism_cache,
                                zone_shape_cache=self._zone_shape_cache)
                        except Exception:
                            components = None
                    if components is not None:
                        reject_component_blocker(components)
            solid_cache_hit = False
            if in_solid_cache:
                solid_cache_hit = True
                self.solid_cache_hits += 1
            else:
                t_s0 = time.perf_counter()
                solid = build_multi_bend_sheet_solid(
                    self.fold_state,
                    self.panel_build,
                    segments=self.config.arc_segments,
                    panel_prism_cache=self._panel_prism_cache,
                    zone_shape_cache=self._zone_shape_cache,
                    **({"component_observer": component_observer}
                       if component_observer is not None else {}),
                )
                self.solid_reconstruction_time_ms += (time.perf_counter() - t_s0) * 1000.0
                _bounded_full_sheet_cache_put(
                    self._solid_cache, solid_cache_key, solid,
                    self._max_cached_full_sheets,
                )
                self.total_solid_reconstructions += 1
                self.solid_cache_misses += 1

        except ComponentBlockerFound as blocked:
            # Deliberately no full-sheet or pair-result cache entry: the volume
            # is a subset proof, not a complete full-sheet clearance report.
            report = blocked.screen.report
            return MotionState3D(
                phase=phase, active_bend_id=active_bend_id,
                signed_angle_deg=signed_angle, path_u=path_u,
                ram_z_mm=ram_z, support_plane_zm=support_z,
                workpiece_placement=tf, completed_angles=dict(prior_angles),
                collision_report=report, is_safe=False,
                conditional_status="PENETRATION",
                punch_end_zone_analysis=report.punch_end_zone_analysis,
                solid_cache_hit=in_solid_cache,
                punch_actual_eval_ms=report.punch_result.elapsed_ms,
                die_actual_eval_ms=report.die_result.elapsed_ms,
            )

        # Check die and punch clearance caches (strictly timed as pure dict lookups)
        die_cache_key = (
            self.config.arc_segments,
            active_bend_id,
            float(support_z),
            float(self.desired_midpoint_xm),
            self.reverse_axis,
            angles_tuple,
            die_geom_sig,
            wp_geom_sig,
            tolerances_sig,
        )
        punch_cache_key = (
            self.config.arc_segments,
            active_bend_id,
            float(ram_z),
            float(support_z),
            float(self.desired_midpoint_xm),
            self.reverse_axis,
            angles_tuple,
            punch_geom_sig,
            wp_geom_sig,
            tolerances_sig,
            cls_ez_len,
            ez_policy_sig,
        )

        t_tool_lookup0 = time.perf_counter()
        cached_die_res = self._die_clearance_cache.get(die_cache_key)
        cached_punch_res = self._punch_clearance_cache.get(punch_cache_key)
        tool_lookup_ms = (time.perf_counter() - t_tool_lookup0) * 1000.0

        cache_lookup_ms = pose_lookup_ms + solid_lookup_ms + tool_lookup_ms
        self.total_cache_lookup_ms += cache_lookup_ms

        die_cache_hit = cached_die_res is not None
        if die_cache_hit:
            self.die_cache_hits += 1
        else:
            self.die_cache_misses += 1

        punch_cache_hit = cached_punch_res is not None
        if punch_cache_hit:
            self.punch_cache_hits += 1
        else:
            self.punch_cache_misses += 1

        # Build machine shapes only if at least one tool clearance was not cached,
        # or if cached punch penetration requires end-zone re-classification.
        can_skip_shapes = die_cache_hit and punch_cache_hit
        if can_skip_shapes and cached_punch_res is not None and cached_punch_res.status == CollisionClass.PENETRATION:
            ez_active = (
                (self.end_zone_policy is not None and (self.end_zone_policy.allow_end_zone_collision or self.end_zone_policy.end_zone_length_mm != 0.0))
                or (self.classify_end_zone_length_mm is not None and self.classify_end_zone_length_mm != 0.0)
            )
            if ez_active:
                req_ez_len = (
                    self.end_zone_policy.end_zone_length_mm
                    if self.end_zone_policy is not None and (self.end_zone_policy.allow_end_zone_collision or self.end_zone_policy.end_zone_length_mm != 0.0)
                    else float(self.classify_end_zone_length_mm or 0.0)
                )
                c_ez = getattr(cached_punch_res, "end_zone_analysis", None)
                if c_ez is None or c_ez.classification == PunchCollisionZoneClassification.UNCLASSIFIED or not math.isclose(c_ez.end_zone_length_mm, req_ez_len, abs_tol=1e-5):
                    can_skip_shapes = False

        # The existing full-sheet solid cache is keyed by ALL fold angles.
        # Once transformed into machine coordinates, its BRep depends on the
        # exact rigid workpiece placement, not on punch ram Z. In particular,
        # BOTTOM/RETRACT often share a sheet and placement but differ in ram.
        # Reuse is opt-in: the ONE retained shape is never modified here or
        # shared with workers; tool Common and distToShape are read-only.
        # Only exact source shape identity + exact placement equality qualifies.
        machine_pose_reuse = os.environ.get("SMBS_MACHINE_POSE_REUSE", "0") == "1"
        validated_machine_shape = None
        if not can_skip_shapes:
            pose_key = (
                solid_cache_key,
                tuple(tuple(float(v) for v in row) for row in tf.rotation),
                tuple(float(v) for v in tf.translation),
            )
            retained = self._machine_pose_cache if machine_pose_reuse else None
            if (retained is not None and retained[0] is solid.shape
                    and retained[1] == pose_key):
                wp_machine = retained[2]
                validated_machine_shape = (wp_machine, retained[3])
                self.machine_pose_reuse_hits += 1
            else:
                if machine_pose_reuse:
                    self.machine_pose_reuse_misses += 1
                mat_wp = App.Matrix(
                    tf.rotation[0][0], tf.rotation[0][1], tf.rotation[0][2], tf.translation[0],
                    tf.rotation[1][0], tf.rotation[1][1], tf.rotation[1][2], tf.translation[1],
                    tf.rotation[2][0], tf.rotation[2][1], tf.rotation[2][2], tf.translation[2],
                    0.0, 0.0, 0.0, 1.0,
                )
                with measure_native_geometry_region("machine_workpiece_copy"):
                    wp_machine = solid.shape.copy()
                with measure_native_geometry_region("machine_workpiece_transform"):
                    wp_machine.transformShape(mat_wp)

                # Rigid-isometry validity propagation:
                # When the source sheet solid is already certified valid/closed with
                # positive volume (e.g. from NativeMultiBendSheet or check_solid_validity),
                # and mat_wp is a proven Euclidean rigid isometry (det = +1.0, R*R^T = I),
                # OCC is guaranteed not to change solid topology, volume, closure or validity.
                sheet_proof = getattr(solid, "validity_proof", None)
                if (sheet_proof is not None
                        and sheet_proof[0] is solid.shape
                        and sheet_proof[1][0] is True
                        and is_rigid_transform_matrix(mat_wp)):
                    validated_machine_shape = (wp_machine, sheet_proof[1])
                    record_native_geometry_cache_event("workpiece_validity_cache_hit")
                else:
                    record_native_geometry_cache_event("workpiece_validity_cache_miss")
                    if machine_pose_reuse:
                        with measure_native_geometry_region("pair_geometry_sanity"):
                            valid = check_solid_validity(wp_machine, "shape_a")
                        validated_machine_shape = (wp_machine, valid)

                if machine_pose_reuse and validated_machine_shape is not None:
                    self._machine_pose_cache = (
                        (solid.shape, pose_key, wp_machine, validated_machine_shape[1])
                        if validated_machine_shape[1][0] else None
                    )
            with measure_native_geometry_region("native_punch_pose"):
                punch_step = set_punch_ram_z(self.punch_base, ram_z)
            punch_shape = punch_step.machine_shape
            die_shape = self.die.machine_shape
            validated_punch_shape = getattr(punch_step, "validity_proof", None)
            validated_die_shape = getattr(self.die, "validity_proof", None)
            if validated_punch_shape is not None and validated_punch_shape[0] is punch_shape:
                record_native_geometry_cache_event("tool_validity_cache_hit")
            else:
                record_native_geometry_cache_event("tool_validity_cache_miss")
            if validated_die_shape is not None and validated_die_shape[0] is die_shape:
                record_native_geometry_cache_event("tool_validity_cache_hit")
            else:
                record_native_geometry_cache_event("tool_validity_cache_miss")
        else:
            wp_machine = None
            punch_shape = None
            die_shape = None
            validated_punch_shape = None
            validated_die_shape = None

        extra_validate_kwargs = {}
        if validated_machine_shape is not None:
            extra_validate_kwargs["_validated_workpiece_shape"] = validated_machine_shape
        if validated_punch_shape is not None:
            extra_validate_kwargs["_validated_punch_shape"] = validated_punch_shape
        if validated_die_shape is not None:
            extra_validate_kwargs["_validated_die_shape"] = validated_die_shape

        # Full oracle remains mandatory whenever the optional component path
        # has not proved a non-waivable blocker.
        col_res = validate_workpiece_tooling_pose(
            wp_machine,
            punch_shape,
            die_shape,
            near_tolerance_mm=self.config.near_tolerance_mm,
            volume_tolerance_mm3=self.config.volume_tolerance_mm3,
            contact_distance_tolerance_mm=self.config.contact_distance_tolerance_mm,
            cached_die_result=cached_die_res,
            cached_punch_result=cached_punch_res,
            end_zone_policy=self.end_zone_policy,
            classify_end_zone_length_mm=self.classify_end_zone_length_mm,
            prefer_punch_overlap_first=(
                self._prefer_punch_overlap_next and not punch_cache_hit
            ),
            **extra_validate_kwargs,
        )
        # A geometric blocker discovered in RETRACT may share the exact
        # folded-sheet cache key with BOTTOM. Probe it AFTER the canonical
        # full-sheet verdict using only the optional diagnostics path.
        # Rebuilding 21 isolated BReps costs extra in shadow mode, but it
        # allows proof/rejection study at the actual first blocking sample.
        if (
            not reject_enabled
            and shadow_results is None
            and self._component_shadow_limit >= 2
            and self._component_shadow_blocker_seen < 1
            and phase == "RETRACT"
            and col_res.status == CollisionClass.PENETRATION
            and not col_res.is_conditional_safe
        ):
            self._component_shadow_blocker_seen += 1
            rebuild_started = time.perf_counter()
            try:
                blocker_components = build_multi_bend_sheet_components(
                    self.fold_state,
                    self.panel_build,
                    segments=self.config.arc_segments,
                    panel_prism_cache=self._panel_prism_cache,
                    zone_shape_cache=self._zone_shape_cache,
                )
            except Exception as shadow_exc:
                from smbs_core.kinematics2d.component_collision_oracle import (
                    ComponentVerdict, MachineComponentScreen,
                )
                shadow_results = {
                    "PUNCH": MachineComponentScreen(
                        ComponentVerdict.FALLBACK_REQUIRED,
                        "blocker_components_unavailable:" + type(shadow_exc).__name__,
                        "PUNCH", 0, 0, 0, 0, 0, 0.0,
                    ),
                }
            finally:
                record_component_shadow_rebuild(
                    time.perf_counter() - rebuild_started,
                )
            if shadow_results is None:
                run_component_shadow(blocker_components)
            # The physical report was already computed, unchanged.
            # Diagnostics are never allowed to replace it.


        if shadow_results is not None:
            try:
                record_component_shadow_probe(
                    shadow_results, col_res, active_bend_id, phase, path_u,
                )
            except Exception:
                # Even a diagnostic serialization/accounting bug cannot
                # change, suppress or misclassify a physical OCC result.
                pass
        if not punch_cache_hit:
            self._prefer_punch_overlap_next = (
                col_res.punch_result.status == CollisionClass.PENETRATION
                and col_res.punch_result.overlap_volume_mm3
                > self.config.volume_tolerance_mm3
            )
        if cached_die_res is None:
            self._die_clearance_cache[die_cache_key] = col_res.die_result
        if cached_punch_res is None:
            self._punch_clearance_cache[punch_cache_key] = col_res.punch_result
        self._pose_collision_cache[pose_cache_key] = col_res

        # Record actual physical operations performed
        p_pairs = 0 if punch_cache_hit else 1
        d_pairs = 0 if die_cache_hit else 1
        self.pair_collision_checks += (p_pairs + d_pairs)

        p_dist = 0 if punch_cache_hit else getattr(col_res.punch_result, "distance_queries_count", 0)
        d_dist = 0 if die_cache_hit else getattr(col_res.die_result, "distance_queries_count", 0)
        self.actual_distance_queries += (p_dist + d_dist)

        p_comm = 0 if punch_cache_hit else getattr(col_res.punch_result, "common_queries_count", 0)
        d_comm = 0 if die_cache_hit else getattr(col_res.die_result, "common_queries_count", 0)
        self.actual_common_queries += (p_comm + d_comm)

        p_actual_eval_ms = 0.0 if punch_cache_hit else (col_res.punch_result.elapsed_ms if col_res and col_res.punch_result else 0.0)
        d_actual_eval_ms = 0.0 if die_cache_hit else (col_res.die_result.elapsed_ms if col_res and col_res.die_result else 0.0)

        state = MotionState3D(
            phase=phase,
            active_bend_id=active_bend_id,
            signed_angle_deg=signed_angle,
            path_u=path_u,
            ram_z_mm=ram_z,
            support_plane_zm=support_z,
            workpiece_placement=tf,
            completed_angles=dict(prior_angles),
            collision_report=col_res,
            is_safe=col_res.is_safe,
            punch_actual_eval_ms=p_actual_eval_ms,
            die_actual_eval_ms=d_actual_eval_ms,
            cache_lookup_ms=cache_lookup_ms,
            die_cache_hit=die_cache_hit,
            solid_cache_hit=solid_cache_hit,
            punch_cache_hit=punch_cache_hit,
            punch_eval_ms=p_actual_eval_ms,
            die_eval_ms=d_actual_eval_ms,
            is_conditional_safe=bool(getattr(col_res, "is_conditional_safe", False)),
            conditional_status=str(getattr(col_res, "conditional_status", col_res.status.value)),
            punch_end_zone_analysis=getattr(col_res, "punch_end_zone_analysis", None),
        )
        return state

    def validate_adaptive_interval(
        self,
        s_left: MotionState3D,
        s_right: MotionState3D,
        sample_fn: Callable[[float], MotionState3D],
        depth: int,
        out_states: List[MotionState3D],
        unresolved: List[Tuple[float, float]],
        diags: List[str],
        start_time: float,
        uninspected: Optional[List[Tuple[float, float]]] = None,
    ) -> int:
        """Recursively subdivide interval [u_left, u_right] with midpoint evaluation."""
        # 1. Depth exhaustion
        if depth > self.config.max_recursion_depth:
            unresolved.append((s_left.path_u, s_right.path_u))
            diags.append(
                f"MAX_RECURSION_DEPTH_EXCEEDED: u=[{s_left.path_u:.4f}, {s_right.path_u:.4f}] depth={depth}"
            )
            return AdaptiveIntervalOutcome(depth, stopped_on_collision=False, first_blocking_state=None)

        # 2. State budget exhaustion for this phase
        if len(out_states) >= self.config.max_evaluated_states_per_phase:
            unresolved.append((s_left.path_u, s_right.path_u))
            diags.append(
                f"MAX_STATES_BUDGET_REACHED: phase_evaluated={len(out_states)} states"
            )
            return AdaptiveIntervalOutcome(depth, stopped_on_collision=False, first_blocking_state=None)

        # 3. Time budget exhaustion
        if (time.perf_counter() - start_time) > self.config.max_phase_time_s:
            unresolved.append((s_left.path_u, s_right.path_u))
            diags.append(
                f"MAX_TIME_BUDGET_EXCEEDED: elapsed={(time.perf_counter() - start_time):.2f}s"
            )
            return AdaptiveIntervalOutcome(depth, stopped_on_collision=False, first_blocking_state=None)

        # Mandatory midpoint evaluation:
        # A conservative interval check MUST sample the interior midpoint to detect
        # collision peaks between collision-free endpoints.
        self.total_subdivisions += 1
        u_mid = 0.5 * (s_left.path_u + s_right.path_u)
        s_mid = sample_fn(u_mid)
        out_states.append(s_mid)

        # Early stop inside adaptive interval:
        # After first confirmed forbidden collision in search mode, do not continue subdividing
        # to investigate an already rejected transition.
        if getattr(self.config, "early_stop_on_blocking_collision", True) and is_blocking_collision(s_mid):
            uninspected_msg = f"UNINSPECTED_INTERVAL_AFTER_FATAL_COLLISION: u=[{s_left.path_u:.4f}, {s_right.path_u:.4f}], fatal_mid_u={u_mid:.4f}"
            diags.append(uninspected_msg)
            if uninspected is not None:
                uninspected.append((s_left.path_u, s_right.path_u))
            return AdaptiveIntervalOutcome(depth, stopped_on_collision=True, first_blocking_state=s_mid)

        # Check continuity across left and right halves
        ok_l, reason_l = check_motion_continuity(s_left, s_mid, self.config)
        if not ok_l:
            diags.append(f"CONTINUITY_FAULT_LEFT: {reason_l}")
            s_mid = dataclass_replace_discontinuity(s_mid, reason_l)

        ok_r, reason_r = check_motion_continuity(s_mid, s_right, self.config)
        if not ok_r:
            diags.append(f"CONTINUITY_FAULT_RIGHT: {reason_r}")
            s_right = dataclass_replace_discontinuity(s_right, reason_r)

        # Check criteria for further subdivision:
        # 1. Any non-conditional blocking collision at endpoints or midpoint
        def _eff_safe(st: MotionState3D) -> bool:
            return bool(getattr(st, "is_safe", False) or getattr(st, "is_conditional_safe", False))

        clash_detected = (not _eff_safe(s_left)) or (not _eff_safe(s_mid)) or (not _eff_safe(s_right))

        # 2. Angular step exceeds threshold
        angle_delta = max(
            abs(s_mid.signed_angle_deg - s_left.signed_angle_deg),
            abs(s_right.signed_angle_deg - s_mid.signed_angle_deg),
        )
        large_angle_step = angle_delta > self.config.max_bend_angle_step_deg

        # 3. Ram displacement step exceeds threshold
        ram_delta = max(
            abs(s_mid.ram_z_mm - s_left.ram_z_mm),
            abs(s_right.ram_z_mm - s_mid.ram_z_mm),
        )
        large_ram_step = ram_delta > self.config.max_ram_displacement_step_mm

        # Stable, non-penetrating near-contact is a valid SAMPLED state, not
        # on its own a reason to subdivide forever. Earlier D6 recursively
        # refined every interval touching the die, reaching budget/depth even
        # when the gap and contact class were unchanged. Rapid gap changes,
        # contact transitions, excessive motion and actual penetration still
        # force subdivision. No continuous collision certification is implied.
        # 5. Clearance changes rapidly near contact:
        # Note: aabb_gap is only a lower bound on distance (is_lower_bound=True).
        # We must not compute clearance differences using lower bounds.
        p_res_l = s_left.collision_report.punch_result
        p_res_m = s_mid.collision_report.punch_result
        p_res_r = s_right.collision_report.punch_result
        d_res_l = s_left.collision_report.die_result
        d_res_m = s_mid.collision_report.die_result
        d_res_r = s_right.collision_report.die_result

        rapid_clearance_change = False
        p_is_lb = (
            getattr(p_res_l, "is_lower_bound", False)
            or getattr(p_res_m, "is_lower_bound", False)
            or getattr(p_res_r, "is_lower_bound", False)
        )
        if not p_is_lb:
            p_clear_diff = max(
                abs(p_res_l.distance_mm - p_res_m.distance_mm),
                abs(p_res_m.distance_mm - p_res_r.distance_mm),
            )
            min_p_dist = min(p_res_l.distance_mm, p_res_m.distance_mm, p_res_r.distance_mm)
            if p_clear_diff > self.config.clearance_change_threshold_mm and min_p_dist < self.config.min_clearance_margin_mm * 2.0:
                rapid_clearance_change = True

        d_is_lb = (
            getattr(d_res_l, "is_lower_bound", False)
            or getattr(d_res_m, "is_lower_bound", False)
            or getattr(d_res_r, "is_lower_bound", False)
        )
        if not d_is_lb:
            d_clear_diff = max(
                abs(d_res_l.distance_mm - d_res_m.distance_mm),
                abs(d_res_m.distance_mm - d_res_r.distance_mm),
            )
            min_d_dist = min(d_res_l.distance_mm, d_res_m.distance_mm, d_res_r.distance_mm)
            if d_clear_diff > self.config.clearance_change_threshold_mm and min_d_dist < self.config.min_clearance_margin_mm * 2.0:
                rapid_clearance_change = True

        min_p_dist = min(p_res_l.distance_mm, p_res_m.distance_mm, p_res_r.distance_mm)

        # 6. Contact status transition near contact
        def _status_for_subdivision(st: MotionState3D) -> Any:
            if getattr(st, "is_conditional_safe", False):
                return CollisionClass.TOUCHING
            return st.collision_report.status

        st_l_eff = _status_for_subdivision(s_left)
        st_m_eff = _status_for_subdivision(s_mid)
        st_r_eff = _status_for_subdivision(s_right)
        status_change = (
            (st_l_eff != st_m_eff or st_m_eff != st_r_eff)
            and min_p_dist < self.config.min_clearance_margin_mm
        )

        further_subdivision = (
            clash_detected
            or large_angle_step
            or large_ram_step
            or rapid_clearance_change
            or status_change
        )

        if not further_subdivision:
            # Interval converged with verified midpoint
            return AdaptiveIntervalOutcome(depth, stopped_on_collision=False, first_blocking_state=None)

        peak_d = depth
        # Recurse left half [u_left, u_mid]
        outcome_l = self.validate_adaptive_interval(
            s_left, s_mid, sample_fn, depth + 1, out_states, unresolved, diags, start_time, uninspected=uninspected
        )
        peak_d = max(peak_d, int(outcome_l))
        if getattr(outcome_l, "stopped_on_collision", False):
            # Left half stopped early on confirmed blocking collision.
            # Do NOT inspect the right half [u_mid, u_right]!
            if uninspected is not None:
                uninspected.append((s_mid.path_u, s_right.path_u))
            diags.append(
                f"UNINSPECTED_RIGHT_HALF_AFTER_LEFT_COLLISION: u=[{s_mid.path_u:.4f}, {s_right.path_u:.4f}]"
            )
            return AdaptiveIntervalOutcome(
                peak_d,
                stopped_on_collision=True,
                first_blocking_state=outcome_l.first_blocking_state,
            )

        # Recurse right half [u_mid, u_right]
        outcome_r = self.validate_adaptive_interval(
            s_mid, s_right, sample_fn, depth + 1, out_states, unresolved, diags, start_time, uninspected=uninspected
        )
        peak_d = max(peak_d, int(outcome_r))
        if getattr(outcome_r, "stopped_on_collision", False):
            return AdaptiveIntervalOutcome(
                peak_d,
                stopped_on_collision=True,
                first_blocking_state=outcome_r.first_blocking_state,
            )

        return AdaptiveIntervalOutcome(peak_d, stopped_on_collision=False, first_blocking_state=None)

    def validate_phase(
        self,
        phase_name: str,
        active_bend_id: str,
        prior_angles: Dict[str, float],
        sample_generator: Callable[[float], Tuple[float, float, float]],  # u -> (angle, ram_z, supp_z)
    ) -> TrajectoryPhaseResult:
        """Validate an entire motion cycle phase with adaptive interval refinement."""
        t_start = time.perf_counter()
        diags: List[str] = []
        unresolved: List[Tuple[float, float]] = []
        discontinuities: List[str] = []
        uninspected_intervals: List[Tuple[float, float]] = []
        first_collision_bracket = None

        # Local sample cache to avoid redundant evaluations of identical path_u values
        cache: Dict[float, MotionState3D] = {}

        initial_eval_count = self.total_states_evaluated
        initial_recon_count = self.total_solid_reconstructions
        initial_collision_count = self.total_collision_queries
        initial_requests = self.state_evaluation_requests
        initial_pose_hits = self.pose_cache_hits
        initial_pose_misses = self.pose_cache_misses
        initial_pair_checks = self.pair_collision_checks
        initial_dist_queries = self.actual_distance_queries
        initial_comm_queries = self.actual_common_queries
        initial_solid_hits = self.solid_cache_hits
        initial_solid_misses = self.solid_cache_misses
        initial_die_hits = self.die_cache_hits
        initial_die_misses = self.die_cache_misses
        initial_punch_hits = self.punch_cache_hits
        initial_punch_misses = self.punch_cache_misses
        initial_solid_recon_time = self.solid_reconstruction_time_ms

        def sample_state(u: float) -> MotionState3D:
            # Round u to 6 decimals for deterministic cache keys
            u_key = round(float(u), 6)
            if u_key in cache:
                return cache[u_key]
            ang, ram_z, supp_z = sample_generator(u)
            st = self.create_state_snapshot(
                active_bend_id=active_bend_id,
                phase=phase_name,
                path_u=u,
                signed_angle=ang,
                ram_z=ram_z,
                support_z=supp_z,
                prior_angles=prior_angles,
            )
            cache[u_key] = st
            return st

        # 1. Coarse endpoint evaluation
        s0 = sample_state(0.0)
        initial_subdivisions = self.total_subdivisions

        if getattr(self.config, "early_stop_on_blocking_collision", True) and is_blocking_collision(s0):
            diags.append(f"EARLY_STOP_ON_BLOCKING_COLLISION_AT_PHASE_START: u=0.0, phase={phase_name}")
            uninspected_intervals.append((0.0, 1.0))
            dedup_states: List[MotionState3D] = [s0]
            peak_depth = 1
        else:
            s1 = sample_state(1.0)
            phase_states: List[MotionState3D] = [s0]

            if getattr(self.config, "early_stop_on_blocking_collision", True) and is_blocking_collision(s1):
                # A collision at 90 degrees does NOT mean the first contact
                # occurred at 90. Search between the last tested nonblocking
                # point and the first blocking point, preserving full OCC
                # validation, time/recursion limits and epistemic uncertainty.
                phase_states.append(s1)
                low, high = s0, s1
                probes = 0
                refinement_started = time.perf_counter()
                for _ in range(max(0, int(self.config.onset_refinement_max_samples))):
                    if (self.total_states_evaluated - initial_eval_count
                            >= self.config.max_evaluated_states_per_phase):
                        diags.append("ONSET_REFINEMENT_STATE_LIMIT")
                        break
                    if (time.perf_counter() - refinement_started
                            >= self.config.onset_refinement_max_s
                            or time.perf_counter() - t_start
                            >= self.config.max_phase_time_s):
                        diags.append("ONSET_REFINEMENT_TIME_LIMIT")
                        break
                    if abs(high.signed_angle_deg - low.signed_angle_deg) <= (
                            self.config.onset_refinement_min_angle_deg
                    ) and phase_name == "BENDING":
                        break
                    if high.path_u - low.path_u <= 1e-6:
                        break
                    middle = sample_state((low.path_u + high.path_u) * 0.5)
                    phase_states.append(middle)
                    probes += 1
                    if is_blocking_collision(middle):
                        high = middle
                    elif (middle.collision_report is None
                          or middle.collision_report.status in (
                              CollisionClass.INVALID_GEOMETRY,
                              CollisionClass.MISSING_EVIDENCE,
                          )):
                        diags.append("ONSET_REFINEMENT_UNVERIFIED_INTERMEDIATE")
                        break
                    elif getattr(middle, "is_conditional_safe", False) or middle.is_safe:
                        low = middle
                    else:
                        diags.append("ONSET_REFINEMENT_AMBIGUOUS_INTERMEDIATE")
                        break
                first_collision_bracket = {
                    "phase": phase_name,
                    "last_sampled_nonblocking_u": round(low.path_u, 6),
                    "first_sampled_blocking_u": round(high.path_u, 6),
                    "last_sampled_nonblocking_angle_deg": round(
                        low.signed_angle_deg, 6,
                    ),
                    "first_sampled_blocking_angle_deg": round(
                        high.signed_angle_deg, 6,
                    ),
                    "last_nonblocking_ram_z_mm": round(low.ram_z_mm, 6),
                    "first_blocking_ram_z_mm": round(high.ram_z_mm, 6),
                    "refinement_samples": probes,
                    "continuous_interval_certified": False,
                    "nonmonotone_collision_not_excluded": True,
                }
                diags.append(
                    "ONSET_BRACKET_SAMPLED: "
                    "angle=[{:.3f},{:.3f}]deg u=[{:.6f},{:.6f}], "
                    "probes={}".format(
                        low.signed_angle_deg, high.signed_angle_deg,
                        low.path_u, high.path_u, probes,
                    )
                )
                # The rest of the path remains uninspected. Do NOT turn
                # BENDING into a PASS from this diagnostic refinement.
                uninspected_intervals.append((0.0, 1.0))
                peak_depth = 1
            elif s0.path_u < s1.path_u:
                peak_depth = self.validate_adaptive_interval(
                    s0, s1, sample_state, depth=1, out_states=phase_states, unresolved=unresolved, diags=diags, start_time=t_start,
                    uninspected=uninspected_intervals,
                )
                phase_states.append(s1)
            else:
                peak_depth = 1
                phase_states.append(s1)

            # Deduplicate states by path_u and sort
            seen_u = set()
            dedup_states: List[MotionState3D] = []
            for s in phase_states:
                uk = round(float(s.path_u), 6)
                if uk not in seen_u:
                    seen_u.add(uk)
                    dedup_states.append(s)
            dedup_states.sort(key=lambda s: s.path_u)

        phase_subdivisions = self.total_subdivisions - initial_subdivisions
        phase_recons = self.total_solid_reconstructions - initial_recon_count
        phase_colls = self.total_collision_queries - initial_collision_count
        phase_requests = self.state_evaluation_requests - initial_requests
        phase_pose_hits = self.pose_cache_hits - initial_pose_hits
        phase_pose_misses = self.pose_cache_misses - initial_pose_misses
        phase_pair_checks = self.pair_collision_checks - initial_pair_checks
        phase_dist_queries = self.actual_distance_queries - initial_dist_queries
        phase_comm_queries = self.actual_common_queries - initial_comm_queries
        phase_solid_hits = self.solid_cache_hits - initial_solid_hits
        phase_solid_misses = self.solid_cache_misses - initial_solid_misses
        phase_die_hits = self.die_cache_hits - initial_die_hits
        phase_die_misses = self.die_cache_misses - initial_die_misses
        phase_punch_hits = self.punch_cache_hits - initial_punch_hits
        phase_punch_misses = self.punch_cache_misses - initial_punch_misses
        phase_solid_recon_ms = self.solid_reconstruction_time_ms - initial_solid_recon_time

        # Compute clearance / overlap extrema
        min_p_clear = min(s.collision_report.punch_result.distance_mm for s in dedup_states)
        min_d_clear = min(s.collision_report.die_result.distance_mm for s in dedup_states)
        max_p_over = max(s.collision_report.punch_result.overlap_volume_mm3 for s in dedup_states)
        max_d_over = max(s.collision_report.die_result.overlap_volume_mm3 for s in dedup_states)

        # Evaluate safety & evidence status
        has_unclassified_end_zone = any(
            (
                getattr(s, "punch_end_zone_analysis", None) is not None
                and getattr(getattr(s, "punch_end_zone_analysis", None), "classification", None) is not None
                and s.punch_end_zone_analysis.classification.value == "UNCLASSIFIED"
            )
            for s in dedup_states
        )
        has_blocking_penetration = any(
            (not s.is_safe and not getattr(s, "is_conditional_safe", False))
            for s in dedup_states
        )
        has_conditional_end_zone = any(
            (not s.is_safe and getattr(s, "is_conditional_safe", False))
            for s in dedup_states
        )
        has_invalid_geom = any(
            s.collision_report.status == CollisionClass.INVALID_GEOMETRY
            for s in dedup_states
        ) or has_unclassified_end_zone

        for s in dedup_states:
            if s.discontinuity_detected:
                discontinuities.append(s.discontinuity_reason)

        is_conditional = False
        if has_invalid_geom:
            evidence_status = TrajectoryEvidenceStatus.INVALID_GEOMETRY
            is_safe = False
        elif has_blocking_penetration:
            evidence_status = TrajectoryEvidenceStatus.DISCRETE_SAMPLES_REJECTED
            is_safe = False
        elif discontinuities:
            evidence_status = TrajectoryEvidenceStatus.DISCRETE_SAMPLES_REJECTED
            is_safe = False
        elif unresolved:
            evidence_status = TrajectoryEvidenceStatus.INTERVAL_UNRESOLVED
            is_safe = False
        elif has_conditional_end_zone:
            # All samples are either strictly safe or conditionally safe (END_ZONE_ONLY)
            evidence_status = TrajectoryEvidenceStatus.SAMPLED_INTERVAL_CONDITIONAL_END_ZONE
            is_safe = False
            is_conditional = True
        else:
            # All adaptive samples and intervals converged without collision or unresolved gaps
            evidence_status = TrajectoryEvidenceStatus.SAMPLED_INTERVAL_CONVERGED
            is_safe = True

        elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        total_punch_eval_ms = sum(s.punch_actual_eval_ms for s in dedup_states)
        total_die_eval_ms = sum(s.die_actual_eval_ms for s in dedup_states)
        total_cache_lookup_ms = sum(s.cache_lookup_ms for s in dedup_states)
        diags.append(
            f"TIMINGS: phase={elapsed_ms:.1f}ms, actual_punch={total_punch_eval_ms:.1f}ms, "
            f"actual_die={total_die_eval_ms:.1f}ms, solid_build={phase_solid_recon_ms:.1f}ms, "
            f"cache_lookups={total_cache_lookup_ms:.1f}ms, hits=(solid={phase_solid_hits}, die={phase_die_hits}, punch={phase_punch_hits}), "
            f"misses=(solid={phase_solid_misses}, die={phase_die_misses}, punch={phase_punch_misses})"
        )

        return TrajectoryPhaseResult(
            phase_name=phase_name,
            active_bend_id=active_bend_id,
            states=dedup_states,
            evidence_status=evidence_status,
            continuous_certification_status=TrajectoryEvidenceStatus.NOT_CONTINUOUSLY_CERTIFIED,
            is_safe=is_safe,
            subdivisions_count=phase_subdivisions,
            peak_recursion_depth=peak_depth,
            unresolved_intervals=unresolved,
            discontinuities=discontinuities,
            solid_reconstructions_count=phase_recons,
            collision_queries_count=phase_colls,
            min_punch_clearance_mm=min_p_clear,
            min_die_clearance_mm=min_d_clear,
            max_punch_overlap_volume_mm3=max_p_over,
            max_die_overlap_volume_mm3=max_d_over,
            elapsed_ms=elapsed_ms,
            diagnostics=diags,
            total_punch_eval_ms=total_punch_eval_ms,
            total_die_eval_ms=total_die_eval_ms,
            total_cache_lookup_ms=total_cache_lookup_ms,
            solid_cache_hits=phase_solid_hits,
            solid_cache_misses=phase_solid_misses,
            die_cache_hits=phase_die_hits,
            die_cache_misses=phase_die_misses,
            punch_cache_hits=phase_punch_hits,
            punch_cache_misses=phase_punch_misses,
            phase_solid_reconstruction_ms=phase_solid_recon_ms,
            is_conditional=is_conditional,
            uninspected_intervals=uninspected_intervals,
            pose_cache_hits=phase_pose_hits,
            pose_cache_misses=phase_pose_misses,
            pair_collision_checks=phase_pair_checks,
            actual_distance_queries=phase_dist_queries,
            actual_common_queries=phase_comm_queries,
            state_evaluation_requests=phase_requests,
            first_collision_bracket=first_collision_bracket,
        )

    def validate_bend_trajectory(
        self,
        active_bend_id: str,
        prior_completed_angles: Dict[str, float],
    ) -> BendTrajectoryResult:
        """Validate complete press-brake motion trajectory for an active bend operation.

        Deterministically preserves the incoming FoldState, restoring original angles
        and panel transforms via try...finally even upon exception.
        """
        t_start = time.perf_counter()
        diags: List[str] = []
        machine_reuse_hits_before = self.machine_pose_reuse_hits
        machine_reuse_misses_before = self.machine_pose_reuse_misses

        # Deterministic FoldState snapshot: preserve original state
        saved_angles = dict(self.fold_state.angles)
        saved_transforms = dict(self.fold_state.transforms)
        try:
            # Each candidate begins with an explicit flat state. Prior completed
            # angles and the active angle are then applied by create_state_snapshot().
            self.fold_state.reset_all()
            bend = self.fold_state.bends[active_bend_id]
            bz = bend.bend_zone
            p0_cad, p1_cad = bend.axis_xyz
            bend_len = math.dist(p0_cad, p1_cad)
            bend_span_xm = (-0.5 * bend_len, 0.5 * bend_len)

            # 1. Tool setup qualification
            effective_bend_span_xm = (
                bend_span_xm[0] + self.desired_midpoint_xm,
                bend_span_xm[1] + self.desired_midpoint_xm,
            )
            punch_cov = check_tool_coverage(effective_bend_span_xm, self.punch_base.span_xm, self.punch_base.sections)
            die_cov = check_tool_coverage(effective_bend_span_xm, self.die.span_xm, self.die.sections)
            tool_ok, tool_status, tool_diags = qualify_tool_setup(punch_cov, die_cov)
            diags.extend(tool_diags)

            # 2. Coupled air bending kinematics
            coupled = CoupledAirBendingKinematics(
                die_opening_v_mm=self.effective_v,
                die_shoulder_radius_mm=self.die.shoulder_radius_mm,
                die_angle_deg=self.die.included_angle_deg,
                die_v_depth_mm=self.die.v_depth_mm,
                punch_tip_radius_mm=self.punch_base.tip_radius_mm,
                punch_included_angle_deg=self.punch_base.included_angle_deg,
                sheet_thickness_mm=bz.thickness_mm,
                sheet_inner_radius_mm=bz.inner_radius_mm,
                die_top_z_mm=0.0,
            )

            signed_target_deg = float(bend.angle_deg)
            abs_target_deg = abs(signed_target_deg)
            sign = 1.0 if signed_target_deg >= 0.0 else -1.0
            ram_cycle = ram_cycle_positions(
                coupled.contact_z_mm,
                coupled.ram_z_for_angle(abs_target_deg),
                self.config.initial_clearance_mm,
            )

            # Both APPROACH and RETRACT use this SAME configured upper Z
            # as the FreeCAD simulation, relative to actual tool contact.
            def gen_approach(u: float) -> Tuple[float, float, float]:
                upper = ram_cycle["approach_start_z_mm"]
                ram_z = upper + u * (ram_cycle["contact_z_mm"] - upper)
                return (0.0, ram_z, bz.outer_radius_mm)

            # 2. CONTACT: stationary contact pose
            def gen_contact(u: float) -> Tuple[float, float, float]:
                return (0.0, coupled.contact_z_mm, bz.outer_radius_mm)

            # 3. BENDING: ram drives sheet from theta=0 to theta=theta_target
            def gen_bending(u: float) -> Tuple[float, float, float]:
                theta = u * abs_target_deg
                ram_z = coupled.ram_z_for_angle(theta)
                supp_z = -coupled.stroke_for_angle(theta) + bz.outer_radius_mm
                return (sign * theta, ram_z, supp_z)

            # 4. BOTTOM: dwell at full target stroke
            def gen_bottom(u: float) -> Tuple[float, float, float]:
                ram_z = coupled.ram_z_for_angle(abs_target_deg)
                supp_z = -coupled.stroke_for_angle(abs_target_deg) + bz.outer_radius_mm
                return (signed_target_deg, ram_z, supp_z)

            # 5. RETRACT: finish at the same contact-relative upper Z as
            # the GUI controller. Sheet stays at completed signed bend angle.
            def gen_retract(u: float) -> Tuple[float, float, float]:
                bottom = ram_cycle["retract_start_z_mm"]
                ram_z = bottom + u * (ram_cycle["retract_end_z_mm"] - bottom)
                supp_z = -coupled.stroke_for_angle(abs_target_deg) + bz.outer_radius_mm
                return (signed_target_deg, ram_z, supp_z)

            phases_to_run = [
                ("APPROACH", gen_approach),
                ("CONTACT", gen_contact),
                ("BENDING", gen_bending),
                ("BOTTOM", gen_bottom),
                ("RETRACT", gen_retract),
            ]

            phase_results: List[TrajectoryPhaseResult] = []
            first_blocking_collision_state: Optional[MotionState3D] = None
            first_any_collision_state: Optional[MotionState3D] = None
            end_zone_collision_states: List[MotionState3D] = []
            max_end_zone_overlap_volume_mm3: float = 0.0
            prev_phase_last_state: Optional[MotionState3D] = None

            for p_name, p_gen in phases_to_run:
                p_res = self.validate_phase(p_name, active_bend_id, prior_completed_angles, p_gen)

                # Check inter-phase continuity between end of previous phase and start of current phase
                if prev_phase_last_state is not None:
                    cur_first_state = p_res.states[0]
                    ok_cont, reason_cont = check_motion_continuity(
                        prev_phase_last_state, cur_first_state, self.config
                    )
                    if not ok_cont:
                        diags.append(f"INTER_PHASE_DISCONTINUITY: {reason_cont}")
                        p_res.discontinuities.append(reason_cont)
                        p_res.is_safe = False
                        p_res.is_conditional = False
                        p_res.evidence_status = TrajectoryEvidenceStatus.DISCRETE_SAMPLES_REJECTED

                prev_phase_last_state = p_res.states[-1]
                phase_results.append(p_res)

                for s in p_res.states:
                    if not s.is_safe:
                        if first_any_collision_state is None:
                            first_any_collision_state = s
                        if not getattr(s, "is_conditional_safe", False) and first_blocking_collision_state is None:
                            first_blocking_collision_state = s
                        ez = getattr(s, "punch_end_zone_analysis", None)
                        if ez is not None and getattr(ez, "classification", None) is not None:
                            if ez.classification.value == "END_ZONE_ONLY":
                                end_zone_collision_states.append(s)
                                max_end_zone_overlap_volume_mm3 = max(
                                    max_end_zone_overlap_volume_mm3,
                                    float(getattr(ez, "total_overlap_volume_mm3", 0.0)),
                                )

                # Early exit: if a proven blocking collision is already established in this or an earlier
                # phase, subsequent trajectory phases (e.g. expensive BENDING/RETRACT) are physically blocked
                # and do not need to be executed.
                if first_blocking_collision_state is not None:
                    curr_phase_names = [p[0] for p in phases_to_run]
                    curr_idx = curr_phase_names.index(p_name)
                    remaining_phase_names = curr_phase_names[curr_idx + 1:]
                    if remaining_phase_names:
                        diags.append(f"UNINSPECTED_REMAINING_PHASES_AFTER_FATAL_COLLISION: {remaining_phase_names}")
                    diags.append(f"EARLY_TERMINATION_ON_BLOCKING_COLLISION: phase={p_name}")
                    break

            first_collision_state = first_blocking_collision_state or first_any_collision_state

            # Aggregate evidence status
            has_invalid = any(pr.evidence_status == TrajectoryEvidenceStatus.INVALID_GEOMETRY for pr in phase_results)
            has_rejected = any(pr.evidence_status == TrajectoryEvidenceStatus.DISCRETE_SAMPLES_REJECTED for pr in phase_results)
            has_unresolved = any(pr.evidence_status == TrajectoryEvidenceStatus.INTERVAL_UNRESOLVED for pr in phase_results)
            has_conditional = any(pr.evidence_status == TrajectoryEvidenceStatus.SAMPLED_INTERVAL_CONDITIONAL_END_ZONE for pr in phase_results)
            all_converged = (
                len(phase_results) == len(phases_to_run)
                and all(
                    pr.evidence_status in (TrajectoryEvidenceStatus.SAMPLED_INTERVAL_CONVERGED, TrajectoryEvidenceStatus.ADAPTIVE_SAMPLING_CONVERGED)
                    and not getattr(pr, "uninspected_intervals", None)
                    for pr in phase_results
                )
            )
            all_converged_or_conditional = (
                len(phase_results) == len(phases_to_run)
                and all(
                    pr.evidence_status in (
                        TrajectoryEvidenceStatus.SAMPLED_INTERVAL_CONVERGED,
                        TrajectoryEvidenceStatus.ADAPTIVE_SAMPLING_CONVERGED,
                        TrajectoryEvidenceStatus.SAMPLED_INTERVAL_CONDITIONAL_END_ZONE,
                    )
                    and not getattr(pr, "uninspected_intervals", None)
                    for pr in phase_results
                )
            )

            is_conditional = False
            if not tool_ok:
                evidence_status = TrajectoryEvidenceStatus.DISCRETE_SAMPLES_REJECTED
                is_qualified = False
                diags.append(f"Tool setup rejected: {tool_status.value}")
            elif has_invalid:
                evidence_status = TrajectoryEvidenceStatus.INVALID_GEOMETRY
                is_qualified = False
            elif has_rejected:
                evidence_status = TrajectoryEvidenceStatus.DISCRETE_SAMPLES_REJECTED
                is_qualified = False
            elif has_unresolved:
                evidence_status = TrajectoryEvidenceStatus.INTERVAL_UNRESOLVED
                is_qualified = False
            elif all_converged:
                evidence_status = TrajectoryEvidenceStatus.SAMPLED_INTERVAL_CONVERGED
                is_qualified = True
            elif all_converged_or_conditional and has_conditional:
                evidence_status = TrajectoryEvidenceStatus.SAMPLED_INTERVAL_CONDITIONAL_END_ZONE
                is_qualified = False
                is_conditional = True
            else:
                evidence_status = TrajectoryEvidenceStatus.MISSING_EVIDENCE
                is_qualified = False

            total_states = sum(len(pr.states) for pr in phase_results)
            total_subdivs = sum(pr.subdivisions_count for pr in phase_results)
            total_recons = sum(pr.solid_reconstructions_count for pr in phase_results)
            total_colls = sum(pr.collision_queries_count for pr in phase_results)
            total_pose_hits = sum(pr.pose_cache_hits for pr in phase_results)
            total_punch_hits = sum(pr.punch_cache_hits for pr in phase_results)
            total_die_hits = sum(pr.die_cache_hits for pr in phase_results)
            total_solid_hits = sum(pr.solid_cache_hits for pr in phase_results)
            total_pair_checks = sum(pr.pair_collision_checks for pr in phase_results)
            total_dist_queries = sum(pr.actual_distance_queries for pr in phase_results)
            total_comm_queries = sum(pr.actual_common_queries for pr in phase_results)
            total_requests = sum(pr.state_evaluation_requests for pr in phase_results)
            elapsed_ms = (time.perf_counter() - t_start) * 1000.0

            return BendTrajectoryResult(
                bend_id=active_bend_id,
                signed_target_angle_deg=signed_target_deg,
                tool_setup_status=tool_status,
                evidence_status=evidence_status,
                continuous_certification_status=TrajectoryEvidenceStatus.NOT_CONTINUOUSLY_CERTIFIED,
                is_qualified=is_qualified,
                phase_results=phase_results,
                total_states_evaluated=total_states,
                total_subdivisions=total_subdivs,
                total_solid_reconstructions=total_recons,
                total_collision_queries=total_colls,
                first_collision_state=first_collision_state,
                fold_state_restored=True,
                elapsed_ms=elapsed_ms,
                diagnostics=diags,
                is_conditional=is_conditional,
                end_zone_collision_states=end_zone_collision_states,
                max_end_zone_overlap_volume_mm3=max_end_zone_overlap_volume_mm3,
                total_pose_cache_hits=total_pose_hits,
                total_punch_cache_hits=total_punch_hits,
                total_die_cache_hits=total_die_hits,
                total_solid_cache_hits=total_solid_hits,
                total_pair_collision_checks=total_pair_checks,
                total_distance_queries=total_dist_queries,
                total_common_queries=total_comm_queries,
                total_state_evaluation_requests=total_requests,
                ram_cycle_positions_mm=ram_cycle,
                total_machine_pose_reuse_hits=(
                    self.machine_pose_reuse_hits - machine_reuse_hits_before
                ),
                total_machine_pose_reuse_misses=(
                    self.machine_pose_reuse_misses - machine_reuse_misses_before
                ),
            )

        finally:
            # Never swallow restoration errors or return a qualified result
            # with corrupted shared kinematic state.
            restore_fold_state_snapshot(self.fold_state, saved_angles, saved_transforms)

    def validate_transfer_phase(
        self,
        from_bend_id: str,
        to_bend_id: str,
        completed_angles: Dict[str, float],
        transfer_3d: Optional[Transfer3D] = None,
    ) -> TrajectoryPhaseResult:
        """Validate multi-bend transfer / repositioning operation."""
        t_start = time.perf_counter()
        if transfer_3d is None:
            return TrajectoryPhaseResult(
                phase_name="TRANSFER",
                active_bend_id=to_bend_id,
                states=[],
                evidence_status=TrajectoryEvidenceStatus.TRANSFER_PATH_UNDEFINED,
                continuous_certification_status=TrajectoryEvidenceStatus.NOT_CONTINUOUSLY_CERTIFIED,
                is_safe=False,
                subdivisions_count=0,
                peak_recursion_depth=0,
                unresolved_intervals=[],
                discontinuities=["No validated transfer path defined between bends."],
                solid_reconstructions_count=0,
                collision_queries_count=0,
                min_punch_clearance_mm=0.0,
                min_die_clearance_mm=0.0,
                max_punch_overlap_volume_mm3=0.0,
                max_die_overlap_volume_mm3=0.0,
                elapsed_ms=(time.perf_counter() - t_start) * 1000.0,
                diagnostics=["TRANSFER_PATH_UNDEFINED: start and end exist but path trajectory undefined"],
            )

        # If a valid Transfer3D path exists, sample along transfer parameter u in [0, 1]
        import FreeCAD as App

        diags: List[str] = []
        states: List[MotionState3D] = []

        # Isolate completed state
        for b_id, ang in completed_angles.items():
            self.fold_state.set_angle(b_id, ang)

        solid = build_multi_bend_sheet_solid(
            self.fold_state,
            self.panel_build,
            segments=self.config.arc_segments,
        )

        steps = 5  # coarse transfer samples
        punch_step = set_punch_ram_z(self.punch_base, 50.0)  # retracted punch clearance during transfer

        for i in range(steps + 1):
            u = i / float(steps)
            elapsed = u * transfer_3d.duration_s
            tf = transfer_3d.pose_at(elapsed)

            mat_wp = App.Matrix(
                tf.rotation[0][0], tf.rotation[0][1], tf.rotation[0][2], tf.translation[0],
                tf.rotation[1][0], tf.rotation[1][1], tf.rotation[1][2], tf.translation[1],
                tf.rotation[2][0], tf.rotation[2][1], tf.rotation[2][2], tf.translation[2],
                0.0, 0.0, 0.0, 1.0,
            )
            wp_machine = solid.shape.copy()
            wp_machine.transformShape(mat_wp)

            col_res = validate_workpiece_tooling_pose(
                wp_machine,
                punch_step.machine_shape,
                self.die.machine_shape,
                near_tolerance_mm=self.config.near_tolerance_mm,
                volume_tolerance_mm3=self.config.volume_tolerance_mm3,
                contact_distance_tolerance_mm=self.config.contact_distance_tolerance_mm,
            )

            st = MotionState3D(
                phase="TRANSFER",
                active_bend_id=to_bend_id,
                signed_angle_deg=0.0,
                path_u=u,
                ram_z_mm=50.0,
                support_plane_zm=0.0,
                workpiece_placement=tf,
                completed_angles=dict(completed_angles),
                collision_report=col_res,
                is_safe=col_res.is_safe,
            )
            states.append(st)

        is_safe = all(s.is_safe for s in states)
        ev_status = (
            TrajectoryEvidenceStatus.DISCRETE_SAMPLES_SAFE
            if is_safe
            else TrajectoryEvidenceStatus.DISCRETE_SAMPLES_REJECTED
        )

        min_p_clear = min(s.collision_report.punch_result.distance_mm for s in states)
        min_d_clear = min(s.collision_report.die_result.distance_mm for s in states)
        max_p_over = max(s.collision_report.punch_result.overlap_volume_mm3 for s in states)
        max_d_over = max(s.collision_report.die_result.overlap_volume_mm3 for s in states)

        return TrajectoryPhaseResult(
            phase_name="TRANSFER",
            active_bend_id=to_bend_id,
            states=states,
            evidence_status=ev_status,
            continuous_certification_status=TrajectoryEvidenceStatus.NOT_CONTINUOUSLY_CERTIFIED,
            is_safe=is_safe,
            subdivisions_count=0,
            peak_recursion_depth=0,
            unresolved_intervals=[],
            discontinuities=[],
            solid_reconstructions_count=1,
            collision_queries_count=len(states),
            min_punch_clearance_mm=min_p_clear,
            min_die_clearance_mm=min_d_clear,
            max_punch_overlap_volume_mm3=max_p_over,
            max_die_overlap_volume_mm3=max_d_over,
            elapsed_ms=(time.perf_counter() - t_start) * 1000.0,
            diagnostics=diags,
        )


def dataclass_replace_discontinuity(state: MotionState3D, reason: str) -> MotionState3D:
    return MotionState3D(
        phase=state.phase,
        active_bend_id=state.active_bend_id,
        signed_angle_deg=state.signed_angle_deg,
        path_u=state.path_u,
        ram_z_mm=state.ram_z_mm,
        support_plane_zm=state.support_plane_zm,
        workpiece_placement=state.workpiece_placement,
        completed_angles=state.completed_angles,
        collision_report=state.collision_report,
        is_safe=False,
        discontinuity_detected=True,
        discontinuity_reason=reason,
        punch_actual_eval_ms=state.punch_actual_eval_ms,
        die_actual_eval_ms=state.die_actual_eval_ms,
        cache_lookup_ms=state.cache_lookup_ms,
        die_cache_hit=state.die_cache_hit,
        solid_cache_hit=state.solid_cache_hit,
        punch_eval_ms=state.punch_eval_ms,
        die_eval_ms=state.die_eval_ms,
        is_conditional_safe=False,
        conditional_status=state.conditional_status,
        punch_end_zone_analysis=state.punch_end_zone_analysis,
    )
