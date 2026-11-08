"""Selective 2.5D + D6 native 3D collision verification for the active GUI pose.

One read-only entry point for the existing PressBrakeSim FoldState. This is an
ON-DEMAND diagnostic, not a second simulation controller or a swept-volume
proof. Missing source solids or tool length must never produce PASS.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .contact_oracle_2d import ContactStatus, check_sheet_tooling_preview
from .machine_section_3d import compute_machine_cutting_plane_section
from .tooling_25d import ValidationStatus


class PhysicalEvidence(str, Enum):
    COLLISION = "COLLISION"
    UNVERIFIED = "UNVERIFIED"
    CONDITIONAL = "CONDITIONAL"
    SECTION_CLEAR_3D_UNVERIFIED = "SECTION_CLEAR_3D_UNVERIFIED"
    SAMPLED_3D_CLEAR_NOT_CONTINUOUS = "SAMPLED_3D_CLEAR_NOT_CONTINUOUS"


@dataclass(frozen=True)
class CurrentPosePhysicalReport:
    status: PhysicalEvidence
    section_status: str
    native_status: str
    reason: str
    checked_scope: tuple[str, ...]
    raw_report: Any = None
    tool_length_info: dict[str, Any] | None = None
    end_zone_analysis: Any = None
    is_conditional: bool = False

    @property
    def is_fully_qualified(self) -> bool:
        # A single sample never certifies an entire press cycle, tool
        # coverage, support, transport or frame/backgauge clearance.
        return False


def _unverified(
    reason: str,
    section_status: str = "NOT_CHECKED",
    native_status: str = "NOT_CHECKED",
    tool_length_info: dict[str, Any] | None = None,
    end_zone_analysis: Any = None,
) -> CurrentPosePhysicalReport:
    return CurrentPosePhysicalReport(
        PhysicalEvidence.UNVERIFIED,
        section_status,
        native_status,
        str(reason),
        (),
        tool_length_info=tool_length_info,
        end_zone_analysis=end_zone_analysis,
        is_conditional=False,
    )


def _build_tool_length_info(
    active_bend_id: str,
    active_bend_length_mm: float,
    effective_bend_span_xm: tuple[float, float],
    desired_midpoint_xm: float,
    punch,
    die,
) -> dict[str, Any]:
    is_virtual = bool(getattr(punch, "is_virtual_length", False) or getattr(die, "is_virtual_length", False))
    p_span = getattr(punch, "span_xm", (0.0, 0.0))
    d_span = getattr(die, "span_xm", (0.0, 0.0))
    return {
        "active_bend_id": str(active_bend_id),
        "active_bend_length_mm": float(active_bend_length_mm),
        "effective_bend_span_xm": [float(effective_bend_span_xm[0]), float(effective_bend_span_xm[1])],
        "desired_midpoint_xm": float(desired_midpoint_xm),
        "is_virtual_tool_geometry": is_virtual,
        "tool_length_mode": str(getattr(punch, "tool_length_mode", "SOURCE_CAD_LENGTH")),
        "punch_tool_id": str(getattr(punch, "tool_id", "")),
        "punch_source_file": str(getattr(punch, "source_file", "")),
        "punch_source_length_mm": float(getattr(punch, "source_length_mm", getattr(punch, "total_length_mm", 0.0))),
        "punch_virtual_length_mm": (
            float(punch.virtual_length_mm) if getattr(punch, "virtual_length_mm", None) is not None else None
        ),
        "punch_effective_length_mm": float(getattr(punch, "total_length_mm", 0.0)),
        "punch_span_xm": [float(p_span[0]), float(p_span[1])],
        "die_tool_id": str(getattr(die, "tool_id", "")),
        "die_source_file": str(getattr(die, "source_file", "")),
        "die_source_length_mm": float(getattr(die, "source_length_mm", getattr(die, "total_length_mm", 0.0))),
        "die_virtual_length_mm": (
            float(die.virtual_length_mm) if getattr(die, "virtual_length_mm", None) is not None else None
        ),
        "die_effective_length_mm": float(getattr(die, "total_length_mm", 0.0)),
        "die_span_xm": [float(d_span[0]), float(d_span[1])],
    }


def _source_path(profile) -> Path | None:
    if profile is None or getattr(profile, "validation_status", None) != ValidationStatus.GEOMETRY_VERIFIED:
        return None
    value = getattr(profile, "source_file", "")
    if not isinstance(value, str) or not value.strip():
        return None
    p = Path(value).expanduser()
    # Native loader resolves CAD datums again by FCStd stem. Refuse a
    # different profile identity instead of checking the wrong tool.
    if (p.is_file() and p.suffix.lower() == ".fcstd"
            and p.stem == getattr(profile, "tool_id", None)):
        return p
    return None


def check_current_25d_pose(
    session,
    press_cycle,
    tooling,
    *,
    native_3d: bool = True,
    desired_midpoint_xm: float = 0.0,
    match_active_bend_length: bool | None = None,
    virtual_tool_length_mm: float | None = None,
    allow_end_zone_collision: bool | None = None,
    end_zone_length_mm: float | None = None,
    classify_end_zone_length_mm: float = 0.0,
):
    """Check the displayed FoldState at its current machine placement.

    No source document or FoldState is rewritten. Optional native D6 checks
    require real FCStd tools; missing data returns UNVERIFIED.
    """
    if session is None or press_cycle is None:
        return _unverified("part_or_press_cycle_missing")
    if getattr(press_cycle, "transfer_fault", None):
        return _unverified("transfer_fault:" + str(press_cycle.transfer_fault))
    if getattr(press_cycle.phase, "value", press_cycle.phase) == "TRANSFER":
        return _unverified("transfer_3d_swept_collision_not_implemented")
    if not getattr(tooling, "aligned", False):
        return _unverified("tooling_not_aligned")

    eff_match_len = (
        bool(getattr(tooling, "match_active_bend_length", False))
        if match_active_bend_length is None
        else bool(match_active_bend_length)
    )
    eff_allow_end_zone = (
        bool(getattr(tooling, "allow_end_zone_collision", False))
        if allow_end_zone_collision is None
        else bool(allow_end_zone_collision)
    )
    eff_end_zone_len = (
        float(getattr(tooling, "end_zone_length_mm", 0.0))
        if end_zone_length_mm is None
        else float(end_zone_length_mm)
    )
    eff_classify_end_zone_len = (
        float(classify_end_zone_length_mm)
        if float(classify_end_zone_length_mm) > 0.0
        else eff_end_zone_len
    )

    # The cross-section is computed from the SAME authoritative machine pose
    # used by the 2.5D GUI. No projection from a different local bend frame.
    try:
        try:
            placement = press_cycle.current_workpiece_placement_3d(desired_midpoint_xm=float(desired_midpoint_xm))
        except TypeError:
            placement = press_cycle.current_workpiece_placement_3d()
            if abs(desired_midpoint_xm) > 1e-6:
                from .workpiece_placement_3d import WorkpiecePlacement3D
                bz = session.fold_state.bends[session.active_bend_id].bend_zone
                supp_z = bz.outer_radius_mm if bz is not None else 0.0
                placement = WorkpiecePlacement3D.compute_placement(
                    session.fold_state,
                    session.active_bend_id,
                    support_plane_zm=supp_z,
                    align_bisector=True,
                    align_bend_arc_center=True,
                    desired_midpoint_xm=float(desired_midpoint_xm),
                )
        section = compute_machine_cutting_plane_section(
            session.fold_state, session.panel_vertices_xy, placement,
            active_bend_id=session.active_bend_id, segments=32,
        )
        sec_report = check_sheet_tooling_preview(section, tooling)
    except Exception as exc:
        return _unverified("machine_section_unavailable:" + str(exc))

    sec = sec_report.status.value
    # For a parked/initial pose, sheet material should not be placed below
    # the selected die support datum. This is a placement diagnostic, NOT a
    # general penetration test during active forming into the V groove.
    support_frame_detail = ""
    phase_name = str(getattr(press_cycle.phase, "value", press_cycle.phase))
    if phase_name in ("IDLE", "APPROACH"):
        try:
            zone = section.get("zone_polygon") or ()
            die_center = tooling.die_v_center_machine()
            if zone and die_center is not None:
                sheet_bottom = min(float(z) for _, z in zone)
                datum_z = float(die_center[1])
                if sheet_bottom < datum_z - 0.1:
                    support_frame_detail = (
                        "; INITIAL_SUPPORT_FRAME_OFFSET:"
                        "sheet_bottom_z={:.4f};die_datum_z={:.4f};"
                        "below_datum_mm={:.4f}"
                    ).format(sheet_bottom, datum_z, datum_z - sheet_bottom)
        except (TypeError, ValueError, AttributeError):
            # Preserve the actual geometric collision result; never hide
            # it behind a failed diagnostic annotation.
            support_frame_detail = "; initial_support_frame_unavailable"
    section_collision = sec_report.status is ContactStatus.INTERFERENCE
    section_ambiguous = sec_report.status is ContactStatus.UNVERIFIED
    section_reason = next(
        (f"{f.tool}/{f.sheet_component}:{f.reason}"
         for f in sec_report.findings
         if f.status in (ContactStatus.INTERFERENCE, ContactStatus.UNVERIFIED)),
        "section_clear_or_touch",
    )
    # A 2D cutting plane and a 3D solid check provide INDEPENDENT
    # evidence. Never skip requested native 3D validation merely because
    # the section reports interference. A disagreement is UNVERIFIED,
    # not a spurious claim that either model proves the full cycle.
    if not native_3d:
        if section_collision:
            return CurrentPosePhysicalReport(
                PhysicalEvidence.COLLISION, sec, "NOT_REQUESTED",
                "2d_section_interference:" + section_reason,
                ("2D_SECTION",),
            )
        if section_ambiguous:
            return CurrentPosePhysicalReport(
                PhysicalEvidence.UNVERIFIED, sec, "NOT_REQUESTED",
                "2d_section_unverified:" + section_reason,
                ("2D_SECTION",),
            )
        return CurrentPosePhysicalReport(
            PhysicalEvidence.SECTION_CLEAR_3D_UNVERIFIED,
            sec, "NOT_REQUESTED", "3d_not_requested", ("2D_SECTION",),
        )

    punch_path = _source_path(getattr(tooling, "punch", None))
    die_path = _source_path(getattr(tooling, "die", None))
    if punch_path is None or die_path is None:
        if section_collision:
            return CurrentPosePhysicalReport(
                PhysicalEvidence.COLLISION, sec, "MISSING_SOURCE_FCSTD",
                "2d_section_interference:" + section_reason
                + "; native_3d_requires_verified_selected_punch_and_die_FCStd",
                ("2D_SECTION",),
            )
        return CurrentPosePhysicalReport(
            PhysicalEvidence.UNVERIFIED if section_ambiguous
            else PhysicalEvidence.SECTION_CLEAR_3D_UNVERIFIED,
            sec, "MISSING_SOURCE_FCSTD",
            "native_3d_requires_verified_selected_punch_and_die_FCStd",
            ("2D_SECTION",),
        )

    tool_len_info = None
    try:
        import FreeCAD as App
        from .native_bend_solid_3d import build_multi_bend_sheet_solid
        from .native_tool_loader import (
            load_native_punch, load_native_die, check_tool_coverage, set_punch_ram_z,
        )
        from .native_collision_3d import (
            validate_workpiece_tooling_pose, CollisionClass, EndZoneCollisionPolicy,
        )

        # Reject missing tool length BEFORE the expensive OCCT solid build.
        active = session.fold_state.bends[session.active_bend_id]
        axis = active.axis_xyz
        from math import dist
        length = dist(axis[0], axis[1])
        span = (-length / 2.0 + desired_midpoint_xm, length / 2.0 + desired_midpoint_xm)
        eff_virt_len = (
            float(virtual_tool_length_mm)
            if virtual_tool_length_mm is not None
            else (float(length) if eff_match_len else None)
        )
        old_active = getattr(getattr(App, "ActiveDocument", None), "Name", None)
        try:
            if eff_virt_len is not None:
                punch = load_native_punch(
                    punch_path,
                    ram_z_mm=float(press_cycle.ram_z_mm),
                    virtual_length_mm=eff_virt_len,
                )
                die = load_native_die(
                    die_path,
                    virtual_length_mm=eff_virt_len,
                )
            else:
                punch = load_native_punch(punch_path, ram_z_mm=float(press_cycle.ram_z_mm))
                die = load_native_die(die_path)
            tool_len_info = _build_tool_length_info(
                session.active_bend_id, length, span, desired_midpoint_xm, punch, die,
            )
            pcov = check_tool_coverage(span, punch.span_xm, punch.sections)
            dcov = check_tool_coverage(span, die.span_xm, die.sections)
            if not pcov.is_covered or not dcov.is_covered:
                return CurrentPosePhysicalReport(
                    PhysicalEvidence.UNVERIFIED, sec, "INSUFFICIENT_TOOL_COVERAGE",
                    "punch:" + pcov.reason + "; die:" + dcov.reason,
                    ("2D_SECTION", "3D_TOOL_LENGTH"),
                    tool_length_info=tool_len_info,
                )

            shape = build_multi_bend_sheet_solid(
                session.fold_state, session.panel_build, segments=64,
            ).shape.copy()
            matrix = App.Matrix(
                placement.rotation[0][0], placement.rotation[0][1], placement.rotation[0][2], placement.translation[0],
                placement.rotation[1][0], placement.rotation[1][1], placement.rotation[1][2], placement.translation[1],
                placement.rotation[2][0], placement.rotation[2][1], placement.rotation[2][2], placement.translation[2],
                0.0, 0.0, 0.0, 1.0,
            )
            shape.transformShape(matrix)
            ez_policy = (
                EndZoneCollisionPolicy(
                    allow_end_zone_collision=eff_allow_end_zone,
                    end_zone_length_mm=eff_end_zone_len,
                )
                if (eff_allow_end_zone or eff_end_zone_len > 0.0)
                else None
            )
            if ez_policy is not None or eff_classify_end_zone_len > 0.0:
                result = validate_workpiece_tooling_pose(
                    shape,
                    set_punch_ram_z(punch, float(press_cycle.ram_z_mm)).machine_shape,
                    die.machine_shape,
                    end_zone_policy=ez_policy,
                    classify_end_zone_length_mm=eff_classify_end_zone_len,
                )
            else:
                result = validate_workpiece_tooling_pose(
                    shape,
                    set_punch_ram_z(punch, float(press_cycle.ram_z_mm)).machine_shape,
                    die.machine_shape,
                )
        finally:
            # FreeCAD tool file loading must not change the user's active doc.
            if old_active is not None and App.getDocument(old_active) is not None:
                App.setActiveDocument(old_active)
                try:
                    import FreeCADGui as Gui
                    if hasattr(Gui, "getDocument"):
                        Gui.getDocument(old_active)
                except (AttributeError, RuntimeError, TypeError):
                    pass
    except Exception as exc:
        exc_msg = str(exc)
        if "VIRTUAL_TOOL_CONSTRUCTION_FAILED" in exc_msg:
            return CurrentPosePhysicalReport(
                PhysicalEvidence.UNVERIFIED,
                sec,
                "VIRTUAL_TOOL_CONSTRUCTION_FAILED",
                exc_msg,
                ("2D_SECTION", "3D_TOOL_LENGTH"),
                tool_length_info=tool_len_info,
            )
        return CurrentPosePhysicalReport(
            PhysicalEvidence.COLLISION if section_collision
            else PhysicalEvidence.UNVERIFIED,
            sec, "EVALUATION_FAILED",
            ("2d_section_interference:" + section_reason + "; " if section_collision
             else "") + "native_3d_unavailable:" + exc_msg,
            ("2D_SECTION",),
            tool_length_info=tool_len_info,
        )

    ez_analysis = getattr(result, "punch_end_zone_analysis", None)
    if (
        ez_analysis is not None
        and getattr(ez_analysis, "classification", None) is not None
        and ez_analysis.classification.value == "UNCLASSIFIED"
    ):
        return CurrentPosePhysicalReport(
            PhysicalEvidence.UNVERIFIED,
            sec,
            "END_ZONE_UNCLASSIFIED",
            "end_zone_classification_unverified:"
            + str(getattr(ez_analysis, "reason", ""))
            + "; details="
            + ";".join(result.diagnostics),
            ("2D_SECTION", "3D_PUNCH_DIE_CURRENT_POSE", "3D_TOOL_LENGTH"),
            raw_report=result,
            tool_length_info=tool_len_info,
            end_zone_analysis=ez_analysis,
        )

    if not result.is_safe and getattr(result, "is_conditional_safe", False):
        if section_collision:
            return CurrentPosePhysicalReport(
                PhysicalEvidence.COLLISION,
                sec,
                result.status.value,
                "2d_section_interference_blocks_conditional_end_zone:" + section_reason,
                ("2D_SECTION", "3D_PUNCH_DIE_CURRENT_POSE", "3D_TOOL_LENGTH"),
                raw_report=result,
                tool_length_info=tool_len_info,
                end_zone_analysis=ez_analysis,
            )
        if section_ambiguous:
            return CurrentPosePhysicalReport(
                PhysicalEvidence.UNVERIFIED,
                sec,
                result.status.value,
                "2d_section_ambiguous_blocks_conditional_end_zone:" + section_reason,
                ("2D_SECTION", "3D_PUNCH_DIE_CURRENT_POSE", "3D_TOOL_LENGTH"),
                raw_report=result,
                tool_length_info=tool_len_info,
                end_zone_analysis=ez_analysis,
            )
        side_str = getattr(ez_analysis, "affected_side", "UNKNOWN")
        ez_len_val = float(getattr(ez_analysis, "end_zone_length_mm", eff_end_zone_len))
        return CurrentPosePhysicalReport(
            PhysicalEvidence.CONDITIONAL,
            sec,
            "CONDITIONAL_END_ZONE",
            "conditional_end_zone_punch_collision:"
            "punch={:.9f}mm3;side={};end_zone_mm={:.2f};details={}".format(
                result.punch_result.overlap_volume_mm3,
                side_str,
                ez_len_val,
                ";".join(result.diagnostics),
            ) + support_frame_detail,
            ("2D_SECTION", "3D_PUNCH_DIE_CURRENT_POSE", "3D_TOOL_LENGTH"),
            raw_report=result,
            tool_length_info=tool_len_info,
            end_zone_analysis=ez_analysis,
            is_conditional=True,
        )

    if result.status is CollisionClass.PENETRATION:
        return CurrentPosePhysicalReport(
            PhysicalEvidence.COLLISION, sec, result.status.value,
            "native_3d_positive_volume_interference:"
            "punch={:.9f}mm3;die={:.9f}mm3;details={}".format(
                result.punch_result.overlap_volume_mm3,
                result.die_result.overlap_volume_mm3,
                ";".join(result.diagnostics),
            ) + support_frame_detail,
            ("2D_SECTION", "3D_PUNCH_DIE_CURRENT_POSE", "3D_TOOL_LENGTH"),
            raw_report=result,
            tool_length_info=tool_len_info,
            end_zone_analysis=ez_analysis,
        )
    if not result.is_safe or result.status not in (
        CollisionClass.CLEAR, CollisionClass.TOUCHING, CollisionClass.NEAR_CONTACT,
    ):
        return CurrentPosePhysicalReport(
            PhysicalEvidence.UNVERIFIED, sec, result.status.value,
            "native_3d_invalid_or_incomplete:" + "; ".join(result.diagnostics),
            ("2D_SECTION", "3D_PUNCH_DIE_CURRENT_POSE", "3D_TOOL_LENGTH"),
            raw_report=result,
            tool_length_info=tool_len_info,
            end_zone_analysis=ez_analysis,
        )
    if section_collision or section_ambiguous:
        return CurrentPosePhysicalReport(
            PhysicalEvidence.UNVERIFIED, sec, result.status.value,
            "2d_3d_evidence_disagreement_or_ambiguity:"
            + section_reason
            + "; native_3d_reports_nonpenetration; check machine coordinate"
            + "_alignment_and_cut_plane_before_acceptance",
            ("2D_SECTION", "3D_PUNCH_DIE_CURRENT_POSE", "3D_TOOL_LENGTH"),
            raw_report=result,
            tool_length_info=tool_len_info,
            end_zone_analysis=ez_analysis,
        )
    return CurrentPosePhysicalReport(
        PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS, sec,
        result.status.value,
        "discrete_pose_only; no_swept_transfer_backgauge_clamps_frame_or_support_certificate",
        ("2D_SECTION", "3D_PUNCH_DIE_CURRENT_POSE", "3D_TOOL_LENGTH"),
        raw_report=result,
        tool_length_info=tool_len_info,
        end_zone_analysis=ez_analysis,
    )


def check_active_25d_bend_trajectory(
    session,
    tooling,
    *,
    config=None,
    desired_midpoint_xm: float = 0.0,
    match_active_bend_length: bool | None = None,
    virtual_tool_length_mm: float | None = None,
    allow_end_zone_collision: bool | None = None,
    end_zone_length_mm: float | None = None,
    classify_end_zone_length_mm: float = 0.0,
):
    """Run the existing D6.3.4 adaptive 3D oracle for ONE active bend.

    Invoked only by explicit request, never from the animation timer.
    The D6 validator restores FoldState in its own finally block. This
    service still checks the incoming state afterward and refuses a result
    if a mutation escaped. The result is bounded SAMPLED evidence only.
    """
    if session is None or getattr(session, "fold_state", None) is None:
        return _unverified("part_not_loaded")
    if not getattr(tooling, "aligned", False):
        return _unverified("tooling_not_aligned")
    punch_file = _source_path(getattr(tooling, "punch", None))
    die_file = _source_path(getattr(tooling, "die", None))
    if punch_file is None or die_file is None:
        return _unverified("real_verified_selected_punch_and_die_fcstd_required")

    eff_match_len = (
        bool(getattr(tooling, "match_active_bend_length", False))
        if match_active_bend_length is None
        else bool(match_active_bend_length)
    )
    eff_allow_end_zone = (
        bool(getattr(tooling, "allow_end_zone_collision", False))
        if allow_end_zone_collision is None
        else bool(allow_end_zone_collision)
    )
    eff_end_zone_len = (
        float(getattr(tooling, "end_zone_length_mm", 0.0))
        if end_zone_length_mm is None
        else float(end_zone_length_mm)
    )
    eff_classify_end_zone_len = (
        float(classify_end_zone_length_mm)
        if float(classify_end_zone_length_mm) > 0.0
        else eff_end_zone_len
    )

    state = session.fold_state
    original_angles = dict(state.angles)
    original_transforms = dict(state.transforms)
    active_id = session.active_bend_id
    order = tuple(session.bend_order)
    if active_id not in order:
        return _unverified("active_bend_not_in_order")
    index = order.index(active_id)
    prior = {}
    for bid in order[:index]:
        target = float(state.bends[bid].angle_deg)
        if abs(float(original_angles.get(bid, 0.0)) - target) > 1e-6:
            return _unverified("prior_bend_not_completed:" + bid)
        prior[bid] = target
    if any(abs(float(original_angles.get(bid, 0.0))) > 1e-6
           for bid in order[index + 1:]):
        return _unverified("later_bend_already_modified")

    report = None
    tool_len_info = None
    try:
        import FreeCAD as App
        from math import dist
        from .native_tool_loader import load_native_die, load_native_punch
        from .native_collision_3d import EndZoneCollisionPolicy
        from .motion_trajectory_3d import (
            AdaptiveTrajectoryValidator3D, TrajectoryEvidenceStatus,
        )
        active = state.bends[active_id]
        axis = getattr(active, "axis_xyz", None)
        if axis is not None:
            length = dist(axis[0], axis[1])
        elif eff_match_len or virtual_tool_length_mm is not None:
            raise ValueError("VIRTUAL_TOOL_CONSTRUCTION_FAILED: active bend missing axis_xyz")
        else:
            length = 0.0
        span = (-length / 2.0 + desired_midpoint_xm, length / 2.0 + desired_midpoint_xm)
        eff_virt_len = (
            float(virtual_tool_length_mm)
            if virtual_tool_length_mm is not None
            else (float(length) if eff_match_len else None)
        )
        prev = getattr(getattr(App, "ActiveDocument", None), "Name", None)
        try:
            if eff_virt_len is not None:
                punch = load_native_punch(punch_file, ram_z_mm=50.0, virtual_length_mm=eff_virt_len)
                die = load_native_die(die_file, virtual_length_mm=eff_virt_len)
            else:
                punch = load_native_punch(punch_file, ram_z_mm=50.0)
                die = load_native_die(die_file)
            tool_len_info = _build_tool_length_info(
                active_id, length, span, desired_midpoint_xm, punch, die,
            )
            ez_policy = (
                EndZoneCollisionPolicy(
                    allow_end_zone_collision=eff_allow_end_zone,
                    end_zone_length_mm=eff_end_zone_len,
                )
                if (eff_allow_end_zone or eff_end_zone_len > 0.0)
                else None
            )
            if ez_policy is not None or eff_classify_end_zone_len > 0.0:
                validator = AdaptiveTrajectoryValidator3D(
                    state,
                    session.panel_build,
                    punch,
                    die,
                    config=config,
                    desired_midpoint_xm=desired_midpoint_xm,
                    end_zone_policy=ez_policy,
                    classify_end_zone_length_mm=eff_classify_end_zone_len,
                )
            else:
                validator = AdaptiveTrajectoryValidator3D(
                    state,
                    session.panel_build,
                    punch,
                    die,
                    config=config,
                    desired_midpoint_xm=desired_midpoint_xm,
                )
            report = validator.validate_bend_trajectory(active_id, prior)
        finally:
            if prev and App.getDocument(prev) is not None:
                App.setActiveDocument(prev)
    except Exception as exc:
        exc_msg = str(exc)
        if "VIRTUAL_TOOL_CONSTRUCTION_FAILED" in exc_msg:
            return _unverified(
                exc_msg,
                native_status="VIRTUAL_TOOL_CONSTRUCTION_FAILED",
                tool_length_info=tool_len_info,
            )
        return _unverified("d634_validation_failed:" + exc_msg, tool_length_info=tool_len_info)
    finally:
        if state.angles != original_angles or state.transforms != original_transforms:
            # Never attempt an unverified automatic repair here. FoldState
            # restoration failure is a real blocker; a qualified status is
            # forbidden and the caller must reset before any further play.
            raise RuntimeError("D634_FOLDSTATE_RESTORATION_FAILED")

    native = report.evidence_status.value
    # Extract representative end_zone_analysis from trajectory states if any
    peak_ez_analysis = None
    ez_states = getattr(report, "end_zone_collision_states", None) or []
    if ez_states:
        peak_st = max(
            ez_states,
            key=lambda st: float(getattr(getattr(st, "punch_end_zone_analysis", None), "total_overlap_volume_mm3", 0.0)),
        )
        peak_ez_analysis = getattr(peak_st, "punch_end_zone_analysis", None)
    elif report.first_collision_state is not None:
        peak_ez_analysis = getattr(report.first_collision_state, "punch_end_zone_analysis", None)

    # Surface phase-level evidence (including exact unresolved intervals),
    # not just the first few aggregate messages.
    phase_details = []
    for phase in report.phase_results:
        status = getattr(phase.evidence_status, "value", str(phase.evidence_status))
        pieces = ["{}={}".format(phase.phase_name, status)]
        if phase.unresolved_intervals:
            pieces.append("unresolved={}".format(len(phase.unresolved_intervals)))
            pieces.append("first_interval={}".format(phase.unresolved_intervals[0]))
        if getattr(phase, "uninspected_intervals", None):
            pieces.append("uninspected={}".format(len(phase.uninspected_intervals)))
            pieces.append("first_uninspected={}".format(phase.uninspected_intervals[0]))
        if phase.discontinuities:
            pieces.append("discontinuity={}".format(phase.discontinuities[0]))
        if phase.diagnostics:
            pieces.append("reason={}".format(phase.diagnostics[0]))
        pieces.append("samples={}".format(len(phase.states)))
        pieces.append("solid_rebuilds={}".format(phase.solid_reconstructions_count))
        pieces.append("3d_collision_queries={}".format(phase.collision_queries_count))
        pieces.append("elapsed_s={:.2f}".format(phase.elapsed_ms / 1000.0))
        p_eval_s = getattr(phase, "total_punch_eval_ms", 0.0) / 1000.0
        d_eval_s = getattr(phase, "total_die_eval_ms", 0.0) / 1000.0
        pieces.append("punch_eval_s={:.2f}".format(p_eval_s))
        pieces.append("die_eval_s={:.2f}".format(d_eval_s))
        pieces.append("die_hits={}".format(getattr(phase, "die_cache_hits", 0)))
        pieces.append("die_misses={}".format(getattr(phase, "die_cache_misses", 0)))
        pieces.append("punch_overlap_mm3={:.6f}".format(phase.max_punch_overlap_volume_mm3))
        pieces.append("die_overlap_mm3={:.6f}".format(phase.max_die_overlap_volume_mm3))
        phase_details.append(",".join(pieces))
    phase_evidence = "; ".join(phase_details)
    if report.evidence_status is TrajectoryEvidenceStatus.DISCRETE_SAMPLES_REJECTED:
        return CurrentPosePhysicalReport(
            PhysicalEvidence.COLLISION if report.first_collision_state is not None
            else PhysicalEvidence.UNVERIFIED,
            "NOT_APPLICABLE", native,
            "native_d634_rejected:" + phase_evidence,
            ("3D_PUNCH_DIE_SAMPLED_BEND", "3D_TOOL_LENGTH"),
            raw_report=report,
            tool_length_info=tool_len_info,
            end_zone_analysis=peak_ez_analysis,
        )
    if (
        report.evidence_status is TrajectoryEvidenceStatus.SAMPLED_INTERVAL_CONDITIONAL_END_ZONE
        and getattr(report, "is_conditional", False)
        and report.fold_state_restored
    ):
        return CurrentPosePhysicalReport(
            PhysicalEvidence.CONDITIONAL,
            "NOT_APPLICABLE",
            native,
            "d634_conditional_end_zone:"
            + "samples=" + str(report.total_states_evaluated)
            + ";max_end_zone_overlap_mm3={:.6f}".format(float(getattr(report, "max_end_zone_overlap_volume_mm3", 0.0)))
            + ";" + phase_evidence,
            ("3D_PUNCH_DIE_SAMPLED_BEND", "3D_TOOL_LENGTH"),
            raw_report=report,
            tool_length_info=tool_len_info,
            end_zone_analysis=peak_ez_analysis,
            is_conditional=True,
        )
    if (report.evidence_status is TrajectoryEvidenceStatus.SAMPLED_INTERVAL_CONVERGED
            and report.is_qualified and report.fold_state_restored):
        return CurrentPosePhysicalReport(
            PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
            "NOT_APPLICABLE", native,
            "d634_samples=" + str(report.total_states_evaluated)
            + "; continuous=" + report.continuous_certification_status.value
            + "; transfer_backgauge_clamps_frame_unverified",
            ("3D_PUNCH_DIE_SAMPLED_BEND", "3D_TOOL_LENGTH"),
            raw_report=report,
            tool_length_info=tool_len_info,
            end_zone_analysis=peak_ez_analysis,
        )
    return CurrentPosePhysicalReport(
        PhysicalEvidence.UNVERIFIED, "NOT_APPLICABLE", native,
        "d634_incomplete:" + phase_evidence,
        ("3D_PUNCH_DIE_SAMPLED_BEND",),
        raw_report=report,
        tool_length_info=tool_len_info,
        end_zone_analysis=peak_ez_analysis,
    )
