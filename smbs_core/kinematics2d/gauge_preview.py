"""Visual-only gauge placement from the SheetMetal flat-pattern outline.

No collision, penetration, stop-face contact or reachability checks are made.
Distances refer to the midpoint of each canonical bend axis, projected
perpendicularly toward its rooted fixed panel until the OUTER blank boundary.
"""
from __future__ import annotations

from math import hypot, isfinite

from .backgauge_2d import BackgaugePosition2D


def bend_to_blank_edge_mm(axis_xy, fixed_panel_xy, outline_xy):
    """Return perpendicular reach from bend midpoint to the first outer edge.

    The fixed-panel centroid selects which side of the axis is the rear side.
    The entire unfolded outline, not the virtual topology-panel boundary,
    supplies the edge. No guessed dimension is returned for ambiguous input.
    """
    if len(axis_xy) != 2 or len(fixed_panel_xy) < 3 or len(outline_xy) < 3:
        raise ValueError("incomplete geometry for bend-to-edge distance")
    (x0, y0), (x1, y1) = ((float(x), float(y)) for x, y in axis_xy)
    dx, dy = x1 - x0, y1 - y0
    length = hypot(dx, dy)
    if not isfinite(length) or length <= 1e-9:
        raise ValueError("degenerate bend axis")
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    nx, ny = -dy / length, dx / length
    cx = sum(float(x) for x, _ in fixed_panel_xy) / len(fixed_panel_xy)
    cy = sum(float(y) for _, y in fixed_panel_xy) / len(fixed_panel_xy)
    side = (cx - mx) * nx + (cy - my) * ny
    if not isfinite(side) or abs(side) <= 1e-7:
        raise ValueError("fixed-panel side of bend is ambiguous")
    if side < 0:
        nx, ny = -nx, -ny

    points = tuple((float(x), float(y)) for x, y in outline_xy)
    if not all(isfinite(x) and isfinite(y) for x, y in points):
        raise ValueError("nonfinite outline geometry")
    intersections = []
    for a, b in zip(points, points[1:] + points[:1]):
        ex, ey = b[0] - a[0], b[1] - a[1]
        det = nx * ey - ny * ex
        if abs(det) <= 1e-10:
            continue
        rx, ry = a[0] - mx, a[1] - my
        t = (rx * ey - ry * ex) / det
        u = (rx * ny - ry * nx) / det
        if t > 1e-7 and -1e-8 <= u <= 1.0 + 1e-8:
            intersections.append(t)
    if not intersections:
        raise ValueError("no outer contour edge on fixed-panel side")
    return min(intersections)


def visual_gauge_target(model, bend, edge_distance_mm):
    """Return display target only; do not use for machine safety decisions.

    An explicit manual target overrides automatic placement, but never the
    measured bend-to-edge distance displayed in the operations table.
    """
    if not model.automatic:
        return model.position_for(bend.bend_id), "VISUAL_ONLY_MANUAL"
    if (edge_distance_mm is None or
            not isfinite(float(edge_distance_mm)) or edge_distance_mm <= 0):
        raise ValueError("missing valid bend-to-edge distance")
    zone = bend.bend_zone
    if zone is None:
        raise ValueError("missing authoritative bend-zone section")
    # The blank's mid-thickness at the flat machine support plane is a
    # display-height approximation, not a verified contact height.
    z_mm = float(zone.outer_radius_mm) - float(zone.thickness_mm) / 2.0
    stop_face_s_mm = float(edge_distance_mm) + model.edge_clearance_mm
    return (
        BackgaugePosition2D(
            stop_face_s_mm + model.width_s_mm / 2.0, z_mm,
        ),
        "VISUAL_ONLY_OUTLINE_DISTANCE",
    )
