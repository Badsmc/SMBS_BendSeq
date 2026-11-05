"""Cutting Plane Section Adapter (Machine X_m = 0).

Constructs the true, physical 2.5D cross-section of the sheet workpiece
by slicing the 3D placed geometry with the machine cutting plane X_m = 0.

Coordinate Convention:
- Slicing plane: X_m = 0
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
) -> Dict[str, Any]:
    """Compute exact 2.5D cross-section at machine cutting plane X_m = 0.

    Uses FoldState as single source of truth for folded angles and panel positions.
    Projects every panel and bend zone through `placement` into Machine Space,
    then intersects with plane X_m = 0.

    Returns:
        dict compatible with 2.5D Viewer drawing pipeline (s, z coordinates).
    """
    thickness_mm = 1.5
    inner_radius_mm = 1.0
    if active_bend_id and active_bend_id in fold_state.bends:
        bz = fold_state.bends[active_bend_id].bend_zone
        if bz is not None:
            thickness_mm = bz.thickness_mm
            inner_radius_mm = bz.inner_radius_mm

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
        "other_inner_curves": (),
        "other_outer_curves": (),
        "other_neutral_curves": (),
        "cutting_plane_xm": 0.0,
    }

    # Material-side convention (D5.1).
    #
    # The panel surface at local z=0 is the OUTER sheet face for the
    # positive/up-bend setup. The existing 0 -> -t extrusion double-counted
    # thickness below the die support datum (observed B01: -1.5 .. -3.0
    # when the actual support is at -1.5). In that setup the sheet occupies
    # local z=0 .. +t. For the negative/down-bend setup, the working side
    # reverses and the original 0 .. -t convention is correct.
    #
    # This selection is based on the active SheetMetal bend orientation.
    # The result is a 2D cutting-plane preview, not a certified 3D solid.
    active_bend_for_material = (
        fold_state.bends.get(active_bend_id) if active_bend_id else None
    )
    material_offset = 0.0
    if active_bend_for_material is not None:
        zone = active_bend_for_material.bend_zone
        source_angle = (
            float(zone.fold_angle_deg)
            if zone is not None and getattr(zone, "fold_angle_deg", None) is not None
            else float(active_bend_for_material.angle_deg)
        )
        if source_angle >= 0.0:
            material_offset = float(thickness_mm)

    # 1. Panels intersecting X_m = 0
    cut_panel_polygons: List[Tuple[str, Tuple[Tuple[float, float], ...]]] = []

    for panel_id in sorted(fold_state.graph.panels):
        verts_local = panel_vertices_xy.get(panel_id)
        if not verts_local or len(verts_local) < 3:
            continue
        p_tf = fold_state.transforms[panel_id]

        pts_top_mach = [
            placement.apply(p_tf.point((x, y, material_offset)))
            for x, y in verts_local
        ]
        pts_bot_mach = [
            placement.apply(p_tf.point((x, y, material_offset - thickness_mm)))
            for x, y in verts_local
        ]

        xs_top = [p[0] for p in pts_top_mach]
        if min(xs_top) > 1e-4 or max(xs_top) < -1e-4:
            # Does not intersect X_m = 0! Do not project onto cutting plane.
            continue

        top_cuts = slice_polygon_3d_at_x(pts_top_mach, target_x=0.0)
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

    # 2. Bend zones intersecting X_m = 0
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

        if min_ax_x > 1e-4 or max_ax_x < -1e-4:
            # Zone does not cross cutting plane X_m = 0
            continue

        # Zone profile at cutting plane
        ax_mid_2d = ((ax[0][0] + ax[1][0]) / 2.0, (ax[0][1] + ax[1][1]) / 2.0)
        zone_sec = fold_state.zone_section(bend_id, segments=segments)
        zone_side = 1.0 if bend.moving_left else -1.0
        ux, uy = zone.left_xy[0] * zone_side, zone.left_xy[1] * zone_side

        def map_profile_to_sz(prof_pts: Sequence[Tuple[float, float]]) -> Tuple[Tuple[float, float], ...]:
            out = []
            for s_val, z_val in prof_pts:
                # Bend-zone material must use the same source-side offset
                # as the panel faces for the active section.
                zone_offset = (
                    material_offset if bend_id == active_bend_id else
                    (float(zone.thickness_mm) if float(getattr(zone, "fold_angle_deg", bend.angle_deg)) >= 0 else 0.0)
                )
                p_local = (
                    ax_mid_2d[0] + ux * s_val,
                    ax_mid_2d[1] + uy * s_val,
                    z_val + zone_offset,
                )
                p_m = placement.apply(p_parent_tf.point(p_local))
                out.append((-p_m[1], p_m[2]))
            return tuple(out)

        inner_sz = map_profile_to_sz(zone_sec["inner"])
        outer_sz = map_profile_to_sz(zone_sec["outer"])
        neutral_sz = map_profile_to_sz(zone_sec["neutral"])

        # Closed zone polygon: inner -> reversed(outer)
        zone_poly_sz = inner_sz + tuple(reversed(outer_sz))

        if active_bend and bend_id == active_bend_id:
            section["zone_polygon"] = zone_poly_sz
            section["inner_surface"] = inner_sz
            section["outer_surface"] = outer_sz
            section["neutral_curve"] = neutral_sz
        else:
            section["other_zone_polygons"].append((bend_id, zone_poly_sz))

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

    return section
