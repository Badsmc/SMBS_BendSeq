"""Resolve a station placement through the existing Machine Pose contract."""
from dataclasses import dataclass, replace
from math import dist

from .machine_pose_contract import MachinePoseFrame, compute_machine_pose_frame
from .machine_pose_solver import tool_station_midpoints
from .shared_strokes import continuous_strokes, stroke_axis


@dataclass(frozen=True)
class StationPose:
    station_id: str
    operation_ids: tuple[str, ...]
    midpoint_xm: float
    bend_span_xm: tuple[float, float]
    frame: MachinePoseFrame


def resolve_station_poses(
    setup, operations, orientations, fold_state, *,
    phase, support_plane_zm, rear_panel_vertices_xy=None,
):
    """Return bounded X candidates for an assigned single operation or stroke.

    This resolves X fit and rigid coordinates only. It does not establish
    mounting height, gauge travel, tool contact, force or clearance.
    """
    operations = tuple(operations)
    if not operations:
        raise ValueError("No operations in station stroke")
    station = setup.assigned_station(operations[0].operation_id)
    if station is None or any(
        setup.assigned_station(op.operation_id) != station for op in operations[1:]
    ):
        raise ValueError("All stroke operations need the same assigned station")
    first = operations[0]
    reverse = orientations.get(first.operation_id)
    if type(reverse) is not bool:
        raise ValueError("Assigned operation has no resolved loading orientation")
    bends = fold_state.bends
    if len(operations) > 1:
        groups = continuous_strokes(operations, bends, orientations)
        if len(groups) != 1 or len(groups[0]) != len(operations):
            raise ValueError("Assigned operations are not a compatible shared stroke")
        if any(abs(fold_state.angles[op.bend_id] - fold_state.angles[first.bend_id]) > 1e-6
               for op in operations[1:]):
            raise ValueError("Shared stroke sections have different current angles")
    axis = stroke_axis(tuple(op.bend_id for op in operations), bends)
    length = dist(*axis)
    coverage = station.working_intervals()
    midpoints = tool_station_midpoints(length, coverage, coverage, preferred_xm=station.x_mm)
    if not midpoints:
        return ()

    reference = compute_machine_pose_frame(
        fold_state, first.bend_id, phase=phase, support_plane_zm=support_plane_zm,
        reverse_axis=reverse, rear_panel_vertices_xy=rear_panel_vertices_xy,
    )
    fixed = fold_state.transforms[bends[first.bend_id].fixed_panel]
    base_x = tuple(reference.placement.apply(fixed.point(point))[0] for point in axis)
    current_midpoint = sum(base_x) / 2
    operation_ids = tuple(op.operation_id for op in operations)
    results = []
    for midpoint in midpoints:
        delta = midpoint - current_midpoint
        x, y, z = reference.placement.translation
        placement = replace(reference.placement, translation=(x + delta, y, z))
        frame = replace(reference, placement=placement, desired_midpoint_xm=delta)
        span = tuple(x + delta for x in sorted(base_x))
        results.append(StationPose(station.station_id, operation_ids, midpoint, span, frame))
    return tuple(results)
