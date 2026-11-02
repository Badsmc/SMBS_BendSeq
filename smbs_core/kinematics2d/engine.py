"""Exact-in-2D rigid-panel rotation, independent of FreeCAD and OCC.

Cross sections model zero-thickness centre-lines. Contact, bend radii and
three-dimensional tooling require a separate validator; this is exploratory.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import cos, sin, isfinite
from typing import Tuple

EPS = 1e-9


@dataclass(frozen=True)
class Point2D:
    x: float
    y: float

    def __post_init__(self) -> None:
        if not isfinite(self.x) or not isfinite(self.y):
            raise ValueError("Point coordinates must be finite")


@dataclass(frozen=True)
class Segment2D:
    a: Point2D
    b: Point2D
    panel_id: str

    def __post_init__(self) -> None:
        if not self.panel_id:
            raise ValueError("panel_id is required")
        if self.a == self.b:
            raise ValueError("Zero-length panel segment")


@dataclass(frozen=True)
class Bend2D:
    bend_id: str
    pivot: Point2D
    moving_panels: Tuple[str, ...]
    angle_rad: float

    def __post_init__(self) -> None:
        if not self.bend_id or not self.moving_panels:
            raise ValueError("bend_id and moving_panels are required")
        if len(set(self.moving_panels)) != len(self.moving_panels):
            raise ValueError("Duplicate moving panel ID")
        if not isfinite(self.angle_rad):
            raise ValueError("Angle must be finite")


@dataclass(frozen=True)
class SectionState:
    segments: Tuple[Segment2D, ...]
    applied_bends: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(set(self.applied_bends)) != len(self.applied_bends):
            raise ValueError("Duplicate applied bend IDs")
        if len(set(s.panel_id for s in self.segments)) != len(self.segments):
            raise ValueError("Each segment must have a unique panel ID")


def _rotate(point: Point2D, pivot: Point2D, angle: float) -> Point2D:
    dx, dy = point.x - pivot.x, point.y - pivot.y
    c, s = cos(angle), sin(angle)
    return Point2D(pivot.x + dx*c - dy*s, pivot.y + dx*s + dy*c)


def fold_section(state: SectionState, bend: Bend2D) -> SectionState:
    """Rotate a specified rigid subset; never mutate the source state.

    The caller must supply the correct moving subtree and pivot from the
    canonical bend graph. This function does not infer physical feasibility.
    """
    if bend.bend_id in state.applied_bends:
        raise ValueError(f"Bend already applied: {bend.bend_id}")
    known = {s.panel_id for s in state.segments}
    unknown = set(bend.moving_panels) - known
    if unknown:
        raise ValueError(f"Unknown moving panels: {sorted(unknown)}")
    moving = set(bend.moving_panels)
    segments = tuple(
        Segment2D(_rotate(s.a, bend.pivot, bend.angle_rad),
                  _rotate(s.b, bend.pivot, bend.angle_rad), s.panel_id)
        if s.panel_id in moving else s for s in state.segments
    )
    return SectionState(segments, state.applied_bends + (bend.bend_id,))


def _cross(a: Point2D, b: Point2D, c: Point2D) -> float:
    return (b.x-a.x)*(c.y-a.y)-(b.y-a.y)*(c.x-a.x)


def _on_segment(a: Point2D, b: Point2D, p: Point2D, eps: float) -> bool:
    return (abs(_cross(a,b,p)) <= eps and
            min(a.x,b.x)-eps <= p.x <= max(a.x,b.x)+eps and
            min(a.y,b.y)-eps <= p.y <= max(a.y,b.y)+eps)


def intersect_segments(first: Segment2D, second: Segment2D, eps: float = EPS) -> bool:
    """True for any centre-line intersection, including endpoint contact.

    A True result is only a collision *candidate*, not an INVALID verdict.
    """
    if eps < 0 or not isfinite(eps):
        raise ValueError("eps must be nonnegative and finite")
    a,b,c,d = first.a,first.b,second.a,second.b
    o1,o2,o3,o4 = _cross(a,b,c),_cross(a,b,d),_cross(c,d,a),_cross(c,d,b)
    if (o1 > eps and o2 < -eps or o1 < -eps and o2 > eps) and (
        o3 > eps and o4 < -eps or o3 < -eps and o4 > eps
    ):
        return True
    return any((_on_segment(a,b,c,eps),_on_segment(a,b,d,eps),
                _on_segment(c,d,a,eps),_on_segment(c,d,b,eps)))


def self_intersections(state: SectionState, eps: float = EPS) -> Tuple[Tuple[str, str], ...]:
    """Return possible intersections; allowed hinge contacts are NOT filtered."""
    found = []
    for i, first in enumerate(state.segments):
        for second in state.segments[i+1:]:
            if intersect_segments(first, second, eps):
                found.append((first.panel_id, second.panel_id))
    return tuple(found)
