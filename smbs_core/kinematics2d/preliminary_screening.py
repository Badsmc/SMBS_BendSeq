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
from time import perf_counter
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .contact_oracle_2d import (
    ContactFinding, ContactReport, ContactStatus, ContactGeometryError,
    check_sheet_tooling_preview, classify_polygon_contact,
)
from .coupled_kinematics_2d import CoupledAirBendingKinematics
from .machine_section_3d import compute_machine_cutting_plane_section
from .machine_station_planner import plan_machine_stations
from .multi_station_screening import screen_machine_stations_2d
from .panel_kinematics import FoldState
from .tooling_25d import ToolingSetup2D
from .screening_execution_metrics import record_station_preview
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
    station_reports: Tuple[Dict[str, Any], ...] = ()
    contact_statuses: Tuple[str, ...] = ()
    backgauge_checked: bool = False
    timing_ms: Tuple[Tuple[str, float], ...] = ()
    diagnostic_findings: Tuple[Tuple[str, str, str, str, str], ...] = ()
    touch_summary: Tuple[Tuple[str, str, str, str, int], ...] = ()

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
            "station_reports": list(self.station_reports),
            "contact_statuses": list(self.contact_statuses),
            "backgauge_checked": self.backgauge_checked,
            "timing_ms": dict(self.timing_ms),
            "diagnostic_findings": [
                {"phase": p, "tool": t, "component": c, "status": s, "reason": r}
                for p, t, c, s, r in self.diagnostic_findings
            ],
            "touch_summary": [
                {"phase": p, "tool": t, "component": c, "reason": r, "count": n}
                for p, t, c, r, n in self.touch_summary
            ],
        }



