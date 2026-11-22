"""Enumerate machine setups for an already signed bend in a FoldState."""
from __future__ import annotations

from copy import copy
from dataclasses import dataclass, replace
from math import isfinite

from .fast_orientation_25d import (
    LoadingPose25D,
    four_loading_poses,
    roll_about_machine_bend_axis,
    upper_punch_face_score,
)
from .machine_pose_contract import MachinePoseFrame, compute_machine_pose_frame
from .machine_section_3d import compute_machine_cutting_plane_section


@dataclass(frozen=True)
class GaugeEdge:
    station_xm: float
    panel_id: str
    distance_mm: float
    height_mm: float


@dataclass(frozen=True)
class MachineSetup:
    orientation: LoadingPose25D
    frame: MachinePoseFrame
    punch_face_ok: bool
    gauge_status: str
    gauge_edges: tuple[GaugeEdge, ...]

    def diagnostics(self) -> dict:
        return {
            **self.frame.diagnostics(),
            "pose_id": self.orientation.pose_id,
            "punch_face_ok": self.punch_face_ok,
            "gauge_status": self.gauge_status,
            "gauge_edges": [
                {
                    "station_xm": edge.station_xm,
                    "panel_id": edge.panel_id,
                    "distance_mm": edge.distance_mm,
                    "height_mm": edge.height_mm,
                } for edge in self.gauge_edges
            ],
            "verification_3d": "NOT_CHECKED",
        }


def loading_orientations(reverse_axis: bool | None = None) -> tuple[LoadingPose25D, ...]:
    """Enumerate rigid orientations compatible with an upper punch and signed bend."""
    return tuple(
        pose for pose in four_loading_poses()
        if pose.eligible_for_fast_ranking
        and (reverse_axis is None or pose.reverse_axis == reverse_axis)
    )


def loading_fold_state(fold_state, operations, completed_mask: int):
    """Rebuild the loading geometry, including intermediate pre-bend angles."""
    angles = dict.fromkeys(fold_state.bends, 0.0)
    for index, step in enumerate(operations):
        if (completed_mask & (1 << index)
                and abs(step.target_angle_deg) > abs(angles[step.bend_id])):
            angles[step.bend_id] = step.target_angle_deg
    state = copy(fold_state)
    state.angles = angles
    state.transforms = state._compute_transforms(angles)
    return state


def _rear_edges(section, bend, station_xm: float) -> tuple[GaugeEdge, ...]:
    panels = (
        (bend.fixed_panel, section["fixed_polygon"]),
        (bend.moving_panel, section["moving_polygon"]),
        *section["other_panel_polygons"],
    )
    edges = []
    for panel_id, polygon in panels:
        rear = max((s for s, _ in polygon), default=0.0)
        if rear <= 0.5:
            continue
        heights = [z for s, z in polygon if abs(s - rear) <= 1e-3]
        edges.append(GaugeEdge(
            station_xm, panel_id, rear, (min(heights) + max(heights)) / 2.0,
        ))
    return tuple(sorted(edges, key=lambda edge: (-edge.distance_mm, edge.panel_id)))


