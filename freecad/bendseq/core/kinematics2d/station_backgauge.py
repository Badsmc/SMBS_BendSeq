"""Static backgauge evidence for a station's already transformed sheet."""
from dataclasses import dataclass

from .gauge_motion_limits import bounded_gauge_height
from .gauge_preview import (
    centered_gauge_stations, gauge_rear_envelope_from_faces, visual_gauge_target,
)


@dataclass(frozen=True)
class StationBackgauge:
    stations: tuple = ()
    contact: tuple | None = None
    reason: str = ""
    requested_z_mm: float | None = None


def die_envelopes_for_gauge(installed_meshes):
    """Conservative native CAD boxes in (S, Z), with each die's actual X spans."""
    return tuple(
        (((-bounds.YMax, bounds.ZMin), (-bounds.YMin, bounds.ZMax)),
         tuple(section.span_xm for section in tool.sections) or (tool.span_xm,))
        for _, kind, tool, _ in installed_meshes if kind == "die"
        for bounds in (tool.machine_shape.BoundBox,)
    )


def resolve_station_backgauge(model, bend_id, faces, machine_x_mm, *,
                              gauge_bounds, face_z_span, die_obstacles):
    """Resolve a static visual stop and reject unsupported or blocked contact."""
    configured = (model.cad_station_xm_mm,)
    if model.cad_second_station_xm_mm is not None:
        configured += (model.cad_second_station_xm_mm,)
    stations = centered_gauge_stations(faces, len(configured)) if model.automatic else configured
    if len(stations) != len(configured):
        return StationBackgauge(reason="stations_gauge_no_edge")
    low, high = machine_x_mm
    x0, x1 = gauge_bounds[2:4]
    if any(x + x0 < low - 1e-6 or x + x1 > high + 1e-6 for x in stations):
        return StationBackgauge(reason="stations_gauge_outside_layout")
    if any(right + x0 < left + x1 - 1e-6
           for left, right in zip(sorted(stations), sorted(stations)[1:])):
        return StationBackgauge(reason="stations_gauge_fingers_overlap")

    edges = tuple(gauge_rear_envelope_from_faces(faces, x) for x in stations)
    if any(edge is None for edge in edges):
        return StationBackgauge(reason="stations_gauge_no_edge")
    if model.automatic:
        if max(s for s, _ in edges) - min(s for s, _ in edges) > 0.05:
            return StationBackgauge(reason="stations_gauge_different_depths")
        # The fingers share one height. Shorten the usable face interval
        # by the spread in rear-edge heights before applying the Z limits.
        edge_z = [z for _, z in edges]
        target, _ = visual_gauge_target(model, bend_id, edges)
        face_z_span = (face_z_span[0] + target.z_mm - min(edge_z),
                       face_z_span[1] + target.z_mm - max(edge_z))
        if face_z_span[0] > face_z_span[1]:
            return StationBackgauge(reason="gauge_z_contact_unreachable")
    else:
        target, _ = visual_gauge_target(model, bend_id, ())
        if any(abs(s - target.s_mm) > 0.05 for s, _ in edges):
            return StationBackgauge(reason="stations_gauge_manual_no_contact")
        if any(not target.z_mm + face_z_span[0] <= z <= target.z_mm + face_z_span[1]
               for _, z in edges):
            return StationBackgauge(reason="stations_gauge_manual_no_contact")
        face_z_span = (0.0, 0.0)

    result = bounded_gauge_height(
        target.z_mm, target.z_mm, model.z_min_mm, model.z_max_mm,
        s_mm=target.s_mm, stations=stations, gauge_bounds=gauge_bounds,
        face_z_span=face_z_span, die_obstacles=die_obstacles,
    )
    if result.z_mm is None or not result.contact_possible:
        return StationBackgauge(reason="gauge_z_contact_unreachable", requested_z_mm=target.z_mm)
    return StationBackgauge(stations, (target.s_mm, result.z_mm), requested_z_mm=target.z_mm)
