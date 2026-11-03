"""Deterministic one-to-one matching of SheetMetal bend axes to sketch segments.

Pure Python: no FreeCAD dependency. Matching is geometric, not index-based.
Different endpoint trims are allowed only when axes are collinear and overlap.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import hypot, isfinite
from typing import Sequence, Tuple

Point = Tuple[float, float]


class BendMatchError(ValueError):
    """No unique, geometrically supported bend-to-sketch assignment."""


@dataclass(frozen=True)
class AxisMatch:
    bend_index: int
    sketch_index: int
    offset_mm: float
    overlap_mm: float
    bend_length_mm: float
    sketch_length_mm: float


def _check(segment):
    if len(segment) != 2 or any(len(p) != 2 for p in segment):
        raise BendMatchError("Expected two XY endpoints per segment")
    a, b = segment
    if not all(isfinite(float(v)) for p in segment for v in p):
        raise BendMatchError("Non-finite coordinate")
    length = hypot(b[0] - a[0], b[1] - a[1])
    if length <= 0:
        raise BendMatchError("Zero-length segment")
    return length


def _candidate(bend, sketch, axis_tol, angle_tol, min_overlap_ratio):
    a, b = bend
    c, d = sketch
    lb, ls = _check(bend), _check(sketch)
    ux, uy = (b[0] - a[0]) / lb, (b[1] - a[1]) / lb
    vx, vy = (d[0] - c[0]) / ls, (d[1] - c[1]) / ls
    if abs(ux * vy - uy * vx) > angle_tol:
        return None
    def dist(p):
        return abs((p[0] - a[0]) * uy - (p[1] - a[1]) * ux)
    offset = max(dist(c), dist(d))
    if offset > axis_tol:
        return None
    t1 = (c[0] - a[0]) * ux + (c[1] - a[1]) * uy
    t2 = (d[0] - a[0]) * ux + (d[1] - a[1]) * uy
    overlap = max(0.0, min(lb, max(t1, t2)) - max(0.0, min(t1, t2)))
    if overlap <= 0 or overlap / min(lb, ls) < min_overlap_ratio:
        return None
    return offset, overlap, lb, ls


def match_bend_axes(
    bends: Sequence[Tuple[Point, Point]],
    sketch_lines: Sequence[Tuple[Point, Point]],
    axis_tolerance_mm: float = 0.01,
    parallel_tolerance: float = 1e-6,
    min_overlap_ratio: float = 0.95,
) -> Tuple[AxisMatch, ...]:
    """Return unique one-to-one matches, or raise on missing/ambiguous geometry.

    A sketch line can be shorter than its BendInfo axis. Parallel offset,
    overlap and uniqueness are validated. Index order is not assumed.
    """
    if not bends or len(bends) != len(sketch_lines):
        raise BendMatchError("Bend and sketch line counts must match and be nonzero")
    if not all(isfinite(v) for v in (axis_tolerance_mm, parallel_tolerance, min_overlap_ratio)):
        raise BendMatchError("Non-finite tolerance")
    if axis_tolerance_mm < 0 or not 0 <= parallel_tolerance < 1 or not 0 < min_overlap_ratio <= 1:
        raise BendMatchError("Invalid tolerance")
    candidates = []
    for i, bend in enumerate(bends):
        matches = []
        for j, line in enumerate(sketch_lines):
            data = _candidate(bend, line, axis_tolerance_mm, parallel_tolerance, min_overlap_ratio)
            if data is not None:
                matches.append(AxisMatch(i, j, *data))
        if len(matches) != 1:
            raise BendMatchError("Bend {} has {} candidate sketch lines (expected 1)".format(i + 1, len(matches)))
        candidates.append(matches[0])
    if len({m.sketch_index for m in candidates}) != len(candidates):
        raise BendMatchError("Multiple bends match the same sketch line")
    return tuple(candidates)
