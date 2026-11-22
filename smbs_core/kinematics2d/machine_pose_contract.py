"""Single machine-space pose contract for FAST, Stage B and 3D playback.

All consumers must use the same deterministic phase-to-pose rule and the
same WorkpiecePlacement3D solver. This module does NOT invent a ram stroke:
the caller must supply the actual/sampled support-plane height from its
kinematics model. That input, the sheet angles and the loading orientation
must agree before claiming that two screenshots/checks represent the same
machine setup.

Only proper rigid transforms are returned (det=+1); geometry, bend signs,
panel ownership and tooling are never mutated here.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from .workpiece_placement_3d import PlacementTransform3D, WorkpiecePlacement3D


# APPROACH and LOAD_TO_GAUGE hold the fixed flange in the initial
# machine loading plane. A forming fold rotates around the actual bend
# bisector, keeping the existing finite-radius arc center at the tool axis.
# Historical end-of-cycle labels share the formed-bend pose.
_FLAT_LOADING_PHASES = frozenset((
    "LOAD_TO_GAUGE", "APPROACH", "CONTACT",
))
_BISECTOR_PHASES = frozenset((
    "SETUP", "BENDING", "BOTTOM", "RETRACT", "STEP_COMPLETE",
    "COMPLETED", "BEND_ONLY", "NATIVE_SAMPLE", "TRANSFER_TARGET",
))
_SUPPORTED_PHASES = _FLAT_LOADING_PHASES | _BISECTOR_PHASES


@dataclass(frozen=True)
class MachinePoseFrame:
    """Resolved machine pose plus reproducible placement inputs.

    This is a geometrical frame, not a 3D clearance or force certificate.
    """
    placement: PlacementTransform3D
    bend_id: str
    phase: str
    signed_angle_deg: float
    support_plane_zm: float
    reverse_axis: bool
    desired_midpoint_xm: float
    rear_frame_applied: bool
    align_bisector: bool

    def diagnostics(self) -> dict:
        return {
            "bend_id": self.bend_id,
            "phase": self.phase,
            "signed_angle_deg": self.signed_angle_deg,
            "support_plane_zm": self.support_plane_zm,
            "reverse_axis": self.reverse_axis,
            "desired_midpoint_xm": self.desired_midpoint_xm,
            "rear_frame_applied": self.rear_frame_applied,
            "align_bisector": self.align_bisector,
            "rotation": [list(row) for row in self.placement.rotation],
            "translation": list(self.placement.translation),
            "evidence": "RIGID_MACHINE_PLACEMENT_NOT_COLLISION_VERIFIED",
        }


def compute_machine_pose_frame(
    fold_state,
    bend_id: str,
    *,
    phase: str,
    support_plane_zm: float,
    reverse_axis: bool = False,
    rear_panel_vertices_xy=None,
    desired_midpoint_xm: float = 0.0,
) -> MachinePoseFrame:
    """Compute the common 2.5D/3D rigid sheet-to-machine placement.

    A phase is mandatory. Do not let FAST and the viewer each choose
    align_bisector differently for the *same* named phase.
    """
    name = str(getattr(phase, "value", phase)).upper()
    if name not in _SUPPORTED_PHASES:
        raise ValueError("unsupported_machine_pose_phase:" + name)
    bend = fold_state.bends[bend_id]
    signed_angle_deg = float(fold_state.angles[bend_id])
    z = float(support_plane_zm)
    x = float(desired_midpoint_xm)
    if not all(isfinite(v) for v in (signed_angle_deg, z, x)):
        raise ValueError("nonfinite_machine_pose_inputs")
    if type(reverse_axis) is not bool:
        raise ValueError("reverse_axis_must_be_bool")
    bisector = name in _BISECTOR_PHASES
    pose = WorkpiecePlacement3D.compute_placement(
        fold_state,
        bend_id,
        desired_midpoint_xm=x,
        support_plane_zm=z,
        align_bisector=bisector,
        align_bend_arc_center=True,
        reverse_axis=reverse_axis,
        rear_panel_vertices_xy=rear_panel_vertices_xy,
    )
    return MachinePoseFrame(
        placement=pose,
        bend_id=str(bend_id),
        phase=name,
        signed_angle_deg=signed_angle_deg,
        support_plane_zm=z,
        reverse_axis=reverse_axis,
        desired_midpoint_xm=x,
        rear_frame_applied=rear_panel_vertices_xy is not None,
        align_bisector=bisector,
    )


def compute_machine_pose(
    fold_state,
    bend_id: str,
    *,
    phase: str,
    support_plane_zm: float,
    reverse_axis: bool = False,
    rear_panel_vertices_xy=None,
    desired_midpoint_xm: float = 0.0,
) -> PlacementTransform3D:
    """Fast path used by all runtimes (including non-Qt search workers)."""
    return compute_machine_pose_frame(
        fold_state, bend_id, phase=phase,
        support_plane_zm=support_plane_zm,
        reverse_axis=reverse_axis,
        rear_panel_vertices_xy=rear_panel_vertices_xy,
        desired_midpoint_xm=desired_midpoint_xm,
    ).placement
