"""Rigid panel hinge kinematics for a 2D-screen preview of a 3D fold.

The graph is derived from a 2D flat pattern, but an out-of-plane fold
requires 3D rotation. No collision, bend radius, or machine feasibility
is asserted. Positive angles follow the right-hand rule along axis_xyz.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import cos, sin, sqrt, radians, isfinite

class KinematicsError(ValueError):
    pass

IDENTITY = ((1.,0.,0.), (0.,1.,0.), (0.,0.,1.))
ZERO = (0.,0.,0.)

@dataclass(frozen=True)
class RigidTransform:
    rotation: tuple = IDENTITY
    translation: tuple = ZERO

    def point(self, xyz):
        return tuple(sum(self.rotation[i][j] * xyz[j] for j in range(3))
                     + self.translation[i] for i in range(3))

    def then(self, after):
        """Apply self, then after."""
        r = tuple(tuple(sum(after.rotation[i][k] * self.rotation[k][j]
                            for k in range(3)) for j in range(3)) for i in range(3))
        t = after.point(self.translation)
        return RigidTransform(r, t)

def hinge_transform(a, b, angle_deg):
    """Right-handed rigid rotation around the directed world-space axis a->b."""
    if not isfinite(angle_deg):
        raise KinematicsError("Nonfinite angle")
    d = tuple(b[i] - a[i] for i in range(3))
    length = sqrt(sum(v*v for v in d))
    if length < 1e-8:
        raise KinematicsError("Degenerate hinge axis")
    x, y, z = (v / length for v in d)
    c, s = cos(radians(angle_deg)), sin(radians(angle_deg))
    k = 1 - c
    r = ((c+x*x*k, x*y*k-z*s, x*z*k+y*s),
         (y*x*k+z*s, c+y*y*k, y*z*k-x*s),
         (z*x*k-y*s, z*y*k+x*s, c+z*z*k))
    origin_rotated = tuple(sum(r[i][j] * a[j] for j in range(3)) for i in range(3))
    return RigidTransform(r, tuple(a[i] - origin_rotated[i] for i in range(3)))

class FoldState:
    """Absolute rigid transforms for each panel; initial flat state is identity.

    Transforms are recomputed deterministically from flat along the rooted tree
    on every angle change, preventing cumulative numerical drift. When a bend
    carries an explicit BendZone, finite-radius tangent/allowance kinematics is
    used; otherwise falls back to the centerline hinge transform.
    """
    def __init__(self, graph, fixed_panel):
        self.graph = graph
        self.fixed_panel = fixed_panel
        self.ordered_bends = tuple(graph.rooted(fixed_panel))
        self.bends = {b.bend_id: b for b in self.ordered_bends}
        self.transforms = {p: RigidTransform() for p in graph.panels}
        self.angles = {bend_id: 0.0 for bend_id in self.bends}

    def _local_bend_transform(self, bend, angle_deg):
        zone = getattr(bend, "bend_zone", None)
        if zone is not None:
            return zone.moving_transform(angle_deg, moving_left=bend.moving_left)
        if abs(angle_deg) < 1e-12:
            return RigidTransform()
        a, b = bend.axis_xyz
        return hinge_transform(a, b, angle_deg)

    def _recompute(self):
        transforms = {self.fixed_panel: RigidTransform()}
        for bend in self.ordered_bends:
            angle = self.angles[bend.bend_id]
            local = self._local_bend_transform(bend, angle)
            transforms[bend.moving_panel] = local.then(transforms[bend.fixed_panel])
        for p in self.graph.panels:
            self.transforms[p] = transforms[p]

    def set_angle(self, bend_id, angle_deg):
        """Set one bend to an absolute angle, preserving all other hinge states.

        Recomputes transforms from flat state so repeated updates never drift.
        """
        if bend_id not in self.bends:
            raise KinematicsError("Unknown bend: " + str(bend_id))
        if not isfinite(angle_deg):
            raise KinematicsError("Nonfinite angle")
        bend = self.bends[bend_id]
        # Validate against bend zone if present before mutating state
        self._local_bend_transform(bend, float(angle_deg))
        self.angles[bend_id] = float(angle_deg)
        self._recompute()

    def set_fraction(self, bend_id, fraction):
        if not isfinite(fraction) or not 0 <= fraction <= 1:
            raise KinematicsError("Fraction must be between zero and one")
        self.set_angle(bend_id, self.bends[bend_id].angle_deg * fraction)

    def zone_section(self, bend_id, segments=16):
        """Return intermediate section profile dict for a bend with a BendZone."""
        if bend_id not in self.bends:
            raise KinematicsError("Unknown bend: " + str(bend_id))
        bend = self.bends[bend_id]
        if getattr(bend, "bend_zone", None) is None:
            raise KinematicsError("Bend has no BendZone: " + str(bend_id))
        return bend.bend_zone.section_profile(self.angles[bend_id], segments=segments)

    def zone_surface_points(self, bend_id, surface="neutral", segments=16):
        """Return 3D world-space polyline on the bend zone cross-section midpoint."""
        if surface not in ("inner", "neutral", "outer"):
            raise KinematicsError("Unsupported surface: " + str(surface))
        prof = self.zone_section(bend_id, segments=segments)
        bend = self.bends[bend_id]
        zone = bend.bend_zone
        (ax, ay), (bx, by) = zone.axis_xy
        mx, my = 0.5 * (ax + bx), 0.5 * (ay + by)
        side = 1.0 if bend.moving_left else -1.0
        ux, uy = zone.left_xy[0] * side, zone.left_xy[1] * side
        parent_tf = self.transforms[bend.fixed_panel]
        return tuple(
            parent_tf.point((mx + ux * s, my + uy * s, z))
            for s, z in prof[surface]
        )

    def transformed_outline(self, panel_id, xy_points):
        """Return 3D points of a flat panel polygon after current folding."""
        if panel_id not in self.transforms:
            raise KinematicsError("Unknown panel")
        return tuple(self.transforms[panel_id].point((x, y, 0.))
                     for x, y in xy_points)

    def snapshot(self):
        return {p: self.transforms[p] for p in self.graph.panels}
