"""Independent flat dimensions and folded-machine rear stop preview.

No collision, penetration, stop-face contact or reachability is checked.
The table's local flat bend dimensions must NOT be used as the physical
backgauge location after preceding folds.
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


def local_bend_dimension_mm(axis_xy, outline_xy, other_bend_axes=()):
    """Local straight flange pitch, NOT cumulative reach to the outer outline.

    Measure from the midpoint of the current bend line along either normal,
    stopping at the first *parallel neighbouring bend line* or real external
    blank edge. This yields 10, 22 for bends at x=10 and x=32 on an x=0..100
    blank, rather than 10, 32. Parallel bend lines are accepted only where
    the perpendicular probe really intersects their finite segment.

    This is a flat-pattern nominal dimension (not an inside/outside formed
    flange length or a verified backgauge position). No contact is checked.
    """
    if len(axis_xy) != 2 or len(outline_xy) < 3:
        raise ValueError("incomplete local bend geometry")
    (x0, y0), (x1, y1) = ((float(x), float(y)) for x, y in axis_xy)
    dx, dy = x1 - x0, y1 - y0
    length = hypot(dx, dy)
    if not isfinite(length) or length <= 1e-9:
        raise ValueError("degenerate bend axis")
    mx, my = (x0 + x1) * 0.5, (y0 + y1) * 0.5
    nx, ny = -dy / length, dx / length

    outline = tuple((float(x), float(y)) for x, y in outline_xy)
    if not all(isfinite(x) and isfinite(y) for x, y in outline):
        raise ValueError("nonfinite blank outline")

    def ray_segment_distance(sign, a, b):
        """Forward ray/finite-segment intersection, in millimetres."""
        rx, ry = nx * sign, ny * sign
        ex, ey = b[0] - a[0], b[1] - a[1]
        det = rx * ey - ry * ex
        if abs(det) <= 1e-10:
            return None
        qx, qy = a[0] - mx, a[1] - my
        distance = (qx * ey - qy * ex) / det
        segment_t = (qx * ry - qy * rx) / det
        if distance > 1e-7 and -1e-8 <= segment_t <= 1.0 + 1e-8:
            return distance
        return None

    segments = list(zip(outline, outline[1:] + outline[:1]))
    for other in other_bend_axes:
        if len(other) != 2:
            raise ValueError("invalid neighbouring bend axis")
        a, b = ((float(x), float(y)) for x, y in other)
        ox, oy = b[0] - a[0], b[1] - a[1]
        other_length = hypot(ox, oy)
        if not isfinite(other_length) or other_length <= 1e-9:
            raise ValueError("invalid neighbouring bend axis")
        # Only a neighbouring bend with almost the same direction creates
        # the requested local flange pitch. Perpendicular/crossing bend lines
        # should not appear as a zero-length flange boundary.
        parallel_cos = abs((dx * ox + dy * oy) / (length * other_length))
        if parallel_cos >= 0.995:
            segments.append((a, b))

    hits = [
        value
        for sign in (-1.0, 1.0)
        for a, b in segments
        for value in (ray_segment_distance(sign, a, b),)
        if value is not None
    ]
    if not hits:
        raise ValueError("no local flange boundary at bend midpoint")
    return min(hits)


def gauge_rear_envelope_from_section(section):
    """Rear-most sheet envelope (S, Z) in a machine X-station cross-section.

    All already folded rigid panels and finite-radius bend zones participate.
    Thickness is carried by the physical panel polygons. This is a projected
    envelope for visual gauge setup ONLY; no first-contact, collision, access
    or finger clearance solver is invoked.
    """
    polygons = (
        [section.get("fixed_polygon"), section.get("moving_polygon"),
         section.get("zone_polygon")]
        + [poly for _pid, poly in section.get("other_panel_polygons", ())]
        + [poly for _bid, poly in section.get("other_zone_polygons", ())]
    )
    candidates = []
    for poly in polygons:
        if not poly:
            continue
        for s, z in poly:
            s, z = float(s), float(z)
            if not (isfinite(s) and isfinite(z)):
                raise ValueError("nonfinite machine cross-section point")
            if s > 1e-7:
                candidates.append((s, z))
    if not candidates:
        raise ValueError("no physical rear-side sheet envelope at gauge station")
    rear_s = max(s for s, _z in candidates)
    # Average the actual rear-most edge's Z vertices. No inferred contact
    # location if the cross-section itself is absent.
    z_at_edge = [z for s, z in candidates if abs(s - rear_s) <= 0.05]
    return rear_s, sum(z_at_edge) / len(z_at_edge)


def sequenced_gauge_envelopes(session, *, station_xm_mm=0.0,
                              reverse_axis_by_bend=None):
    """Compute physical S/Z per bend before it is formed, in user bend order.

    Previous bends are at their final signed angles; the current and future
    bends are still flat. Every step is evaluated in an isolated FoldState
    using the same machine-axis alignment as the pre-bend 2.5D viewer.
    This avoids mutating the playback, returning cumulative flat lengths,
    and hard-coding a thickness offset (which is wrong for +/-45 deg).

    Returns (targets_by_bend, errors_by_bend) with values (S, Z) in mm.
    """
    from .panel_kinematics import FoldState
    from .workpiece_placement_3d import WorkpiecePlacement3D
    from .machine_section_3d import compute_machine_cutting_plane_section

    order = tuple(session.bend_order)
    if len(order) != len(set(order)):
        raise ValueError("duplicate bends in sequence")
    if any(bid not in session.fold_state.bends for bid in order):
        raise ValueError("unknown bend in sequence")
    if not isfinite(float(station_xm_mm)):
        raise ValueError("invalid CAD backgauge X station")

    orientations = reverse_axis_by_bend or {}
    state = FoldState(session.graph, session.fixed_panel)
    targets, errors = {}, {}
    for bend_id in order:
        bend = state.bends[bend_id]
        zone = bend.bend_zone
        try:
            if zone is None:
                raise ValueError("missing authoritative bend zone")
            placement = WorkpiecePlacement3D.compute_placement(
                state, bend_id,
                desired_midpoint_xm=0.0,
                support_plane_zm=float(zone.outer_radius_mm),
                align_bisector=True,
                align_bend_arc_center=True,
                reverse_axis=bool(orientations.get(bend_id, False)),
                rear_panel_vertices_xy=session.panel_vertices_xy,
            )
            section = compute_machine_cutting_plane_section(
                state, session.panel_vertices_xy, placement,
                active_bend_id=bend_id,
                segments=32, target_xm=float(station_xm_mm),
            )
            targets[bend_id] = gauge_rear_envelope_from_section(section)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            errors[bend_id] = str(exc)
        # An unresolvable setup must not erase the signed history required to
        # measure later bends; keep the state independent of the live viewer.
        state.set_angle(bend_id, float(bend.angle_deg))
    return targets, errors


def visual_gauge_target(model, bend, rear_s_mm, rear_z_mm):
    """Return the visual gauge centre from the PHYSICAL folded envelope.

    A manual position overrides the calculated setup. The supplied machine
    S/Z coordinates represent a stop-face target, not a flat-pattern length.
    Real finger contact and manufacturing clearance remain UNVERIFIED.
    """
    if not model.automatic:
        return model.position_for(bend.bend_id), "VISUAL_ONLY_MANUAL"
    if (rear_s_mm is None or rear_z_mm is None
            or not isfinite(float(rear_s_mm))
            or not isfinite(float(rear_z_mm))
            or float(rear_s_mm) <= 0):
        raise ValueError("missing physical machine rear envelope")
    return (
        BackgaugePosition2D(
            float(rear_s_mm) + model.edge_clearance_mm
            + model.width_s_mm / 2.0,
            float(rear_z_mm),
        ),
        "VISUAL_ONLY_FOLDED_MACHINE_ENVELOPE",
    )
