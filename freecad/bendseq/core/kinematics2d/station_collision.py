"""Sampled volumetric checks against all installed station tool solids."""
from dataclasses import dataclass
from math import isfinite

from .mesh_sequence_screen import _is_forming_contact_sliver
from .native_bend_solid_3d import _apply_rigid_transform, build_multi_bend_sheet_components
from .native_collision_3d import _evaluate_common_volume, compute_aabb_gap
from .native_tool_loader import set_punch_ram_z


@dataclass(frozen=True)
class StationCollision:
    station_id: str
    tool_kind: str
    tool_id: str
    sheet_component: str
    volume_mm3: float
    bounds: dict | None


def check_station_tool_overlap(components, meshes, ram_z, *, active_station, active_bend,
                               phase, minimum_volume_mm3=1e-4, move_punch=set_punch_ram_z):
    """Return the first confirmed interference. OCC errors propagate."""
    if not isfinite(ram_z) or not isfinite(minimum_volume_mm3) or minimum_volume_mm3 <= 0:
        raise ValueError("Invalid sampled station collision parameters")
    bounded = tuple((name, shape, shape.BoundBox) for name, shape in components)
    for station_id, kind, tool, _ in meshes:
        # Move each punch once for the whole sample, using the same ram Z.
        shape = move_punch(tool, ram_z).machine_shape if kind == "punch" else tool.machine_shape
        box = shape.BoundBox
        for component, sheet, sheet_box in bounded:
            gap = compute_aabb_gap(sheet_box, box)
            if gap is None:
                raise ValueError("Cannot compare station collision bounds")
            if gap > 1e-7:
                continue
            try:
                volume, bounds = _evaluate_common_volume(sheet, shape, include_bbox=True)
            except Exception as exc:
                raise ValueError(f"{station_id}/{kind}/{component}: {exc}") from exc
            if volume <= minimum_volume_mm3:
                continue
            if station_id == active_station and _is_forming_contact_sliver(
                kind, phase, component.removeprefix("bend_zone:"), active_bend,
                volume, bounds, fraction=0.0 if phase.value == "BENDING" else 1.0,
            ):
                continue
            return StationCollision(station_id, kind, tool.tool_id, component, volume, bounds)
    return None


class StationCollisionSampler:
    """Cache source prisms and folded components for one preview run."""

    def __init__(self, panel_build, meshes):
        if not meshes:
            raise ValueError("No installed station geometry to check")
        self.panel_build = panel_build
        self.meshes = meshes
        self.prisms = {}
        self.zones = {}
        self.folded_key = None
        self.folded = ()
        self.samples = 0

    def sample(self, state, placement, ram_z, *, active_station, active_bend, phase):
        import FreeCAD as App

        key = tuple(state.angles.items())
        if key != self.folded_key:
            self.folded = build_multi_bend_sheet_components(
                state, self.panel_build, panel_prism_cache=self.prisms, zone_shape_cache=self.zones,
            ).named_shapes()
            self.folded_key = key
        components = tuple(
            (name, _apply_rigid_transform(shape, placement, App)) for name, shape in self.folded
        )
        result = check_station_tool_overlap(
            components, self.meshes, ram_z, active_station=active_station,
            active_bend=active_bend, phase=phase,
        )
        self.samples += 1
        return result
