"""Scoped proof-of-execution counters for actual multi-station preview results.

The counts describe sampled 2D evaluations, never continuous 3D safety.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class ScreeningExecutionCounters:
    previews: int = 0
    station_samples: int = 0
    evaluated_sections: int = 0
    unverified_stations: int = 0
    interference_stations: int = 0
    preview_statuses: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence": "OBSERVED_2D_SAMPLED_EXECUTION_NOT_3D_PROOF",
            "previews": self.previews,
            "station_samples": self.station_samples,
            "evaluated_sections": self.evaluated_sections,
            "unverified_stations": self.unverified_stations,
            "interference_stations": self.interference_stations,
            "preview_statuses": dict(sorted(self.preview_statuses.items())),
        }


_current: ContextVar[ScreeningExecutionCounters | None] = ContextVar(
    "smbs_multi_station_execution_counters", default=None,
)


@contextmanager
def count_station_screenings() -> Iterator[ScreeningExecutionCounters]:
    stats = ScreeningExecutionCounters()
    token = _current.set(stats)
    try:
        yield stats
    finally:
        _current.reset(token)


def record_station_preview(preview: Any) -> None:
    stats = _current.get()
    if stats is None:
        return
    stats.previews += 1
    status = str(getattr(preview, "status", "UNKNOWN"))
    stats.preview_statuses[status] = stats.preview_statuses.get(status, 0) + 1
    for station in getattr(preview, "stations", ()):
        stats.station_samples += 1
        station_status = str(getattr(station, "status", "UNVERIFIED"))
        reason = str(getattr(station, "reason", ""))
        if station_status == "UNVERIFIED" or reason != "sampled_2d_section_not_3d_validated":
            stats.unverified_stations += 1
        else:
            stats.evaluated_sections += 1
            if station_status == "INTERFERENCE":
                stats.interference_stations += 1
