"""Station-aware entry point for the shared Mesh/OCC screening engine."""
from dataclasses import asdict
from math import isfinite
from time import perf_counter
import traceback

from .mesh_sequence_screen import screen_mesh_sequence, native_tool_meshes, MeshBudgetExpired
from .mounted_station_preview import mounted_station_meshes
from .station_cycle import build_station_cycle
from .station_layout import validate_station_layout


def screen_station_sequence(session, setup, profiles, stock, clamps, orientations, config,
                            *, budget_seconds=60.0, min_overlap_mm3=1e-4,
                            progress=None, on_collision=None, trace=lambda *a, **k: None):
    if not isfinite(budget_seconds) or budget_seconds <= 0 or not isfinite(min_overlap_mm3) or min_overlap_mm3 <= 0:
        raise ValueError("Invalid station Mesh settings")
    started = perf_counter()
    order = tuple(op.operation_id for op in session.controller.operation_steps)
    def check_budget():
        if perf_counter()-started >= budget_seconds:
            raise MeshBudgetExpired
    try:
        trace("STARTED", setup=asdict(setup), order=order, orientations=orientations,
              budget_seconds=budget_seconds, min_overlap_mm3=min_overlap_mm3)
        validate_station_layout(setup, stock, clamps)
        check_budget()
        cycle, ids = build_station_cycle(setup, session.controller, profiles, orientations,
                                         session.panel_vertices_xy, config)
        check_budget()
        trace("POSES_READY", stations=ids, midpoints=[x for _,x in cycle.operation_setup])
        def loader(tooling, virtual_length):
            yield from native_tool_meshes(tooling, virtual_length, check_budget)
        meshes = mounted_station_meshes(setup.stations, profiles, mesh_loader=loader)
        expected = {(s.station_id,k) for s in setup.stations for k in ("punch","die")}
        if {(s,k) for s,k,_,_ in meshes} != expected or len(meshes) != len(expected):
            raise ValueError("Installed meshes do not match station layout")
        trace("MESHES_READY", tools=[dict(station=s,kind=k,facets=len(f)) for s,k,_,f in meshes])
        check_budget()
        report = screen_mesh_sequence(session, cycle, order,
            budget_seconds=budget_seconds-(perf_counter()-started), min_overlap_mm3=min_overlap_mm3,
            progress=progress, on_collision=on_collision, mounted_meshes=meshes,
            station_ids=ids, trace=trace)
    except Exception as exc:
        reason = "TIME_BUDGET" if isinstance(exc, MeshBudgetExpired) else f"{type(exc).__name__}: {exc}"
        trace("PREPARATION_ERROR", error=reason, traceback=traceback.format_exc())
        report = dict(completed=[], pending=list(order), findings=[], unresolved=[],
                      conditional_findings=[], forming_contact_slivers=[], samples=0,
                      reason=reason, scope="STATION_MESH_NOT_COMPLETED")
    report["elapsed_seconds"] = perf_counter()-started
    trace("FINISHED", report=report)
    return report
