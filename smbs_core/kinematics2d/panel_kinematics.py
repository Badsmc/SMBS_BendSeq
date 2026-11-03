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
    """Absolute rigid transforms for each panel; initial flat state is identity."""
    def __init__(self, graph, fixed_panel):
        self.graph = graph
        self.fixed_panel = fixed_panel
        self.bends = {b.bend_id: b for b in graph.rooted(fixed_panel)}
        self.transforms = {p: RigidTransform() for p in graph.panels}
        self.angles = {bend_id: 0.0 for bend_id in self.bends}

    def set_angle(self, bend_id, angle_deg):
        """Set one bend to an absolute angle, preserving all other hinge states.

        The hinge is evaluated in the current frame of its fixed-side panel.
        All panels downstream of the hinge rotate together, including those
        previously bent around other hinges.
        """
        if bend_id not in self.bends:
            raise KinematicsError("Unknown bend: " + str(bend_id))
        if not isfinite(angle_deg):
            raise KinematicsError("Nonfinite angle")
        bend = self.bends[bend_id]
        delta = angle_deg - self.angles[bend_id]
        if abs(delta) < 1e-12:
            return
        parent = self.transforms[bend.fixed_panel]
        a, b = (parent.point(p) for p in bend.axis_xyz)
        change = hinge_transform(a, b, delta)
        for panel in bend.moving_subtree:
            self.transforms[panel] = self.transforms[panel].then(change)
        self.angles[bend_id] = float(angle_deg)

    def set_fraction(self, bend_id, fraction):
        if not isfinite(fraction) or not 0 <= fraction <= 1:
            raise KinematicsError("Fraction must be between zero and one")
        self.set_angle(bend_id, self.bends[bend_id].angle_deg * fraction)

    def transformed_outline(self, panel_id, xy_points):
        """Return 3D points of a flat panel polygon after current folding."""
        if panel_id not in self.transforms:
            raise KinematicsError("Unknown panel")
        return tuple(self.transforms[panel_id].point((x, y, 0.))
                     for x, y in xy_points)

    def snapshot(self):
        return {p: self.transforms[p] for p in self.graph.panels}
