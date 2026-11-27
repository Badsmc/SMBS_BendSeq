"""Identify potential shared strokes without changing the bend sequence."""
from math import dist, isclose

LINE_TOLERANCE_MM = 0.01


def _fits_stroke(candidate, group, bends, orientations):
    if candidate.kind != "SINGLE":
        return False
    bend = bends[candidate.bend_id]
    first = group[0]
    reference = bends[first.bend_id]
    if (
        first.kind != "SINGLE"
        or bend.fixed_panel != reference.fixed_panel
        or candidate.operation_id not in orientations
        or first.operation_id not in orientations
        or orientations[candidate.operation_id] != orientations[first.operation_id]
        or not isclose(candidate.target_angle_deg, first.target_angle_deg, abs_tol=1e-6)
    ):
        return False
    if bend.bend_zone is None or reference.bend_zone is None:
        return False
    for attr in ("thickness_mm", "inner_radius_mm", "bend_allowance_mm"):
        if not isclose(getattr(bend.bend_zone, attr), getattr(reference.bend_zone, attr),
                       abs_tol=1e-6):
            return False

    start, end = reference.axis_xyz
    length = dist(start, end)
    direction = tuple((b - a) / length for a, b in zip(start, end))

    def interval(axis):
        limits = []
        for point in axis:
            offset = tuple(p - a for p, a in zip(point, start))
            along = sum(a * b for a, b in zip(offset, direction))
            projected = tuple(along * component for component in direction)
            if dist(offset, projected) > LINE_TOLERANCE_MM:
                return None
            limits.append(along)
        return tuple(sorted(limits))

    # With reversed endpoints, the same physical moving side has the
    # opposite moving_left flag. Signed fold angles still describe Up/Down.
    a, b = bend.axis_xyz
    same_axis_direction = sum((q - p) * d for p, q, d in zip(a, b, direction)) > 0
    if (bend.moving_left == same_axis_direction) != reference.moving_left:
        return False

    span = interval(bend.axis_xyz)
    if span is None:
        return False
    for op in group:
        existing = bends[op.bend_id]
        if set(bend.moving_subtree).intersection(existing.moving_subtree):
            return False
        lower, upper = interval(existing.axis_xyz)
        gap = max(lower - span[1], span[0] - upper)
        if gap <= LINE_TOLERANCE_MM:
            return False
    return True


def propose_shared_strokes(operations, bends, orientations):
    """Partition the unchanged order into single or potentially shared strokes.

    Only consecutive, independent full bends on a common support qualify.
    Results do not validate the gap, tool spans, gauge setup or simultaneous
    movement. In particular, an intervening flange can require segmented tools.
    """
    groups = []
    for operation in operations:
        if groups and _fits_stroke(operation, groups[-1], bends, orientations):
            groups[-1].append(operation)
        else:
            groups.append([operation])
    return tuple(tuple(op.operation_id for op in group) for group in groups)


def stroke_axis(group, bends):
    """Return the extreme endpoints of the collinear sections."""
    start, end = bends[group[0]].axis_xyz
    direction = tuple(b - a for a, b in zip(start, end))
    points = [point for bid in group for point in bends[bid].axis_xyz]
    def position(point):
        return sum((p - a) * d for p, a, d in zip(point, start, direction))
    return min(points, key=position), max(points, key=position)


def spans_cover_interval(spans, start, end):
    """Require continuous coverage, including both outer endpoints."""
    cursor = start
    for lo, hi in sorted(spans):
        if lo > cursor + 1e-6:
            break
        cursor = max(cursor, hi)
        if cursor >= end - 1e-6:
            return True
    return False


def continuous_strokes(operations, bends, orientations):
    """Split groups if a continuous tool would straddle another bend line."""
    result = []
    by_operation = {op.operation_id: op.bend_id for op in operations}
    for operations_group in propose_shared_strokes(operations, bends, orientations):
        group = tuple(by_operation[op] for op in operations_group)
        if len(group) == 1:
            result.append(group)
            continue
        a, b = stroke_axis(group, bends)
        length = dist(a, b)
        direction = tuple((q - p) / length for p, q in zip(a, b))
        blocked = False
        for bid, bend in bends.items():
            if bid in group:
                continue
            offsets = [tuple(p - q for p, q in zip(point, a)) for point in bend.axis_xyz]
            stations = [sum(x * d for x, d in zip(offset, direction)) for offset in offsets]
            if (max(stations) > LINE_TOLERANCE_MM
                    and min(stations) < length - LINE_TOLERANCE_MM
                    and all(dist(offset, tuple(x * s for x in direction)) <= LINE_TOLERANCE_MM
                            for offset, s in zip(offsets, stations))):
                blocked = True
                break
        if blocked:
            result.extend((bid,) for bid in group)
        else:
            result.append(group)
    return tuple(result)
