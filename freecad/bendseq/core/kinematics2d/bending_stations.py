"""Installed tooling on a shared press beam, in machine X coordinates.

Local segment intervals share the station datum, which need not be the center
of the usable working interval. Disabled stations are still installed.
"""
from collections import Counter
from dataclasses import dataclass
from math import isfinite

from .tooling_assembly import normalize_section_spans


@dataclass(frozen=True)
class StationLimits:
    """Optional declared limits. Unknown values are kept as None."""

    max_thickness_mm: float | None = None
    max_angle_deg: float | None = None
    min_inner_radius_mm: float | None = None
    v_opening_mm: float | None = None
    min_flange_mm: float | None = None
    max_load_kn: float | None = None

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if value is not None and (not isfinite(value) or value <= 0):
                raise ValueError("Invalid station limit: " + name)


@dataclass(frozen=True)
class BendingStation:
    station_id: str
    name: str
    punch_id: str
    die_id: str
    x_mm: float
    punch_spans: tuple[tuple[float, float], ...]
    die_spans: tuple[tuple[float, float], ...]
    punch_reversed: bool = False
    enabled: bool = True
    color: str = "#4E8CC4"
    limits: StationLimits = StationLimits()

    def __post_init__(self):
        if any(not isinstance(value, str) or not value.strip()
               for value in (self.station_id, self.name, self.punch_id, self.die_id)):
            raise ValueError("Station, punch and die identifiers must be specified")
        if not isfinite(self.x_mm):
            raise ValueError("Non-finite station coordinate")
        if not isinstance(self.limits, StationLimits):
            raise ValueError("Invalid station limits")
        for name in ("punch_spans", "die_spans"):
            spans = normalize_section_spans(getattr(self, name))
            if not spans:
                raise ValueError("Both tools must have installed sections")
            object.__setattr__(self, name, spans)

    def spans_xm(self, kind):
        if kind not in ("punch", "die"):
            raise ValueError("Unknown tool kind: " + str(kind))
        return tuple((self.x_mm + lo, self.x_mm + hi)
                     for lo, hi in getattr(self, kind + "_spans"))

    def occupied_interval(self):
        spans = self.spans_xm("punch") + self.spans_xm("die")
        return min(lo for lo, _ in spans), max(hi for _, hi in spans)

    def working_intervals(self):
        """Intersect actual coverage. Do not bridge untooled gaps."""
        punch = _continuous_intervals(self.spans_xm("punch"))
        die = _continuous_intervals(self.spans_xm("die"))
        return tuple((max(a, c), min(b, d)) for a, b in punch for c, d in die
                     if min(b, d) > max(a, c))

    def segments(self, kind):
        return Counter(round(hi - lo, 6) for lo, hi in getattr(self, kind + "_spans"))


def _continuous_intervals(spans):
    runs = []
    for lo, hi in spans:
        if runs and lo <= runs[-1][1] + 1e-6:
            runs[-1] = runs[-1][0], max(hi, runs[-1][1])
        else:
            runs.append((lo, hi))
    return runs


@dataclass(frozen=True)
class StationSetup:
    """Immutable input for layout and operation assignment.

    Assignments use operation IDs so pre-bend and finish can use different
    stations without changing the physical bend graph.
    """

    machine_x_mm: tuple[float, float]
    stations: tuple[BendingStation, ...] = ()
    minimum_gap_mm: float = 0.0
    assignments: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        x0, x1 = self.machine_x_mm
        if not all(isfinite(x) for x in (x0, x1, self.minimum_gap_mm)):
            raise ValueError("Invalid machine limits")
        if x0 >= x1 or self.minimum_gap_mm < 0:
            raise ValueError("Invalid machine length or station gap")
        object.__setattr__(self, "machine_x_mm", (x0, x1))
        object.__setattr__(self, "stations", tuple(self.stations))
        object.__setattr__(self, "assignments", tuple(sorted(
            (operation_id, station_id) for operation_id, station_id in self.assignments
        )))
        if len({s.station_id for s in self.stations}) != len(self.stations):
            raise ValueError("Duplicate station ID")
        if len(dict(self.assignments)) != len(self.assignments):
            raise ValueError("Duplicate operation assignment")
        enabled = {s.station_id for s in self.stations if s.enabled}
        if any(not operation_id or station_id not in enabled
               for operation_id, station_id in self.assignments):
            raise ValueError("Operation references a missing or disabled station")

    def assigned_station(self, operation_id):
        station_id = dict(self.assignments).get(operation_id)
        return next((s for s in self.stations if s.station_id == station_id), None)
