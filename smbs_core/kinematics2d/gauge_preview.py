"""Independent flat dimensions and folded-machine rear stop preview.

The backgauge preview measures the formed sheet before each bend.
The separate flat-pattern dimension measures the nominal local flange.
"""
from __future__ import annotations

from math import hypot, isfinite

from .backgauge_2d import BackgaugePosition2D


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


def gauge_rear_envelope_from_faces(faces, station_xm_mm):
    """Rear sheet edge (S, Z) on the same geometry shown in Coin3D."""
    from .machine_section_3d import slice_polygon_3d_at_x

    candidates = []
    for face in faces:
        # Coin vertices are (S, machine X, Z). Slice at the finger's X
        # through the displayed sheet, including its formed bend zones.
        candidates.extend(
            (s, z) for s, z in slice_polygon_3d_at_x(
                [(x, s, z) for s, x, z in face.vertices], target_x=station_xm_mm,
            )
            if s > 1e-7
        )
    if not candidates:
        return None
    rear_s = max(s for s, _ in candidates)
    z_at_edge = [z for s, z in candidates if rear_s - s <= 1e-5]
    return rear_s, (min(z_at_edge) + max(z_at_edge)) / 2.0


def centered_gauge_stations(faces, count):
    """Place finger centers at the midpoints of equal parts of the rear edge."""
    faces = tuple(faces)
    rear_s = max((s for face in faces for s, _, _ in face.vertices), default=0.0)
    tolerance = 1e-5
    if rear_s <= tolerance or count < 1:
        return ()
    spans = []
    for face in faces:
        vertices = tuple(face.vertices)
        for a, b in zip(vertices, vertices[1:] + vertices[:1]):
            if rear_s - min(a[0], b[0]) <= tolerance:
                left, right = sorted((a[1], b[1]))
                if right - left > tolerance:
                    spans.append((left, right))
    # Adjacent faces can describe one edge. Keep real gaps between them.
    merged = []
    for left, right in sorted(spans):
        if merged and left <= merged[-1][1] + tolerance:
            merged[-1] = (merged[-1][0], max(right, merged[-1][1]))
        else:
            merged.append((left, right))
    if not merged:
        return ()
    left, right = max(merged, key=lambda span: span[1] - span[0])
    return tuple(left + (index + 0.5) * (right - left) / count
                 for index in range(count))


def sequenced_gauge_envelopes(session, *, finger_count,
                              reverse_axis_by_bend, rear_frame_bend_ids=()):
    """Calculate automatic stations and their rear envelopes for each bend."""
    from .panel_kinematics import FoldState
    from .preview_mesh_3d import build_sheet_preview_faces
    from .machine_pose_contract import compute_machine_pose

    order = tuple(session.bend_order)
    if len(order) != len(set(order)):
        raise ValueError("duplicate bends in sequence")
    if any(bid not in session.fold_state.bends for bid in order):
        raise ValueError("unknown bend in sequence")

    state = FoldState(session.graph, session.fixed_panel)
    targets, errors = {}, {}
    for bend_id in order:
        bend = state.bends[bend_id]
        zone = bend.bend_zone
        try:
            if zone is None:
                raise ValueError("missing authoritative bend zone")
            placement = compute_machine_pose(
                state, bend_id,
                support_plane_zm=float(zone.outer_radius_mm),
                phase="APPROACH",
                rear_panel_vertices_xy=(
                    session.panel_vertices_xy if bend_id in rear_frame_bend_ids else None
                ),
                reverse_axis=bool(reverse_axis_by_bend.get(bend_id, False)),
            )
            faces = build_sheet_preview_faces(
                state, session.panel_vertices_xy, bend_id, None,
                machine_placement=placement,
            )
            stations = centered_gauge_stations(faces, finger_count)
            targets[bend_id] = (stations, tuple(
                gauge_rear_envelope_from_faces(faces, station) for station in stations
            ))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            errors[bend_id] = str(exc)
        state.set_angle(bend_id, float(bend.angle_deg))
    return targets, errors


def visual_gauge_target(model, bend_id, envelopes):
    """Return stop-face coordinates and any differences between finger stations."""
    if not model.automatic:
        centre = model.position_for(bend_id)
        return BackgaugePosition2D(centre.s_mm - model.width_s_mm / 2.0, centre.z_mm), ""

    if not envelopes:
        return None, "Не найдена задняя грань для автоматической расстановки лапок"
    edges = tuple(edge for edge in envelopes if edge is not None)
    if not edges:
        return None, "Край детали вне станций X заднего упора"
    # The fingers share the S/R axes. Set the common S beyond the rear-most
    # edge, so the other station's shallower edge cannot push that finger in.
    rear_s, rear_z = max(edges, key=lambda edge: edge[0])
    warnings = []
    for index, edge in enumerate(envelopes, 1):
        if edge is None:
            warnings.append(f"упор {index} вне контура детали по X")
        elif rear_s - edge[0] > 0.05:
            warnings.append(f"упор {index} до края {rear_s - edge[0] + model.edge_clearance_mm:.2f} мм")
    return BackgaugePosition2D(rear_s + model.edge_clearance_mm, rear_z), " · ".join(warnings)
