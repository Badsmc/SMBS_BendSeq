"""Explicit finite-radius bend-zone geometry for the 2.5D press-brake model.

Pure Python, no FreeCAD dependency.

Frames
------
Sketch frame (SF): SheetMetal flat pattern. The unfold reference face lies in
z = 0 and the material occupies z in [-t, 0]. The bend axis (a -> b) is the
SheetMetal bend line, i.e. the centerline of the flattened bend zone.
``left`` is the in-plane unit normal z x (b - a); the zone spans
[-BA/2, +BA/2] along ``left`` and its tangent lines are the two zone edges.

Sign convention (SheetMetal BendDirection): a positive angle is an UP bend.
For UP the reference face (z = 0) is the inner surface and the moving flange
rises toward +z; for DOWN the reference face is the outer surface and the
moving flange rises toward -z.

Deformation model (documented assumption, not a forming simulation)
------------------------------------------------------------------
* Inner radius is constant for every intermediate angle (air bending on a
  fixed punch radius). Neutral radius rn = r_in + K * t.
* At intermediate |phi| the arc of neutral length rn*|phi| is centered in the
  zone; the remaining (BA - rn*|phi|) stays straight, split equally on both
  sides. Neutral-layer length is therefore exactly BA at every angle and the
  zone is a full arc at the final angle (BA = |theta| * rn by definition).
* Panels are rigid. Inner, neutral and outer surfaces are kept separate.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import cos, sin, radians, degrees, hypot, isfinite

from .panel_kinematics import IDENTITY, RigidTransform, hinge_transform


class BendZoneError(ValueError):
    """Bend parameters are missing, inconsistent or outside the model."""


def _finite(*values):
    return all(isinstance(v, (int, float)) and isfinite(v) for v in values)


@dataclass(frozen=True)
class BendParameters:
    fold_angle_deg: float      # signed bend angle (SheetMetal sign), 0 < |a| < 180
    thickness_mm: float
    k_factor_ansi: float
    inner_radius_mm: float
    neutral_radius_mm: float
    outer_radius_mm: float
    bend_allowance_mm: float


def bend_parameters(source_angle_deg, reference_radius_mm, thickness_mm, k_factor_ansi):
    """Derive bend parameters from SheetMetal BendInfo values.

    source_angle_deg: SheetMetal BendInfo.angle = sign * (180 - |theta|).
    reference_radius_mm: BendInfo.radius, the reference-face-side cylinder.
    BA = |theta_rad| * (inner_radius + K * thickness).
    """
    if not _finite(source_angle_deg, reference_radius_mm, thickness_mm, k_factor_ansi):
        raise BendZoneError("Missing or non-finite bend parameter")
    if not 0 < abs(source_angle_deg) < 180:
        raise BendZoneError("Unsupported SheetMetal angle {!r}".format(source_angle_deg))
    if thickness_mm <= 0 or reference_radius_mm <= 0:
        raise BendZoneError("Thickness and radius must be positive")
    if not 0 <= k_factor_ansi <= 1:
        raise BendZoneError("ANSI K-factor must be within [0, 1]")
    sign = 1.0 if source_angle_deg > 0 else -1.0
    fold = sign * (180.0 - abs(source_angle_deg))
    inner = reference_radius_mm if sign > 0 else reference_radius_mm - thickness_mm
    if inner < -1e-9:
        raise BendZoneError("Reference radius {} smaller than thickness {} for a DOWN "
                            "bend".format(reference_radius_mm, thickness_mm))
    inner = max(inner, 0.0)
    neutral = inner + k_factor_ansi * thickness_mm
    return BendParameters(fold, float(thickness_mm), float(k_factor_ansi), inner,
                          neutral, inner + thickness_mm, radians(abs(fold)) * neutral)


@dataclass(frozen=True)
class BendZone:
    bend_id: str
    fold_angle_deg: float
    thickness_mm: float
    k_factor_ansi: float
    inner_radius_mm: float
    neutral_radius_mm: float
    outer_radius_mm: float
    bend_allowance_mm: float
    axis_xy: tuple             # ((ax, ay), (bx, by)) in SF; zone centerline
    origin_xy: tuple           # axis start a
    axis_dir_xy: tuple         # unit (b - a)
    left_xy: tuple             # unit z x axis_dir

    @property
    def half_width_mm(self):
        return self.bend_allowance_mm / 2.0

    def tangent_lines(self):
        """(left_tangent, right_tangent) segments in SF, offset +-BA/2."""
        (ax, ay), (bx, by) = self.axis_xy
        lx, ly = self.left_xy
        h = self.half_width_mm
        return (((ax + lx * h, ay + ly * h), (bx + lx * h, by + ly * h)),
                ((ax - lx * h, ay - ly * h), (bx - lx * h, by - ly * h)))

    def signed_offset(self, xy):
        """Signed distance of a SF point from the zone centerline (+ = left)."""
        return ((xy[0] - self.origin_xy[0]) * self.left_xy[0]
                + (xy[1] - self.origin_xy[1]) * self.left_xy[1])

    # -- section geometry -------------------------------------------------
    def _check_angle(self, angle_deg):
        if not _finite(angle_deg):
            raise BendZoneError("Non-finite angle")
        if abs(angle_deg) > abs(self.fold_angle_deg) + 1e-9:
            raise BendZoneError("{}: |angle| exceeds the source bend; BA would be "
                                "violated".format(self.bend_id))
        return radians(abs(angle_deg))

    def _up_sign(self, angle_deg=None):
        if angle_deg is not None and angle_deg != 0:
            return 1.0 if angle_deg > 0 else -1.0
        return 1.0 if self.fold_angle_deg > 0 else -1.0

    def section_heights(self, angle_deg=None):
        """(z_center, z_neutral) of the arc center and neutral layer in SF."""
        t, k, r = self.thickness_mm, self.k_factor_ansi, self.inner_radius_mm
        if self._up_sign(angle_deg) > 0:
            return r, -k * t
        return -t - r, -t + k * t

    def arc_start_offset(self, angle_deg):
        """Offset (from the centerline, + toward the moving side) where the
        neutral arc starts at angle_deg. Equals -BA/2 at the final angle."""
        phi = self._check_angle(angle_deg)
        return -self.half_width_mm + (self.bend_allowance_mm - self.neutral_radius_mm * phi) / 2

    def section_profile(self, angle_deg, segments=16):
        """Section polylines (s, z) for the zone at angle_deg.

        s is measured from the centerline toward the moving side in the flat
        state. Returns dict with 'neutral', 'inner', 'outer' polylines and
        'neutral_length'. Inner/outer are the concave/convex surfaces.
        """
        phi = self._check_angle(angle_deg)
        if segments < 1:
            raise BendZoneError("segments must be >= 1")
        rn = self.neutral_radius_mm
        rest = (self.bend_allowance_mm - rn * phi) / 2
        zc, zn = self.section_heights(angle_deg)
        s0 = -self.half_width_mm + rest
        up = self._up_sign(angle_deg)
        out = {}
        for name, radius in (("neutral", rn), ("inner", self.inner_radius_mm),
                             ("outer", self.outer_radius_mm)):
            z0 = zc - up * radius            # flat height of this surface
            pts = [(-self.half_width_mm, z0), (s0, z0)]
            for i in range(1, segments + 1):
                a = phi * i / segments
                pts.append((s0 + radius * sin(a), zc - up * radius * cos(a)))
            ex, ez = cos(phi), up * sin(phi)
            end = pts[-1]
            pts.append((end[0] + rest * ex, end[1] + rest * ez))
            out[name] = tuple(pts)
        out["neutral_length"] = 2 * rest + rn * phi
        return out

    def moving_transform(self, angle_deg, moving_left):
        """Rigid SF transform of the moving side for a single bend.

        Maps the flat neutral point at the moving tangent line onto the end of
        the folded neutral profile. Identity at 0 degrees.
        """
        phi = self._check_angle(angle_deg)
        if phi == 0.0:
            return RigidTransform()
        side = 1.0 if moving_left else -1.0
        u = (self.left_xy[0] * side, self.left_xy[1] * side, 0.0)
        up = self._up_sign(angle_deg)
        e = (0.0, 0.0, up)
        w = (u[1] * e[2] - u[2] * e[1], u[2] * e[0] - u[0] * e[2], u[0] * e[1] - u[1] * e[0])
        zc, _ = self.section_heights(angle_deg)
        s0 = self.arc_start_offset(angle_deg)
        ox, oy = self.origin_xy
        c = (ox + u[0] * s0, oy + u[1] * s0, zc)
        length = self.neutral_radius_mm * phi
        shift = RigidTransform(IDENTITY, (-u[0] * length, -u[1] * length, 0.0))
        hinge = hinge_transform(c, (c[0] + w[0], c[1] + w[1], c[2] + w[2]), degrees(phi))
        return shift.then(hinge)


def make_bend_zone(bend_id, axis_xy, params):
    """Build a BendZone from a SF axis segment and BendParameters."""
    if not isinstance(params, BendParameters):
        raise BendZoneError("BendParameters required")
    (ax, ay), (bx, by) = axis_xy
    if not _finite(ax, ay, bx, by):
        raise BendZoneError("Non-finite axis")
    length = hypot(bx - ax, by - ay)
    if length <= 1e-9:
        raise BendZoneError("Degenerate axis")
    d = ((bx - ax) / length, (by - ay) / length)
    return BendZone(bend_id, params.fold_angle_deg, params.thickness_mm,
                    params.k_factor_ansi, params.inner_radius_mm,
                    params.neutral_radius_mm, params.outer_radius_mm,
                    params.bend_allowance_mm, ((ax, ay), (bx, by)), (ax, ay), d,
                    (-d[1], d[0]))
