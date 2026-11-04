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

    # Calculate the MACHINE presentation from the actual FoldState vectors.
    # Previously this rejected any pair that was not already left/right
    # horizontal; after B01 on a Z part those directions are no longer flat.
    def heading(v):
        return atan2(v[1], v[0])

    def angle_distance(a, b):
        return atan2(sin(a - b), cos(a - b))

    def unit(v):
        length = hypot(*v)
        return (v[0] / length, v[1] / length)

    fu = unit(fv)
    mu = unit(mv)
    included = max(-1.0, min(1.0, fu[0] * mu[0] + fu[1] * mu[1]))
    # Magnitude of the true opening between adjacent panel rays:
    # 180 deg at flat, 90 deg after a 90 deg bend.
    from math import acos, pi
    opening = acos(included)
    rise = (pi - opening) / 2.0
    flip = target < -1e-9
    desired_fixed = rise if flip else pi - rise
    desired_moving = pi - rise if flip else rise
    f_heading, m_heading = heading(fu), heading(mu)
    correction = angle_distance(desired_fixed, f_heading)
    actual_moving = m_heading + correction
    mismatch = abs(angle_distance(actual_moving, desired_moving))
    # A rigid turn preserves the actual sheet angle. If the two panel rays
    # cannot be put upward at the same time, flag the geometry rather than
    # silently drawing an upside-down press operation.
    if mismatch > 0.035:
        raise WorkpiecePoseError(
            "BEND_PRESENTATION_UNSUPPORTED: active flanges cannot both face upward "
            "without changing their included angle (mismatch {:.1f} deg)".format(
                degrees(mismatch)
            )
        )
    rotation = degrees(correction)
    return WorkpiecePose2D(
        pivot=pivot,
        rotation_deg=rotation,
        mirror_z=False,
        mode=("ASSUMED_RIGID_WORKPIECE_TURN" if flip else "SYMMETRIC_V_PREVIEW"),
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


def upper_zone_at_machine_axis(section, pose, machine_s):
    """Sample upper bend-zone boundary at punch s axis, not zone global max."""
    zone = section.get("zone_polygon")
    if not zone:
        raise WorkpiecePoseError("Cannot track punch: missing bend zone")
    pts = pose.points(tuple(zone) + (zone[0],))
    heights = []
    for a, b in zip(pts, pts[1:]):
        dx = b[0] - a[0]
        if abs(dx) < 1e-10:
            if abs(a[0] - machine_s) <= 1e-6:
                heights.extend((a[1], b[1]))
        else:
            frac = (machine_s - a[0]) / dx
            if -1e-8 <= frac <= 1.0 + 1e-8:
                heights.append(a[1] + frac * (b[1] - a[1]))
    if not heights:
        raise WorkpiecePoseError("Punch axis does not cross the current bend zone")
    return max(heights)


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
    """Place the symmetric workpiece on its verified V-die shoulders.

    A single vertical machine translation is allowed; it MUST NOT rotate the
    sheet away from the symmetric bisector merely to make both shoulders fit.
    If the material geometry cannot simultaneously sit on two shoulders,
    show one real shoulder contact and disclose PARTIAL support instead of
    falling back to a floating, unpositioned sheet.
    """
    if len(shoulders) != 2:
        raise WorkpiecePoseError("DIE_SUPPORT_UNAVAILABLE: two V shoulders required")
    (ls, lz), (rs, rz) = sorted(shoulders, key=lambda item: item[0])
    if not all(isfinite(v) for v in (ls, lz, rs, rz)) or rs - ls <= 1e-6:
        raise WorkpiecePoseError("DIE_SUPPORT_INVALID: coincident/nonfinite shoulders")

    left = _lower_sheet_at_s(section, pose, ls)
    right = _lower_sheet_at_s(section, pose, rs)
    contacts = []
    if left is not None and isfinite(left):
        contacts.append((ls, lz - left))
    if right is not None and isfinite(right):
        contacts.append((rs, rz - right))
    if not contacts:
        raise WorkpiecePoseError(
            "DIE_SUPPORT_MISSING: sheet does not reach either V shoulder"
        )

    # The maximal shift prevents penetration of the shoulder support points.
    # It brings at least one of them into actual contact. It never invents
    # a second contact when the two heights disagree.
    translation_z = max(delta for _s, delta in contacts)
    balanced = (
        len(contacts) == 2
        and abs(contacts[0][1] - contacts[1][1]) <= 0.05
    )
    mode = (
        "_TWO_SHOULDER_SUPPORTED_PREVIEW" if balanced
        else "_PARTIAL_SHOULDER_SUPPORTED_PREVIEW"
    )
    return replace(
        pose,
        shift=(pose.shift[0], translation_z),
        mode=pose.mode + mode,
    )


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


def remap_section_to_reference(section, from_zone, reference_zone):
    """Convert an entire active cross-section to a persistent sheet axis.

    The bend-axis directions must be parallel. A 2D cross-section of bends
    about different axes is not an honest representation: reject it.
    This is only a coordinate basis conversion; no sheet shape or angle moves.
    """
    def axis(zone):
        v = zone.left_xy
        mul = 1.0 if getattr(zone, "moving_left", False) else -1.0
        # moving_left is on BendRecord, not BendZone: the caller passes
        # prepared (zone, side) pairs when necessary.
        return (float(v[0]) * mul, float(v[1]) * mul)

    if isinstance(from_zone, tuple):
        src, src_side = from_zone
    else:
        src, src_side = from_zone, 1.0
    if isinstance(reference_zone, tuple):
        ref, ref_side = reference_zone
    else:
        ref, ref_side = reference_zone, 1.0

    us = (src.left_xy[0] * src_side, src.left_xy[1] * src_side)
    ur = (ref.left_xy[0] * ref_side, ref.left_xy[1] * ref_side)
    n1, n2 = hypot(*us), hypot(*ur)
    if min(n1, n2) < 1e-9:
        raise WorkpiecePoseError("Invalid bend section axis")
    us, ur = (us[0]/n1,us[1]/n1),(ur[0]/n2,ur[1]/n2)
    sign = us[0]*ur[0]+us[1]*ur[1]
    if abs(sign) < 0.999:
        raise WorkpiecePoseError("NONPARALLEL_BEND_AXES: no shared 2.5D projection")
    offset = ((src.origin_xy[0]-ref.origin_xy[0])*ur[0]
              +(src.origin_xy[1]-ref.origin_xy[1])*ur[1])
    def map_point(pt):
        return (sign*float(pt[0])+offset, float(pt[1]))
    result = dict(section)
    for key in _SINGLE_POLYGONS:
        if key in section:
            result[key] = tuple(map_point(p) for p in section[key])
    for key in _NAMED_POLYGONS:
        if key in section:
            result[key] = tuple(
                (ident, tuple(map_point(p) for p in points))
                for ident, points in section[key]
            )
    for key in _CURVE_GROUPS:
        if key in section:
            result[key] = tuple(
                tuple(map_point(p) for p in points) for points in section[key]
            )
    for key in _LINE_GROUPS:
        if key in section:
            result[key] = tuple((map_point(a),map_point(b)) for a,b in section[key])
    return result


def _origin_translation(pose):
    """Affine origin offset for the rigid WorkpiecePose2D rotation."""
    a = radians(pose.rotation_deg)
    px,pz = pose.pivot
    return (
        px + pose.shift[0] - cos(a)*px + sin(a)*pz,
        pz + pose.shift[1] - sin(a)*px - cos(a)*pz,
    )


def interpolate_rigid_placement(start, end, fraction, clearance_mm=0.0):
    """Rigid continuous transition between two machine-space sheet poses.

    Both endpoints are exact; no frame reset or interpolated sheet geometry.
    During transit the same complete FoldState shape is rotated/translated.
    """
    t = max(0.0,min(1.0,float(fraction)))
    if not isfinite(t) or clearance_mm < 0 or not isfinite(clearance_mm):
        raise WorkpiecePoseError("Invalid transfer interpolation")
    if t <= 0.0:
        return start
    if t >= 1.0:
        return end
    ease=lambda x: x*x*(3.0-2.0*x)
    f=ease(t)
    a0 = float(start.rotation_deg)
    diff = (float(end.rotation_deg)-a0+180.0)%360.0-180.0
    # For exact opposite orientation explicitly retain the positive flip.
    if abs(abs(diff)-180.0)<1e-7:
        diff = 180.0
    angle = a0 + diff*f
    x0,z0=_origin_translation(start)
    x1,z1=_origin_translation(end)
    # Smooth vertical clearance rises/falls to zero at both endpoints.
    lift = clearance_mm * sin(3.141592653589793*t)**2
    return WorkpiecePose2D(
        rotation_deg=angle,
        shift=(x0+(x1-x0)*f, z0+(z1-z0)*f+lift),
        mode="RIGID_CONTINUOUS_WORKPIECE_TRANSFER",
    )


def animated_machine_pose_for_cycle(section, cycle, tooling, next_section=None):
    """Active machine pose or continuous sheet transfer to next bend placement.

    The next_section MUST already be expressed in the same fixed sheet frame.
    """
    pose = machine_pose_for_cycle(section, cycle, tooling)
    phase = getattr(getattr(cycle, "phase", None), "value", getattr(cycle, "phase", None))
    if phase != "TRANSFER":
        return pose
    if next_section is None:
        raise WorkpiecePoseError("TRANSFER_TARGET_MISSING: next bend pose not supplied")
    end_pose = machine_pose_for_cycle(next_section, cycle, tooling)
    return interpolate_rigid_placement(
        pose, end_pose, cycle.transfer_progress, cycle.transfer_lift_mm
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
