"""Bounded station draft search using the installed station/stock contracts.

This is allocation in the existing operation order, not a multi-station
collision or sequence search. All native geometry remains unverified.
"""
from dataclasses import dataclass, replace, asdict
from math import isfinite
from time import monotonic
from itertools import pairwise

from .bending_stations import BendingStation, StationSetup, StationLimits
from .bending_technology import TechnologyInputs, assess_technology
from .flange_die_check import positive_mm
from .segment_assembly_planner import validate_inventory
from .station_layout import calculate_station_segments, remaining_stock, validate_station_layout


@dataclass(frozen=True)
class StationPlan:
    setup: StationSetup | None
    status: str
    examined: int
    elapsed_s: float
    warnings: tuple[str, ...] = ()


def plan_stations(requirements, catalog, clamps, machine_x_mm, minimum_gap_mm,
                  *, technology=TechnologyInputs(), max_stations=3,
                  beam_width=12, max_trials=1500, budget_s=10.0,
                  cancelled=lambda: False, trace=lambda *a, **k: None):
    """Search a bounded beam of partial assignments. No live state is mutated.

    Requirements are operation-ID keyed. Staged operations carry angle=None
    for force screening. Unknown geometry or mounting cannot imply clearance.
    """
    requirements = tuple(requirements)
    if (not requirements or len({r.bend_id for r in requirements}) != len(requirements)
            or type(max_stations) is not int or not 1 <= max_stations <= 10
            or type(beam_width) is not int or beam_width < 1
            or type(max_trials) is not int or max_trials < 1
            or not isfinite(budget_s) or budget_s <= 0):
        raise ValueError("Invalid station search parameters")
    initial = StationSetup(machine_x_mm, minimum_gap_mm=minimum_gap_mm)
    if not clamps.centres_mm:
        raise ValueError("Configure the installed clamp positions")
    start = monotonic()
    examined = 0
    stock, descriptors = {}, {}
    for t in catalog.all_tools():
        try:
            rows = validate_inventory(t.metadata.get("segments", []))
            if not t.has_sidecar or not rows:
                raise ValueError("NO_SEGMENT_INVENTORY")
            stock[t.kind, t.tool_id] = rows
            descriptors[t.kind, t.tool_id] = t
        except (ValueError, TypeError) as exc:
            trace("STATION_TOOL_REJECTED", kind=t.kind, tool=t.tool_id, reason=str(exc))
    pairs = [(p,d) for p in catalog.punches for d in catalog.dies
             if ("punch",p.tool_id) in stock and ("die",d.tool_id) in stock]
    trace("STATION_SEARCH_STARTED", requirements=[asdict(r) for r in requirements],
          machine_x_mm=machine_x_mm, minimum_gap_mm=minimum_gap_mm,
          clamps=asdict(clamps), stock=[dict(kind=k[0],tool=k[1],segments=v) for k,v in stock.items()],
          technology=asdict(technology), max_stations=max_stations,
          beam_width=beam_width, max_trials=max_trials, budget_s=budget_s)
    order = tuple(r.bend_id for r in requirements)
    # Longest first avoids reserving a short assembly before a long bend;
    # final assignment and transition scoring use the original operation order.
    work = sorted(requirements, key=lambda r: -r.length_mm)
    compatibility = {}

    def interrupted():
        return cancelled() or examined >= max_trials or monotonic()-start >= budget_s

    def compatible(r, p, d):
        key = (r.bend_id,p.tool_id,d.tool_id)
        if key not in compatibility:
            opening = positive_mm(d.metadata.get("v_width_mm")) or positive_mm(d.metadata.get("v_opening_mm"))
            report = assess_technology((r,), opening, p.metadata, d.metadata, technology)
            flange = positive_mm(r.flange_mm)
            if opening is not None and flange is not None and flange < opening:
                report["warnings"].append("FLANGE_BELOW_NOMINAL_V: " + r.bend_id)
            compatibility[key] = (opening, report)
            trace("STATION_COMPATIBILITY", operation=r.bend_id, punch=p.tool_id, die=d.tool_id, report=report)
        return compatibility[key]

    def score(setup, warnings):
        assignments = dict(setup.assignments)
        assigned = [assignments[o] for o in order if o in assignments]
        transitions = sum(a != b for a,b in pairwise(assigned))
        width = sum(hi-lo for lo,hi in (s.occupied_interval() for s in setup.stations))
        return (len(setup.stations), len(warnings), transitions, width,
                tuple((s.punch_id,s.die_id,s.x_mm) for s in setup.stations), setup.assignments)

    def extend(setup, station, r, warnings, report):
        return (replace(setup, assignments=(*setup.assignments,(r.bend_id,station.station_id))),
                (*warnings, *report["warnings"]))

    beam = [(initial, ())]
    for r in work:
        candidates = []
        limited = False
        for setup, warnings in beam:
            if interrupted():
                limited = True
                break
            for station in setup.stations:
                if not station.enabled or not any(hi-lo >= r.length_mm-1e-6 for lo,hi in station.working_intervals()):
                    continue
                p = descriptors["punch",station.punch_id]
                d = descriptors["die",station.die_id]
                _, report = compatible(r,p,d)
                if not report["blocked"]:
                    candidates.append(extend(setup,station,r,warnings,report))
            if len(setup.stations) >= max_stations:
                continue
            available = remaining_stock(setup,stock)
            low,high = setup.machine_x_mm
            half = r.length_mm/2
            centers = tuple(clamps.centres_mm)
            positions = {low+half,high-half,(low+high)/2,*centers}
            positions.update((a+b)/2 for a,b in pairwise(centers))
            for s in setup.stations:
                lo,hi = s.occupied_interval()
                positions.update((lo-minimum_gap_mm-half,hi+minimum_gap_mm+half))
            positions = sorted(x for x in positions if low+half-1e-6 <= x <= high-half+1e-6)
            for p,d in pairs:
                if interrupted():
                    limited = True
                    break
                opening, report = compatible(r,p,d)
                if report["blocked"]:
                    continue
                if any(sum(a["length_mm"]*a["quantity"] for a in available.get(key,()))+1e-6 < r.length_mm
                       for key in (("punch",p.tool_id),("die",d.tool_id))):
                    trace("STATION_STOCK_REJECTED", operation=r.bend_id, punch=p.tool_id, die=d.tool_id)
                    continue
                for x in positions:
                    if interrupted():
                        limited = True
                        break
                    examined += 1
                    sid = "S" + str(len(setup.stations)+1)
                    nominal = ((-half,half),)
                    station = BendingStation(sid,sid,p.tool_id,d.tool_id,x,nominal,nominal,
                                             limits=StationLimits(v_opening_mm=opening))
                    try:
                        station = calculate_station_segments(setup,station,r.length_mm,stock,clamps)
                        updated = replace(setup,stations=(*setup.stations,station))
                        validate_station_layout(updated,stock,clamps)
                        # Die and punch may have different lengths; require real overlap.
                        if not any(hi-lo >= r.length_mm-1e-6 for lo,hi in station.working_intervals()):
                            raise ValueError("Insufficient continuous working interval")
                        candidates.append(extend(updated,station,r,warnings,report))
                        trace("STATION_TRIAL_ACCEPTED", operation=r.bend_id, station=asdict(station))
                    except (ValueError,TypeError) as exc:
                        trace("STATION_TRIAL_REJECTED", operation=r.bend_id, punch=p.tool_id, die=d.tool_id,
                              x_mm=x, reason=str(exc))
        if not candidates:
            status = "CANCELLED" if cancelled() else "SEARCH_LIMIT" if limited or interrupted() else "NO_DRAFT_FOUND"
            trace("STATION_SEARCH_FINISHED", status=status, operation=r.bend_id, examined=examined,
                  elapsed_s=monotonic()-start)
            return StationPlan(None,status,examined,monotonic()-start)
        # Deterministic deduplication of equal immutable layouts.
        unique = {}
        for setup,warnings in candidates:
            signature = (setup.stations,setup.assignments)
            if signature not in unique or score(setup,warnings)<score(setup,unique[signature][1]):
                unique[signature]=(setup,warnings)
        beam = sorted(unique.values(),key=lambda a:score(*a))[:beam_width]
        trace("STATION_SEARCH_DEPTH", operation=r.bend_id, retained=len(beam), examined=examined,
              elapsed_s=monotonic()-start)
        if cancelled() or ((limited or interrupted()) and r is not work[-1]):
            status = "CANCELLED" if cancelled() else "SEARCH_LIMIT"
            trace("STATION_SEARCH_FINISHED", status=status, examined=examined, elapsed_s=monotonic()-start)
            return StationPlan(None,status,examined,monotonic()-start)
    setup,warnings = beam[0]
    validate_station_layout(setup,stock,clamps)
    result = StationPlan(setup,"DRAFT_UNVERIFIED",examined,monotonic()-start,
                         tuple(dict.fromkeys((*warnings,"MULTI_STATION_GEOMETRY_AND_COLLISIONS_UNVERIFIED"))))
    trace("STATION_SEARCH_FINISHED", status=result.status, setup=asdict(setup), warnings=result.warnings,
          examined=examined, elapsed_s=result.elapsed_s)
    return result
