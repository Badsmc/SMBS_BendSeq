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


def _lower_sheet_at_s(section, pose, s_coordinate):
    """Lowest sheet-surface height at the selected shoulder coordinate.

    This samples the actual finite-radius surface curves and all visible
    rigid-panel outlines after the *same* global workpiece pose. If several
    surfaces share s, choose the lowest exterior point. No bbox datums.
    """
    curves = []
    for key in ("inner_surface", "outer_surface"):
        if section.get(key):
            curves.append(section[key])
    for key in ("fixed_polygon", "moving_polygon", "zone_polygon"):
        if section.get(key):
            points = section[key]
            curves.append(tuple(points) + (points[0],))
    # The V shoulders support the two panels adjacent to the ACTIVE bend.
    # Other already-folded Z flanges are not alternative 'support sheets':
    # including them picked an unrelated lower surface and created floating.
    heights = []
    for curve in curves:
        pts = pose.points(curve)
        for a, b in zip(pts, pts[1:]):
            left, right = a[0], b[0]
            if abs(left - right) < 1e-10:
                if abs(left - s_coordinate) < 1e-6:
                    heights.extend((a[1], b[1]))
                continue
            t = (s_coordinate - left) / (right - left)
            if -1e-8 <= t <= 1.0 + 1e-8:
                heights.append(a[1] + t * (b[1] - a[1]))
    return min(heights) if heights else None


def _die_shoulders_machine(tooling):
    """Return actual left/right V-shoulder points in machine coordinates."""
    die = getattr(tooling, "die", None)
    if die is None or getattr(die, "die_datums", None) is None:
        return ()
    datums = die.die_datums
    to_local = die.source_to_local_transform()
    machine_tf = tooling.die_machine_transform
    return tuple(
        machine_tf.apply(to_local.apply(point))
        for point in (datums.left_shoulder, datums.right_shoulder)
    )


def _two_shoulder_support(section, pose, shoulders):
    """Solve vertical placement and a small whole-sheet tilt from both supports.

    A single translation cannot satisfy two unequal contact heights. The tilt
    is a machine-space placement correction, NOT a change to FoldState angles.
    Never claim support if a shoulder lies outside the actual sheet profile.
    """
    if len(shoulders) != 2:
        raise WorkpiecePoseError("DIE_SUPPORT_UNAVAILABLE: two V shoulders required")
    (ls, lz), (rs, rz) = sorted(shoulders, key=lambda item: item[0])
    if not all(isfinite(v) for v in (ls, lz, rs, rz)) or rs - ls <= 1e-6:
        raise WorkpiecePoseError("DIE_SUPPORT_INVALID: coincident or nonfinite shoulders")

    def levels(rotation_deg):
        trial = replace(pose, rotation_deg=rotation_deg)
        left = _lower_sheet_at_s(section, trial, ls)
        right = _lower_sheet_at_s(section, trial, rs)
        if left is None or right is None:
            return None
        return (left - lz, right - rz)

    base = levels(pose.rotation_deg)
    if base is None:
        raise WorkpiecePoseError("DIE_SUPPORT_MISSING: sheet does not span both V shoulders")

    # At equal support heights a pure vertical translation is sufficient.
    delta = base[0] - base[1]
    rotation = pose.rotation_deg
    if abs(delta) > 0.05:
        # A limited correction of machine placement, not of the bend angle.
        # Never rotate a nominally symmetric bend far away from its bisector.
        limit = 6.0
        lo, hi = rotation - limit, rotation + limit
        low, high = levels(lo), levels(hi)
        if low is None or high is None:
            raise WorkpiecePoseError("DIE_SUPPORT_MISSING: shoulder contact lost during positioning")
        f_lo = low[0] - low[1]
        f_hi = high[0] - high[1]
        if f_lo * f_hi > 0.0:
            raise WorkpiecePoseError(
                "DIE_SUPPORT_IMBALANCE: both shoulders cannot be seated within 6 deg"
            )
        for _ in range(32):
            mid = (lo + hi) / 2.0
            values = levels(mid)
            if values is None:
                raise WorkpiecePoseError("DIE_SUPPORT_MISSING: shoulder contact lost")
            f_mid = values[0] - values[1]
            if abs(f_mid) < 1e-7:
                lo = hi = mid
                break
            if f_lo * f_mid <= 0.0:
                hi, f_hi = mid, f_mid
            else:
                lo, f_lo = mid, f_mid
        rotation = (lo + hi) / 2.0

    adjusted = replace(pose, rotation_deg=rotation)
    pair = levels(rotation)
    if pair is None:
        raise WorkpiecePoseError("DIE_SUPPORT_MISSING: unsupported final position")
    # A valid support offset cannot be based on just one shoulder.
    if abs(pair[0] - pair[1]) > 0.05:
        raise WorkpiecePoseError("DIE_SUPPORT_IMBALANCE: unresolved shoulder separation")
    shift_z = -0.5 * (pair[0] + pair[1])
    result = replace(
        adjusted,
        shift=(adjusted.shift[0], shift_z),
        mode=adjusted.mode + "_TWO_SHOULDER_SUPPORTED_PREVIEW",
    )
    return result


def machine_pose_for_cycle(
    section: Mapping[str, Any],
    cycle: Any,
    tooling: Any,
) -> WorkpiecePose2D:
    """Place folded sheet on both die shoulders in FULL_CYCLE preview.

    The relative flange angle comes exclusively from FoldState. We try two
    actual working shoulders, using one rigid placement shared by every sheet
    surface. If that fit is impossible we expose a precise diagnostic instead
    of silently claiming that the sheet is supported.

    Kinematic support does NOT establish punch contact, force or collision
    freedom and does NOT compute a physically feasible ram trajectory.
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

    zone = section.get("zone_polygon")
    if not zone:
        raise WorkpiecePoseError("Missing finite-radius bend zone")

    machine_s = pose.pivot[0]
    if getattr(tooling, "aligned", False):
        center = tooling.die_v_center_machine()
        if center is not None:
            machine_s = float(center[0])
        else:
            machine_s = float(tooling.alignment_anchor_sz[0])
    if not isfinite(machine_s):
        raise WorkpiecePoseError("Invalid machine alignment datum")
    pose = replace(pose, shift=(machine_s - pose.pivot[0], 0.0))

    shoulders = _die_shoulders_machine(tooling) if getattr(tooling, "aligned", False) else ()
    if shoulders:
        return _two_shoulder_support(section, pose, shoulders)

    # No verified shoulders: preserve prior ram-only preview but explicitly
    # label missing real support. Never describe this as DIE_SUPPORTED.
    if phase == "BENDING":
        desired_top = min(contact_z, ram_z)
    elif phase in ("BOTTOM", "RETRACT", "STEP_COMPLETE", "COMPLETED"):
        desired_top = bottom_z
    else:
        desired_top = contact_z
    zone_top = max(z for _s, z in pose.points(zone))
    return replace(
        pose, shift=(pose.shift[0], desired_top - zone_top),
        mode=pose.mode + "_DIE_SUPPORT_UNKNOWN_RAM_ONLY",
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
