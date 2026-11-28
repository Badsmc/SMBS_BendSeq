"""Build a visual installed tool set using the same meshes as Mesh 3D."""
from dataclasses import replace
from math import isclose

from .mesh_sequence_screen import native_tool_meshes
from .tooling_25d import ToolingSetup2D
from .tooling_assembly import ToolAssembly


def mounted_station_meshes(stations, profiles, *, mesh_loader=native_tool_meshes):
    """Keep all mounted tools, including those inactive for assignments.

    The existing loader zeros the punch tip and die shoulder. Until shared
    mounting offsets are modeled, differing top/base heights cannot be shown
    as one physically mounted beam. Fail before returning any partial scene.
    """
    installed = []
    mount_z = {}
    for station in stations:
        try:
            punch = profiles["punch", station.punch_id]
            die = profiles["die", station.die_id]
            tools = ToolingSetup2D(
                punch=replace(punch, punch_reversed=station.punch_reversed), die=die,
                tool_assembly=ToolAssembly(
                    station.occupied_interval()[1] - station.occupied_interval()[0],
                    station.spans_xm("punch"), station.spans_xm("die"), (),
                ),
            )
            for kind, tool, facets in mesh_loader(tools, None):
                # Native machine geometry supplies the candidate mount plane
                # used for a visual fit. This does not validate mount datums.
                bounds = tool.machine_shape.BoundBox
                height = bounds.ZMax if kind == "punch" else bounds.ZMin
                if not isclose(height, mount_z.setdefault(kind, height), rel_tol=0, abs_tol=0.01):
                    raise ValueError(f"incompatible {kind} mounting height in shared-beam preview")
                installed.append((station.station_id, kind, tool, facets))
        except (KeyError, OSError, ValueError, RuntimeError) as exc:
            raise ValueError(f"{station.name}: {exc}") from exc
    return tuple(installed)
