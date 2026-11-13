"""Opt-in wall-clock attribution for native full-sheet solid construction.

Scope: instrumented construction regions only. It does not count all OCC
operations, change geometric inputs, or alter qualification evidence.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter
from typing import Iterator

_ALLOWED = frozenset({
    "panel_extrude", "panel_transform", "bend_zone_build",
    "sheet_union", "sheet_qualification",
    # Measured separately from OCC Common/distance and full-sheet union.
    # These paths currently account for much of the uninstrumented wall time.
    "machine_workpiece_copy", "machine_workpiece_transform",
    "native_punch_pose", "pair_geometry_sanity",
})
_CACHE_EVENTS = frozenset({
    "bend_zone_cache_hit", "bend_zone_cache_miss",
    "bend_zone_cache_eviction",
})


@dataclass
class NativeGeometryBuildMetrics:
    calls: dict[str, int] = field(default_factory=dict)
    elapsed_s: dict[str, float] = field(default_factory=dict)
    failures: dict[str, int] = field(default_factory=dict)
    cache_events: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        names = sorted(_ALLOWED)
        return {
            "scope": "INSTRUMENTED_NATIVE_SHEET_AND_POSE_REGIONS_ONLY",
            "calls": {k: self.calls.get(k, 0) for k in names},
            "elapsed_s": {k: self.elapsed_s.get(k, 0.0) for k in names},
            "failures": {k: self.failures.get(k, 0) for k in names},
            "cache_events": {
                k: self.cache_events.get(k, 0) for k in sorted(_CACHE_EVENTS)
            },
            "measured_total_s": sum(self.elapsed_s.values()),
            "note": "Nonoverlapping timed sheet-build/pose/sanity regions only; excludes exact OCC Common, distance and other validation.",
        }


_active: ContextVar[NativeGeometryBuildMetrics | None] = ContextVar(
    "smbs_native_geometry_build_metrics", default=None
)


@contextmanager
def count_native_geometry_builds() -> Iterator[NativeGeometryBuildMetrics]:
    """Enable timing within this scope; a nested scope owns separate metrics."""
    data = NativeGeometryBuildMetrics()
    token = _active.set(data)
    try:
        yield data
    finally:
        _active.reset(token)


@contextmanager
def measure_native_geometry_region(label: str) -> Iterator[None]:
    """Time a named build region without swallowing exceptions or changing values."""
    if label not in _ALLOWED:
        raise ValueError("unknown geometry measurement label: " + str(label))
    data = _active.get()
    if data is None:
        yield
        return
    start = perf_counter()
    try:
        yield
    except BaseException:
        data.failures[label] = data.failures.get(label, 0) + 1
        raise
    finally:
        data.calls[label] = data.calls.get(label, 0) + 1
        data.elapsed_s[label] = data.elapsed_s.get(label, 0.0) + (perf_counter() - start)


def record_native_geometry_cache_event(label: str) -> None:
    """Count cache operations only inside an active opt-in metrics scope."""
    if label not in _CACHE_EVENTS:
        raise ValueError("unknown geometry cache event: " + str(label))
    data = _active.get()
    if data is not None:
        data.cache_events[label] = data.cache_events.get(label, 0) + 1