def _record_planar_tool_interference(
    cad_checker: Any, section: Dict[str, Any], tooling: ToolingSetup2D,
    finding: ContactFinding, *, bend_id: str, phase: str,
    angle_fraction: float, angle_deg: float, ram_z_mm: float,
    tolerance_mm: float,
) -> None:
    """Capture exact input polygons for a decisive 2D tool intersection.

    This is diagnostics only. Never influence the contact oracle or its
    verdict if tracing is unavailable. The coordinates are in the same
    machine (s,z) frame consumed by classify_polygon_contact, at X_m=0.
    """
    if cad_checker is None or getattr(cad_checker, "_trace_writer", None) is None:
        return

    def _ring(points):
        if points is None:
            return None
        arr = [(float(v[0]), float(v[1])) for v in points]
        if not arr or not all(
            math.isfinite(s) and math.isfinite(z) for s, z in arr
        ):
            return None
        return {
            "vertices_count": len(arr),
            "truncated": len(arr) > 256,
            "vertices_sz_mm": [
                [round(a, 6), round(b, 6)] for a, b in arr[:256]
            ],
            "bbox_sz_mm": [
                round(min(v[0] for v in arr), 6),
                round(min(v[1] for v in arr), 6),
                round(max(v[0] for v in arr), 6),
                round(max(v[1] for v in arr), 6),
            ],
        }

    try:
        component = finding.sheet_component
        match = {
            "active_fixed": section.get("fixed_polygon"),
            "active_moving": section.get("moving_polygon"),
            "active_bend_zone": section.get("zone_polygon"),
        }.get(component)
        if match is None:
            for collection in ("other_panel_polygons", "other_zone_polygons"):
                for label, polygon in section.get(collection, ()):
                    if str(label) == str(component):
                        match = polygon
                        break
                if match is not None:
                    break

        if finding.tool == "PUNCH":
            profile = tooling.punch_polygons_machine(arc_segments=24)
        elif finding.tool == "DIE":
            profile = tooling.die_polygons_machine(arc_segments=24)
        else:
            profile = None

        outer, holes = (profile if profile is not None else (None, ()))
        cad_checker._trace(
            "PLANAR_TOOL_INTERFERENCE",
            bend_id=str(bend_id),
            phase=str(phase),
            angle_fraction=round(float(angle_fraction), 6),
            bend_angle_deg=round(float(angle_deg), 6),
            ram_z_mm=round(float(ram_z_mm), 6),
            station_xm_mm=0.0,
            tolerance_mm=float(tolerance_mm),
            tool=str(finding.tool),
            sheet_component=str(component),
            interference_reason=str(finding.reason),
            sheet_polygon=_ring(match),
            tool_outer_polygon=_ring(outer),
            tool_holes=[
                _ring(hole) for hole in tuple(holes)[:8]
            ],
            geometry_scope="SAMPLED_2D_ONLY_NOT_3D_VERIFIED",
        )
    except Exception as exc:
        # A failed optional diagnostic cannot turn a physical collision
        # into a usable candidate or invalidate the search.
        try:
            cad_checker._trace(
                "PLANAR_TOOL_TRACE_FAILED",
                bend_id=str(bend_id),
                phase=str(phase),
                reason=type(exc).__name__ + ":" + str(exc),
            )
        except Exception:
            pass



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
    allow_retract_warning: bool = False,
    reverse_axis: bool = False,
    tolerance_mm: float = 0.02,
    clearance_mm: float = 5.0,
    fractions: Tuple[float, ...] = (0.0, 0.5, 1.0),
    stroke_fractions: Optional[Tuple[float, ...]] = None,
    backgauge_polygon_machine: Optional[Sequence[Tuple[float, float]]] = None,
    enforce_rear_frame: bool = False,
    backgauge_cad_checker: Any = None,
    backgauge_contact_s_mm: Optional[float] = None,
    backgauge_contact_z_mm: Optional[float] = None,
    backgauge_contact_metadata: Optional[dict] = None,
    backgauge_initial_feed_mm: float = 0.0,
    deadline_monotonic: Optional[float] = None,
    multi_station_enabled: bool = False,
    verified_punch_span_xm: Optional[Tuple[float, float]] = None,
    verified_die_span_xm: Optional[Tuple[float, float]] = None,
) -> PreliminaryScreeningReport:
    """Stage B: Multi-phase 2D cross-section preview at machine cutting plane X_m = 0.

    Evaluates:
    - APPROACH: parked punch at upper clearance
    - BENDING: formed angle sampled at fractions u in [0, 1]
    - BOTTOM: full bottom position at target angle
    - RETRACT: retracted punch after completion

    Isolation:
    - Completely preserves and restores original ToolingSetup2D state (alignment,
      ram Z, virtual lengths, transforms) and FoldState (angles, transforms) in finally.
    - Does NOT rely on tooling.aligned from prior candidate checks; explicitly aligns
      tooling specifically for `candidate_bend_id`.
    - Preliminary 2D section findings require 3D validation (CONTINUE_TO_3D) rather
      than final rejection, ensuring no false blocking rejections.
    """
    if not panel_vertices_xy:
        return PreliminaryScreeningReport(
            decision=ScreeningDecision.CONTINUE_TO_3D,
            stage="STAGE_B_PREVIEW",
            reason="panel_vertices_xy_missing_for_section",
        )

    if candidate_bend_id not in getattr(fold_state, "bends", {}):
        return PreliminaryScreeningReport(
            decision=ScreeningDecision.PROVEN_BLOCKING,
            stage="STAGE_B_INPUT",
            reason=f"unknown_bend_id:{candidate_bend_id}",
        )

    # Save original tooling state
    orig_match_len = getattr(tooling, "match_active_bend_length", False)
    orig_active_len = getattr(tooling, "active_bend_length_mm", 0.0)
    orig_aligned = getattr(tooling, "aligned", False)
    orig_aligned_bend_id = getattr(tooling, "aligned_bend_id", None)
    orig_anchor = getattr(tooling, "alignment_anchor_sz", (0.0, 0.0))
    orig_ram_z = getattr(tooling, "ram_z_mm", 0.0)
    orig_ram_stroke = getattr(tooling, "ram_stroke_mm", 0.0)
    orig_sheet_top = getattr(tooling, "sheet_top_z_mm", 0.0)
    orig_sheet_bot = getattr(tooling, "sheet_bottom_z_mm", 0.0)
    orig_punch_tf = getattr(tooling, "punch_machine_transform", None)
    orig_die_tf = getattr(tooling, "die_machine_transform", None)

    # Save original fold state
    orig_angles = dict(getattr(fold_state, "angles", {}))
    orig_transforms = dict(getattr(fold_state, "transforms", {}))

    try:
        bend = fold_state.bends[candidate_bend_id]
        axis = getattr(bend, "axis_xyz", None)
        bend_len = math.dist(axis[0], axis[1]) if axis else 0.0
        if bend_len <= 1e-6:
            bend_len = 1.0

        # Synchronize tooling virtual length
        if match_active_bend_length and hasattr(tooling, "set_match_active_bend_length"):
            tooling.set_match_active_bend_length(True, bend_len)

        try:
            # Check initial contact section and explicitly align tooling for candidate_bend_id
            first_pose = WorkpiecePlacement3D.compute_placement(
                fold_state,
                candidate_bend_id,
                desired_midpoint_xm=float(desired_midpoint_xm),
                support_plane_zm=float(getattr(bend.bend_zone, "outer_radius_mm", 0.0) if bend.bend_zone else 0.0),
                align_bisector=True,
                align_bend_arc_center=True,
                reverse_axis=reverse_axis,
                rear_panel_vertices_xy=(
                    panel_vertices_xy if (enforce_rear_frame or backgauge_polygon_machine is not None or backgauge_cad_checker is not None)
                    else None
                ),
            )
            first_sec = compute_machine_cutting_plane_section(
                fold_state,
                panel_vertices_xy,
                first_pose,
                active_bend_id=candidate_bend_id,
                segments=32,
            )
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
        station_reports: List[Dict[str, Any]] = []
        contact_statuses: List[str] = []
        diagnostic_findings: List[Tuple[str, str, str, str, str]] = []
        retract_warning_found = False
        touch_counts: Dict[Tuple[str, str, str, str], int] = {}

        def _touch_summary() -> Tuple[Tuple[str, str, str, str, int], ...]:
            return tuple((*key, count) for key, count in sorted(touch_counts.items()))
        timing_ms: Dict[str, float] = {
            'pose_and_section': 0.0, 'tooling_and_contact': 0.0,
            'total_probe': 0.0,
        }

        # Build sequence of phase probes: (phase_name, frac_angle, ram_z, bisector)
        # Backward-compatible default: one endpoint of APPROACH and RETRACT.
        # Section-only mode may sample the whole ram travel, without calling
        # any native 3D collision oracle. These remain DISCRETE samples.
        if stroke_fractions is None:
            probes: List[Tuple[str, float, float, bool]] = [
                ("APPROACH", 0.0, coupled.contact_z_mm + clearance_mm, False),
            ]
        else:
            st = tuple(sorted(set(float(f) for f in stroke_fractions)))
            if (not st or st[0] != 0.0 or st[-1] != 1.0
                    or any(not math.isfinite(f) or f < 0.0 or f > 1.0 for f in st)):
                return PreliminaryScreeningReport(
                    decision=ScreeningDecision.UNVERIFIED_NEEDS_3D,
                    stage="STAGE_B_INVALID_SAMPLING",
                    reason="stroke_fractions_must_include_0_and_1",
                )
            probes = [
                ("APPROACH", 0.0,
                 coupled.contact_z_mm + clearance_mm * (1.0 - f), False)
                for f in st
            ]
        for f in sorted(set(fractions)):
            probes.append(("BENDING", f, coupled.ram_z_for_angle(abs(f * target_deg)), True))
        bottom_ram = coupled.ram_z_for_angle(abs(target_deg))
        probes.append(("BOTTOM", 1.0, bottom_ram, True))
        if stroke_fractions is None:
            probes.append(("RETRACT", 1.0, coupled.contact_z_mm + clearance_mm, True))
        else:
            probes.extend(
                ("RETRACT", 1.0,
                 bottom_ram + f * (coupled.contact_z_mm + clearance_mm - bottom_ram), True)
                for f in st
            )

        outer_r = float(getattr(bend.bend_zone, "outer_radius_mm", 0.0) if bend.bend_zone else 0.0)

        loading_route_unverified = False
        if (backgauge_contact_metadata is not None
                and not backgauge_contact_metadata.get(
                    "selected_datum_is_contact", False
                )):
            # Actual first CAD contact is valid but is NOT the certified
            # selected working datum (Pad.Face6). Preserve geometry and
            # flag setup as unverified; never claim physical approval.
            diagnostic_findings.append((
                "CONTACT_SETUP", "BACKGAUGE_CAD_3D",
                "actual_faces={}".format(
                    backgauge_contact_metadata.get("actual_faces")
                ),
                "UNVERIFIED",
                "selected_working_face_not_first_physical_contact:"
                + str(backgauge_contact_metadata.get("note", "")),
            ))
            contact_statuses.append("UNVERIFIED")
            loading_route_unverified = True
        # INITIAL LOAD: follow the same stationary real CAD stop while a
        # rigid flat/folded sheet feeds from operator left (-s) to the
        # contact target. This is sampled 3D gauge-only evidence; punch/die,
        # clamps and whole-machine swept paths remain separately unverified.
        if backgauge_initial_feed_mm > 0 and backgauge_cad_checker is not None:
            from .workpiece_placement_3d import PlacementTransform3D
            feed_mm = float(backgauge_initial_feed_mm)
            if not math.isfinite(feed_mm):
                raise ValueError("backgauge_initial_feed_mm must be finite")
            initial = WorkpiecePlacement3D.compute_placement(
                fold_state, candidate_bend_id,
                desired_midpoint_xm=float(desired_midpoint_xm),
                support_plane_zm=outer_r,
                align_bisector=False,
                align_bend_arc_center=True,
                reverse_axis=reverse_axis,
                rear_panel_vertices_xy=panel_vertices_xy,
            )
            for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
                if (deadline_monotonic is not None
                        and perf_counter() >= deadline_monotonic):
                    return PreliminaryScreeningReport(
                        decision=ScreeningDecision.UNVERIFIED_NEEDS_3D,
                        stage="STAGE_B_BUDGET",
                        reason="cad_initial_feed_sample_budget_exhausted",
                        sampled_phases=tuple(sampled_phases),
                        contact_statuses=tuple(contact_statuses),
                        backgauge_checked=True,
                    )
                pose_feed = PlacementTransform3D(
                    rotation=initial.rotation,
                    translation=(
                        initial.translation[0],
                        initial.translation[1] + feed_mm * (1.0-fraction),
                        initial.translation[2],
                    ),
                )
                sample = backgauge_cad_checker.check_phase(
                    fold_state, panel_vertices_xy, pose_feed,
                    active_bend_id=candidate_bend_id,
                    contact_s_mm=float(backgauge_contact_s_mm),
                    contact_z_mm=float(backgauge_contact_z_mm),
                    tolerance_mm=tolerance_mm,
                )
                sampled_phases.append("LOAD_TO_GAUGE")
                if sample["status"] == "INTERFERENCE":
                    finding = (
                        "LOAD_TO_GAUGE", "BACKGAUGE_CAD_3D",
                        str(sample["component"]), "INTERFERENCE",
                        (
                            "material_overlap_mm3={:.6f};"
                            "gauge_station_xm_mm={:.3f};"
                            "body_ahead_of_stop_face_mm={:.3f};"
                            "finger_bbox_xyz_mm={};"
                            "panel_bbox_xyz_mm={}"
                        ).format(
                            float(sample["volume_mm3"]),
                            float(sample.get("station_xm_mm", float("nan"))),
                            float(sample.get(
                                "body_protrusion_ahead_of_stop_face_mm",
                                float("nan")
                            )),
                            sample.get("finger_bbox_xyz_mm", []),
                            sample.get("sheet_component_bbox_xyz_mm", []),
                        ),
                    )
                    # This is a genuine OCC overlap at the *assumed*
                    # loading pose, but the starting position is a preview
                    # offset chosen by the user, not a validated loading
                    # configuration. It must remain visible and UNVERIFIED,
                    # but cannot prove that the bend order is impossible.
                    # Continue to the independently sampled machine bend
                    # setup, whose CAD gauge collisions remain blocking.
                    diagnostic_findings.append(finding)
                    contact_statuses.append("UNVERIFIED")
                    loading_route_unverified = True
                    break
                if sample["status"] != "SAMPLED_CLEAR_OR_TOUCH":
                    raise ValueError("CAD load phase returned unknown verdict")

        # Evaluate each probe
        for phase, frac, ram_z, align_bis in probes:
            if deadline_monotonic is not None and perf_counter() >= deadline_monotonic:
                return PreliminaryScreeningReport(
                    decision=ScreeningDecision.UNVERIFIED_NEEDS_3D,
                    stage='STAGE_B_BUDGET', reason='2d_probe_time_budget_reached',
                    sampled_phases=tuple(sampled_phases),
                    findings_count=total_findings,
                    station_reports=tuple(station_reports),
                    contact_statuses=tuple(contact_statuses),
                    backgauge_checked=(backgauge_polygon_machine is not None or backgauge_cad_checker is not None),
                    timing_ms=tuple(timing_ms.items()),
                    diagnostic_findings=tuple(diagnostic_findings),
                    touch_summary=_touch_summary(),
                )
            sample_start = perf_counter()
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
                    reverse_axis=reverse_axis,
                    rear_panel_vertices_xy=(
                        panel_vertices_xy if (enforce_rear_frame or backgauge_polygon_machine is not None or backgauge_cad_checker is not None)
                        else None
                    ),
                )
                sec = compute_machine_cutting_plane_section(
                    fold_state,
                    panel_vertices_xy,
                    pose,
                    active_bend_id=candidate_bend_id,
                    segments=32,
                )
                pose_end = perf_counter()
                tooling.set_punch_ram_z(float(ram_z), stroke_mm=max(0.0, coupled.contact_z_mm - ram_z))
                sec_report = check_sheet_tooling_preview(
                    sec, tooling, tolerance_mm=tolerance_mm,
                    timing_collector=timing_ms,
                    cascade=True,
                )
                # Retain a bounded, useful reason for the actual UNVERIFIED
                # geometry instead of only the aggregate phase status.
                for finding in sec_report.findings:
                    if finding.status is ContactStatus.TOUCH:
                        key = (phase, finding.tool, finding.sheet_component,
                               finding.reason)
                        touch_counts[key] = touch_counts.get(key, 0) + 1
                    # Include decisive interference findings, not just
                    # ambiguity: component-only reason strings previously
                    # hid which tool (PUNCH or DIE) actually collided.
                    if (finding.status in (ContactStatus.UNVERIFIED, ContactStatus.INTERFERENCE)
                            and len(diagnostic_findings) < 32):
                        diagnostic_findings.append((
                            phase, finding.tool, finding.sheet_component,
                            finding.status.value, finding.reason,
                        ))
                contact_end = perf_counter()
                timing_ms['pose_and_section'] += 1000.0 * (pose_end - sample_start)
                timing_ms['tooling_and_contact'] += 1000.0 * (contact_end - pose_end)
                timing_ms['total_probe'] += 1000.0 * (contact_end - sample_start)
                # Opt-in risk-only multi-station screening under the exact same
                # temporary phase pose and aligned tooling. Absence of verified
                # source-CAD spans is UNVERIFIED, never a geometry-free PASS.
                if multi_station_enabled:
                    try:
                        station_plan = plan_machine_stations(
                            fold_state, panel_vertices_xy, pose, candidate_bend_id,
                            verified_punch_span_xm=verified_punch_span_xm,
                            verified_die_span_xm=verified_die_span_xm,
                        )
                        station_result = screen_machine_stations_2d(
                            fold_state, panel_vertices_xy, pose, candidate_bend_id,
                            tooling, station_plan.stations_xm,
                            verified_punch_span_xm=verified_punch_span_xm,
                            verified_die_span_xm=verified_die_span_xm,
                            tolerance_mm=tolerance_mm,
                        )
                        record_station_preview(station_result)
                        station_reports.append({
                            "phase": phase,
                            "angle_deg": angle_at_probe,
                            "plan": station_plan.to_dict(),
                            "preview": station_result.to_dict(),
                        })
                    except Exception as station_exc:
                        station_reports.append({
                            "phase": phase,
                            "angle_deg": angle_at_probe,
                            "preview": {
                                "status": "UNVERIFIED_NEEDS_3D",
                                "reason": "station_preview_error:"
                                          + type(station_exc).__name__ + ":" + str(station_exc),
                            },
                        })
                total_findings += len(sec_report.findings)
                section_status = sec_report.status
                backgauge_reason = ""
                if (backgauge_cad_checker is not None
                        and section_status is not ContactStatus.INTERFERENCE):
                    # Real 3D stop and 3D FoldState sheet solids are checked
                    # at the SAME pose as punch/die; never use the synthetic
                    # gauge rectangle in CAD mode.
                    if backgauge_polygon_machine is not None:
                        raise ValueError("CAD gauge and 2D rectangle are exclusive")
                    if backgauge_contact_s_mm is None or backgauge_contact_z_mm is None:
                        raise ValueError("CAD gauge stop-face position is required")
                    cad_verdict = backgauge_cad_checker.check_phase(
                        fold_state, panel_vertices_xy, pose,
                        active_bend_id=candidate_bend_id,
                        contact_s_mm=float(backgauge_contact_s_mm),
                        contact_z_mm=float(backgauge_contact_z_mm),
                        tolerance_mm=tolerance_mm,
                    )
                    if cad_verdict["status"] == "INTERFERENCE":
                        section_status = ContactStatus.INTERFERENCE
                        comp = str(cad_verdict["component"])
                        backgauge_reason = (
                            "BACKGAUGE_CAD_3D:" + comp + ":"
                            + (
                                "material_overlap_mm3={:.6f};"
                                "gauge_station_xm_mm={:.3f};"
                                "body_ahead_of_stop_face_mm={:.3f};"
                                "finger_bbox_xyz_mm={};"
                                "panel_bbox_xyz_mm={}"
                            ).format(
                                float(cad_verdict["volume_mm3"]),
                                float(cad_verdict.get(
                                    "station_xm_mm", float("nan")
                                )),
                                float(cad_verdict.get(
                                    "body_protrusion_ahead_of_stop_face_mm",
                                    float("nan")
                                )),
                                cad_verdict.get("finger_bbox_xyz_mm", []),
                                cad_verdict.get(
                                    "sheet_component_bbox_xyz_mm", []
                                ),
                            )
                        )
                        if len(diagnostic_findings) < 32:
                            diagnostic_findings.append((
                                phase, "BACKGAUGE_CAD_3D", comp,
                                ContactStatus.INTERFERENCE.value,
                                backgauge_reason,
                            ))
                    elif cad_verdict["status"] != "SAMPLED_CLEAR_OR_TOUCH":
                        raise ValueError(
                            "CAD backgauge verification returned unsupported status"
                        )
                if (backgauge_polygon_machine is not None
                        and section_status is not ContactStatus.INTERFERENCE):
                    # The backgauge polygon must be supplied in machine (s,z).
                    # Never invent a default gauge or assume its absence is clear.
                    section_polygons = []
                    for key in ("fixed_polygon", "moving_polygon", "zone_polygon"):
                        if sec.get(key):
                            section_polygons.append((key, sec[key]))
                    for key in ("other_panel_polygons", "other_zone_polygons"):
                        section_polygons.extend(sec.get(key, ()))
                    if not section_polygons:
                        section_status = ContactStatus.UNVERIFIED
                        backgauge_reason = "no_sheet_section_for_backgauge"
                    else:
                        for label, poly in section_polygons:
                            try:
                                gauge_status, why = classify_polygon_contact(
                                    poly, backgauge_polygon_machine,
                                    tolerance_mm=tolerance_mm,
                                )
                            except ContactGeometryError as exc:
                                gauge_status, why = ContactStatus.UNVERIFIED, str(exc)
                            total_findings += 1
                            if gauge_status in (ContactStatus.INTERFERENCE, ContactStatus.UNVERIFIED):
                                if len(diagnostic_findings) < 32:
                                    diagnostic_findings.append((
                                        phase, "BACKGAUGE", str(label),
                                        gauge_status.value, str(why),
                                    ))
                            elif gauge_status is ContactStatus.TOUCH:
                                key = (phase, "BACKGAUGE", str(label), str(why))
                                touch_counts[key] = touch_counts.get(key, 0) + 1
                            if gauge_status is ContactStatus.INTERFERENCE:
                                section_status = gauge_status
                                backgauge_reason = "BACKGAUGE:" + str(label) + ":" + why
                                break
                            if gauge_status is ContactStatus.UNVERIFIED and section_status is not ContactStatus.INTERFERENCE:
                                section_status = ContactStatus.UNVERIFIED
                                backgauge_reason = "BACKGAUGE:" + str(label) + ":" + why
                            if (gauge_status is ContactStatus.TOUCH
                                    and section_status is ContactStatus.CLEAR):
                                section_status = ContactStatus.TOUCH
                contact_statuses.append(section_status.value)

                if section_status is ContactStatus.INTERFERENCE:
                    # Conditional exploratory mode: only a PUNCH-vs-sheet
                    # RETRACT intersection can continue with a warning.
                    # Never accept DIE, BACKGAUGE, APPROACH or BENDING overlap.
                    # This is not proof of passive resting contact or 3D safety.
                    retract_only_punch = (
                        allow_retract_warning and phase == "RETRACT"
                        and not backgauge_reason
                        and any(f.status is ContactStatus.INTERFERENCE
                                for f in sec_report.findings)
                        and all(f.tool == "PUNCH"
                                for f in sec_report.findings
                                if f.status is ContactStatus.INTERFERENCE)
                    )
                    if retract_only_punch:
                        retract_warning_found = True
                        continue
                    # 2D cross-section planar findings require authoritative 3D verification
                    finding = next((f for f in sec_report.findings if f.status is ContactStatus.INTERFERENCE), None)
                    if finding is not None and backgauge_cad_checker is not None:
                        _record_planar_tool_interference(
                            backgauge_cad_checker, sec, tooling, finding,
                            bend_id=candidate_bend_id, phase=phase,
                            angle_fraction=frac, angle_deg=angle_at_probe,
                            ram_z_mm=ram_z, tolerance_mm=tolerance_mm,
                        )
                    reason_msg = (
                        f"{phase}:{backgauge_reason}" if backgauge_reason
                        else f"{phase}:{finding.sheet_component}:{finding.reason}" if finding
                        else phase
                    )
                    return PreliminaryScreeningReport(
                        decision=ScreeningDecision.CONTINUE_TO_3D,
                        stage="STAGE_B_SECTION_REQUIRES_3D",
                        reason=f"2d_section_finding_requires_3d_validation:{reason_msg}",
                        sampled_phases=tuple(sampled_phases),
                        findings_count=total_findings,
                        station_reports=tuple(station_reports),
                        contact_statuses=tuple(contact_statuses),
                        backgauge_checked=(backgauge_polygon_machine is not None or backgauge_cad_checker is not None),
                        timing_ms=tuple(timing_ms.items()),
                        diagnostic_findings=tuple(diagnostic_findings),
                        touch_summary=_touch_summary(),
                    )
            except Exception as exc:
                # Never prune on section computation failure; forward to exact 3D check
                return PreliminaryScreeningReport(
                    decision=ScreeningDecision.UNVERIFIED_NEEDS_3D,
                    stage="STAGE_B_ERROR",
                    reason=f"section_eval_error:{exc}",
                    sampled_phases=tuple(sampled_phases),
                    findings_count=total_findings,
                    station_reports=tuple(station_reports),
                    contact_statuses=tuple(contact_statuses),
                    backgauge_checked=(backgauge_polygon_machine is not None or backgauge_cad_checker is not None),
                    timing_ms=tuple(timing_ms.items()),
                    diagnostic_findings=tuple(diagnostic_findings),
                    touch_summary=_touch_summary(),
                )

        return PreliminaryScreeningReport(
            decision=ScreeningDecision.CONTINUE_TO_3D,
            stage=("STAGE_B_RETRACT_WARNING" if retract_warning_found
                   else "STAGE_B_LOADING_ROUTE_UNVERIFIED"
                   if loading_route_unverified else "STAGE_B_PREVIEW"),
            reason=("retract_punch_interference_requires_3d_review"
                    if retract_warning_found else
                    "initial_cad_loading_route_intersection_at_assumed_start_not_bend_rejection"
                    if loading_route_unverified else "sampled_2d_section_unverified" if "UNVERIFIED" in contact_statuses
                    else "all_sampled_2d_sections_clear_or_touch"
                    if "TOUCH" in contact_statuses else "all_sampled_2d_sections_clear"),
            sampled_phases=tuple(sampled_phases),
            findings_count=total_findings,
            station_reports=tuple(station_reports),
            contact_statuses=tuple(contact_statuses),
            backgauge_checked=(backgauge_polygon_machine is not None or backgauge_cad_checker is not None),
            timing_ms=tuple(timing_ms.items()),
            diagnostic_findings=tuple(diagnostic_findings),
            touch_summary=_touch_summary(),
        )

    finally:
        # Fully restore fold_state
        if hasattr(fold_state, "angles"):
            fold_state.angles.clear()
            fold_state.angles.update(orig_angles)
        if hasattr(fold_state, "transforms"):
            fold_state.transforms.clear()
            fold_state.transforms.update(orig_transforms)

        # Fully restore tooling
        if hasattr(tooling, "match_active_bend_length"):
            tooling.match_active_bend_length = orig_match_len
        if hasattr(tooling, "active_bend_length_mm"):
            tooling.active_bend_length_mm = orig_active_len
        if hasattr(tooling, "aligned"):
            tooling.aligned = orig_aligned
        if hasattr(tooling, "aligned_bend_id"):
            tooling.aligned_bend_id = orig_aligned_bend_id
        if hasattr(tooling, "alignment_anchor_sz"):
            tooling.alignment_anchor_sz = orig_anchor
        if hasattr(tooling, "ram_z_mm"):
            tooling.ram_z_mm = orig_ram_z
        if hasattr(tooling, "ram_stroke_mm"):
            tooling.ram_stroke_mm = orig_ram_stroke
        if hasattr(tooling, "sheet_top_z_mm"):
            tooling.sheet_top_z_mm = orig_sheet_top
        if hasattr(tooling, "sheet_bottom_z_mm"):
            tooling.sheet_bottom_z_mm = orig_sheet_bot
        if orig_punch_tf is not None and hasattr(tooling, "punch_machine_transform"):
            tooling.punch_machine_transform = orig_punch_tf
        if orig_die_tf is not None and hasattr(tooling, "die_machine_transform"):
            tooling.die_machine_transform = orig_die_tf

    return PreliminaryScreeningReport(
        decision=ScreeningDecision.CONTINUE_TO_3D,
        stage="STAGE_B_PASSED",
        reason="2d_section_preview_clear_not_3d_certified",
        sampled_phases=tuple(sampled_phases),
        findings_count=total_findings,
    )
