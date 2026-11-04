"""Headless machine-space placement of 2.5D folded sheet geometry.

FoldState owns the actual bend angles and finite-radius shapes. Machine pose is
a single rigid transform applied to every sheet component in a FULL_CYCLE
kinematic preview, never to source FreeCAD geometry or the Bend Only view.

Symmetric placement is an idealized free-flange V-bending visualization. The
ram-to-sheet link assumes geometric contact at the active bend zone and is NOT
a contact-mechanics, collision, force or springback solver.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from math import atan2, cos, degrees, hypot, isfinite, radians, sin
from typing import Any, Mapping


class WorkpiecePoseError(ValueError):
    """Unusable active cross-section or unsupported machine placement."""


@dataclass(frozen=True)
class WorkpiecePose2D:
    pivot: tuple[float, float] = (0.0, 0.0)
    rotation_deg: float = 0.0
    mirror_z: bool = False
    shift: tuple[float, float] = (0.0, 0.0)
    mode: str = "IDENTITY"

    def apply(self, point):
        s, z = float(point[0]) - self.pivot[0], float(point[1]) - self.pivot[1]
        if self.mirror_z:
            z = -z
        a = radians(self.rotation_deg)
        return (
            self.pivot[0] + self.shift[0] + cos(a) * s - sin(a) * z,
            self.pivot[1] + self.shift[1] + sin(a) * s + cos(a) * z,
        )

    def inverse(self, point):
        s = float(point[0]) - self.pivot[0] - self.shift[0]
        z = float(point[1]) - self.pivot[1] - self.shift[1]
        a = radians(self.rotation_deg)
        ls, lz = cos(a) * s + sin(a) * z, -sin(a) * s + cos(a) * z
        if self.mirror_z:
            lz = -lz
        return (self.pivot[0] + ls, self.pivot[1] + lz)

    def points(self, points):
        return tuple(self.apply(point) for point in points)


def symmetric_v_pose(section: Mapping[str, Any]) -> WorkpiecePose2D:
    """Orient the actual neighboring flange directions with equal upward rise.

    The relative angle, thickness, bend radius and all sheet topology remain
    untouched. Negative bend signs are visualized with an *assumed* global
    workpiece flip (a mirror in cross-section), not a machine re-gripping model.
    """
    try:
        fixed = section["fixed_polygon"]
        moving = section["moving_polygon"]
        neutral = section["neutral_curve"]
        target = float(section["target_angle_deg"])
        fv = (
            float(fixed[0][0]) - float(fixed[1][0]),
            float(fixed[0][1]) - float(fixed[1][1]),
        )
        mv = (
            float(moving[1][0]) - float(moving[0][0]),
            float(moving[1][1]) - float(moving[0][1]),
        )
        if hypot(*fv) <= 1e-7 or hypot(*mv) <= 1e-7 or not neutral:
            raise WorkpiecePoseError("Degenerate flange or missing bend center")
        if not isfinite(target):
            raise WorkpiecePoseError("Nonfinite target bend angle")
        pivot = tuple(float(v) for v in neutral[len(neutral) // 2])
        if len(pivot) != 2 or not all(isfinite(v) for v in pivot):
            raise WorkpiecePoseError("Invalid bend pivot")
    except (KeyError, IndexError, TypeError, ValueError, OverflowError) as exc:
        raise WorkpiecePoseError("Invalid cross-section for symmetric preview") from exc

    mirror = target < -1e-9
    if mirror:
        fv, mv = (fv[0], -fv[1]), (mv[0], -mv[1])
    if fv[0] >= -1e-7 or mv[0] < -1e-7:
        raise WorkpiecePoseError("Unsupported flange orientation in current 2.5D section")
    left_rise = atan2(fv[1], -fv[0])
    right_rise = atan2(mv[1], mv[0])
    rotation = (left_rise - right_rise) / 2.0
    return WorkpiecePose2D(
        pivot=pivot,
        rotation_deg=degrees(rotation),
        mirror_z=mirror,
        mode="ASSUMED_FLIP_SYMMETRIC_V_PREVIEW" if mirror else "SYMMETRIC_V_PREVIEW",
    )


def machine_pose_for_cycle(
    section: Mapping[str, Any],
    cycle: Any,
    tooling: Any,
) -> WorkpiecePose2D:
    """Place a sheet in press coordinates using the existing ram state.

    The sheet holds still throughout APPROACH/CONTACT, follows the selected
    *preview* punch stroke in BENDING/BOTTOM, then stays in its bent position
    while the punch retracts. An explicit 2.5D zone-envelope assumption is
    used instead of an unverified real contact solution.
    """
    pose = symmetric_v_pose(section)
    try:
        phase = getattr(cycle.phase, "value", cycle.phase)
        contact_z = float(cycle.contact_z_mm)
        bottom_z = float(cycle.bottom_z_mm)
        ram_z = float(cycle.ram_z_mm)
        if not all(isfinite(v) for v in (contact_z, bottom_z, ram_z)):
            raise WorkpiecePoseError("Nonfinite ram coordinate")
    except (AttributeError, ValueError, TypeError) as exc:
        raise WorkpiecePoseError("Press cycle has no valid ram state") from exc

    if phase == "BENDING":
        desired_top = min(contact_z, ram_z)
    elif phase in ("BOTTOM", "RETRACT", "STEP_COMPLETE", "COMPLETED"):
        desired_top = bottom_z
    else:
        desired_top = contact_z

    # The active zone is the only contact-height reference; never use
    # far-flange max Z, which rises during symmetric bending.
    zone = section.get("zone_polygon")
    if not zone:
        raise WorkpiecePoseError("Missing finite-radius bend zone")
    zone_points = pose.points(zone)
    reference_z = max(z for _s, z in zone_points)
    if not isfinite(reference_z):
        raise WorkpiecePoseError("Nonfinite zone envelope")

    machine_s = pose.pivot[0]
    if getattr(tooling, "aligned", False):
        # Align to the actual installed V-groove, not a profile bounding box.
        die_center = tooling.die_v_center_machine()
        if die_center is not None:
            machine_s = float(die_center[0])
        else:
            machine_s = float(tooling.alignment_anchor_sz[0])
    if not isfinite(machine_s):
        raise WorkpiecePoseError("Invalid machine alignment datum")
    return replace(
        pose,
        shift=(machine_s - pose.pivot[0], desired_top - reference_z),
    )


# All geometric keys from FoldState.cross_section_2d() are projected through
# the SAME transform. Nongeometric metadata stays in its source contract.
_SINGLE_POLYGONS = (
    "fixed_polygon", "moving_polygon", "zone_polygon",
    "inner_surface", "outer_surface", "neutral_curve",
    "tangent_points_top", "tangent_points_bottom",
)
_NAMED_POLYGONS = ("other_panel_polygons", "other_zone_polygons")
_CURVE_GROUPS = ("other_inner_curves", "other_outer_curves", "other_neutral_curves")
_LINE_GROUPS = ("other_tangent_lines",)


def transform_sheet_section(section: Mapping[str, Any], pose: WorkpiecePose2D):
    """Return a transformed copy; never mutate an original FoldState section."""
    result = dict(section)
    for key in _SINGLE_POLYGONS:
        if key in section:
            result[key] = pose.points(section[key])
    for key in _NAMED_POLYGONS:
        if key in section:
            result[key] = tuple((ident, pose.points(points)) for ident, points in section[key])
    for key in _CURVE_GROUPS:
        if key in section:
            result[key] = tuple(pose.points(points) for points in section[key])
    for key in _LINE_GROUPS:
        if key in section:
            result[key] = tuple((pose.apply(a), pose.apply(b)) for a, b in section[key])
    return result
