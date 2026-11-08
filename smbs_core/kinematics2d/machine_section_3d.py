"""Cutting Plane Section Adapter (Machine X_m = target_xm).

Constructs the true, physical 2.5D cross-section of the sheet workpiece
by slicing placed panel geometry with the machine plane X_m = target_xm.

Coordinate Convention:
- Slicing plane: X_m = target_xm
- Machine Space: (X_m, Y_m, Z_m)
- Output 2D Section: s = -Y_m, z = Z_m
  (Matches existing 2.5D screen mapping: horizontal s, vertical z).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .fold_sequence import ANGLE_EPS
from .panel_kinematics import FoldState
from .workpiece_placement_3d import PlacementTransform3D, WorkpiecePlacement3D
from .workpiece_pose_2d import WorkpiecePoseError


def slice_polygon_3d_at_x(
    polygon_3d: Sequence[Tuple[float, float, float]],
    target_x: float = 0.0,
    tol: float = 1e-6,
) -> List[Tuple[float, float]]:
    """Intersect a 3D planar polygon perimeter with the plane X = target_x.

    Returns:
        List of intersection points (Y, Z) in the cutting plane.
    """
    n = len(polygon_3d)
    if n < 3:
        return []

    intersections: List[Tuple[float, float]] = []
    for i in range(n):
        j = (i + 1) % n
        p1 = polygon_3d[i]
        p2 = polygon_3d[j]

        x1, y1, z1 = p1
        x2, y2, z2 = p2

        # Check if segment crosses target_x
        min_x = min(x1, x2)
        max_x = max(x1, x2)

        if min_x <= target_x + tol and max_x >= target_x - tol:
            dx = x2 - x1
            if abs(dx) > 1e-9:
                u = (target_x - x1) / dx
                if -1e-6 <= u <= 1.0 + 1e-6:
                    u_clamped = min(1.0, max(0.0, u))
                    y = y1 + u_clamped * (y2 - y1)
                    z = z1 + u_clamped * (z2 - z1)
                    # Deduplicate consecutive identical points
                    if not intersections or math.dist(intersections[-1], (y, z)) > 1e-7:
                        intersections.append((y, z))
            else:
                # Segment lies completely on target_x
                for pt in (p1, p2):
                    yz = (pt[1], pt[2])
                    if not intersections or math.dist(intersections[-1], yz) > 1e-7:
                        intersections.append(yz)

    # If first and last points match, drop last
    if len(intersections) > 1 and math.dist(intersections[0], intersections[-1]) < 1e-7:
        intersections.pop()

    return intersections


def compute_machine_cutting_plane_section(
    fold_state: FoldState,
    panel_vertices_xy: Dict[str, Sequence[Tuple[float, float]]],
    placement: PlacementTransform3D,
    active_bend_id: Optional[str] = None,
    segments: int = 16,
    *,
    target_xm: float = 0.0,
) -> Dict[str, Any]:
    """Compute a 2.5D section at machine plane X_m = target_xm.

    Uses FoldState as single source of truth for folded angles and panel positions.
    Projects every panel and bend zone through `placement` into Machine Space,
    then intersects with plane X_m = 0.

    Returns:
        dict compatible with 2.5D Viewer drawing pipeline (s, z coordinates).
    """
    if not math.isfinite(float(target_xm)):
        raise ValueError("target_xm must be finite")
    target_xm = float(target_xm)
    thickness_mm = None
    inner_radius_mm = None
    if active_bend_id and active_bend_id in fold_state.bends:
        bz = fold_state.bends[active_bend_id].bend_zone
        if bz is not None:
            thickness_mm = bz.thickness_mm
            inner_radius_mm = bz.inner_radius_mm
    if thickness_mm is None or inner_radius_mm is None:
        for b in fold_state.bends.values():
            if b.bend_zone is not None:
                thickness_mm = b.bend_zone.thickness_mm
                inner_radius_mm = b.bend_zone.inner_radius_mm
                break
    if thickness_mm is None or inner_radius_mm is None:
        raise WorkpiecePoseError("UNSUPPORTED: Missing authoritative BendZone thickness and radius data.")

    section: Dict[str, Any] = {
        "active_bend_id": active_bend_id or "",
        "thickness_mm": thickness_mm,
        "inner_radius_mm": inner_radius_mm,
        "fixed_polygon": (),
        "moving_polygon": (),
        "zone_polygon": (),
        "inner_surface": (),
        "outer_surface": (),
        "neutral_curve": (),
        "other_panel_polygons": [],
        "other_zone_polygons": [],
        "tangent_points_top": (),
        "tangent_points_bottom": (),
        "other_tangent_lines": (),
        "other_inner_curves": [],
        "other_outer_curves": [],
        "other_neutral_curves": [],
        "cutting_plane_xm": target_xm,
    }

    # 1. Panels intersecting X_m = target_xm
    cut_panel_polygons: List[Tuple[str, Tuple[Tuple[float, float], ...]]] = []

    for panel_id in sorted(fold_state.graph.panels):
        verts_local = panel_vertices_xy.get(panel_id)
        if not verts_local or len(verts_local) < 3:
            continue
        p_tf = fold_state.transforms[panel_id]

        pts_top_mach = [placement.apply(p_tf.point((x, y, 0.0))) for x, y in verts_local]
        pts_bot_mach = [placement.apply(p_tf.point((x, y, -thickness_mm))) for x, y in verts_local]

        xs_top = [p[0] for p in pts_top_mach]
        if min(xs_top) > target_xm + 1e-4 or max(xs_top) < target_xm - 1e-4:
            # Does not intersect the requested cutting plane! Do not project onto cutting plane.
            continue

        top_cuts = slice_polygon_3d_at_x(pts_top_mach, target_x=target_xm)
        bot_cuts = slice_polygon_3d_at_x(pts_bot_mach, target_x=0.0)

        if len(top_cuts) >= 2 and len(bot_cuts) >= 2:
            # Sort endpoints along Y_m to form closed solid quad
            # In 2.5D coordinates: s = -Y_m, z = Z_m
            t1, t2 = top_cuts[0], top_cuts[-1]
            b1, b2 = bot_cuts[0], bot_cuts[-1]

            # Form closed cross-section quad: top_start -> top_end -> bot_end -> bot_start
            quad_sz = (
                (-t1[0], t1[1]),
                (-t2[0], t2[1]),
                (-b2[0], b2[1]),
                (-b1[0], b1[1]),
            )
            cut_panel_polygons.append((panel_id, quad_sz))

    # 2. Bend zones intersecting X_m = target_xm
    active_bend = fold_state.bends.get(active_bend_id) if active_bend_id else None

    for bend_id, bend in fold_state.bends.items():
        zone = bend.bend_zone
        if zone is None:
            continue
        p_parent_tf = fold_state.transforms[bend.fixed_panel]
        ax = zone.axis_xy
        p0_m = placement.apply(p_parent_tf.point((ax[0][0], ax[0][1], 0.0)))
        p1_m = placement.apply(p_parent_tf.point((ax[1][0], ax[1][1], 0.0)))

        min_ax_x = min(p0_m[0], p1_m[0])
        max_ax_x = max(p0_m[0], p1_m[0])

        if min_ax_x > target_xm + 1e-4 or max_ax_x < target_xm - 1e-4:
            # Zone does not cross requested cutting plane
            continue

        # Zone profile at cutting plane
        ax_mid_2d = ((ax[0][0] + ax[1][0]) / 2.0, (ax[0][1] + ax[1][1]) / 2.0)
        zone_sec = fold_state.zone_section(bend_id, segments=segments)
        zone_side = 1.0 if bend.moving_left else -1.0
        ux, uy = zone.left_xy[0] * zone_side, zone.left_xy[1] * zone_side

        def map_profile_to_sz(prof_pts: Sequence[Tuple[float, float]]) -> Tuple[Tuple[float, float], ...]:
            out: List[Tuple[float, float]] = []
            for s_val, z_val in prof_pts:
                p_local = (ax_mid_2d[0] + ux * s_val, ax_mid_2d[1] + uy * s_val, z_val)
                p_m = placement.apply(p_parent_tf.point(p_local))
                pt = (-p_m[1], p_m[2])
                if not out or math.dist(out[-1], pt) > 1e-5:
                    out.append(pt)
            return tuple(out)

        inner_sz = map_profile_to_sz(zone_sec["inner"])
        outer_sz = map_profile_to_sz(zone_sec["outer"])
        neutral_sz = map_profile_to_sz(zone_sec["neutral"])

        # Closed zone polygon: inner -> reversed(outer) with deduplication of degenerate segments
        raw_poly = list(inner_sz) + list(reversed(outer_sz))
        dedup_poly: List[Tuple[float, float]] = []
        for p in raw_poly:
            if not dedup_poly or math.dist(dedup_poly[-1], p) > 1e-5:
                dedup_poly.append(p)
        if len(dedup_poly) > 1 and math.dist(dedup_poly[0], dedup_poly[-1]) <= 1e-5:
            dedup_poly.pop()
        zone_poly_sz = tuple(dedup_poly)

        if active_bend and bend_id == active_bend_id:
            section["zone_polygon"] = zone_poly_sz
            section["inner_surface"] = inner_sz
            section["outer_surface"] = outer_sz
            section["neutral_curve"] = neutral_sz
        else:
            section["other_zone_polygons"].append((bend_id, zone_poly_sz))
            if inner_sz:
                section["other_inner_curves"].append(inner_sz)
            if outer_sz:
                section["other_outer_curves"].append(outer_sz)
            if neutral_sz:
                section["other_neutral_curves"].append(neutral_sz)

    # 3. Classify active fixed and moving panels vs other panels
    if active_bend:
        fixed_pid = active_bend.fixed_panel
        moving_pid = active_bend.moving_panel

        for pid, poly in cut_panel_polygons:
            if pid == fixed_pid and not section["fixed_polygon"]:
                section["fixed_polygon"] = poly
            elif pid == moving_pid and not section["moving_polygon"]:
                section["moving_polygon"] = poly
            else:
                section["other_panel_polygons"].append((pid, poly))
    else:
        section["other_panel_polygons"] = cut_panel_polygons

    section["other_inner_curves"] = tuple(section["other_inner_curves"])
    section["other_outer_curves"] = tuple(section["other_outer_curves"])
    section["other_neutral_curves"] = tuple(section["other_neutral_curves"])
    return section
