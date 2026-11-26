"""Choose longitudinal 2D stations from the CURRENT physical machine pose.

No flat-pattern X/Y shortcuts: FoldState panel transforms are applied before
workpiece placement. Tool ends must come from independently verified source CAD
in machine coordinates. Stations are probes, never a continuous-coverage proof.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import dist, isfinite
from typing import Any, Mapping, Sequence

from .axis_screening import longitudinal_probes


@dataclass(frozen=True)
class MachineStationPlan:
    stations_xm: tuple[float, ...]
    bend_span_xm: tuple[float, float]
    panel_vertex_count: int
    punch_span_verified: bool
    die_span_verified: bool

    def to_dict(self) -> dict:
        return {
            "stations_xm": list(self.stations_xm),
            "bend_span_xm": list(self.bend_span_xm),
            "panel_vertex_count": self.panel_vertex_count,
            "punch_span_verified": self.punch_span_verified,
            "die_span_verified": self.die_span_verified,
            "evidence": "STATION_SELECTION_ONLY_NOT_CONTINUOUS_PROOF",
        }


def _span(span: tuple[float, float] | None) -> tuple[float, float] | None:
    if span is None:
        return None
    if len(span) != 2:
        raise ValueError("tool span must have two endpoints")
    a, b = float(span[0]), float(span[1])
    if not (isfinite(a) and isfinite(b) and a <= b):
        raise ValueError("invalid verified source-tool machine span")
    return a, b


def plan_machine_stations(
    fold_state: Any,
    panel_vertices_xy: Mapping[str, Sequence[tuple[float, float]]],
    placement: Any,
    active_bend_id: str,
    *,
    verified_punch_span_xm: tuple[float, float] | None = None,
    verified_die_span_xm: tuple[float, float] | None = None,
    edge_offset_mm: float = 0.5,
    max_stations: int = 128,
) -> MachineStationPlan:
    """Generate section positions in machine X_m, using the actual pose."""
    punch_span = _span(verified_punch_span_xm)
    die_span = _span(verified_die_span_xm)
    bend = fold_state.bends[active_bend_id]
    tf = fold_state.transforms[bend.fixed_panel]
    axis = bend.axis_xyz
    p0 = placement.apply(tf.point(axis[0]))
    p1 = placement.apply(tf.point(axis[1]))
    bend_length = dist(p0, p1)
    if bend_length <= 1e-9:
        raise ValueError("zero length machine bend axis")
    # Check alignment promised by WorkpiecePlacement3D, not merely projection.
    if abs(p1[1] - p0[1]) > 1e-4 or abs(p1[2] - p0[2]) > 1e-4:
        raise ValueError("active bend not aligned with machine X")
    mid_x = (p0[0] + p1[0]) * 0.5
    vertices = []
    for panel_id in sorted(fold_state.graph.panels):
        pts = panel_vertices_xy.get(panel_id)
        if pts is None:
            continue
        panel_tf = fold_state.transforms[panel_id]
        for point in pts:
            if len(point) != 2:
                raise ValueError(f"invalid panel vertex in {panel_id}")
            x, y = float(point[0]), float(point[1])
            xyz = placement.apply(panel_tf.point((x, y, 0.0)))
            if not all(isfinite(v) for v in xyz):
                raise ValueError("nonfinite machine panel vertex")
            vertices.append(xyz)
    # axis_screening.longitudinal_probes operates on local-axis coordinates;
    # use a *machine-space* X-directed axis centered at the active bend.
    x_axis = ((mid_x - bend_length / 2, 0.0, 0.0),
              (mid_x + bend_length / 2, 0.0, 0.0))
    tool_ends = ()
    if punch_span is not None:
        tool_ends += punch_span
    if die_span is not None:
        tool_ends += die_span
    stations_relative = longitudinal_probes(
        x_axis,
        tool_end_positions_mm=(x - mid_x for x in tool_ends),
        panel_vertices_xyz=vertices,
        edge_offset_mm=edge_offset_mm,
        max_probes=max_stations,
    )
    return MachineStationPlan(
        stations_xm=tuple(round(mid_x + x, 8) for x in stations_relative),
        bend_span_xm=(min(p0[0], p1[0]), max(p0[0], p1[0])),
        panel_vertex_count=len(vertices),
        punch_span_verified=punch_span is not None,
        die_span_verified=die_span is not None,
    )
