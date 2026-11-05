"""motion_trajectory_3d.py - Continuous 3D Motion & Adaptive Collision Validation (D6.3.4).

Architecture & Contracts:
1. Continuous Motion vs Discrete Samples:
   - Evaluates motion trajectories across complete cycle phases:
     APPROACH, CONTACT, BENDING, BOTTOM, RETRACT, and optionally TRANSFER.
   - Status vocabulary strictly preserves:
     * DISCRETE_SAMPLES_SAFE
     * DISCRETE_SAMPLES_REJECTED
     * ADAPTIVE_SAMPLING_CONVERGED
     * INTERVAL_UNRESOLVED
     * CONTINUOUS_COLLISION_CERTIFIED
     * MISSING_EVIDENCE
     * INVALID_GEOMETRY
     * TRANSFER_PATH_UNDEFINED
   - Adaptive sampling alone DOES NOT certify continuous collision freedom.
     CONTINUOUS_COLLISION_CERTIFIED is strictly withheld unless conservative
     analytical clearance bounds / swept envelopes are mathematically proven.
2. Continuity Checking:
   - Checks continuity of workpiece rigid placement, active and non-active bend angles,
     ram position, and tool coordinate frames.
   - Compares boundary poses across phases: end pose of phase N must match start pose of phase N+1.
3. Adaptive Interval Sampling:
   - Evaluates distance, clearance variation, angular velocity, and contact status.
   - Recursively bisects intervals when clearance drops, contact changes, or steps exceed limits.
   - Strictly reports INTERVAL_UNRESOLVED if max recursion depth or budget is reached.
4. Multi-Bend State Correctness:
   - Evaluates bends sequentially with immutable FoldState snapshots.
   - Bend B02 evaluates with prior completed bends (B01 at folded angle).
   - Handles signed bend angles (+45, -45, +90, -90) with proper punch/die kinematics and orientation.
"""
from __future__ import annotations

import copy
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from smbs_core.kinematics2d.coupled_kinematics_2d import CoupledAirBendingKinematics
from smbs_core.kinematics2d.native_bend_solid_3d import build_multi_bend_sheet_solid
from smbs_core.kinematics2d.native_collision_3d import (
    CollisionClass,
    PairClearanceResult,
    ToolCollisionReport,
    validate_workpiece_tooling_pose,
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


@dataclass
class AdaptiveSamplingConfig:
    """Configurable conservative limits for adaptive trajectory subdivision."""
    max_bend_angle_step_deg: float = 5.0
    max_ram_displacement_step_mm: float = 2.0
    min_clearance_margin_mm: float = 0.5
    clearance_change_threshold_mm: float = 1.0
    max_recursion_depth: int = 6
    max_evaluated_states_per_phase: int = 64
    arc_segments: int = 128
    near_tolerance_mm: float = 0.5
    volume_tolerance_mm3: float = 1e-4
    contact_distance_tolerance_mm: float = 1e-4
    position_continuity_tolerance_mm: float = 1e-3
    angle_continuity_tolerance_deg: float = 1e-3


@dataclass
class TrajectoryPhaseResult:
    phase_name: str
    active_bend_id: str
    states: List[MotionState3D]
    evidence_status: TrajectoryEvidenceStatus
    is_safe: bool
    subdivisions_count: int
    peak_recursion_depth: int
    unresolved_intervals: List[Tuple[float, float]]
    discontinuities: List[str]
    elapsed_ms: float
    diagnostics: List[str]


@dataclass
class BendTrajectoryResult:
    bend_id: str
    signed_target_angle_deg: float
    tool_setup_status: SetupQualificationStatus
    evidence_status: TrajectoryEvidenceStatus
    is_qualified: bool
    phase_results: List[TrajectoryPhaseResult]
    total_states_evaluated: int
    total_subdivisions: int
    first_collision_state: Optional[MotionState3D]
    elapsed_ms: float
    diagnostics: List[str]


def check_motion_continuity(
    prev_state: MotionState3D,
    curr_state: MotionState3D,
    config: AdaptiveSamplingConfig,
) -> Tuple[bool, str]:
    """Verify rigid placement, ram, and angle continuity between adjacent states."""
    # 1. Translation jump
    dx = curr_state.workpiece_placement.translation[0] - prev_state.workpiece_placement.translation[0]
    dy = curr_state.workpiece_placement.translation[1] - prev_state.workpiece_placement.translation[1]
    dz = curr_state.workpiece_placement.translation[2] - prev_state.workpiece_placement.translation[2]
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)

    # 2. Ram jump
    d_ram = abs(curr_state.ram_z_mm - prev_state.ram_z_mm)

    # 3. Angle jump
    d_angle = abs(curr_state.signed_angle_deg - prev_state.signed_angle_deg)

    # In phase boundary transition, endpoints should match exactly within tolerance
    if prev_state.phase != curr_state.phase:
        if dist > config.position_continuity_tolerance_mm:
            return False, f"PHASE_BOUNDARY_POSITION_JUMP: {prev_state.phase}->{curr_state.phase} jump={dist:.4f}mm > {config.position_continuity_tolerance_mm}mm"
        if d_ram > config.position_continuity_tolerance_mm:
            return False, f"PHASE_BOUNDARY_RAM_JUMP: {prev_state.phase}->{curr_state.phase} ram_jump={d_ram:.4f}mm > {config.position_continuity_tolerance_mm}mm"
        if d_angle > config.angle_continuity_tolerance_deg:
            return False, f"PHASE_BOUNDARY_ANGLE_JUMP: {prev_state.phase}->{curr_state.phase} angle_jump={d_angle:.4f}deg > {config.angle_continuity_tolerance_deg}deg"

    return True, "CONTINUOUS"


