"""Diagnostic four-pose 2.5D loading study for a press brake.

The legacy 2.5D viewer compares a 180-degree longitudinal sheet roll and
left/right flange presentation.  Those are NOT four interchangeable machine
setups: a longitudinal roll can present the wrong face of an already signed
bend to a fixed upper punch.  Here every pose is a genuine 3D rigid transform
(det=+1), never a mirrored copy of a panel or a changed bend angle.

This module is deliberately read-only and diagnostic.  Only the two yaw
positions (roll=False) may be used by FAST ranking until punch-face,
backgauge, transfer and continuous 3D validation are implemented.
"""
from __future__ import annotations

from dataclasses import dataclass

from .workpiece_placement_3d import PlacementTransform3D


@dataclass(frozen=True)
class LoadingPose25D:
    """One combination of yaw and longitudinal roll, in machine coordinates."""

    reverse_axis: bool
    roll_180: bool

    @property
    def pose_id(self) -> str:
        return ("YAW_180" if self.reverse_axis else "YAW_0") + (
            "_ROLL_180" if self.roll_180 else "_ROLL_0"
        )

    @property
    def eligible_for_fast_ranking(self) -> bool:
        # Rolling an existing signed bend reverses its upper-punch contact
        # normal.  It can be inspected but cannot silently certify clearance.
        return not self.roll_180


def four_loading_poses() -> tuple[LoadingPose25D, ...]:
    """Legacy 2.5D roll x left/right enumeration, with explicit pose IDs."""
    return tuple(
        LoadingPose25D(reverse_axis=yaw, roll_180=roll)
        for roll in (False, True)
        for yaw in (False, True)
    )


def roll_about_machine_bend_axis(
    placement: PlacementTransform3D, *, axis_z_mm: float
) -> PlacementTransform3D:
    """Physically turn the complete part 180 degrees about machine X.

    Machine X is the active bend axis; the target line at (Y=0, Z=axis_z_mm)
    remains fixed.  Unlike a 2D mirror, this rotation preserves handedness,
    distances and all already bent panel geometry.
    """
    z = float(axis_z_mm)
    if not -1.0e12 < z < 1.0e12:
        raise ValueError("nonfinite_machine_bend_axis_height")
    rotation = PlacementTransform3D(
        rotation=((1.0, 0.0, 0.0),
                  (0.0, -1.0, 0.0),
                  (0.0, 0.0, -1.0)),
        translation=(0.0, 0.0, 2.0 * z),
    )
    return placement.then(rotation)


def upper_punch_face_score(fold_state, bend_id: str, placement) -> float:
    """Signed Z component of the fixed flange's punch-contact normal.

    Positive = the intended inner face still points toward the upper punch.
    Negative = the roll has reversed that face.  This orientation criterion
    is necessary but NOT a sufficient bending/clearance certificate.
    """
    bend = fold_state.bends[bend_id]
    zone = getattr(bend, "bend_zone", None)
    signed = getattr(zone, "fold_angle_deg", None)
    if signed is None:
        signed = bend.angle_deg
    up = 1.0 if float(signed) >= 0.0 else -1.0
    fixed = fold_state.transforms[bend.fixed_panel].rotation
    world_normal = tuple(up * float(fixed[i][2]) for i in range(3))
    r = placement.rotation
    return sum(float(r[2][i]) * world_normal[i] for i in range(3))
