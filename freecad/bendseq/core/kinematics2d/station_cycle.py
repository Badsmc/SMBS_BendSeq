"""Bind assigned station tooling and Machine Pose to the existing press cycle."""
from .coupled_kinematics_2d import CoupledAirBendingKinematics
from .machine_pose_solver import loading_fold_state
from .mounted_station_preview import tooling_for_station
from .panel_kinematics import FoldState
from .press_cycle import PressCycleController, PressCycleError
from .staged_bends import StagedBendSequenceController
from .station_pose import resolve_station_poses


def build_station_cycle(setup, source, profiles, orientations, vertices, config):
    state = FoldState(source.fold_state.graph, source.fold_state.fixed_panel)
    sequence = StagedBendSequenceController(state, source.order)
    sequence.restore_operation_snapshot(source.operation_snapshot())
    steps = sequence.operation_steps
    orientations = {
        op.operation_id: orientations.get(op.operation_id, orientations.get(op.bend_id))
        for op in steps
    }
    tooling_by_station = {}
    layouts = []
    station_ids = []
    for index, op in enumerate(steps):
        try:
            station = setup.assigned_station(op.operation_id)
            if station is None:
                raise ValueError("no assigned station")
            if station.station_id not in tooling_by_station:
                tooling_by_station[station.station_id] = tooling_for_station(station, profiles)
            tooling = tooling_by_station[station.station_id]
            start_state = loading_fold_state(state, steps, (1 << index) - 1)
            zone = start_state.bends[op.bend_id].bend_zone
            if zone is None:
                raise ValueError("missing bend zone")
            kinematics = CoupledAirBendingKinematics.from_tooling_and_fold_state(
                tooling, start_state, op.bend_id,
            )
            if kinematics.tooling_status != "VERIFIED":
                raise ValueError("forming datums missing for this tool pair")
            kinematics.stroke_for_angle(abs(op.target_angle_deg))
            poses = resolve_station_poses(
                setup, (op,), orientations, start_state, phase="APPROACH",
                support_plane_zm=zone.outer_radius_mm, rear_panel_vertices_xy=vertices,
            )
            if not poses:
                raise ValueError("station does not cover the bend span")
        except (KeyError, ValueError, RuntimeError) as exc:
            raise PressCycleError(f"{op.operation_id}: {exc}") from exc
        layouts.append((tooling, poses[0].midpoint_xm))
        station_ids.append(station.station_id)
    if not layouts:
        raise PressCycleError("No operations")
    cycle = PressCycleController(
        sequence, layouts[0][0], config=config, animate_workpiece_transfer=True,
        operation_setup=tuple(layouts),
    )
    cycle.loading_orientation_by_bend = orientations
    cycle.loading_rear_frame_for_step = lambda _index: vertices
    return cycle, tuple(station_ids)