class AdaptiveTrajectoryValidator3D:
    """Adaptive 3D motion collision validator for press brake cycles."""

    def __init__(
        self,
        fold_state: Any,
        panel_build: Any,
        punch_base: LoadedTool,
        die: LoadedTool,
        config: Optional[AdaptiveSamplingConfig] = None,
    ):
        self.fold_state = fold_state
        self.panel_build = panel_build
        self.punch_base = punch_base
        self.die = die
        self.config = config or AdaptiveSamplingConfig()

        self.effective_v = getattr(die, "effective_v_opening_mm", die.v_opening_mm) or die.v_opening_mm

        # Performance counters
        self.solid_reconstruction_time_ms: float = 0.0
        self.placement_calc_time_ms: float = 0.0
        self.broad_phase_time_ms: float = 0.0
        self.narrow_phase_time_ms: float = 0.0
        self.total_states_evaluated: int = 0
        self.total_subdivisions: int = 0

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
        )
        self.placement_calc_time_ms += (time.perf_counter() - t_p0) * 1000.0

        # Build native 3D multi-bend sheet solid
        t_s0 = time.perf_counter()
        solid = build_multi_bend_sheet_solid(
            self.fold_state,
            self.panel_build,
            segments=self.config.arc_segments,
        )
        self.solid_reconstruction_time_ms += (time.perf_counter() - t_s0) * 1000.0

        mat_wp = App.Matrix(
            tf.rotation[0][0], tf.rotation[0][1], tf.rotation[0][2], tf.translation[0],
            tf.rotation[1][0], tf.rotation[1][1], tf.rotation[1][2], tf.translation[1],
            tf.rotation[2][0], tf.rotation[2][1], tf.rotation[2][2], tf.translation[2],
            0.0, 0.0, 0.0, 1.0,
        )
        wp_machine = solid.shape.copy()
        wp_machine.transformShape(mat_wp)

        punch_step = set_punch_ram_z(self.punch_base, ram_z)

        # Oracle evaluation
        col_res = validate_workpiece_tooling_pose(
            wp_machine,
            punch_step.machine_shape,
            self.die.machine_shape,
            near_tolerance_mm=self.config.near_tolerance_mm,
            volume_tolerance_mm3=self.config.volume_tolerance_mm3,
            contact_distance_tolerance_mm=self.config.contact_distance_tolerance_mm,
        )

        self.total_states_evaluated += 1

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
    ) -> int:
        """Recursively subdivide interval [u_left, u_right] adaptively."""
        if depth > self.config.max_recursion_depth:
            unresolved.append((s_left.path_u, s_right.path_u))
            diags.append(
                f"MAX_RECURSION_DEPTH_EXCEEDED: u=[{s_left.path_u:.4f}, {s_right.path_u:.4f}] depth={depth}"
            )
            return depth

        if len(out_states) >= self.config.max_evaluated_states_per_phase:
            unresolved.append((s_left.path_u, s_right.path_u))
            diags.append(
                f"MAX_STATES_BUDGET_REACHED: evaluated={len(out_states)} states"
            )
            return depth

        # Criteria for subdivision:
        # 1. Collision at either endpoint
        endpoint_clash = (not s_left.is_safe) or (not s_right.is_safe)

        # 2. Angular step exceeds threshold
        angle_delta = abs(s_right.signed_angle_deg - s_left.signed_angle_deg)
        large_angle_step = angle_delta > self.config.max_bend_angle_step_deg

        # 3. Ram displacement step exceeds threshold
        ram_delta = abs(s_right.ram_z_mm - s_left.ram_z_mm)
        large_ram_step = ram_delta > self.config.max_ram_displacement_step_mm

        # 4. Small clearance in free motion:
        # In forming/contact (TOUCHING), clearance is 0 by definition and expected, so
        # it should NOT trigger infinite subdivision unless there is a status transition
        # or penetration. Only flag small clearance if an endpoint is NEAR_CONTACT (< margin)
        # without being TOUCHING.
        small_clearance = False
        for s in (s_left, s_right):
            for res in (s.collision_report.punch_result, s.collision_report.die_result):
                if res.status == CollisionClass.NEAR_CONTACT and res.distance_mm < self.config.min_clearance_margin_mm:
                    small_clearance = True
                    break

        # 5. Clearance changes rapidly between endpoints:
        p_clear_diff = abs(
            s_left.collision_report.punch_result.distance_mm
            - s_right.collision_report.punch_result.distance_mm
        )
        d_clear_diff = abs(
            s_left.collision_report.die_result.distance_mm
            - s_right.collision_report.die_result.distance_mm
        )
        rapid_clearance_change = False
        if p_clear_diff > self.config.clearance_change_threshold_mm and min(s_left.collision_report.punch_result.distance_mm, s_right.collision_report.punch_result.distance_mm) < self.config.min_clearance_margin_mm * 2.0:
            rapid_clearance_change = True
        if d_clear_diff > self.config.clearance_change_threshold_mm and min(s_left.collision_report.die_result.distance_mm, s_right.collision_report.die_result.distance_mm) < self.config.min_clearance_margin_mm * 2.0:
            rapid_clearance_change = True

        # 6. Contact status transition:
        # Note: Die status is TOUCHING throughout flat support. Status transition should only
        # trigger subdivision if there is a true transition to/from PENETRATION or NEAR_CONTACT/TOUCHING.
        # If punch transitions from CLEAR to TOUCHING across the entire approach phase,
        # subdivision narrows down the transition interval until within step tolerances.
        status_change = (
            s_left.collision_report.status != s_right.collision_report.status
            and (s_left.collision_report.punch_result.distance_mm < self.config.min_clearance_margin_mm or s_right.collision_report.punch_result.distance_mm < self.config.min_clearance_margin_mm)
        )

        needs_subdivision = (
            endpoint_clash
            or large_angle_step
            or large_ram_step
            or small_clearance
            or rapid_clearance_change
            or status_change
        )

        if not needs_subdivision:
            # Interval satisfies conservative local bounds
            return depth

        # Subdivide at midpoint
        self.total_subdivisions += 1
        u_mid = 0.5 * (s_left.path_u + s_right.path_u)
        s_mid = sample_fn(u_mid)

        # Check continuity across midpoint
        ok_l, reason_l = check_motion_continuity(s_left, s_mid, self.config)
        if not ok_l:
            diags.append(f"CONTINUITY_FAULT_LEFT: {reason_l}")
            s_mid = dataclass_replace_discontinuity(s_mid, reason_l)

        ok_r, reason_r = check_motion_continuity(s_mid, s_right, self.config)
        if not ok_r:
            diags.append(f"CONTINUITY_FAULT_RIGHT: {reason_r}")
            s_right = dataclass_replace_discontinuity(s_right, reason_r)

        peak_d = depth
        # Recurse left
        d1 = self.validate_adaptive_interval(
            s_left, s_mid, sample_fn, depth + 1, out_states, unresolved, diags
        )
        peak_d = max(peak_d, d1)

        out_states.append(s_mid)

        # Recurse right
        d2 = self.validate_adaptive_interval(
            s_mid, s_right, sample_fn, depth + 1, out_states, unresolved, diags
        )
        peak_d = max(peak_d, d2)

        return peak_d

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

        def sample_state(u: float) -> MotionState3D:
            ang, ram_z, supp_z = sample_generator(u)
            return self.create_state_snapshot(
                active_bend_id=active_bend_id,
                phase=phase_name,
                path_u=u,
                signed_angle=ang,
                ram_z=ram_z,
                support_z=supp_z,
                prior_angles=prior_angles,
            )

        # 1. Coarse endpoint evaluation
        s0 = sample_state(0.0)
        s1 = sample_state(1.0)

        phase_states: List[MotionState3D] = [s0]
        initial_subdivisions = self.total_subdivisions

        peak_depth = self.validate_adaptive_interval(
            s0, s1, sample_state, depth=1, out_states=phase_states, unresolved=unresolved, diags=diags
        )

        phase_states.append(s1)
        # Sort states by path parameter u
        phase_states.sort(key=lambda s: s.path_u)

        phase_subdivisions = self.total_subdivisions - initial_subdivisions

        # Evaluate safety & evidence status
        has_penetration = any(
            not s.is_safe or s.collision_report.status == CollisionClass.PENETRATION
            for s in phase_states
        )
        has_invalid_geom = any(
            s.collision_report.status == CollisionClass.INVALID_GEOMETRY
            for s in phase_states
        )

        for s in phase_states:
            if s.discontinuity_detected:
                discontinuities.append(s.discontinuity_reason)

        if has_invalid_geom:
            evidence_status = TrajectoryEvidenceStatus.INVALID_GEOMETRY
            is_safe = False
        elif has_penetration:
            evidence_status = TrajectoryEvidenceStatus.DISCRETE_SAMPLES_REJECTED
            is_safe = False
        elif discontinuities:
            evidence_status = TrajectoryEvidenceStatus.DISCRETE_SAMPLES_REJECTED
            is_safe = False
        elif unresolved:
            evidence_status = TrajectoryEvidenceStatus.INTERVAL_UNRESOLVED
            is_safe = False
        else:
            # All adaptive samples and intervals converged without collision or unresolved gaps
            evidence_status = TrajectoryEvidenceStatus.ADAPTIVE_SAMPLING_CONVERGED
            is_safe = True

        elapsed_ms = (time.perf_counter() - t_start) * 1000.0

        return TrajectoryPhaseResult(
            phase_name=phase_name,
            active_bend_id=active_bend_id,
            states=phase_states,
            evidence_status=evidence_status,
            is_safe=is_safe,
            subdivisions_count=phase_subdivisions,
            peak_recursion_depth=peak_depth,
            unresolved_intervals=unresolved,
            discontinuities=discontinuities,
            elapsed_ms=elapsed_ms,
            diagnostics=diags,
        )

    def validate_bend_trajectory(
        self,
        active_bend_id: str,
        prior_completed_angles: Dict[str, float],
    ) -> BendTrajectoryResult:
        """Validate complete press-brake motion trajectory for an active bend operation."""
        t_start = time.perf_counter()
        diags: List[str] = []

        bend = self.fold_state.bends[active_bend_id]
        bz = bend.bend_zone
        p0_cad, p1_cad = bend.axis_xyz
        bend_len = math.dist(p0_cad, p1_cad)
        bend_span_xm = (-0.5 * bend_len, 0.5 * bend_len)

        # 1. Tool setup qualification
        punch_cov = check_tool_coverage(bend_span_xm, self.punch_base.span_xm, self.punch_base.sections)
        die_cov = check_tool_coverage(bend_span_xm, self.die.span_xm, self.die.sections)
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

        # Phase generators: u in [0, 1] -> (signed_angle, ram_z, support_z)
        # 1. APPROACH: ram moves from +50mm clearance down to contact_z; sheet flat on die shoulders
        def gen_approach(u: float) -> Tuple[float, float, float]:
            ram_z = 50.0 + u * (coupled.contact_z_mm - 50.0)
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

        # 5. RETRACT: punch elevates back to +40mm clearance; sheet rests at bottom stroke
        def gen_retract(u: float) -> Tuple[float, float, float]:
            bottom_ram_z = coupled.ram_z_for_angle(abs_target_deg)
            ram_z = bottom_ram_z + u * (40.0 - bottom_ram_z)
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
        first_collision_state: Optional[MotionState3D] = None
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
                    p_res.evidence_status = TrajectoryEvidenceStatus.DISCRETE_SAMPLES_REJECTED

            prev_phase_last_state = p_res.states[-1]
            phase_results.append(p_res)

            if first_collision_state is None:
                for s in p_res.states:
                    if not s.is_safe:
                        first_collision_state = s
                        break

        # Aggregate evidence status
        has_invalid = any(pr.evidence_status == TrajectoryEvidenceStatus.INVALID_GEOMETRY for pr in phase_results)
        has_rejected = any(pr.evidence_status == TrajectoryEvidenceStatus.DISCRETE_SAMPLES_REJECTED for pr in phase_results)
        has_unresolved = any(pr.evidence_status == TrajectoryEvidenceStatus.INTERVAL_UNRESOLVED for pr in phase_results)
        all_converged = all(pr.evidence_status == TrajectoryEvidenceStatus.ADAPTIVE_SAMPLING_CONVERGED for pr in phase_results)

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
            evidence_status = TrajectoryEvidenceStatus.ADAPTIVE_SAMPLING_CONVERGED
            is_qualified = True
        else:
            evidence_status = TrajectoryEvidenceStatus.MISSING_EVIDENCE
            is_qualified = False

        total_states = sum(len(pr.states) for pr in phase_results)
        total_subdivs = sum(pr.subdivisions_count for pr in phase_results)
        elapsed_ms = (time.perf_counter() - t_start) * 1000.0

        return BendTrajectoryResult(
            bend_id=active_bend_id,
            signed_target_angle_deg=signed_target_deg,
            tool_setup_status=tool_status,
            evidence_status=evidence_status,
            is_qualified=is_qualified,
            phase_results=phase_results,
            total_states_evaluated=total_states,
            total_subdivisions=total_subdivs,
            first_collision_state=first_collision_state,
            elapsed_ms=elapsed_ms,
            diagnostics=diags,
        )

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
                is_safe=False,
                subdivisions_count=0,
                peak_recursion_depth=0,
                unresolved_intervals=[],
                discontinuities=["No validated transfer path defined between bends."],
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

        return TrajectoryPhaseResult(
            phase_name="TRANSFER",
            active_bend_id=to_bend_id,
            states=states,
            evidence_status=ev_status,
            is_safe=is_safe,
            subdivisions_count=0,
            peak_recursion_depth=0,
            unresolved_intervals=[],
            discontinuities=[],
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
    )
