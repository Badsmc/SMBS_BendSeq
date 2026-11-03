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
        if not isfinite(angle_deg):
            raise KinematicsError("Nonfinite angle")
        if abs(angle_deg) > 1e-12 and (angle_deg > 0) != (bend.angle_deg > 0):
            raise KinematicsError(
                "{}: angle {} has the opposite sign of target bend {}".format(
                    bend.bend_id, angle_deg, bend.angle_deg
                )
            )
        if abs(angle_deg) > abs(bend.angle_deg) + 1e-9:
            raise KinematicsError(
                "{}: |angle| {} exceeds target bend {}".format(
                    bend.bend_id, abs(angle_deg), abs(bend.angle_deg)
                )
            )
        zone = getattr(bend, "bend_zone", None)
        if zone is not None:
            return zone.moving_transform(angle_deg, moving_left=bend.moving_left)
        if abs(angle_deg) < 1e-12:
            return RigidTransform()
        a, b = bend.axis_xyz
        return hinge_transform(a, b, angle_deg)

    def _compute_transforms(self, candidate_angles):
        transforms = {self.fixed_panel: RigidTransform()}
        for bend in self.ordered_bends:
            angle = candidate_angles[bend.bend_id]
            local = self._local_bend_transform(bend, angle)
            transforms[bend.moving_panel] = local.then(transforms[bend.fixed_panel])
        return {p: transforms[p] for p in self.graph.panels}

    def set_angle(self, bend_id, angle_deg):
        """Atomically set one bend to an absolute angle, preserving all other hinge states.

        Recomputes transforms from flat state so repeated updates never drift.
        If validation or transform computation fails, state remains unchanged.
        """
        if bend_id not in self.bends:
            raise KinematicsError("Unknown bend: " + str(bend_id))
        if not isfinite(angle_deg):
            raise KinematicsError("Nonfinite angle")
        candidate_angles = dict(self.angles)
        candidate_angles[bend_id] = 0.0 if abs(float(angle_deg)) < 1e-12 else float(angle_deg)
        candidate_transforms = self._compute_transforms(candidate_angles)
        self.angles = candidate_angles
        self.transforms = candidate_transforms

    def set_single_bend_angle(self, bend_id, angle_deg):
        """Atomically set bend_id to angle_deg while holding all other bends at 0 deg."""
        if bend_id not in self.bends:
            raise KinematicsError("Unknown bend: " + str(bend_id))
        if not isfinite(angle_deg):
            raise KinematicsError("Nonfinite angle")
        candidate_angles = {b: 0.0 for b in self.bends}
        candidate_angles[bend_id] = 0.0 if abs(float(angle_deg)) < 1e-12 else float(angle_deg)
        candidate_transforms = self._compute_transforms(candidate_angles)
        self.angles = candidate_angles
        self.transforms = candidate_transforms

    def reset_all(self):
        """Atomically reset all bends to 0 deg (flat state)."""
        self.angles = {b: 0.0 for b in self.bends}
        self.transforms = {p: RigidTransform() for p in self.graph.panels}

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

    def cross_section_2d(self, bend_id, panel_vertices_xy, segments=24):
        """Compute 2D cross-section geometry (s, z) of the sheet for bend_id.

        Uses FoldState panel transforms for the rigid panels and BendZone
        section_profile for the finite-radius bend zone and both sheet surfaces.
        """
        if bend_id not in self.bends:
            raise KinematicsError("Unknown bend: " + str(bend_id))
        bend = self.bends[bend_id]
        zone = getattr(bend, "bend_zone", None)
        if zone is None:
            raise KinematicsError("Bend has no BendZone: " + str(bend_id))
        if bend.fixed_panel not in panel_vertices_xy or bend.moving_panel not in panel_vertices_xy:
            raise KinematicsError("Missing panel vertices for bend " + str(bend_id))

        ox, oy = zone.origin_xy
        side = 1.0 if bend.moving_left else -1.0
        ux, uy = zone.left_xy[0] * side, zone.left_xy[1] * side
        t = zone.thickness_mm
        half_ba = zone.half_width_mm

        def flat_s(xy):
            return (xy[0] - ox) * ux + (xy[1] - oy) * uy

        def project_panel_pt(panel_id, s_val, z_val):
            px = ox + ux * s_val
            py = oy + uy * s_val
            qx, qy, qz = self.transforms[panel_id].point((px, py, z_val))
            return ((qx - ox) * ux + (qy - oy) * uy, qz)

        fixed_s = [flat_s(pt) for pt in panel_vertices_xy[bend.fixed_panel]]
        moving_s = [flat_s(pt) for pt in panel_vertices_xy[bend.moving_panel]]
        s_fixed_min = min(fixed_s)
        s_moving_max = max(moving_s)
        if s_fixed_min >= -half_ba - 1e-6 or s_moving_max <= half_ba + 1e-6:
            raise KinematicsError("Degenerate panel extent along cross-section for " + str(bend_id))

        fixed_top = (
            project_panel_pt(bend.fixed_panel, s_fixed_min, 0.0),
            project_panel_pt(bend.fixed_panel, -half_ba, 0.0),
        )
        fixed_bottom = (
            project_panel_pt(bend.fixed_panel, s_fixed_min, -t),
            project_panel_pt(bend.fixed_panel, -half_ba, -t),
        )
        moving_top = (
            project_panel_pt(bend.moving_panel, half_ba, 0.0),
            project_panel_pt(bend.moving_panel, s_moving_max, 0.0),
        )
        moving_bottom = (
            project_panel_pt(bend.moving_panel, half_ba, -t),
            project_panel_pt(bend.moving_panel, s_moving_max, -t),
        )

        prof = self.zone_section(bend_id, segments=segments)
        parent_tf = self.transforms[bend.fixed_panel]

        def transform_zone_curve(pts):
            out = []
            for s_val, z_val in pts:
                qx, qy, qz = parent_tf.point((ox + ux * s_val, oy + uy * s_val, z_val))
                out.append(((qx - ox) * ux + (qy - oy) * uy, qz))
            return tuple(out)

        inner_curve = transform_zone_curve(prof["inner"])
        neutral_curve = transform_zone_curve(prof["neutral"])
        outer_curve = transform_zone_curve(prof["outer"])

        top_zone = inner_curve if zone.fold_angle_deg > 0 else outer_curve
        bottom_zone = outer_curve if zone.fold_angle_deg > 0 else inner_curve

        other_polygons = []
        for pid in self.graph.panels:
            if pid in (bend.fixed_panel, bend.moving_panel) or pid not in panel_vertices_xy:
                continue
            pts_s = [flat_s(pt) for pt in panel_vertices_xy[pid]]
            if not pts_s:
                continue
            s0_p, s1_p = min(pts_s), max(pts_s)
            if s1_p - s0_p <= 1e-6:
                continue
            other_polygons.append((
                pid,
                (
                    project_panel_pt(pid, s0_p, 0.0),
                    project_panel_pt(pid, s1_p, 0.0),
                    project_panel_pt(pid, s1_p, -t),
                    project_panel_pt(pid, s0_p, -t),
                ),
            ))

        return {
            "bend_id": bend_id,
            "fixed_panel": bend.fixed_panel,
            "moving_panel": bend.moving_panel,
            "angle_deg": self.angles[bend_id],
            "target_angle_deg": bend.angle_deg,
            "thickness_mm": t,
            "inner_radius_mm": zone.inner_radius_mm,
            "neutral_radius_mm": zone.neutral_radius_mm,
            "outer_radius_mm": zone.outer_radius_mm,
            "bend_allowance_mm": zone.bend_allowance_mm,
            "fixed_length_mm": (-half_ba) - s_fixed_min,
            "moving_length_mm": s_moving_max - half_ba,
            "fixed_polygon": (fixed_top[0], fixed_top[1], fixed_bottom[1], fixed_bottom[0]),
            "moving_polygon": (moving_top[0], moving_top[1], moving_bottom[1], moving_bottom[0]),
            "other_panel_polygons": tuple(other_polygons),
            "zone_polygon": inner_curve + tuple(reversed(outer_curve)),
            "inner_surface": (
                (fixed_top[0],) + inner_curve + (moving_top[1],)
                if zone.fold_angle_deg > 0
                else (fixed_bottom[0],) + inner_curve + (moving_bottom[1],)
            ),
            "outer_surface": (
                (fixed_bottom[0],) + outer_curve + (moving_bottom[1],)
                if zone.fold_angle_deg > 0
                else (fixed_top[0],) + outer_curve + (moving_top[1],)
            ),
            "neutral_curve": neutral_curve,
            "tangent_points_top": (fixed_top[1], moving_top[0]),
            "tangent_points_bottom": (fixed_bottom[1], moving_bottom[0]),
        }

    def transformed_outline(self, panel_id, xy_points):
        """Return 3D points of a flat panel polygon after current folding."""
        if panel_id not in self.transforms:
            raise KinematicsError("Unknown panel")
        return tuple(self.transforms[panel_id].point((x, y, 0.))
                     for x, y in xy_points)

    def snapshot(self):
        return {p: self.transforms[p] for p in self.graph.panels}
