"""Selective 2.5D + D6 native 3D collision verification for the active GUI pose.

One read-only entry point for the existing PressBrakeSim FoldState. This is an
ON-DEMAND diagnostic, not a second simulation controller or a swept-volume
proof. Missing source solids or tool length must never produce PASS.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .contact_oracle_2d import ContactStatus, check_sheet_tooling_preview
from .machine_section_3d import compute_machine_cutting_plane_section
from .tooling_25d import ValidationStatus


class PhysicalEvidence(str, Enum):
    COLLISION = "COLLISION"
    UNVERIFIED = "UNVERIFIED"
    SECTION_CLEAR_3D_UNVERIFIED = "SECTION_CLEAR_3D_UNVERIFIED"
    SAMPLED_3D_CLEAR_NOT_CONTINUOUS = "SAMPLED_3D_CLEAR_NOT_CONTINUOUS"


@dataclass(frozen=True)
class CurrentPosePhysicalReport:
    status: PhysicalEvidence
    section_status: str
    native_status: str
    reason: str
    checked_scope: tuple[str, ...]

    @property
    def is_fully_qualified(self) -> bool:
        # A single sample never certifies an entire press cycle, tool
        # coverage, support, transport or frame/backgauge clearance.
        return False


def _unverified(reason: str, section_status: str = "NOT_CHECKED") -> CurrentPosePhysicalReport:
    return CurrentPosePhysicalReport(
        PhysicalEvidence.UNVERIFIED, section_status,
        "NOT_CHECKED", str(reason), (),
    )


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


def check_current_25d_pose(session, press_cycle, tooling, *, native_3d: bool = True):
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

    # The cross-section is computed from the SAME authoritative machine pose
    # used by the 2.5D GUI. No projection from a different local bend frame.
    try:
        placement = press_cycle.current_workpiece_placement_3d()
        section = compute_machine_cutting_plane_section(
            session.fold_state, session.panel_vertices_xy, placement,
            active_bend_id=session.active_bend_id, segments=32,
        )
        sec_report = check_sheet_tooling_preview(section, tooling)
    except Exception as exc:
        return _unverified("machine_section_unavailable:" + str(exc))

    sec = sec_report.status.value
    if sec_report.status is ContactStatus.INTERFERENCE:
        detail = next(
            (f"{f.tool}/{f.sheet_component}:{f.reason}"
             for f in sec_report.findings if f.status is ContactStatus.INTERFERENCE),
            "section_penetration",
        )
        return CurrentPosePhysicalReport(
            PhysicalEvidence.COLLISION, sec, "NOT_CHECKED",
            "2d_section_interference:" + detail, ("2D_SECTION",),
        )
    if sec_report.status is ContactStatus.UNVERIFIED:
        return CurrentPosePhysicalReport(
            PhysicalEvidence.UNVERIFIED, sec, "NOT_CHECKED",
            "2d_section_ambiguous_or_missing_geometry", ("2D_SECTION",),
        )
    if not native_3d:
        return CurrentPosePhysicalReport(
            PhysicalEvidence.SECTION_CLEAR_3D_UNVERIFIED,
            sec, "NOT_REQUESTED", "3d_not_requested", ("2D_SECTION",),
        )

    punch_path = _source_path(getattr(tooling, "punch", None))
    die_path = _source_path(getattr(tooling, "die", None))
    if punch_path is None or die_path is None:
        return CurrentPosePhysicalReport(
            PhysicalEvidence.SECTION_CLEAR_3D_UNVERIFIED, sec,
            "MISSING_SOURCE_FCSTD",
            "native_3d_requires_verified_selected_punch_and_die_FCStd",
            ("2D_SECTION",),
        )

    try:
        import FreeCAD as App
        from .native_bend_solid_3d import build_multi_bend_sheet_solid
        from .native_tool_loader import (
            load_native_punch, load_native_die, check_tool_coverage, set_punch_ram_z,
        )
        from .native_collision_3d import (
            validate_workpiece_tooling_pose, CollisionClass,
        )

        # Reject missing tool length BEFORE the expensive OCCT solid build.
        active = session.fold_state.bends[session.active_bend_id]
        axis = active.axis_xyz
        from math import dist
        length = dist(axis[0], axis[1])
        span = (-length / 2.0, length / 2.0)
        old_active = getattr(getattr(App, "ActiveDocument", None), "Name", None)
        try:
            punch = load_native_punch(punch_path, ram_z_mm=float(press_cycle.ram_z_mm))
            die = load_native_die(die_path)
            pcov = check_tool_coverage(span, punch.span_xm, punch.sections)
            dcov = check_tool_coverage(span, die.span_xm, die.sections)
            if not pcov.is_covered or not dcov.is_covered:
                return CurrentPosePhysicalReport(
                    PhysicalEvidence.UNVERIFIED, sec, "INSUFFICIENT_TOOL_COVERAGE",
                    "punch:" + pcov.reason + "; die:" + dcov.reason,
                    ("2D_SECTION", "3D_TOOL_LENGTH"),
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
        return CurrentPosePhysicalReport(
            PhysicalEvidence.UNVERIFIED, sec, "EVALUATION_FAILED",
            "native_3d_unavailable:" + str(exc),
            ("2D_SECTION",),
        )

    if result.status is CollisionClass.PENETRATION:
        return CurrentPosePhysicalReport(
            PhysicalEvidence.COLLISION, sec, result.status.value,
            "native_3d_positive_volume_interference",
            ("2D_SECTION", "3D_PUNCH_DIE_CURRENT_POSE", "3D_TOOL_LENGTH"),
        )
    if not result.is_safe or result.status not in (
        CollisionClass.CLEAR, CollisionClass.TOUCHING, CollisionClass.NEAR_CONTACT,
    ):
        return CurrentPosePhysicalReport(
            PhysicalEvidence.UNVERIFIED, sec, result.status.value,
            "native_3d_invalid_or_incomplete:" + "; ".join(result.diagnostics),
            ("2D_SECTION", "3D_PUNCH_DIE_CURRENT_POSE", "3D_TOOL_LENGTH"),
        )
    return CurrentPosePhysicalReport(
        PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS, sec,
        result.status.value,
        "discrete_pose_only; no_swept_transfer_backgauge_clamps_frame_or_support_certificate",
        ("2D_SECTION", "3D_PUNCH_DIE_CURRENT_POSE", "3D_TOOL_LENGTH"),
    )


def check_active_25d_bend_trajectory(session, tooling, *, config=None):
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
    try:
        import FreeCAD as App
        from .native_tool_loader import load_native_die, load_native_punch
        from .motion_trajectory_3d import (
            AdaptiveTrajectoryValidator3D, TrajectoryEvidenceStatus,
        )
        prev = getattr(getattr(App, "ActiveDocument", None), "Name", None)
        try:
            punch = load_native_punch(punch_file, ram_z_mm=50.0)
            die = load_native_die(die_file)
            validator = AdaptiveTrajectoryValidator3D(
                state, session.panel_build, punch, die, config=config,
            )
            report = validator.validate_bend_trajectory(active_id, prior)
        finally:
            if prev and App.getDocument(prev) is not None:
                App.setActiveDocument(prev)
    except Exception as exc:
        return _unverified("d634_validation_failed:" + str(exc))
    finally:
        if state.angles != original_angles or state.transforms != original_transforms:
            # Never attempt an unverified automatic repair here. FoldState
            # restoration failure is a real blocker; a qualified status is
            # forbidden and the caller must reset before any further play.
            raise RuntimeError("D634_FOLDSTATE_RESTORATION_FAILED")

    native = report.evidence_status.value
    if report.evidence_status is TrajectoryEvidenceStatus.DISCRETE_SAMPLES_REJECTED:
        return CurrentPosePhysicalReport(
            PhysicalEvidence.COLLISION if report.first_collision_state is not None
            else PhysicalEvidence.UNVERIFIED,
            "NOT_APPLICABLE", native,
            "native_d634_rejected:" + ";".join(report.diagnostics[:3]),
            ("3D_PUNCH_DIE_SAMPLED_BEND", "3D_TOOL_LENGTH"),
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
        )
    return CurrentPosePhysicalReport(
        PhysicalEvidence.UNVERIFIED, "NOT_APPLICABLE", native,
        "d634_incomplete:" + ";".join(report.diagnostics[:3]),
        ("3D_PUNCH_DIE_SAMPLED_BEND",),
    )
