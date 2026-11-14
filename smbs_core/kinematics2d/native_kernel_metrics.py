"""Opt-in counters for native collision-kernel operations.

The counter measures exact *instrumented* FreeCAD Part calls, not all
possible OpenCASCADE activity (e.g. solid construction in other modules).
Calls are counted even when the underlying kernel raises. Disabled by
default; no modification to collision inputs, tolerances or return values.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Iterator


@dataclass
class NativeKernelCounters:
    common_calls: int = 0
    distance_calls: int = 0
    common_elapsed_s: float = 0.0
    distance_elapsed_s: float = 0.0
    failures: int = 0
    labels: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": "INSTRUMENTED_NATIVE_COLLISION_CALLS_ONLY",
            "common_calls": self.common_calls,
            "distance_calls": self.distance_calls,
            "total_instrumented_calls": self.common_calls + self.distance_calls,
            "common_elapsed_s": self.common_elapsed_s,
            "distance_elapsed_s": self.distance_elapsed_s,
            "total_elapsed_s": self.common_elapsed_s + self.distance_elapsed_s,
            "failures": self.failures,
            "labels": dict(sorted(self.labels.items())),
        }


_current: ContextVar[NativeKernelCounters | None] = ContextVar(
    "smbs_native_collision_kernel_counters", default=None
)


@contextmanager
def count_native_kernel_calls() -> Iterator[NativeKernelCounters]:
    """Record native operations within this context; nested scopes are isolated."""
    counter = NativeKernelCounters()
    token = _current.set(counter)
    try:
        yield counter
    finally:
        _current.reset(token)


def _call(shape: Any, method_name: str, other: Any, label: str) -> Any:
    counter = _current.get()
    if counter is None:
        return getattr(shape, method_name)(other)
    start = perf_counter()
    try:
        return getattr(shape, method_name)(other)
    except BaseException:
        counter.failures += 1
        raise
    finally:
        elapsed = perf_counter() - start
        if method_name == "common":
            counter.common_calls += 1
            counter.common_elapsed_s += elapsed
        else:
            counter.distance_calls += 1
            counter.distance_elapsed_s += elapsed
        counter.labels[label] = counter.labels.get(label, 0) + 1


def measured_common(shape: Any, other: Any, *, label: str) -> Any:
    return _call(shape, "common", other, label)


def measured_distance(shape: Any, other: Any, *, label: str) -> Any:
    return _call(shape, "distToShape", other, label)