def evaluate_loading_setup(
    fold_state,
    bend_id: str,
    panel_vertices_xy,
    orientation: LoadingPose25D,
    *,
    midpoint_xm: float = 0.0,
    backgauge=None,
) -> MachineSetup:
    """Resolve a setup and screen possible rear contact in configured X sections.

    Gauge evidence is sectional. CAD finger shape, clamp and motion clearance
    still need independent validation.
    """
    bend = fold_state.bends[bend_id]
    support_z = float(bend.bend_zone.outer_radius_mm)
    frame = compute_machine_pose_frame(
        fold_state, bend_id, phase="APPROACH",
        support_plane_zm=support_z,
        reverse_axis=orientation.reverse_axis,
        desired_midpoint_xm=midpoint_xm,
        rear_panel_vertices_xy=panel_vertices_xy,
    )
    placement = frame.placement
    if orientation.roll_180:
        placement = roll_about_machine_bend_axis(placement, axis_z_mm=support_z)
        frame = replace(frame, placement=placement)
    face_ok = upper_punch_face_score(fold_state, bend_id, placement) > 1e-6
    if not face_ok:
        return MachineSetup(orientation, frame, False, "INVALID_PUNCH_FACE", ())
    if backgauge is None or not backgauge.automatic:
        return MachineSetup(orientation, frame, True, "CONTACT_REQUIRES_CHECK", ())

    stations = (float(midpoint_xm),)
    if backgauge.cad_geometry_enabled:
        stations = (backgauge.cad_station_xm_mm,)
        if backgauge.cad_second_station_xm_mm is not None:
            stations += (backgauge.cad_second_station_xm_mm,)
    contacts = []
    for station in stations:
        try:
            section = compute_machine_cutting_plane_section(
                fold_state, panel_vertices_xy, placement,
                active_bend_id=bend_id, target_xm=station, segments=8,
            )
        except ValueError:
            return MachineSetup(
                orientation, frame, True, "GAUGE_SECTION_UNAVAILABLE", tuple(contacts),
            )
        edges = _rear_edges(section, bend, station)
        fixed = next((edge for edge in edges if edge.panel_id == bend.fixed_panel), None)
        if fixed is None:
            status = "NO_FIXED_PANEL_REAR_EDGE_IN_SECTION"
        elif not backgauge.z_min_mm <= fixed.height_mm <= backgauge.z_max_mm:
            status = "REAR_EDGE_OUTSIDE_GAUGE_Z_TRAVEL"
        else:
            contacts.append(fixed)
            if edges[0].distance_mm <= fixed.distance_mm + 1e-3:
                continue
            status = "OTHER_PANEL_EXTENDS_BEHIND_GAUGE_EDGE"
        return MachineSetup(orientation, frame, True, status, tuple(contacts))

    return MachineSetup(orientation, frame, True, "REAR_EDGE_CANDIDATE_2D", tuple(contacts))


def tool_station_midpoints(
    bend_length_mm: float,
    punch_spans,
    die_spans,
    *,
    preferred_xm: float = 0.0,
) -> tuple[float, ...]:
    """Sample feasible midpoint intervals from continuous usable tool spans.

    Spans are machine-X intervals with any unusable gaps already removed.
    Each candidate keeps the entire active bend in both tools.
    """
    if not isfinite(bend_length_mm) or bend_length_mm <= 0 or not isfinite(preferred_xm):
        raise ValueError("invalid tool station inputs")
    half = bend_length_mm / 2.0
    punch, die = tuple(punch_spans), tuple(die_spans)
    for lo, hi in (*punch, *die):
        if not (isfinite(lo) and isfinite(hi) and lo < hi):
            raise ValueError("invalid continuous tool span")
    candidates = set()
    for punch_lo, punch_hi in punch:
        for die_lo, die_hi in die:
            lo, hi = max(punch_lo, die_lo) + half, min(punch_hi, die_hi) - half
            if lo <= hi:
                candidates.update((lo, hi, (lo + hi) / 2.0, min(max(preferred_xm, lo), hi)))
    return tuple(sorted(candidates, key=lambda x: (abs(x - preferred_xm), x)))



def solve_machine_setups(
    fold_state,
    bend_id: str,
    panel_vertices_xy,
    *,
    midpoint_candidates=(0.0,),
    reverse_axis: bool | None = None,
    backgauge=None,
) -> tuple[MachineSetup, ...]:
    """Return punch-face-compatible setups at each supplied machine station.

    Callers supplying tool coverage can obtain midpoint candidates from
    tool_station_midpoints. Gauge observations accompany each candidate so
    downstream screening can rank them or qualify the contact in 3D.
    """
    return tuple(
        setup
        for midpoint in midpoint_candidates
        for orientation in loading_orientations(reverse_axis)
        if (setup := evaluate_loading_setup(
            fold_state, bend_id, panel_vertices_xy, orientation,
            midpoint_xm=midpoint, backgauge=backgauge,
        )).punch_face_ok
    )
