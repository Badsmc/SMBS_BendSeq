"""Preliminary candidate screening pipeline for press brake sequence search.

Implements conservative 2-stage pre-filtering before invoking expensive native 3D checks:
- Stage A: Cheap validation of inputs, bend zone geometry, coupled kinematics, tool coverage.
- Stage B: 2.5D multi-phase cross-section polygon preview across APPROACH, BENDING, BOTTOM, RETRACT.

Decision contracts:
- PROVEN_BLOCKING: Strict proven interference (die collision or central punch collision).
  Guaranteed to be physically invalid under identical coordinate and tooling settings.
  Safe to prune without running expensive 3D trajectory calculation.
- CONTINUE_TO_3D: Cross sections clear or boundary contact. Does NOT certify 3D clearance.
  Must proceed to authoritative native 3D check (Stage C).
- UNVERIFIED_NEEDS_3D: Geometry ambiguous, narrow tolerance, or end-zone classification needed.
  Must NOT be pruned; must proceed to native 3D check.

Bounding box intersections never trigger PROVEN_BLOCKING.
Absence of 2D collision is never treated as a 3D PASS.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .contact_oracle_2d import ContactFinding, ContactReport, ContactStatus, check_sheet_tooling_preview
from .coupled_kinematics_2d import CoupledAirBendingKinematics
from .machine_section_3d import compute_machine_cutting_plane_section
from .panel_kinematics import FoldState
from .tooling_25d import ToolingSetup2D
from .workpiece_placement_3d import WorkpiecePlacement3D


class ScreeningDecision(str, Enum):
    PROVEN_BLOCKING = "PROVEN_BLOCKING"
    CONTINUE_TO_3D = "CONTINUE_TO_3D"
    UNVERIFIED_NEEDS_3D = "UNVERIFIED_NEEDS_3D"


@dataclass(frozen=True)
class PreliminaryScreeningReport:
    decision: ScreeningDecision
    stage: str
    reason: str
    sampled_phases: Tuple[str, ...] = ()
    findings_count: int = 0

    @property
    def is_blocking(self) -> bool:
        return self.decision is ScreeningDecision.PROVEN_BLOCKING

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision.value,
            "stage": self.stage,
            "reason": self.reason,
            "sampled_phases": list(self.sampled_phases),
            "findings_count": self.findings_count,
        }


def screen_candidate_stage_a(
    fold_state: FoldState,
    candidate_bend_id: str,
    tooling: ToolingSetup2D,
    *,
    match_active_bend_length: bool = True,
    desired_midpoint_xm: float = 0.0,
) -> PreliminaryScreeningReport:
    """Stage A: Fast geometric feasibility and parameter validity checks."""
    if candidate_bend_id not in getattr(fold_state, "bends", {}):
        return PreliminaryScreeningReport(
            decision=ScreeningDecision.PROVEN_BLOCKING,
            stage="STAGE_A_INPUT",
            reason=f"unknown_bend_id:{candidate_bend_id}",
        )

    bend = fold_state.bends[candidate_bend_id]
    zone = getattr(bend, "bend_zone", None)
    if zone is not None:
        t_mm = getattr(zone, "thickness_mm", 0.0)
        r_mm = getattr(zone, "inner_radius_mm", 0.0)
        if math.isfinite(t_mm) and t_mm <= 0.0:
            return PreliminaryScreeningReport(
                decision=ScreeningDecision.PROVEN_BLOCKING,
                stage="STAGE_A_GEOMETRY",
                reason=f"invalid_sheet_thickness:{t_mm}",
            )
        if math.isfinite(r_mm) and r_mm < 0.0:
            return PreliminaryScreeningReport(
                decision=ScreeningDecision.PROVEN_BLOCKING,
                stage="STAGE_A_GEOMETRY",
                reason=f"invalid_inner_radius:{r_mm}",
            )

    axis = getattr(bend, "axis_xyz", None)
    if axis and len(axis) >= 2:
        bend_len = math.dist(axis[0], axis[1])
        # Tooling span coverage check (when not automatically matching bend length)
        if not match_active_bend_length and hasattr(tooling, "coverage_report"):
            cov_punch = tooling.coverage_report(bend_len, desired_midpoint_xm)
            if getattr(cov_punch, "punch_status", None) == "INSUFFICIENT_PUNCH_LENGTH":
                return PreliminaryScreeningReport(
                    decision=ScreeningDecision.PROVEN_BLOCKING,
                    stage="STAGE_A_TOOLING",
                    reason=f"punch_shorter_than_bend:{cov_punch.punch_span_mm:.1f}<{bend_len:.1f}",
                )
            if getattr(cov_punch, "die_status", None) == "INSUFFICIENT_DIE_LENGTH":
                return PreliminaryScreeningReport(
                    decision=ScreeningDecision.PROVEN_BLOCKING,
                    stage="STAGE_A_TOOLING",
                    reason=f"die_shorter_than_bend:{cov_punch.die_span_mm:.1f}<{bend_len:.1f}",
                )

    return PreliminaryScreeningReport(
        decision=ScreeningDecision.CONTINUE_TO_3D,
        stage="STAGE_A_PASSED",
        reason="stage_a_geometric_inputs_valid",
    )


def screen_candidate_stage_b(
    fold_state: FoldState,
    candidate_bend_id: str,
    tooling: ToolingSetup2D,
    panel_vertices_xy: Dict[str, Sequence[Tuple[float, float]]],
    *,
    desired_midpoint_xm: float = 0.0,
    match_active_bend_length: bool = True,
    allow_end_zone_collision: bool = False,
    end_zone_length_mm: float = 0.0,
    tolerance_mm: float = 0.02,
    clearance_mm: float = 5.0,
    fractions: Tuple[float, ...] = (0.0, 0.5, 1.0),
) -> PreliminaryScreeningReport:
    """Stage B: Multi-phase 2D cross-section preview at machine cutting plane X_m = 0.

    Evaluates:
    - APPROACH: parked punch at upper clearance
    - BENDING: formed angle sampled at fractions u in [0, 1]
    - BOTTOM: full bottom position at target angle
    - RETRACT: retracted punch after completion

    Collision policy:
    - DIE interference is strictly forbidden -> PROVEN_BLOCKING.
    - PUNCH interference in central zone is strictly forbidden -> PROVEN_BLOCKING.
    - PUNCH interference in allowed end-zone -> CONTINUE_TO_3D for exact 3D classification.
    - CLEAR / TOUCH / UNVERIFIED -> CONTINUE_TO_3D (never treated as 3D PASS).
    """
    if not panel_vertices_xy:
        return PreliminaryScreeningReport(
            decision=ScreeningDecision.CONTINUE_TO_3D,
            stage="STAGE_B_PREVIEW",
            reason="panel_vertices_xy_missing_for_section",
        )

    bend = fold_state.bends[candidate_bend_id]
    axis = getattr(bend, "axis_xyz", None)
    bend_len = math.dist(axis[0], axis[1]) if axis else 0.0
    if bend_len <= 1e-6:
        bend_len = 1.0

    # Synchronize tooling virtual length
    if match_active_bend_length and hasattr(tooling, "set_match_active_bend_length"):
        tooling.set_match_active_bend_length(True, bend_len)

    try:
        # Check initial contact section to align tooling
        first_pose = WorkpiecePlacement3D.compute_placement(
            fold_state,
            candidate_bend_id,
            desired_midpoint_xm=float(desired_midpoint_xm),
            support_plane_zm=float(getattr(bend.bend_zone, "outer_radius_mm", 0.0) if bend.bend_zone else 0.0),
            align_bisector=True,
            align_bend_arc_center=True,
        )
        first_sec = compute_machine_cutting_plane_section(
            fold_state,
            panel_vertices_xy,
            first_pose,
            active_bend_id=candidate_bend_id,
            segments=32,
        )
        if not getattr(tooling, "aligned", False):
            tooling.align_to_cross_section(first_sec, clearance_mm=clearance_mm, bend_id=candidate_bend_id)

        coupled = CoupledAirBendingKinematics.from_tooling_and_fold_state(
            tooling, fold_state, candidate_bend_id
        )
        if coupled.tooling_status == "INVALID":
            return PreliminaryScreeningReport(
                decision=ScreeningDecision.PROVEN_BLOCKING,
                stage="STAGE_B_KINEMATICS",
                reason=f"coupled_air_bending_invalid:{candidate_bend_id}",
            )
    except Exception as exc:
        return PreliminaryScreeningReport(
            decision=ScreeningDecision.UNVERIFIED_NEEDS_3D,
            stage="STAGE_B_SETUP",
            reason=f"section_alignment_exception:{exc}",
        )

    target_deg = float(bend.angle_deg)
    sampled_phases: List[str] = []
    total_findings = 0

    # Build sequence of phase probes: (phase_name, frac_angle, ram_z, bisector)
    probes: List[Tuple[str, float, float, bool]] = [
        ("APPROACH", 0.0, coupled.contact_z_mm + clearance_mm, False),
    ]
    for f in sorted(set(fractions)):
        probes.append(("BENDING", f, coupled.ram_z_for_angle(f * target_deg), True))
    probes.append(("BOTTOM", 1.0, coupled.ram_z_for_angle(target_deg), True))
    probes.append(("RETRACT", 1.0, coupled.contact_z_mm + clearance_mm, True))

    outer_r = float(getattr(bend.bend_zone, "outer_radius_mm", 0.0) if bend.bend_zone else 0.0)
    orig_angle = float(fold_state.angles.get(candidate_bend_id, 0.0))

    try:
        # Evaluate each probe
        for phase, frac, ram_z, align_bis in probes:
            sampled_phases.append(phase)
            angle_at_probe = frac * target_deg
            fold_state.set_angle(candidate_bend_id, angle_at_probe)
            if frac == 0.0 or phase == "APPROACH":
                supp_z = outer_r
            else:
                stroke = coupled.stroke_for_angle(abs(angle_at_probe))
                supp_z = outer_r - stroke
            try:
                pose = WorkpiecePlacement3D.compute_placement(
                    fold_state,
                    candidate_bend_id,
                    desired_midpoint_xm=float(desired_midpoint_xm),
                    support_plane_zm=supp_z,
                    align_bisector=align_bis,
                    align_bend_arc_center=True,
                )
                sec = compute_machine_cutting_plane_section(
                    fold_state,
                    panel_vertices_xy,
                    pose,
                    active_bend_id=candidate_bend_id,
                    segments=32,
                )
                tooling.set_punch_ram_z(float(ram_z), stroke_mm=max(0.0, coupled.contact_z_mm - ram_z))
                sec_report = check_sheet_tooling_preview(sec, tooling, tolerance_mm=tolerance_mm)
                total_findings += len(sec_report.findings)

                if sec_report.status is ContactStatus.INTERFERENCE:
                    # Inspect findings to determine if interference is die or punch
                    die_finding = next((f for f in sec_report.findings if f.tool == "DIE" and f.status is ContactStatus.INTERFERENCE), None)
                    if die_finding is not None:
                        # DIE interference is strictly forbidden everywhere
                        return PreliminaryScreeningReport(
                            decision=ScreeningDecision.PROVEN_BLOCKING,
                            stage="STAGE_B_SECTION",
                            reason=f"2d_section_die_collision:{phase}:{die_finding.sheet_component}:{die_finding.reason}",
                            sampled_phases=tuple(sampled_phases),
                            findings_count=total_findings,
                        )

                    punch_finding = next((f for f in sec_report.findings if f.tool == "PUNCH" and f.status is ContactStatus.INTERFERENCE), None)
                    if punch_finding is not None:
                        if not allow_end_zone_collision:
                            return PreliminaryScreeningReport(
                                decision=ScreeningDecision.PROVEN_BLOCKING,
                                stage="STAGE_B_SECTION",
                                reason=f"2d_section_punch_collision:{phase}:{punch_finding.sheet_component}:{punch_finding.reason}",
                                sampled_phases=tuple(sampled_phases),
                                findings_count=total_findings,
                            )
                        # When end_zone_collision is allowed:
                        # At cutting plane X_m = 0, is it strictly in the central zone?
                        # The central zone is [-L/2 + ez, L/2 - ez].
                        # If bend_len >= 2 * end_zone_length_mm, X_m = 0 is strictly in the central zone!
                        if bend_len >= (2.0 * end_zone_length_mm + 1e-4):
                            return PreliminaryScreeningReport(
                                decision=ScreeningDecision.PROVEN_BLOCKING,
                                stage="STAGE_B_SECTION",
                                reason=f"2d_section_central_punch_collision:{phase}:{punch_finding.sheet_component}:{punch_finding.reason}",
                                sampled_phases=tuple(sampled_phases),
                                findings_count=total_findings,
                            )
                        # If bend_len < 2 * end_zone_length_mm, cutting plane X_m = 0 falls in the end zone.
                        # We cannot distinguish end-zone from central in 2D section alone.
                        # Must NOT reject; send to 3D classification!
                        return PreliminaryScreeningReport(
                            decision=ScreeningDecision.CONTINUE_TO_3D,
                            stage="STAGE_B_END_ZONE_AMBIGUITY",
                            reason=f"2d_section_punch_overlap_in_end_zone_span:{phase}",
                            sampled_phases=tuple(sampled_phases),
                            findings_count=total_findings,
                        )
            except Exception as exc:
                # Never prune on section computation failure; forward to exact 3D check
                return PreliminaryScreeningReport(
                    decision=ScreeningDecision.UNVERIFIED_NEEDS_3D,
                    stage="STAGE_B_ERROR",
                    reason=f"section_eval_error:{exc}",
                    sampled_phases=tuple(sampled_phases),
                    findings_count=total_findings,
                )
    finally:
        fold_state.set_angle(candidate_bend_id, orig_angle)

    return PreliminaryScreeningReport(
        decision=ScreeningDecision.CONTINUE_TO_3D,
        stage="STAGE_B_PASSED",
        reason="2d_section_preview_clear_not_3d_certified",
        sampled_phases=tuple(sampled_phases),
        findings_count=total_findings,
    )
