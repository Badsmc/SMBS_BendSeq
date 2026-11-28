"""Longitudinal station layout and stock reservation in machine coordinates."""
from collections import Counter
from dataclasses import replace
from itertools import pairwise

from freecad.bendseq.core.geometry_backend.io.clamp_validator import (
    PunchSegment, validate_punch_chain,
)

from .segment_assembly_planner import plan_pair_for_bend, validate_inventory
from .tooling_assembly import CONTACT_EPS_MM, clamp_spans_for_centres, punch_segment_supports


class StationLayoutError(ValueError):
    pass


def remaining_stock(setup, stock):
    """Reserve installed sections globally, including disabled stations."""
    used = Counter()
    for station in setup.stations:
        for kind, tool_id in (("punch", station.punch_id), ("die", station.die_id)):
            for size, count in station.segments(kind).items():
                used[kind, tool_id, size] += count
    left = {}
    for key, inventory in stock.items():
        left[key] = []
        for row in validate_inventory(inventory):
            size = round(row["length_mm"], 6)
            available = row["quantity"] - used.pop((*key, size), 0)
            if available < 0:
                raise StationLayoutError(f"Insufficient stock: {key[1]}, {size:g} mm")
            if available:
                left[key].append({"length_mm": row["length_mm"], "quantity": available})
    if used:
        kind, tool, size = next(iter(used))
        raise StationLayoutError(f"Missing {kind} stock: {tool}, {size:g} mm")
    return left


def validate_station_layout(setup, stock, clamps):
    """Validate X bounds, spacing, inventory and existing punch mount rules."""
    left = remaining_stock(setup, stock)
    low, high = setup.machine_x_mm
    occupied = sorted((*s.occupied_interval(), s.station_id) for s in setup.stations)
    for start, end, station_id in occupied:
        if start < low - CONTACT_EPS_MM or end > high + CONTACT_EPS_MM:
            raise StationLayoutError(f"{station_id}: outside machine working length")
    for (_, end, station_id), (start, _, following) in pairwise(occupied):
        if start - end < setup.minimum_gap_mm - CONTACT_EPS_MM:
            raise StationLayoutError(f"{station_id}, {following}: station gap too small")

    centers = clamps.centres_mm
    if setup.stations and not centers:
        raise StationLayoutError("Configure the installed clamp positions")
    zones = clamp_spans_for_centres(centers, clamps.width_mm)
    if any(b[0] - a[1] < clamps.gap_between_mm - CONTACT_EPS_MM for a, b in pairwise(zones)):
        raise StationLayoutError("Installed clamps violate minimum spacing")
    for station in setup.stations:
        spans = station.spans_xm("punch")
        for index, contacts in enumerate(punch_segment_supports(spans, zones), 1):
            if not contacts:
                raise StationLayoutError(f"{station.station_id}: punch segment {index} is unsupported")
        result = validate_punch_chain([PunchSegment(*span) for span in spans], clamps)
        if not result.valid:
            raise StationLayoutError(f"{station.station_id}: " + "; ".join(result.errors))
    return left


def calculate_station_segments(setup, station, length_mm, stock, clamps):
    """Replan one station with stock left after reserving all other stations."""
    others = replace(setup, stations=tuple(
        s for s in setup.stations if s.station_id != station.station_id
    ), assignments=())
    left = remaining_stock(others, stock)
    if not clamps.centres_mm:
        raise StationLayoutError("Configure the installed clamp positions")
    local_clamps = replace(clamps, positions_mm=tuple(
        center - station.x_mm for center in clamps.centres_mm
    ), repositionable=False)
    plan = plan_pair_for_bend(
        left.get(("punch", station.punch_id), ()),
        left.get(("die", station.die_id), ()), length_mm, local_clamps,
        full_coverage=True,
    )
    if plan["status"] != "ASSEMBLY_CANDIDATE_UNVERIFIED":
        raise StationLayoutError(f"{station.station_id}: {plan['status']}")
    updated = replace(station, **{
        kind + "_spans": tuple((row["start_x_mm"], row["end_x_mm"])
                              for row in plan[kind + "_segments"])
        for kind in ("punch", "die")
    })
    new_stations = tuple(updated if s.station_id == station.station_id else s for s in setup.stations)
    if not any(s.station_id == station.station_id for s in setup.stations):
        new_stations += (updated,)
    validate_station_layout(replace(setup, stations=new_stations), stock, clamps)
    return updated


def auto_layout_stations(setup, stock, clamps):
    """Place stations left to right at bounds, clamp centers and clamp events.

    This bounded greedy layout can fail even when another order fits.
    It never edits the input config, inventory or clamp positions.
    """
    remaining_stock(setup, stock)
    low, high = setup.machine_x_mm
    widths = [b - a for a, b in (s.occupied_interval() for s in setup.stations)]
    if sum(widths) + max(0, len(widths) - 1) * setup.minimum_gap_mm > high - low + CONTACT_EPS_MM:
        raise StationLayoutError("Station spans and gaps exceed machine working length")
    placed = []
    for station in sorted(setup.stations, key=lambda s: s.x_mm):
        start, end = station.occupied_interval()
        local_lo, local_hi = start - station.x_mm, end - station.x_mm
        lower, upper = low - local_lo, high - local_hi
        if placed:
            lower = max(lower, placed[-1].occupied_interval()[1] + setup.minimum_gap_mm - local_lo)
        if lower > upper + CONTACT_EPS_MM:
            raise StationLayoutError(f"{station.station_id}: no length remains in this layout order")
        events = {lower, upper, station.x_mm}
        for center in clamps.centres_mm:
            events.add(center - (local_lo + local_hi) / 2)
            for lo, hi in station.punch_spans:
                for edge in (center - clamps.width_mm / 2, center + clamps.width_mm / 2):
                    events.update((
                        edge - lo, edge - hi,
                        edge - lo - clamps.overhang_ratio * (hi - lo),
                        edge - hi + clamps.overhang_ratio * (hi - lo),
                        edge - lo + clamps.joint_tolerance_mm,
                        edge - hi - clamps.joint_tolerance_mm,
                    ))
        bounds = sorted(x for x in events if lower <= x <= upper)
        candidates = sorted(set(bounds).union((a + b) / 2 for a, b in pairwise(bounds)))
        for x in candidates:
            candidate = replace(station, x_mm=x)
            try:
                validate_station_layout(replace(setup, stations=(*placed, candidate),
                                                assignments=()), stock, clamps)
            except StationLayoutError:
                continue
            placed.append(candidate)
            break
        else:
            raise StationLayoutError(f"{station.station_id}: no supported placement found in this layout order")
    result = replace(setup, stations=tuple(placed))
    validate_station_layout(result, stock, clamps)
    return result
