"""Custom Punch & Die Tooling System for PressBrakeSim 2.5D (Phase 5A).

Provides:
- Exact 2D geometric primitives (`LineSegment2D`, `ArcSegment2D`, `Circle2D`,
  `Contour2D`, `ProfileGeometry2D`) that preserve straight lines and circular
  arcs without polyline degradation.
- Explicit coordinate systems and transforms (`Transform2D`, `CoordinateFrame2D`,
  source -> tool_local -> machine -> view).
- Geometric datums (`PunchDatums2D`, `DieDatums2D`) extracted from exact
  profile geometry or overridden explicitly by the user.
- Unified tool data contract (`ToolProfile`) with strict validation statuses
  (`IMPORTED`, `NEEDS_ALIGNMENT`, `GEOMETRY_VERIFIED`, `INVALID`) and honest
  `"NOT_SPECIFIED"` markers for unknown parameters.
- Importers for DXF (2D profile), STEP/STP (3D model via FreeCAD/OpenCASCADE),
  and open FreeCAD Shapes (read-only copy without mutating source objects).
- Persistent versioned JSON `ToolLibrary` with geometry caching and reload
  equivalence verification.
- Static 2.5D scene positioning (`ToolingSetup2D`) relative to the active sheet
  bend without mutating `PanelGraph`, `FoldState`, `BendZone`, or
  `BendSequenceController`.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union


@dataclass(frozen=True)
class Transform2D:
    """Explicit 2D affine rigid transform `[r00 r01 tx; r10 r11 ty; 0 0 1]`."""
    r00: float = 1.0
    r01: float = 0.0
    tx: float = 0.0
    r10: float = 0.0
    r11: float = 1.0
    ty: float = 0.0

    @staticmethod
    def identity() -> "Transform2D":
        return Transform2D()

    @staticmethod
    def translation(dx: float, dy: float) -> "Transform2D":
        return Transform2D(1.0, 0.0, float(dx), 0.0, 1.0, float(dy))

    @staticmethod
    def rotation(angle_rad: float, center: Tuple[float, float] = (0.0, 0.0)) -> "Transform2D":
        c = math.cos(float(angle_rad))
        s = math.sin(float(angle_rad))
        cx, cy = float(center[0]), float(center[1])
        tx = cx - (c * cx - s * cy)
        ty = cy - (s * cx + c * cy)
        return Transform2D(c, -s, tx, s, c, ty)

    def apply(self, pt: Tuple[float, float]) -> Tuple[float, float]:
        x, y = float(pt[0]), float(pt[1])
        return (
            self.r00 * x + self.r01 * y + self.tx,
            self.r10 * x + self.r11 * y + self.ty,
        )

    def then(self, after: "Transform2D") -> "Transform2D":
        """Return composite transform that applies `self` first, then `after`."""
        return Transform2D(
            r00=after.r00 * self.r00 + after.r01 * self.r10,
            r01=after.r00 * self.r01 + after.r01 * self.r11,
            tx=after.r00 * self.tx + after.r01 * self.ty + after.tx,
            r10=after.r10 * self.r00 + after.r11 * self.r10,
            r11=after.r10 * self.r01 + after.r11 * self.r11,
            ty=after.r10 * self.tx + after.r11 * self.ty + after.ty,
        )

    def to_matrix_tuple(self) -> Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]]:
        return (
            (self.r00, self.r01, self.tx),
            (self.r10, self.r11, self.ty),
            (0.0, 0.0, 1.0),
        )


NOT_SPECIFIED = "NOT_SPECIFIED"
SCHEMA_VERSION = "2.5D-tooling-1.0"
DEFAULT_GEOMETRY_TOL_MM = 1e-3
DEFAULT_INITIAL_PUNCH_CLEARANCE_MM = 5.0

Point2D = Tuple[float, float]
BBox2D = Tuple[float, float, float, float]  # (min_x, min_y, max_x, max_y)


class ToolingError(RuntimeError):
    """Raised when tool import, validation, alignment, or persistence fails."""


class ToolKind(str, Enum):
    PUNCH = "PUNCH"
    DIE = "DIE"


class ValidationStatus(str, Enum):
    IMPORTED = "IMPORTED"
    NEEDS_ALIGNMENT = "NEEDS_ALIGNMENT"
    GEOMETRY_VERIFIED = "GEOMETRY_VERIFIED"
    INVALID = "INVALID"


class CoordinateSpace(str, Enum):
    SOURCE = "SOURCE"
    TOOL_LOCAL = "TOOL_LOCAL"
    MACHINE = "MACHINE"
    VIEW = "VIEW"


# Standard DXF $INSUNITS mapping to millimeters
DXF_INSUNITS_TO_MM: Dict[int, Tuple[str, float]] = {
    0: ("unitless_mm", 1.0),
    1: ("in", 25.4),
    2: ("ft", 304.8),
    4: ("mm", 1.0),
    5: ("cm", 10.0),
    6: ("m", 1000.0),
}

UNIT_SCALE_TO_MM: Dict[str, float] = {
    "mm": 1.0,
    "millimeter": 1.0,
    "millimeters": 1.0,
    "unitless_mm": 1.0,
    "cm": 10.0,
    "centimeter": 10.0,
    "m": 1000.0,
    "meter": 1000.0,
    "in": 25.4,
    "inch": 25.4,
    "inches": 25.4,
    "ft": 304.8,
}


def _is_finite_pt(pt: Sequence[float]) -> bool:
    return len(pt) == 2 and math.isfinite(float(pt[0])) and math.isfinite(float(pt[1]))


def _dist(a: Point2D, b: Point2D) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _normalize_angle_deg(angle_deg: float) -> float:
    res = math.fmod(float(angle_deg), 360.0)
    if res < 0.0:
        res += 360.0
    return res


def _ccw_sweep_deg(start_deg: float, end_deg: float) -> float:
    s = _normalize_angle_deg(start_deg)
    e = _normalize_angle_deg(end_deg)
    diff = e - s
    if diff <= 1e-9:
        diff += 360.0
    return diff


@dataclass(frozen=True)
class LineSegment2D:
    """Exact 2D straight line segment."""
    start: Point2D
    end: Point2D

    def __post_init__(self) -> None:
        if not (_is_finite_pt(self.start) and _is_finite_pt(self.end)):
            raise ToolingError("Non-finite coordinates in LineSegment2D")
        object.__setattr__(self, "start", (float(self.start[0]), float(self.start[1])))
        object.__setattr__(self, "end", (float(self.end[0]), float(self.end[1])))

    @property
    def kind(self) -> str:
        return "LINE"

    @property
    def length(self) -> float:
        return _dist(self.start, self.end)

    def reversed(self) -> "LineSegment2D":
        return LineSegment2D(self.end, self.start)

    def sample_points(self, arc_segments: int = 16) -> Tuple[Point2D, ...]:
        return (self.start, self.end)

    def transformed(self, tr: Transform2D) -> "LineSegment2D":
        return LineSegment2D(tr.apply(self.start), tr.apply(self.end))

    def scaled(self, factor: float) -> "LineSegment2D":
        return LineSegment2D(
            (self.start[0] * factor, self.start[1] * factor),
            (self.end[0] * factor, self.end[1] * factor),
        )

    def bbox(self) -> BBox2D:
        return (
            min(self.start[0], self.end[0]),
            min(self.start[1], self.end[1]),
            max(self.start[0], self.end[0]),
            max(self.start[1], self.end[1]),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "LINE",
            "start": [self.start[0], self.start[1]],
            "end": [self.end[0], self.end[1]],
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "LineSegment2D":
        return LineSegment2D(
            start=(float(data["start"][0]), float(data["start"][1])),
            end=(float(data["end"][0]), float(data["end"][1])),
        )


@dataclass(frozen=True)
class ArcSegment2D:
    """Exact 2D circular arc segment.

    Parameterized by `center`, `radius`, `start`, `end`, and `ccw` traversal
    direction from `start` to `end`.
    """
    start: Point2D
    end: Point2D
    center: Point2D
    radius: float
    ccw: bool = True

    def __post_init__(self) -> None:
        if not (
            _is_finite_pt(self.start)
            and _is_finite_pt(self.end)
            and _is_finite_pt(self.center)
            and math.isfinite(float(self.radius))
        ):
            raise ToolingError("Non-finite parameters in ArcSegment2D")
        r = float(self.radius)
        if r <= 0.0:
            raise ToolingError(f"Arc radius must be positive, got {r}")
        object.__setattr__(self, "start", (float(self.start[0]), float(self.start[1])))
        object.__setattr__(self, "end", (float(self.end[0]), float(self.end[1])))
        object.__setattr__(self, "center", (float(self.center[0]), float(self.center[1])))
        object.__setattr__(self, "radius", r)
        object.__setattr__(self, "ccw", bool(self.ccw))

    @property
    def kind(self) -> str:
        return "ARC"

    @property
    def start_angle_deg(self) -> float:
        return _normalize_angle_deg(
            math.degrees(math.atan2(self.start[1] - self.center[1], self.start[0] - self.center[0]))
        )

    @property
    def end_angle_deg(self) -> float:
        return _normalize_angle_deg(
            math.degrees(math.atan2(self.end[1] - self.center[1], self.end[0] - self.center[0]))
        )

    @property
    def sweep_angle_deg(self) -> float:
        """Signed angular sweep in degrees (+ for CCW, - for CW)."""
        if self.ccw:
            return _ccw_sweep_deg(self.start_angle_deg, self.end_angle_deg)
        return -_ccw_sweep_deg(self.end_angle_deg, self.start_angle_deg)

    @property
    def length(self) -> float:
        return self.radius * math.radians(abs(self.sweep_angle_deg))

    def midpoint(self) -> Point2D:
        mid_ang = math.radians(self.start_angle_deg + 0.5 * self.sweep_angle_deg)
        return (
            self.center[0] + self.radius * math.cos(mid_ang),
            self.center[1] + self.radius * math.sin(mid_ang),
        )

    def reversed(self) -> "ArcSegment2D":
        return ArcSegment2D(
            start=self.end,
            end=self.start,
            center=self.center,
            radius=self.radius,
            ccw=not self.ccw,
        )

    def sample_points(self, arc_segments: int = 16) -> Tuple[Point2D, ...]:
        sweep = self.sweep_angle_deg
        n = max(4, int(round(max(arc_segments, abs(sweep) / 5.0))))
        a0 = self.start_angle_deg
        pts: List[Point2D] = [self.start]
        for i in range(1, n):
            ang = math.radians(a0 + sweep * (i / n))
            pts.append((
                self.center[0] + self.radius * math.cos(ang),
                self.center[1] + self.radius * math.sin(ang),
            ))
        pts.append(self.end)
        return tuple(pts)

    def transformed(self, tr: Transform2D) -> "ArcSegment2D":
        det = tr.r00 * tr.r11 - tr.r01 * tr.r10
        new_ccw = self.ccw if det >= 0.0 else (not self.ccw)
        return ArcSegment2D(
            start=tr.apply(self.start),
            end=tr.apply(self.end),
            center=tr.apply(self.center),
            radius=self.radius,
            ccw=new_ccw,
        )

    def scaled(self, factor: float) -> "ArcSegment2D":
        if factor <= 0.0:
            raise ToolingError(f"Scale factor must be positive, got {factor}")
        return ArcSegment2D(
            start=(self.start[0] * factor, self.start[1] * factor),
            end=(self.end[0] * factor, self.end[1] * factor),
            center=(self.center[0] * factor, self.center[1] * factor),
            radius=self.radius * factor,
            ccw=self.ccw,
        )

    def bbox(self) -> BBox2D:
        pts = list(self.sample_points(arc_segments=24))
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "ARC",
            "start": [self.start[0], self.start[1]],
            "end": [self.end[0], self.end[1]],
            "center": [self.center[0], self.center[1]],
            "radius": self.radius,
            "ccw": self.ccw,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "ArcSegment2D":
        return ArcSegment2D(
            start=(float(data["start"][0]), float(data["start"][1])),
            end=(float(data["end"][0]), float(data["end"][1])),
            center=(float(data["center"][0]), float(data["center"][1])),
            radius=float(data["radius"]),
            ccw=bool(data.get("ccw", True)),
        )

    @staticmethod
    def from_dxf_arc(cx: float, cy: float, radius: float, start_deg: float, end_deg: float) -> "ArcSegment2D":
        """Create an exact CCW arc from DXF center, radius, and start/end degrees."""
        r = float(radius)
        if r <= 0.0:
            raise ToolingError(f"DXF ARC radius must be positive, got {r}")
        a0 = math.radians(float(start_deg))
        a1 = math.radians(float(end_deg))
        p0 = (float(cx) + r * math.cos(a0), float(cy) + r * math.sin(a0))
        p1 = (float(cx) + r * math.cos(a1), float(cy) + r * math.sin(a1))
        return ArcSegment2D(start=p0, end=p1, center=(float(cx), float(cy)), radius=r, ccw=True)

    @staticmethod
    def from_bulge(p0: Point2D, p1: Point2D, bulge: float) -> "ArcSegment2D":
        """Create an exact circular arc from LWPOLYLINE/POLYLINE vertex pair and bulge."""
        chord = _dist(p0, p1)
        if chord <= 1e-12:
            raise ToolingError("Degenerate zero-length bulge segment")
        b = float(bulge)
        theta = 4.0 * math.atan(abs(b))
        radius = chord / (2.0 * math.sin(theta / 2.0))
        mx = 0.5 * (p0[0] + p1[0])
        my = 0.5 * (p0[1] + p1[1])
        ux = (p1[0] - p0[0]) / chord
        uy = (p1[1] - p0[1]) / chord
        # Left normal
        nx, ny = -uy, ux
        cot_half = (1.0 - b * b) / (2.0 * b)
        cx = mx + nx * (0.5 * chord * cot_half)
        cy = my + ny * (0.5 * chord * cot_half)
        return ArcSegment2D(
            start=p0,
            end=p1,
            center=(cx, cy),
            radius=abs(radius),
            ccw=(b > 0.0),
        )


@dataclass(frozen=True)
class Circle2D:
    """Exact full 2D circle (e.g. mounting/lightening hole inside a tool profile)."""
    center: Point2D
    radius: float

    def __post_init__(self) -> None:
        if not (_is_finite_pt(self.center) and math.isfinite(float(self.radius))):
            raise ToolingError("Non-finite parameters in Circle2D")
        r = float(self.radius)
        if r <= 0.0:
            raise ToolingError(f"Circle radius must be positive, got {r}")
        object.__setattr__(self, "center", (float(self.center[0]), float(self.center[1])))
        object.__setattr__(self, "radius", r)

    @property
    def kind(self) -> str:
        return "CIRCLE"

    @property
    def start(self) -> Point2D:
        return (self.center[0] + self.radius, self.center[1])

    @property
    def end(self) -> Point2D:
        return self.start

    @property
    def length(self) -> float:
        return 2.0 * math.pi * self.radius

    def reversed(self) -> "Circle2D":
        return self

    def sample_points(self, arc_segments: int = 32) -> Tuple[Point2D, ...]:
        n = max(16, int(arc_segments))
        pts: List[Point2D] = []
        for i in range(n + 1):
            ang = 2.0 * math.pi * (i / n)
            pts.append((
                self.center[0] + self.radius * math.cos(ang),
                self.center[1] + self.radius * math.sin(ang),
            ))
        return tuple(pts)

    def transformed(self, tr: Transform2D) -> "Circle2D":
        return Circle2D(center=tr.apply(self.center), radius=self.radius)

    def scaled(self, factor: float) -> "Circle2D":
        if factor <= 0.0:
            raise ToolingError(f"Scale factor must be positive, got {factor}")
        return Circle2D(
            center=(self.center[0] * factor, self.center[1] * factor),
            radius=self.radius * factor,
        )

    def bbox(self) -> BBox2D:
        return (
            self.center[0] - self.radius,
            self.center[1] - self.radius,
            self.center[0] + self.radius,
            self.center[1] + self.radius,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "CIRCLE",
            "center": [self.center[0], self.center[1]],
            "radius": self.radius,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Circle2D":
        return Circle2D(
            center=(float(data["center"][0]), float(data["center"][1])),
            radius=float(data["radius"]),
        )


Segment2D = Union[LineSegment2D, ArcSegment2D, Circle2D]


def segment_from_dict(data: Dict[str, Any]) -> Segment2D:
    t = data.get("type")
    if t == "LINE":
        return LineSegment2D.from_dict(data)
    if t == "ARC":
        return ArcSegment2D.from_dict(data)
    if t == "CIRCLE":
        return Circle2D.from_dict(data)
    raise ToolingError(f"Unsupported 2D segment type: {t!r}")


def _polygon_signed_area(pts: Sequence[Point2D]) -> float:
    if len(pts) < 3:
        return 0.0
    area2 = 0.0
    for i in range(len(pts)):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % len(pts)]
        area2 += x0 * y1 - x1 * y0
    return 0.5 * area2


def _point_in_polygon(pt: Point2D, poly: Sequence[Point2D]) -> bool:
    """Ray-casting point-in-polygon check."""
    x, y = pt
    inside = False
    n = len(poly)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-15) + xi):
            inside = not inside
        j = i
    return inside


@dataclass(frozen=True)
class Contour2D:
    """Closed 2D loop composed of ordered exact `LineSegment2D` / `ArcSegment2D` primitives, or a `Circle2D`."""
    segments: Tuple[Segment2D, ...]
    is_hole: bool = False

    def __post_init__(self) -> None:
        if not self.segments:
            raise ToolingError("Contour2D must contain at least one segment")
        object.__setattr__(self, "segments", tuple(self.segments))
        object.__setattr__(self, "is_hole", bool(self.is_hole))

    def is_closed(self, tol: float = 1e-2) -> bool:
        if len(self.segments) == 1 and isinstance(self.segments[0], Circle2D):
            return True
        if any(isinstance(s, Circle2D) for s in self.segments):
            return False
        for i in range(len(self.segments)):
            cur_end = self.segments[i].end
            nxt_start = self.segments[(i + 1) % len(self.segments)].start
            if _dist(cur_end, nxt_start) > tol:
                return False
        return True

    def sample_polygon(self, arc_segments: int = 16) -> Tuple[Point2D, ...]:
        if len(self.segments) == 1 and isinstance(self.segments[0], Circle2D):
            return self.segments[0].sample_points(arc_segments=max(24, arc_segments))[:-1]
        pts: List[Point2D] = []
        for seg in self.segments:
            s_pts = seg.sample_points(arc_segments=arc_segments)
            if not pts:
                pts.extend(s_pts[:-1])
            else:
                pts.extend(s_pts[:-1])
        return tuple(pts)

    @property
    def signed_area(self) -> float:
        if len(self.segments) == 1 and isinstance(self.segments[0], Circle2D):
            c = self.segments[0]
            return math.pi * c.radius * c.radius * (-1.0 if self.is_hole else 1.0)
        return _polygon_signed_area(self.sample_polygon(arc_segments=24))

    @property
    def area(self) -> float:
        return abs(self.signed_area)

    def bbox(self) -> BBox2D:
        boxes = [s.bbox() for s in self.segments]
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )

    def transformed(self, tr: Transform2D) -> "Contour2D":
        return Contour2D(
            segments=tuple(s.transformed(tr) for s in self.segments),
            is_hole=self.is_hole,
        )

    def scaled(self, factor: float) -> "Contour2D":
        return Contour2D(
            segments=tuple(s.scaled(factor) for s in self.segments),
            is_hole=self.is_hole,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_hole": self.is_hole,
            "segments": [s.to_dict() for s in self.segments],
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Contour2D":
        return Contour2D(
            segments=tuple(segment_from_dict(s) for s in data["segments"]),
            is_hole=bool(data.get("is_hole", False)),
        )


@dataclass(frozen=True)
class ProfileGeometry2D:
    """Exact 2D cross-section geometry with one outer contour and optional inner holes."""
    outer_contour: Contour2D
    holes: Tuple[Contour2D, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "holes", tuple(self.holes))

    @property
    def all_segments(self) -> Tuple[Segment2D, ...]:
        segs = list(self.outer_contour.segments)
        for h in self.holes:
            segs.extend(h.segments)
        return tuple(segs)

    @property
    def line_count(self) -> int:
        return sum(1 for s in self.all_segments if isinstance(s, LineSegment2D))

    @property
    def arc_count(self) -> int:
        return sum(1 for s in self.all_segments if isinstance(s, (ArcSegment2D, Circle2D)))

    def bbox(self) -> BBox2D:
        return self.outer_contour.bbox()

    def transformed(self, tr: Transform2D) -> "ProfileGeometry2D":
        return ProfileGeometry2D(
            outer_contour=self.outer_contour.transformed(tr),
            holes=tuple(h.transformed(tr) for h in self.holes),
        )

    def scaled(self, factor: float) -> "ProfileGeometry2D":
        return ProfileGeometry2D(
            outer_contour=self.outer_contour.scaled(factor),
            holes=tuple(h.scaled(factor) for h in self.holes),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "outer_contour": self.outer_contour.to_dict(),
            "holes": [h.to_dict() for h in self.holes],
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "ProfileGeometry2D":
        return ProfileGeometry2D(
            outer_contour=Contour2D.from_dict(data["outer_contour"]),
            holes=tuple(Contour2D.from_dict(h) for h in data.get("holes", ())),
        )


def _segments_properly_intersect(
    a0: Point2D, a1: Point2D, b0: Point2D, b1: Point2D, eps: float = 1e-6
) -> bool:
    """Return True if two open line segments `(a0, a1)` and `(b0, b1)` cross or overlap improperly."""
    if (
        _dist(a0, b0) <= eps
        or _dist(a0, b1) <= eps
        or _dist(a1, b0) <= eps
        or _dist(a1, b1) <= eps
    ):
        return False

    def ccw(p: Point2D, q: Point2D, r: Point2D) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    d1 = ccw(a0, a1, b0)
    d2 = ccw(a0, a1, b1)
    d3 = ccw(b0, b1, a0)
    d4 = ccw(b0, b1, a1)

    if ((d1 > eps and d2 < -eps) or (d1 < -eps and d2 > eps)) and (
        (d3 > eps and d4 < -eps) or (d3 < -eps and d4 > eps)
    ):
        return True
    return False


def has_self_intersection(contour: Contour2D, eps: float = 1e-5) -> bool:
    """Check whether a closed contour self-intersects."""
    if len(contour.segments) == 1 and isinstance(contour.segments[0], Circle2D):
        return False
    pts = contour.sample_polygon(arc_segments=12)
    n = len(pts)
    if n < 3:
        return True
    edges = [(pts[i], pts[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            if _segments_properly_intersect(edges[i][0], edges[i][1], edges[j][0], edges[j][1], eps=eps):
                return True
    return False


def assemble_contours_from_segments(
    segments: Sequence[Segment2D],
    tol: float = 1e-2,
) -> ProfileGeometry2D:
    """Chain unordered 2D segments into closed contours and classify outer contour vs inner holes.

    Raises `ToolingError` on empty input, degenerate segments, open contours,
    multiple disconnected outer islands, or self-intersections.
    """
    if not segments:
        raise ToolingError("No geometric segments found in profile")

    circles: List[Circle2D] = []
    open_segs: List[Union[LineSegment2D, ArcSegment2D]] = []
    for s in segments:
        if s.length <= 1e-6:
            raise ToolingError(f"Degenerate zero-length segment detected (length={s.length:.6g})")
        if isinstance(s, Circle2D):
            circles.append(s)
        else:
            open_segs.append(s)

    loops: List[Contour2D] = []
    used = [False] * len(open_segs)

    for start_idx in range(len(open_segs)):
        if used[start_idx]:
            continue
        used[start_idx] = True
        chain: List[Segment2D] = [open_segs[start_idx]]
        loop_start = chain[0].start
        cur_end = chain[0].end

        while _dist(cur_end, loop_start) > tol:
            best_idx = -1
            best_rev = False
            best_d = float("inf")
            for j, cand in enumerate(open_segs):
                if used[j]:
                    continue
                d_fwd = _dist(cur_end, cand.start)
                if d_fwd < best_d:
                    best_d = d_fwd
                    best_idx = j
                    best_rev = False
                d_rev = _dist(cur_end, cand.end)
                if d_rev < best_d:
                    best_d = d_rev
                    best_idx = j
                    best_rev = True
            if best_idx < 0 or best_d > tol:
                raise ToolingError(
                    f"Open contour detected: endpoint ({cur_end[0]:.4f}, {cur_end[1]:.4f}) "
                    f"does not connect within tolerance {tol} mm (nearest gap={best_d:.4f} mm)"
                )
            used[best_idx] = True
            nxt_seg = open_segs[best_idx].reversed() if best_rev else open_segs[best_idx]
            chain.append(nxt_seg)
            cur_end = nxt_seg.end

        contour = Contour2D(segments=tuple(chain), is_hole=False)
        if not contour.is_closed(tol=tol):
            raise ToolingError("Contour is not closed within tolerance")
        if has_self_intersection(contour):
            raise ToolingError("Self-intersecting contour detected in tool profile")
        if contour.area <= 1e-4:
            raise ToolingError("Degenerate zero-area contour detected")
        loops.append(contour)

    for c in circles:
        loops.append(Contour2D(segments=(c,), is_hole=False))

    if not loops:
        raise ToolingError("Failed to assemble any closed contour")

    # Sort by enclosed area descending; largest contour is the outer boundary
    loops.sort(key=lambda lp: lp.area, reverse=True)
    outer = loops[0]
    outer_poly = outer.sample_polygon(arc_segments=20)
    outer_bbox = outer.bbox()

    holes: List[Contour2D] = []
    for sub in loops[1:]:
        sub_poly = sub.sample_polygon(arc_segments=12)
        sb = sub.bbox()
        if (
            sb[0] < outer_bbox[0] - tol
            or sb[1] < outer_bbox[1] - tol
            or sb[2] > outer_bbox[2] + tol
            or sb[3] > outer_bbox[3] + tol
            or not _point_in_polygon(sub_poly[0], outer_poly)
        ):
            raise ToolingError(
                "Ambiguous profile geometry: multiple disconnected outer contours detected"
            )
        holes.append(Contour2D(segments=sub.segments, is_hole=True))

    return ProfileGeometry2D(outer_contour=outer, holes=tuple(holes))


@dataclass(frozen=True)
class PunchDatums2D:
    """Geometric datums for a PUNCH tool in tool coordinates."""
    working_tip: Point2D
    axis_direction: Point2D
    mounting_base: Tuple[Point2D, Point2D]
    side_bounds: Tuple[float, float]
    tip_radius_mm: Union[float, str] = NOT_SPECIFIED
    included_angle_deg: Union[float, str] = NOT_SPECIFIED
    source: str = "geometry"  # "geometry" | "manual" | "sidecar"

    def transformed(self, tr: Transform2D) -> "PunchDatums2D":
        wt = tr.apply(self.working_tip)
        # Transform direction vector without translation
        dx = tr.r00 * self.axis_direction[0] + tr.r01 * self.axis_direction[1]
        dy = tr.r10 * self.axis_direction[0] + tr.r11 * self.axis_direction[1]
        norm = math.hypot(dx, dy) or 1.0
        m0 = tr.apply(self.mounting_base[0])
        m1 = tr.apply(self.mounting_base[1])
        return PunchDatums2D(
            working_tip=wt,
            axis_direction=(dx / norm, dy / norm),
            mounting_base=(m0, m1),
            side_bounds=self.side_bounds,
            tip_radius_mm=self.tip_radius_mm,
            included_angle_deg=self.included_angle_deg,
            source=self.source,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "working_tip": [self.working_tip[0], self.working_tip[1]],
            "axis_direction": [self.axis_direction[0], self.axis_direction[1]],
            "mounting_base": [
                [self.mounting_base[0][0], self.mounting_base[0][1]],
                [self.mounting_base[1][0], self.mounting_base[1][1]],
            ],
            "side_bounds": [self.side_bounds[0], self.side_bounds[1]],
            "tip_radius_mm": self.tip_radius_mm,
            "included_angle_deg": self.included_angle_deg,
            "source": self.source,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "PunchDatums2D":
        mb = data["mounting_base"]
        sb = data["side_bounds"]
        tr = data.get("tip_radius_mm", NOT_SPECIFIED)
        ang = data.get("included_angle_deg", NOT_SPECIFIED)
        return PunchDatums2D(
            working_tip=(float(data["working_tip"][0]), float(data["working_tip"][1])),
            axis_direction=(float(data["axis_direction"][0]), float(data["axis_direction"][1])),
            mounting_base=(
                (float(mb[0][0]), float(mb[0][1])),
                (float(mb[1][0]), float(mb[1][1])),
            ),
            side_bounds=(float(sb[0]), float(sb[1])),
            tip_radius_mm=float(tr) if isinstance(tr, (int, float)) else NOT_SPECIFIED,
            included_angle_deg=float(ang) if isinstance(ang, (int, float)) else NOT_SPECIFIED,
            source=str(data.get("source", "geometry")),
        )


@dataclass(frozen=True)
class DieDatums2D:
    """Geometric datums for a DIE tool in tool coordinates."""
    left_shoulder: Point2D
    right_shoulder: Point2D
    v_groove_center: Point2D
    top_datum_plane_y: float
    mounting_datum: Tuple[Point2D, Point2D]
    axis_direction: Point2D = (0.0, 1.0)
    v_opening_mm: Union[float, str] = NOT_SPECIFIED
    die_angle_deg: Union[float, str] = NOT_SPECIFIED
    shoulder_radius_mm: Union[float, str] = NOT_SPECIFIED
    v_depth_mm: Union[float, str] = NOT_SPECIFIED
    source: str = "geometry"  # "geometry" | "manual" | "sidecar"

    def transformed(self, tr: Transform2D) -> "DieDatums2D":
        ls = tr.apply(self.left_shoulder)
        rs = tr.apply(self.right_shoulder)
        vc = tr.apply(self.v_groove_center)
        m0 = tr.apply(self.mounting_datum[0])
        m1 = tr.apply(self.mounting_datum[1])
        dx = tr.r00 * self.axis_direction[0] + tr.r01 * self.axis_direction[1]
        dy = tr.r10 * self.axis_direction[0] + tr.r11 * self.axis_direction[1]
        norm = math.hypot(dx, dy) or 1.0
        top_pt = tr.apply((0.0, self.top_datum_plane_y))
        return DieDatums2D(
            left_shoulder=ls,
            right_shoulder=rs,
            v_groove_center=vc,
            top_datum_plane_y=top_pt[1],
            mounting_datum=(m0, m1),
            axis_direction=(dx / norm, dy / norm),
            v_opening_mm=self.v_opening_mm,
            die_angle_deg=self.die_angle_deg,
            shoulder_radius_mm=self.shoulder_radius_mm,
            v_depth_mm=self.v_depth_mm,
            source=self.source,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "left_shoulder": [self.left_shoulder[0], self.left_shoulder[1]],
            "right_shoulder": [self.right_shoulder[0], self.right_shoulder[1]],
            "v_groove_center": [self.v_groove_center[0], self.v_groove_center[1]],
            "top_datum_plane_y": self.top_datum_plane_y,
            "mounting_datum": [
                [self.mounting_datum[0][0], self.mounting_datum[0][1]],
                [self.mounting_datum[1][0], self.mounting_datum[1][1]],
            ],
            "axis_direction": [self.axis_direction[0], self.axis_direction[1]],
            "v_opening_mm": self.v_opening_mm,
            "die_angle_deg": self.die_angle_deg,
            "shoulder_radius_mm": self.shoulder_radius_mm,
            "v_depth_mm": self.v_depth_mm,
            "source": self.source,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "DieDatums2D":
        md = data["mounting_datum"]
        ad = data.get("axis_direction", [0.0, 1.0])
        vo = data.get("v_opening_mm", NOT_SPECIFIED)
        da = data.get("die_angle_deg", NOT_SPECIFIED)
        sr = data.get("shoulder_radius_mm", NOT_SPECIFIED)
        vd = data.get("v_depth_mm", NOT_SPECIFIED)
        return DieDatums2D(
            left_shoulder=(float(data["left_shoulder"][0]), float(data["left_shoulder"][1])),
            right_shoulder=(float(data["right_shoulder"][0]), float(data["right_shoulder"][1])),
            v_groove_center=(float(data["v_groove_center"][0]), float(data["v_groove_center"][1])),
            top_datum_plane_y=float(data["top_datum_plane_y"]),
            mounting_datum=(
                (float(md[0][0]), float(md[0][1])),
                (float(md[1][0]), float(md[1][1])),
            ),
            axis_direction=(float(ad[0]), float(ad[1])),
            v_opening_mm=float(vo) if isinstance(vo, (int, float)) else NOT_SPECIFIED,
            die_angle_deg=float(da) if isinstance(da, (int, float)) else NOT_SPECIFIED,
            shoulder_radius_mm=float(sr) if isinstance(sr, (int, float)) else NOT_SPECIFIED,
            v_depth_mm=float(vd) if isinstance(vd, (int, float)) else NOT_SPECIFIED,
            source=str(data.get("source", "geometry")),
        )


def _extract_punch_datums_from_geometry(
    profile: ProfileGeometry2D,
) -> Tuple[Optional[PunchDatums2D], List[str]]:
    """Recognize punch working tip, tip radius, mounting base, and side bounds from exact segments.

    Does NOT rely merely on BoundingBox: requires an identifiable top horizontal mounting line
    and a bottom tip (either a convex tip arc or a unique sharp V-tip vertex where two angled
    flank lines meet). If ambiguous, returns `(None, reasons)` so the tool is marked
    `NEEDS_ALIGNMENT` until manual datums are provided.
    """
    notes: List[str] = []
    segs = profile.outer_contour.segments
    bbox = profile.bbox()
    min_x, min_y, max_x, max_y = bbox
    height = max_y - min_y
    if height <= 1e-3 or (max_x - min_x) <= 1e-3:
        return None, ["Zero or negative punch dimensions"]

    # 1. Identify top horizontal mounting line(s) near max_y
    horiz_top_lines: List[LineSegment2D] = []
    for s in segs:
        if isinstance(s, LineSegment2D):
            dy = abs(s.end[1] - s.start[1])
            dx = abs(s.end[0] - s.start[0])
            if dy <= 1e-2 and dx >= 1.0 and abs(0.5 * (s.start[1] + s.end[1]) - max_y) <= 0.05 * height:
                horiz_top_lines.append(s)

    if not horiz_top_lines:
        return None, ["Cannot unambiguously identify horizontal top mounting base on punch"]

    best_mount = max(horiz_top_lines, key=lambda l: l.length)
    m_left = best_mount.start if best_mount.start[0] <= best_mount.end[0] else best_mount.end
    m_right = best_mount.end if best_mount.start[0] <= best_mount.end[0] else best_mount.start

    # 2. Identify working tip near min_y from exact ARC or converging flank LINEs
    bottom_arcs: List[ArcSegment2D] = []
    for s in segs:
        if isinstance(s, ArcSegment2D):
            sb = s.bbox()
            if abs(sb[1] - min_y) <= max(0.5, 0.02 * height) and s.radius <= 0.4 * (max_x - min_x):
                bottom_arcs.append(s)

    tip_pt: Optional[Point2D] = None
    tip_radius: Union[float, str] = NOT_SPECIFIED

    if len(bottom_arcs) == 1:
        arc = bottom_arcs[0]
        tip_radius = round(float(arc.radius), 6)
        # Bottom-most point of the tip arc (either 270 deg on circle or arc midpoint)
        cand_bottom = (arc.center[0], arc.center[1] - arc.radius)
        sb = arc.bbox()
        if sb[0] - 1e-4 <= cand_bottom[0] <= sb[2] + 1e-4:
            tip_pt = (cand_bottom[0], min(sb[1], cand_bottom[1]))
        else:
            tip_pt = arc.midpoint()
    elif len(bottom_arcs) > 1:
        # Check if there is a tiny flat bottom line between two symmetric corner arcs (flat-coining punch)
        bottom_lines = [
            s for s in segs
            if isinstance(s, LineSegment2D)
            and abs(0.5 * (s.start[1] + s.end[1]) - min_y) <= 1e-2
            and abs(s.end[1] - s.start[1]) <= 1e-3
        ]
        if len(bottom_lines) == 1 and bottom_lines[0].length <= 2.0:
            bl = bottom_lines[0]
            tip_pt = (0.5 * (bl.start[0] + bl.end[0]), 0.5 * (bl.start[1] + bl.end[1]))
            radii = sorted({round(a.radius, 4) for a in bottom_arcs})
            if len(radii) == 1:
                tip_radius = radii[0]
        else:
            return None, ["Ambiguous punch tip: multiple bottom arcs detected"]
    else:
        # Check for two converging flank lines meeting at a single bottom vertex
        bottom_verts: List[Point2D] = []
        for s in segs:
            for p in (s.start, s.end):
                if abs(p[1] - min_y) <= 1e-3:
                    if not any(_dist(p, q) <= 1e-3 for q in bottom_verts):
                        bottom_verts.append(p)
        if len(bottom_verts) == 1:
            tip_pt = bottom_verts[0]
            tip_radius = NOT_SPECIFIED
        else:
            return None, ["Ambiguous punch tip: flat or multi-point bottom without tip arc"]

    # 3. Check for converging punch flanks near the tip to estimate included angle if unambiguous
    included_angle: Union[float, str] = NOT_SPECIFIED
    flank_lines: List[LineSegment2D] = []
    for s in segs:
        if isinstance(s, LineSegment2D) and s.length >= 2.0:
            sb = s.bbox()
            if sb[1] <= min_y + 0.15 * height:
                dx = abs(s.end[0] - s.start[0])
                dy = abs(s.end[1] - s.start[1])
                if dy > 1e-3 and dx > 1e-3:
                    flank_lines.append(s)

    if len(flank_lines) >= 2 and tip_pt is not None:
        left_flanks = [l for l in flank_lines if 0.5 * (l.start[0] + l.end[0]) < tip_pt[0]]
        right_flanks = [l for l in flank_lines if 0.5 * (l.start[0] + l.end[0]) > tip_pt[0]]
        if left_flanks and right_flanks:
            lf = min(left_flanks, key=lambda l: l.bbox()[1])
            rf = min(right_flanks, key=lambda l: l.bbox()[1])
            ang_l = math.degrees(math.atan2(abs(lf.end[0] - lf.start[0]), abs(lf.end[1] - lf.start[1])))
            ang_r = math.degrees(math.atan2(abs(rf.end[0] - rf.start[0]), abs(rf.end[1] - rf.start[1])))
            if abs(ang_l - ang_r) <= 1.0:
                included_angle = round(ang_l + ang_r, 4)

    return (
        PunchDatums2D(
            working_tip=tip_pt,
            axis_direction=(0.0, -1.0),
            mounting_base=(m_left, m_right),
            side_bounds=(min_x, max_x),
            tip_radius_mm=tip_radius,
            included_angle_deg=included_angle,
            source="geometry",
        ),
        notes,
    )


def _line_intersection_x_at_y(seg: LineSegment2D, y_val: float) -> Optional[float]:
    x0, y0 = seg.start
    x1, y1 = seg.end
    dy = y1 - y0
    if abs(dy) <= 1e-9:
        return None
    t = (y_val - y0) / dy
    return x0 + t * (x1 - x0)


def _extract_die_datums_from_geometry(
    profile: ProfileGeometry2D,
) -> Tuple[Optional[DieDatums2D], List[str]]:
    """Recognize die shoulders, V-groove center, top datum plane, mounting base,
    V-opening, die angle, and shoulder radius from exact 2D segments.

    Does NOT rely solely on BoundingBox: inspects the top horizontal shoulder
    flats, shoulder fillet arcs, inclined V-wall lines, and bottom mounting base.
    """
    notes: List[str] = []
    segs = profile.outer_contour.segments
    bbox = profile.bbox()
    min_x, min_y, max_x, max_y = bbox
    height = max_y - min_y
    width = max_x - min_x
    if height <= 1e-3 or width <= 1e-3:
        return None, ["Zero or negative die dimensions"]

    # 1. Bottom horizontal mounting base near min_y
    bottom_lines = [
        s for s in segs
        if isinstance(s, LineSegment2D)
        and abs(s.end[1] - s.start[1]) <= 1e-2
        and abs(0.5 * (s.start[1] + s.end[1]) - min_y) <= 0.05 * height
        and s.length >= 2.0
    ]
    if not bottom_lines:
        return None, ["Cannot unambiguously identify bottom mounting base on die"]
    best_mount = max(bottom_lines, key=lambda l: l.length)
    m_left = best_mount.start if best_mount.start[0] <= best_mount.end[0] else best_mount.end
    m_right = best_mount.end if best_mount.start[0] <= best_mount.end[0] else best_mount.start

    # 2. Top horizontal shoulder flats near max_y (must have at least 2 flats separated by the V-groove)
    top_flats = [
        s for s in segs
        if isinstance(s, LineSegment2D)
        and abs(s.end[1] - s.start[1]) <= 1e-2
        and abs(0.5 * (s.start[1] + s.end[1]) - max_y) <= 0.05 * height
        and s.length >= 1.0
    ]
    if len(top_flats) < 2:
        return None, ["Cannot identify left and right top shoulder flats on die (no V-groove opening found)"]

    top_flats.sort(key=lambda l: 0.5 * (l.start[0] + l.end[0]))
    left_flat = top_flats[0]
    right_flat = top_flats[-1]
    top_y = 0.5 * (
        0.5 * (left_flat.start[1] + left_flat.end[1])
        + 0.5 * (right_flat.start[1] + right_flat.end[1])
    )
    # Inner endpoints of the left and right top shoulder flats facing the V-groove
    left_inner_x = max(left_flat.start[0], left_flat.end[0])
    right_inner_x = min(right_flat.start[0], right_flat.end[0])
    if right_inner_x <= left_inner_x + 1e-3:
        return None, ["Invalid die shoulder geometry: right shoulder is not to the right of left shoulder"]

    # 3. Inspect segments inside the V-groove region (between left_inner_x and right_inner_x, excluding bottom mounting base)
    groove_segs = [
        s for s in segs
        if s is not best_mount
        and left_inner_x - 1.0 <= 0.5 * (s.bbox()[0] + s.bbox()[2]) <= right_inner_x + 1.0
        and s.bbox()[1] < top_y - 1e-3
        and s.bbox()[3] > min_y + 0.05 * height
    ]
    if not groove_segs:
        return None, ["No V-groove contour found between top shoulders"]

    # Find lowest point in the V-groove
    groove_min_y = min(s.bbox()[1] for s in groove_segs)
    v_depth = round(top_y - groove_min_y, 6)

    # Identify inclined V-walls
    inclined_walls: List[LineSegment2D] = []
    for s in groove_segs:
        if isinstance(s, LineSegment2D) and s.length >= 1.5:
            dx = abs(s.end[0] - s.start[0])
            dy = abs(s.end[1] - s.start[1])
            if dx > 0.2 and dy > 0.5:
                inclined_walls.append(s)

    if len(inclined_walls) < 2:
        return None, ["Cannot identify two inclined V-groove walls in die profile"]

    inclined_walls.sort(key=lambda l: 0.5 * (l.start[0] + l.end[0]))
    left_wall = inclined_walls[0]
    right_wall = inclined_walls[-1]

    # Compute included die angle from the two inclined V-walls
    ang_l = math.degrees(math.atan2(abs(left_wall.end[0] - left_wall.start[0]), abs(left_wall.end[1] - left_wall.start[1])))
    ang_r = math.degrees(math.atan2(abs(right_wall.end[0] - right_wall.start[0]), abs(right_wall.end[1] - right_wall.start[1])))
    die_angle: Union[float, str] = NOT_SPECIFIED
    if abs(ang_l - ang_r) <= 1.0:
        die_angle = round(ang_l + ang_r, 4)

    # Identify shoulder fillet arcs near top_y
    shoulder_arcs: List[ArcSegment2D] = []
    for s in groove_segs:
        if isinstance(s, ArcSegment2D):
            sb = s.bbox()
            if abs(sb[3] - top_y) <= max(1.0, 0.05 * height):
                shoulder_arcs.append(s)

    shoulder_radius: Union[float, str] = NOT_SPECIFIED
    if len(shoulder_arcs) >= 2:
        shoulder_arcs.sort(key=lambda a: a.center[0])
        r_left = shoulder_arcs[0].radius
        r_right = shoulder_arcs[-1].radius
        if abs(r_left - r_right) <= 1e-2:
            shoulder_radius = round(0.5 * (r_left + r_right), 6)

    # Intersection of extrapolated V-wall lines with top_y plane (standard V-opening definition)
    x_left_top = _line_intersection_x_at_y(left_wall, top_y)
    x_right_top = _line_intersection_x_at_y(right_wall, top_y)
    if x_left_top is not None and x_right_top is not None and x_right_top > x_left_top:
        left_shoulder_pt = (round(x_left_top, 6), round(top_y, 6))
        right_shoulder_pt = (round(x_right_top, 6), round(top_y, 6))
        v_opening: Union[float, str] = round(x_right_top - x_left_top, 6)
        v_center_x = 0.5 * (x_left_top + x_right_top)
    else:
        left_shoulder_pt = (round(left_inner_x, 6), round(top_y, 6))
        right_shoulder_pt = (round(right_inner_x, 6), round(top_y, 6))
        v_opening = round(right_inner_x - left_inner_x, 6)
        v_center_x = 0.5 * (left_inner_x + right_inner_x)

    v_center_pt = (round(v_center_x, 6), round(top_y, 6))

    return (
        DieDatums2D(
            left_shoulder=left_shoulder_pt,
            right_shoulder=right_shoulder_pt,
            v_groove_center=v_center_pt,
            top_datum_plane_y=round(top_y, 6),
            mounting_datum=(m_left, m_right),
            axis_direction=(0.0, 1.0),
            v_opening_mm=v_opening,
            die_angle_deg=die_angle,
            shoulder_radius_mm=shoulder_radius,
            v_depth_mm=v_depth,
            source="geometry",
        ),
        notes,
    )


@dataclass(frozen=True)
class ToolPerformanceMetrics:
    """Measured timings and resource usage for tool import, sectioning, and drawing."""
    import_time_ms: float = 0.0
    section_time_ms: float = 0.0
    validation_time_ms: float = 0.0
    last_draw_time_ms: float = 0.0
    memory_bytes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "import_time_ms": round(self.import_time_ms, 3),
            "section_time_ms": round(self.section_time_ms, 3),
            "validation_time_ms": round(self.validation_time_ms, 3),
            "last_draw_time_ms": round(self.last_draw_time_ms, 3),
            "memory_bytes": int(self.memory_bytes),
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "ToolPerformanceMetrics":
        return ToolPerformanceMetrics(
            import_time_ms=float(data.get("import_time_ms", 0.0)),
            section_time_ms=float(data.get("section_time_ms", 0.0)),
            validation_time_ms=float(data.get("validation_time_ms", 0.0)),
            last_draw_time_ms=float(data.get("last_draw_time_ms", 0.0)),
            memory_bytes=int(data.get("memory_bytes", 0)),
        )


@dataclass
class ToolProfile:
    """Unified custom tool profile contract for PressBrakeSim 2.5D (Phase 5A).

    Required fields:
    - `tool_id`
    - `name`
    - `kind`: `ToolKind.PUNCH` or `ToolKind.DIE`
    - `source_format`: `"DXF"`, `"STEP"`, `"FREECAD_SHAPE"`, `"FCSTD"`
    - `source_file`
    - `units`
    - `profile_geometry`: `ProfileGeometry2D` (in `source` coordinates)
    - `length_mm`: float or `"NOT_SPECIFIED"`
    - `origin`: `(x, y)` origin of tool local frame in source coordinates
    - `orientation`: rotation angle in degrees from source frame to tool local frame
    - `mounting_datum`: dict or `"NOT_SPECIFIED"`
    - `working_datum`: dict or `"NOT_SPECIFIED"`
    - `bbox`: `(min_x, min_y, max_x, max_y)` in tool local coordinates (mm)
    - `validation_status`: `ValidationStatus`

    Optional parameters (never fabricated; default to `"NOT_SPECIFIED"`):
    - `tip_radius_mm` (for punch)
    - `v_opening_mm` (for die)
    - `die_angle_deg`
    - `shoulder_radius_mm`
    - `height_mm`
    - `manufacturer`
    - `model`
    - `material`
    - `maximum_load`
    """
    tool_id: str
    name: str
    kind: ToolKind
    source_format: str
    source_file: str
    units: str
    profile_geometry: ProfileGeometry2D
    length_mm: Union[float, str] = NOT_SPECIFIED
    origin: Point2D = (0.0, 0.0)
    orientation: float = 0.0
    mounting_datum: Union[Dict[str, Any], str] = NOT_SPECIFIED
    working_datum: Union[Dict[str, Any], str] = NOT_SPECIFIED
    bbox: BBox2D = (0.0, 0.0, 0.0, 0.0)
    validation_status: ValidationStatus = ValidationStatus.IMPORTED

    # Optional geometry/catalog parameters (strictly NOT_SPECIFIED unless verified)
    tip_radius_mm: Union[float, str] = NOT_SPECIFIED
    v_opening_mm: Union[float, str] = NOT_SPECIFIED
    die_angle_deg: Union[float, str] = NOT_SPECIFIED
    shoulder_radius_mm: Union[float, str] = NOT_SPECIFIED
    height_mm: Union[float, str] = NOT_SPECIFIED
    manufacturer: str = NOT_SPECIFIED
    model: str = NOT_SPECIFIED
    material: str = NOT_SPECIFIED
    maximum_load: Union[float, str] = NOT_SPECIFIED

    # Extended datums, limitations, and diagnostics
    punch_datums: Optional[PunchDatums2D] = None
    die_datums: Optional[DieDatums2D] = None
    unit_scale_to_mm: float = 1.0
    has_variable_section_25d_limitation: bool = False
    limitations_25d: Tuple[str, ...] = ()
    validation_messages: Tuple[str, ...] = ()
    metrics: ToolPerformanceMetrics = field(default_factory=ToolPerformanceMetrics)
    brep_filename: Optional[str] = None

    # Prepared local geometry & polygon cache (invalidated when origin/orientation/datums change)
    _cached_local_geometry: Optional[ProfileGeometry2D] = field(default=None, init=False, repr=False, compare=False)
    _cached_local_polygons: Optional[Tuple[Tuple[Point2D, ...], Tuple[Tuple[Point2D, ...], ...]]] = field(
        default=None, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if isinstance(self.kind, str):
            self.kind = ToolKind(self.kind.upper())
        if isinstance(self.validation_status, str):
            self.validation_status = ValidationStatus(self.validation_status.upper())
        self.origin = (float(self.origin[0]), float(self.origin[1]))
        self.orientation = float(self.orientation)
        self.limitations_25d = tuple(self.limitations_25d)
        self.validation_messages = tuple(self.validation_messages)

    def invalidate_cache(self) -> None:
        self._cached_local_geometry = None
        self._cached_local_polygons = None

    def source_to_local_transform(self) -> Transform2D:
        """Explicit transform from Source coordinates (scaled to mm) to Tool Local coordinates.

        Translates `self.origin` to `(0, 0)` and rotates by `self.orientation` degrees.
        Thus in Tool Local coordinates, the working datum (`working_tip` for PUNCH or
        `v_groove_center` for DIE) lies at `(0.0, 0.0)`.
        """
        translate = Transform2D(1.0, 0.0, -self.origin[0], 0.0, 1.0, -self.origin[1])
        if abs(self.orientation) <= 1e-12:
            return translate
        rot = Transform2D.rotation(math.radians(self.orientation), (0.0, 0.0))
        return translate.then(rot)

    @property
    def local_geometry(self) -> ProfileGeometry2D:
        """Return exact 2D profile geometry in Tool Local coordinates (mm), cached."""
        if self._cached_local_geometry is None:
            scaled = (
                self.profile_geometry
                if abs(self.unit_scale_to_mm - 1.0) <= 1e-12
                else self.profile_geometry.scaled(self.unit_scale_to_mm)
            )
            self._cached_local_geometry = scaled.transformed(self.source_to_local_transform())
        return self._cached_local_geometry

    def local_polygons(self, arc_segments: int = 20) -> Tuple[Tuple[Point2D, ...], Tuple[Tuple[Point2D, ...], ...]]:
        """Return sampled `(outer_polygon, hole_polygons)` in Tool Local coordinates (mm), cached."""
        if self._cached_local_polygons is None:
            geom = self.local_geometry
            outer_pts = geom.outer_contour.sample_polygon(arc_segments=arc_segments)
            hole_pts = tuple(h.sample_polygon(arc_segments=arc_segments) for h in geom.holes)
            self._cached_local_polygons = (outer_pts, hole_pts)
        return self._cached_local_polygons

    def assign_punch_datums(
        self,
        working_tip: Point2D,
        mounting_base: Tuple[Point2D, Point2D],
        axis_direction: Point2D = (0.0, -1.0),
        tip_radius_mm: Union[float, str] = NOT_SPECIFIED,
    ) -> None:
        """Manually assign or override PUNCH datums in source (mm) coordinates and re-validate."""
        if self.kind != ToolKind.PUNCH:
            raise ToolingError("Cannot assign punch datums to a DIE tool")
        scaled = (
            self.profile_geometry
            if abs(self.unit_scale_to_mm - 1.0) <= 1e-12
            else self.profile_geometry.scaled(self.unit_scale_to_mm)
        )
        sb = scaled.bbox()
        norm = math.hypot(axis_direction[0], axis_direction[1])
        if norm <= 1e-9:
            raise ToolingError("Punch axis_direction cannot be zero")
        unit_axis = (axis_direction[0] / norm, axis_direction[1] / norm)
        # Rotate so that unit_axis aligns with (0, -1) in tool local coordinates
        target_angle = math.degrees(math.atan2(-1.0, 0.0) - math.atan2(unit_axis[1], unit_axis[0]))
        self.origin = (float(working_tip[0]), float(working_tip[1]))
        self.orientation = target_angle
        self.punch_datums = PunchDatums2D(
            working_tip=(float(working_tip[0]), float(working_tip[1])),
            axis_direction=unit_axis,
            mounting_base=mounting_base,
            side_bounds=(sb[0], sb[2]),
            tip_radius_mm=tip_radius_mm,
            included_angle_deg=self.punch_datums.included_angle_deg if self.punch_datums else NOT_SPECIFIED,
            source="manual",
        )
        if isinstance(tip_radius_mm, (int, float)):
            self.tip_radius_mm = float(tip_radius_mm)
        self.invalidate_cache()
        validate_tool_profile(self)

    def assign_die_datums(
        self,
        left_shoulder: Point2D,
        right_shoulder: Point2D,
        v_groove_center: Point2D,
        mounting_datum: Tuple[Point2D, Point2D],
        top_datum_plane_y: Optional[float] = None,
        v_opening_mm: Union[float, str] = NOT_SPECIFIED,
        die_angle_deg: Union[float, str] = NOT_SPECIFIED,
        shoulder_radius_mm: Union[float, str] = NOT_SPECIFIED,
    ) -> None:
        """Manually assign or override DIE datums in source (mm) coordinates and re-validate."""
        if self.kind != ToolKind.DIE:
            raise ToolingError("Cannot assign die datums to a PUNCH tool")
        top_y = float(top_datum_plane_y) if top_datum_plane_y is not None else 0.5 * (left_shoulder[1] + right_shoulder[1])
        self.origin = (float(v_groove_center[0]), float(v_groove_center[1]))
        self.orientation = 0.0
        if v_opening_mm == NOT_SPECIFIED:
            v_opening_mm = round(abs(right_shoulder[0] - left_shoulder[0]), 6)
        self.die_datums = DieDatums2D(
            left_shoulder=left_shoulder,
            right_shoulder=right_shoulder,
            v_groove_center=v_groove_center,
            top_datum_plane_y=top_y,
            mounting_datum=mounting_datum,
            axis_direction=(0.0, 1.0),
            v_opening_mm=v_opening_mm,
            die_angle_deg=die_angle_deg,
            shoulder_radius_mm=shoulder_radius_mm,
            source="manual",
        )
        if isinstance(v_opening_mm, (int, float)):
            self.v_opening_mm = float(v_opening_mm)
        if isinstance(die_angle_deg, (int, float)):
            self.die_angle_deg = float(die_angle_deg)
        if isinstance(shoulder_radius_mm, (int, float)):
            self.shoulder_radius_mm = float(shoulder_radius_mm)
        self.invalidate_cache()
        validate_tool_profile(self)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "tool_id": self.tool_id,
            "name": self.name,
            "kind": self.kind.value,
            "source_format": self.source_format,
            "source_file": self.source_file,
            "units": self.units,
            "unit_scale_to_mm": self.unit_scale_to_mm,
            "profile_geometry": self.profile_geometry.to_dict(),
            "length_mm": self.length_mm,
            "origin": [self.origin[0], self.origin[1]],
            "orientation": self.orientation,
            "mounting_datum": self.mounting_datum,
            "working_datum": self.working_datum,
            "bbox": [self.bbox[0], self.bbox[1], self.bbox[2], self.bbox[3]],
            "validation_status": self.validation_status.value,
            "tip_radius_mm": self.tip_radius_mm,
            "v_opening_mm": self.v_opening_mm,
            "die_angle_deg": self.die_angle_deg,
            "shoulder_radius_mm": self.shoulder_radius_mm,
            "height_mm": self.height_mm,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "material": self.material,
            "maximum_load": self.maximum_load,
            "punch_datums": self.punch_datums.to_dict() if self.punch_datums else None,
            "die_datums": self.die_datums.to_dict() if self.die_datums else None,
            "has_variable_section_25d_limitation": self.has_variable_section_25d_limitation,
            "limitations_25d": list(self.limitations_25d),
            "validation_messages": list(self.validation_messages),
            "metrics": self.metrics.to_dict(),
            "brep_filename": self.brep_filename,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "ToolProfile":
        ver = data.get("schema_version")
        if ver != SCHEMA_VERSION:
            raise ToolingError(f"Unsupported ToolProfile schema_version {ver!r} (expected {SCHEMA_VERSION!r})")
        pd = data.get("punch_datums")
        dd = data.get("die_datums")
        bb = data["bbox"]
        lm = data.get("length_mm", NOT_SPECIFIED)
        tr = data.get("tip_radius_mm", NOT_SPECIFIED)
        vo = data.get("v_opening_mm", NOT_SPECIFIED)
        da = data.get("die_angle_deg", NOT_SPECIFIED)
        sr = data.get("shoulder_radius_mm", NOT_SPECIFIED)
        hm = data.get("height_mm", NOT_SPECIFIED)
        ml = data.get("maximum_load", NOT_SPECIFIED)

        profile = ToolProfile(
            tool_id=str(data["tool_id"]),
            name=str(data["name"]),
            kind=ToolKind(str(data["kind"]).upper()),
            source_format=str(data["source_format"]),
            source_file=str(data["source_file"]),
            units=str(data["units"]),
            unit_scale_to_mm=float(data.get("unit_scale_to_mm", 1.0)),
            profile_geometry=ProfileGeometry2D.from_dict(data["profile_geometry"]),
            length_mm=float(lm) if isinstance(lm, (int, float)) else NOT_SPECIFIED,
            origin=(float(data["origin"][0]), float(data["origin"][1])),
            orientation=float(data.get("orientation", 0.0)),
            mounting_datum=data.get("mounting_datum", NOT_SPECIFIED),
            working_datum=data.get("working_datum", NOT_SPECIFIED),
            bbox=(float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])),
            validation_status=ValidationStatus(str(data["validation_status"]).upper()),
            tip_radius_mm=float(tr) if isinstance(tr, (int, float)) else NOT_SPECIFIED,
            v_opening_mm=float(vo) if isinstance(vo, (int, float)) else NOT_SPECIFIED,
            die_angle_deg=float(da) if isinstance(da, (int, float)) else NOT_SPECIFIED,
            shoulder_radius_mm=float(sr) if isinstance(sr, (int, float)) else NOT_SPECIFIED,
            height_mm=float(hm) if isinstance(hm, (int, float)) else NOT_SPECIFIED,
            manufacturer=str(data.get("manufacturer", NOT_SPECIFIED)),
            model=str(data.get("model", NOT_SPECIFIED)),
            material=str(data.get("material", NOT_SPECIFIED)),
            maximum_load=float(ml) if isinstance(ml, (int, float)) else NOT_SPECIFIED,
            punch_datums=PunchDatums2D.from_dict(pd) if isinstance(pd, dict) else None,
            die_datums=DieDatums2D.from_dict(dd) if isinstance(dd, dict) else None,
            has_variable_section_25d_limitation=bool(data.get("has_variable_section_25d_limitation", False)),
            limitations_25d=tuple(str(x) for x in data.get("limitations_25d", ())),
            validation_messages=tuple(str(x) for x in data.get("validation_messages", ())),
            metrics=ToolPerformanceMetrics.from_dict(data.get("metrics", {})),
            brep_filename=data.get("brep_filename"),
        )
        return profile


def validate_tool_profile(tool: ToolProfile) -> ValidationStatus:
    """Validate imported `ToolProfile` geometry, units, closure, self-intersections, and datums.

    Updates `tool.validation_status`, `tool.validation_messages`, `tool.bbox`,
    `tool.height_mm`, `tool.mounting_datum`, and `tool.working_datum` in place.
    """
    t0 = time.perf_counter()
    msgs: List[str] = []

    # 1. Validate units & scale
    unit_key = tool.units.strip().lower()
    if unit_key not in UNIT_SCALE_TO_MM or not math.isfinite(tool.unit_scale_to_mm) or tool.unit_scale_to_mm <= 0.0:
        tool.validation_status = ValidationStatus.INVALID
        tool.validation_messages = (f"Invalid or unsupported units/scale: units={tool.units!r}, scale={tool.unit_scale_to_mm}",)
        return tool.validation_status

    if isinstance(tool.length_mm, (int, float)) and tool.length_mm <= 0.0:
        tool.validation_status = ValidationStatus.INVALID
        tool.validation_messages = (f"Tool length_mm must be positive when specified, got {tool.length_mm}",)
        return tool.validation_status

    # 2. Validate outer contour & holes
    outer = tool.profile_geometry.outer_contour
    if not outer.segments:
        tool.validation_status = ValidationStatus.INVALID
        tool.validation_messages = ("Profile outer contour has no segments",)
        return tool.validation_status

    for seg in tool.profile_geometry.all_segments:
        if seg.length * tool.unit_scale_to_mm <= 1e-5:
            tool.validation_status = ValidationStatus.INVALID
            tool.validation_messages = ("Degenerate zero-length segment in tool profile",)
            return tool.validation_status

    if not outer.is_closed(tol=1e-2 / tool.unit_scale_to_mm):
        tool.validation_status = ValidationStatus.INVALID
        tool.validation_messages = ("Outer contour is not closed",)
        return tool.validation_status

    if has_self_intersection(outer):
        tool.validation_status = ValidationStatus.INVALID
        tool.validation_messages = ("Outer contour has self-intersections",)
        return tool.validation_status

    for idx, hole in enumerate(tool.profile_geometry.holes):
        if not hole.is_closed(tol=1e-2 / tool.unit_scale_to_mm):
            tool.validation_status = ValidationStatus.INVALID
            tool.validation_messages = (f"Inner hole contour #{idx + 1} is not closed",)
            return tool.validation_status
        if has_self_intersection(hole):
            tool.validation_status = ValidationStatus.INVALID
            tool.validation_messages = (f"Inner hole contour #{idx + 1} has self-intersections",)
            return tool.validation_status

    # 3. Scale check (reject implausible microscopic or kilometer-scale profiles)
    scaled_geom = (
        tool.profile_geometry
        if abs(tool.unit_scale_to_mm - 1.0) <= 1e-12
        else tool.profile_geometry.scaled(tool.unit_scale_to_mm)
    )
    sb = scaled_geom.bbox()
    w_mm = sb[2] - sb[0]
    h_mm = sb[3] - sb[1]
    if w_mm < 1.0 or h_mm < 1.0 or w_mm > 5000.0 or h_mm > 5000.0:
        tool.validation_status = ValidationStatus.INVALID
        tool.validation_messages = (
            f"Implausible tool cross-section dimensions ({w_mm:.3f} mm x {h_mm:.3f} mm); check units/scale",
        )
        return tool.validation_status

    tool.height_mm = round(h_mm, 6)

    # 4. Datum presence & alignment
    if tool.kind == ToolKind.PUNCH:
        if tool.punch_datums is None:
            datums, notes = _extract_punch_datums_from_geometry(scaled_geom)
            msgs.extend(notes)
            tool.punch_datums = datums
        if tool.punch_datums is None:
            tool.origin = (0.5 * (sb[0] + sb[2]), sb[1])
            tool.orientation = 0.0
            tool.mounting_datum = NOT_SPECIFIED
            tool.working_datum = NOT_SPECIFIED
            tool.invalidate_cache()
            tool.bbox = tool.local_geometry.bbox()
            tool.validation_status = ValidationStatus.NEEDS_ALIGNMENT
            tool.validation_messages = tuple(msgs or ["Punch datums require manual alignment"])
            return tool.validation_status

        pd = tool.punch_datums
        tool.origin = pd.working_tip
        if tool.tip_radius_mm == NOT_SPECIFIED and pd.tip_radius_mm != NOT_SPECIFIED:
            tool.tip_radius_mm = pd.tip_radius_mm
        tool.mounting_datum = {
            "role": "mounting_base",
            "line_source_mm": [list(pd.mounting_base[0]), list(pd.mounting_base[1])],
            "source": pd.source,
        }
        tool.working_datum = {
            "role": "working_tip",
            "point_source_mm": list(pd.working_tip),
            "axis_direction": list(pd.axis_direction),
            "side_bounds_mm": list(pd.side_bounds),
            "source": pd.source,
        }
    else:
        if tool.die_datums is None:
            datums, notes = _extract_die_datums_from_geometry(scaled_geom)
            msgs.extend(notes)
            tool.die_datums = datums
        if tool.die_datums is None:
            tool.origin = (0.5 * (sb[0] + sb[2]), sb[3])
            tool.orientation = 0.0
            tool.mounting_datum = NOT_SPECIFIED
            tool.working_datum = NOT_SPECIFIED
            tool.invalidate_cache()
            tool.bbox = tool.local_geometry.bbox()
            tool.validation_status = ValidationStatus.NEEDS_ALIGNMENT
            tool.validation_messages = tuple(msgs or ["Die datums require manual alignment"])
            return tool.validation_status

        dd = tool.die_datums
        tool.origin = dd.v_groove_center
        if tool.v_opening_mm == NOT_SPECIFIED and dd.v_opening_mm != NOT_SPECIFIED:
            tool.v_opening_mm = dd.v_opening_mm
        if tool.die_angle_deg == NOT_SPECIFIED and dd.die_angle_deg != NOT_SPECIFIED:
            tool.die_angle_deg = dd.die_angle_deg
        if tool.shoulder_radius_mm == NOT_SPECIFIED and dd.shoulder_radius_mm != NOT_SPECIFIED:
            tool.shoulder_radius_mm = dd.shoulder_radius_mm
        tool.mounting_datum = {
            "role": "mounting_datum",
            "line_source_mm": [list(dd.mounting_datum[0]), list(dd.mounting_datum[1])],
            "source": dd.source,
        }
        tool.working_datum = {
            "role": "v_groove_center",
            "point_source_mm": list(dd.v_groove_center),
            "left_shoulder_mm": list(dd.left_shoulder),
            "right_shoulder_mm": list(dd.right_shoulder),
            "top_datum_plane_y_mm": dd.top_datum_plane_y,
            "axis_direction": list(dd.axis_direction),
            "source": dd.source,
        }

    tool.invalidate_cache()
    lb = tool.local_geometry.bbox()
    tool.bbox = (round(lb[0], 6), round(lb[1], 6), round(lb[2], 6), round(lb[3], 6))
    tool.validation_status = ValidationStatus.GEOMETRY_VERIFIED
    if tool.has_variable_section_25d_limitation:
        msgs.append(
            "2.5D LIMITATION: 3D tool has variable cross-section along length; "
            "a single 2D profile is insufficient for full 3D collision verification."
        )
    msgs.append("Geometry and datums verified (GEOMETRY_VERIFIED; physical collision NOT CHECKED).")
    tool.validation_messages = tuple(msgs)
    dt_ms = (time.perf_counter() - t0) * 1000.0
    tool.metrics = replace(tool.metrics, validation_time_ms=dt_ms)
    return tool.validation_status


# ---------------------------------------------------------------------------
# DXF Importer (supports LINE, ARC, CIRCLE, LWPOLYLINE, POLYLINE + $INSUNITS)
# ---------------------------------------------------------------------------

def _parse_dxf_pairs(text: str) -> List[Tuple[int, str]]:
    lines = [ln.strip() for ln in text.splitlines()]
    if len(lines) < 4:
        raise ToolingError("Corrupt or empty DXF file")
    pairs: List[Tuple[int, str]] = []
    i = 0
    n = len(lines)
    while i + 1 < n:
        code_str = lines[i]
        val_str = lines[i + 1]
        if not code_str and i + 2 >= n:
            break
        try:
            code = int(code_str)
        except ValueError as exc:
            raise ToolingError(f"Malformed DXF group code at line {i + 1}: {code_str!r}") from exc
        pairs.append((code, val_str))
        i += 2
    if not any(c == 0 and v == "EOF" for c, v in pairs) and not any(c == 0 and v == "SECTION" for c, v in pairs):
        raise ToolingError("Invalid DXF structure: missing SECTION/EOF markers")
    return pairs


def _extract_dxf_units(pairs: Sequence[Tuple[int, str]], override_units: Optional[str]) -> Tuple[str, float]:
    if override_units is not None:
        uk = override_units.strip().lower()
        if uk not in UNIT_SCALE_TO_MM:
            raise ToolingError(f"Unsupported unit override: {override_units!r}")
        return uk, UNIT_SCALE_TO_MM[uk]

    in_header = False
    for idx, (code, val) in enumerate(pairs):
        if code == 2 and val == "HEADER":
            in_header = True
        elif code == 0 and val == "ENDSEC" and in_header:
            break
        elif in_header and code == 9 and val == "$INSUNITS":
            if idx + 1 < len(pairs) and pairs[idx + 1][0] == 70:
                try:
                    u_code = int(pairs[idx + 1][1])
                except ValueError as exc:
                    raise ToolingError(f"Invalid $INSUNITS code in DXF: {pairs[idx + 1][1]!r}") from exc
                if u_code not in DXF_INSUNITS_TO_MM:
                    raise ToolingError(f"Unsupported DXF $INSUNITS code: {u_code}")
                return DXF_INSUNITS_TO_MM[u_code]
    return ("mm", 1.0)


def _lwpolyline_to_segments(props: Sequence[Tuple[int, str]]) -> List[Segment2D]:
    flags = 0
    vertices: List[Tuple[float, float, float]] = []
    cur_x: Optional[float] = None
    cur_y: Optional[float] = None
    cur_bulge: float = 0.0

    for code, val in props:
        if code == 70:
            flags = int(val)
        elif code == 10:
            if cur_x is not None and cur_y is not None:
                vertices.append((cur_x, cur_y, cur_bulge))
            cur_x = float(val)
            cur_y = None
            cur_bulge = 0.0
        elif code == 20:
            cur_y = float(val)
        elif code == 42:
            cur_bulge = float(val)

    if cur_x is not None and cur_y is not None:
        vertices.append((cur_x, cur_y, cur_bulge))

    if len(vertices) < 2:
        raise ToolingError("LWPOLYLINE has fewer than 2 vertices")

    closed = bool(flags & 1)
    segs: List[Segment2D] = []
    count = len(vertices) if closed else len(vertices) - 1
    for i in range(count):
        x0, y0, bulge = vertices[i]
        x1, y1, _ = vertices[(i + 1) % len(vertices)]
        p0, p1 = (x0, y0), (x1, y1)
        if _dist(p0, p1) <= 1e-9:
            if i == count - 1 and closed:
                continue
            raise ToolingError("Degenerate zero-length segment in LWPOLYLINE")
        if abs(bulge) <= 1e-9:
            segs.append(LineSegment2D(p0, p1))
        else:
            segs.append(ArcSegment2D.from_bulge(p0, p1, bulge))
    return segs


def import_tool_from_dxf(
    file_path: Union[str, Path],
    kind: Union[ToolKind, str],
    name: Optional[str] = None,
    tool_id: Optional[str] = None,
    units_override: Optional[str] = None,
    length_mm: Union[float, str] = NOT_SPECIFIED,
) -> ToolProfile:
    """Import a 2D tool profile (`PUNCH` or `DIE`) from a DXF file.

    Supports `LINE`, `ARC`, `CIRCLE`, `LWPOLYLINE`, and `POLYLINE` entities.
    Preserves exact line and circular arc radii, detects outer contour and
    inner holes, rejects open or self-intersecting profiles, and validates
    geometric datums.
    """
    t0 = time.perf_counter()
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise ToolingError(f"DXF file not found: {path}")

    tool_kind = ToolKind(kind.upper()) if isinstance(kind, str) else kind
    try:
        raw_text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        raise ToolingError(f"Cannot read DXF file {path}: {exc}") from exc

    pairs = _parse_dxf_pairs(raw_text)
    units_name, scale_to_mm = _extract_dxf_units(pairs, units_override)

    # Extract entities in ENTITIES section
    segments: List[Segment2D] = []
    in_entities = False
    i = 0
    n = len(pairs)
    while i < n:
        code, val = pairs[i]
        if code == 2 and val == "ENTITIES":
            in_entities = True
            i += 1
            continue
        if in_entities and code == 0 and val == "ENDSEC":
            break
        if not in_entities:
            i += 1
            continue

        if code == 0:
            ent_type = val.upper()
            i += 1
            props: List[Tuple[int, str]] = []
            while i < n and pairs[i][0] != 0:
                props.append(pairs[i])
                i += 1

            pmap: Dict[int, List[str]] = {}
            for k, v in props:
                pmap.setdefault(k, []).append(v)

            if ent_type == "LINE":
                try:
                    x0, y0 = float(pmap[10][0]), float(pmap[20][0])
                    x1, y1 = float(pmap[11][0]), float(pmap[21][0])
                except (KeyError, ValueError, IndexError) as exc:
                    raise ToolingError(f"Malformed LINE entity in DXF {path.name}") from exc
                segments.append(LineSegment2D((x0, y0), (x1, y1)))

            elif ent_type == "ARC":
                try:
                    cx, cy = float(pmap[10][0]), float(pmap[20][0])
                    r = float(pmap[40][0])
                    a0, a1 = float(pmap[50][0]), float(pmap[51][0])
                except (KeyError, ValueError, IndexError) as exc:
                    raise ToolingError(f"Malformed ARC entity in DXF {path.name}") from exc
                segments.append(ArcSegment2D.from_dxf_arc(cx, cy, r, a0, a1))

            elif ent_type == "CIRCLE":
                try:
                    cx, cy = float(pmap[10][0]), float(pmap[20][0])
                    r = float(pmap[40][0])
                except (KeyError, ValueError, IndexError) as exc:
                    raise ToolingError(f"Malformed CIRCLE entity in DXF {path.name}") from exc
                segments.append(Circle2D((cx, cy), r))

            elif ent_type == "LWPOLYLINE":
                segments.extend(_lwpolyline_to_segments(props))

            elif ent_type == "POLYLINE":
                poly_flags = int(pmap.get(70, ["0"])[0])
                poly_verts: List[Tuple[float, float, float]] = []
                while i < n:
                    c0, v0 = pairs[i]
                    if c0 == 0 and v0.upper() == "VERTEX":
                        i += 1
                        vprops: Dict[int, str] = {}
                        while i < n and pairs[i][0] != 0:
                            vprops[pairs[i][0]] = pairs[i][1]
                            i += 1
                        vx = float(vprops[10])
                        vy = float(vprops[20])
                        vb = float(vprops.get(42, "0.0"))
                        poly_verts.append((vx, vy, vb))
                    elif c0 == 0 and v0.upper() == "SEQEND":
                        i += 1
                        while i < n and pairs[i][0] != 0:
                            i += 1
                        break
                    else:
                        break
                if len(poly_verts) < 2:
                    raise ToolingError("POLYLINE has fewer than 2 vertices")
                closed = bool(poly_flags & 1)
                cnt = len(poly_verts) if closed else len(poly_verts) - 1
                for vi in range(cnt):
                    x0, y0, b = poly_verts[vi]
                    x1, y1, _ = poly_verts[(vi + 1) % len(poly_verts)]
                    if _dist((x0, y0), (x1, y1)) <= 1e-9:
                        if vi == cnt - 1 and closed:
                            continue
                        raise ToolingError("Degenerate zero-length segment in POLYLINE")
                    if abs(b) <= 1e-9:
                        segments.append(LineSegment2D((x0, y0), (x1, y1)))
                    else:
                        segments.append(ArcSegment2D.from_bulge((x0, y0), (x1, y1), b))
        else:
            i += 1

    profile_geom = assemble_contours_from_segments(segments, tol=1e-2 / scale_to_mm)
    import_ms = (time.perf_counter() - t0) * 1000.0

    stem = path.stem
    safe_id = tool_id or re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("_") or "tool"
    tool_name = name or stem

    tool = ToolProfile(
        tool_id=safe_id,
        name=tool_name,
        kind=tool_kind,
        source_format="DXF",
        source_file=str(path),
        units=units_name,
        unit_scale_to_mm=scale_to_mm,
        profile_geometry=profile_geom,
        length_mm=length_mm,
        validation_status=ValidationStatus.IMPORTED,
        metrics=ToolPerformanceMetrics(
            import_time_ms=import_ms,
            section_time_ms=0.0,
            memory_bytes=len(raw_text.encode("utf-8")),
        ),
    )
    validate_tool_profile(tool)
    return tool


# ---------------------------------------------------------------------------
# STEP / STP & FreeCAD Shape 3D Cross-Section Importer
# ---------------------------------------------------------------------------

def _axis_vector(axis_spec: Union[str, Tuple[float, float, float]]) -> Tuple[float, float, float]:
    if isinstance(axis_spec, str):
        key = axis_spec.strip().upper()
        if key in ("X", "+X"):
            return (1.0, 0.0, 0.0)
        if key == "-X":
            return (-1.0, 0.0, 0.0)
        if key in ("Y", "+Y"):
            return (0.0, 1.0, 0.0)
        if key == "-Y":
            return (0.0, -1.0, 0.0)
        if key in ("Z", "+Z"):
            return (0.0, 0.0, 1.0)
        if key == "-Z":
            return (0.0, 0.0, -1.0)
        raise ToolingError(f"Unsupported length_axis specification: {axis_spec!r}")
    if len(axis_spec) != 3:
        raise ToolingError(f"length_axis vector must have 3 components, got {axis_spec!r}")
    nx, ny, nz = float(axis_spec[0]), float(axis_spec[1]), float(axis_spec[2])
    m = math.sqrt(nx * nx + ny * ny + nz * nz)
    if m <= 1e-9:
        raise ToolingError("Zero-length length_axis vector")
    return (nx / m, ny / m, nz / m)


def _orthonormal_section_basis(
    normal_xyz: Tuple[float, float, float],
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    """Return `(u_axis, v_axis)` spanning the 2D cross-section plane orthogonal to `normal_xyz`."""
    nx, ny, nz = normal_xyz
    # Standard right-handed choice so:
    # - if length_axis == Z (0,0,1), u = X (1,0,0), v = Y (0,1,0)
    # - if length_axis == X (1,0,0), u = Y (0,1,0), v = Z (0,0,1)
    # - if length_axis == Y (0,1,0), u = X (1,0,0), v = Z (0,0,1)
    if abs(nz) > 0.99:
        return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    if abs(nx) > 0.99:
        return ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    if abs(ny) > 0.99:
        return ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0))

    ref = (0.0, 0.0, 1.0) if abs(nz) < 0.9 else (1.0, 0.0, 0.0)
    ux = ref[1] * nz - ref[2] * ny
    uy = ref[2] * nx - ref[0] * nz
    uz = ref[0] * ny - ref[1] * nx
    um = math.sqrt(ux * ux + uy * uy + uz * uz)
    u = (ux / um, uy / um, uz / um)
    vx = ny * u[2] - nz * u[1]
    vy = nz * u[0] - nx * u[2]
    vz = nx * u[1] - ny * u[0]
    return (u, (vx, vy, vz))


def _project_3d_point(
    pt_xyz: Tuple[float, float, float],
    u_axis: Tuple[float, float, float],
    v_axis: Tuple[float, float, float],
) -> Point2D:
    return (
        pt_xyz[0] * u_axis[0] + pt_xyz[1] * u_axis[1] + pt_xyz[2] * u_axis[2],
        pt_xyz[0] * v_axis[0] + pt_xyz[1] * v_axis[1] + pt_xyz[2] * v_axis[2],
    )


def _occ_edge_to_segment2d(
    edge: Any,
    u_axis: Tuple[float, float, float],
    v_axis: Tuple[float, float, float],
) -> Segment2D:
    """Convert a planar FreeCAD/OpenCASCADE `Part.Edge` to an exact `Segment2D`."""
    curve = edge.Curve
    ctype = type(curve).__name__
    verts = edge.Vertexes
    p0_3d = (float(verts[0].Point.x), float(verts[0].Point.y), float(verts[0].Point.z))
    p1_3d = (float(verts[-1].Point.x), float(verts[-1].Point.y), float(verts[-1].Point.z))
    p0 = _project_3d_point(p0_3d, u_axis, v_axis)
    p1 = _project_3d_point(p1_3d, u_axis, v_axis)

    if hasattr(curve, "Radius") or "Circle" in ctype:
        c_3d = (float(curve.Center.x), float(curve.Center.y), float(curve.Center.z))
        center_2d = _project_3d_point(c_3d, u_axis, v_axis)
        r = float(curve.Radius)
        if len(verts) == 1 or (_dist(p0, p1) <= 1e-6 and edge.Length > math.pi * r):
            return Circle2D(center=center_2d, radius=r)
        # Determine CCW vs CW using the interior midpoint of the edge parameter range
        u_first, u_last = float(edge.FirstParameter), float(edge.LastParameter)
        mid_pt3 = edge.valueAt(0.5 * (u_first + u_last))
        mid_2d = _project_3d_point((float(mid_pt3.x), float(mid_pt3.y), float(mid_pt3.z)), u_axis, v_axis)
        arc_ccw = ArcSegment2D(start=p0, end=p1, center=center_2d, radius=r, ccw=True)
        arc_cw = ArcSegment2D(start=p0, end=p1, center=center_2d, radius=r, ccw=False)
        if _dist(mid_2d, arc_ccw.midpoint()) <= _dist(mid_2d, arc_cw.midpoint()):
            return arc_ccw
        return arc_cw

    if "Line" in ctype or len(verts) == 2:
        return LineSegment2D(start=p0, end=p1)

    raise ToolingError(f"Unsupported 3D section curve type {ctype!r}")


def extract_cross_section_from_shape(
    shape: Any,
    length_axis: Optional[Union[str, Tuple[float, float, float]]] = None,
    section_offset_mm: Optional[float] = None,
    solid_index: Optional[int] = None,
) -> Tuple[ProfileGeometry2D, float, bool, Tuple[str, ...]]:
    """Extract an exact 2D `ProfileGeometry2D` cross-section from a FreeCAD `Part.Shape`.

    Returns `(profile_geometry, length_mm, has_variable_section, limitations_25d)`.
    - Copies the input shape so the caller's object is never mutated.
    - Requires explicit `solid_index` when the shape has multiple Solids.
    - Detects variable cross-section along the tool length by comparing slices at
      20%, 50%, and 80% of the length, flagging it as a 2.5D limitation.
    """
    if shape is None or getattr(shape, "isNull", lambda: True)():
        raise ToolingError("Cannot extract cross-section from null or empty Shape")

    work_shape = shape.copy()
    solids = list(getattr(work_shape, "Solids", ()))
    if len(solids) > 1:
        if solid_index is None:
            raise ToolingError(
                f"Multi-solid 3D tool model contains {len(solids)} solids; "
                "specify solid_index explicitly."
            )
        if not 0 <= int(solid_index) < len(solids):
            raise ToolingError(f"solid_index {solid_index} out of range (0..{len(solids) - 1})")
        target_solid = solids[int(solid_index)].copy()
    elif len(solids) == 1:
        if solid_index is not None and int(solid_index) != 0:
            raise ToolingError(f"solid_index {solid_index} out of range for single-solid model")
        target_solid = solids[0].copy()
    else:
        # Planar face or wire shape (e.g. Sketch)
        wires = list(getattr(work_shape, "Wires", ()))
        if not wires:
            raise ToolingError("Shape contains neither Solids nor closed Wires")
        bb = work_shape.BoundBox
        spans = [("X", bb.XLength), ("Y", bb.YLength), ("Z", bb.ZLength)]
        thin_axis = min(spans, key=lambda item: item[1])[0]
        normal = _axis_vector(length_axis or thin_axis)
        u_axis, v_axis = _orthonormal_section_basis(normal)
        segs = [_occ_edge_to_segment2d(e, u_axis, v_axis) for e in work_shape.Edges]
        prof = assemble_contours_from_segments(segs)
        return prof, 0.0, False, ()

    bb = target_solid.BoundBox
    axis_bounds = {
        "X": (float(bb.XMin), float(bb.XMax), float(bb.XLength)),
        "Y": (float(bb.YMin), float(bb.YMax), float(bb.YLength)),
        "Z": (float(bb.ZMin), float(bb.ZMax), float(bb.ZLength)),
    }

    if length_axis is None:
        # Choose axis along which cylindrical/planar faces are extruded, or longest span if unambiguous
        normal = (0.0, 0.0, 1.0)
        axis_name = "Z"
        # Check if all planar end-caps or extrusion directions align with X, Y, or Z
        sorted_axes = sorted(axis_bounds.items(), key=lambda kv: kv[1][2], reverse=True)
        axis_name = sorted_axes[0][0]
        normal = _axis_vector(axis_name)
    else:
        normal = _axis_vector(length_axis)

    # Project bounding box onto `normal`
    corners = [
        (x, y, z)
        for x in (bb.XMin, bb.XMax)
        for y in (bb.YMin, bb.YMax)
        for z in (bb.ZMin, bb.ZMax)
    ]
    proj = [c[0] * normal[0] + c[1] * normal[1] + c[2] * normal[2] for c in corners]
    s_min, s_max = min(proj), max(proj)
    length_mm = round(s_max - s_min, 6)
    if length_mm <= 1e-3:
        raise ToolingError(f"Invalid 3D tool length along selected axis: {length_mm} mm")

    mid_offset = 0.5 * (s_min + s_max) if section_offset_mm is None else float(section_offset_mm)
    if not (s_min + 1e-4 < mid_offset < s_max - 1e-4):
        raise ToolingError(
            f"Section plane offset {mid_offset:.3f} mm lies outside solid bounds [{s_min:.3f}, {s_max:.3f}] mm"
        )

    import FreeCAD as App

    n_vec = App.Vector(*normal)
    wires_mid = target_solid.slice(n_vec, mid_offset)
    if not wires_mid:
        raise ToolingError("Cross-section plane produced no intersection wires")

    u_axis, v_axis = _orthonormal_section_basis(normal)
    edges_mid = [e for w in wires_mid for e in w.Edges]
    segs_mid = [_occ_edge_to_segment2d(e, u_axis, v_axis) for e in edges_mid]
    profile_geom = assemble_contours_from_segments(segs_mid)

    # Check longitudinal uniformity at 20% and 80% of tool length
    has_variable_section = False
    limitations: List[str] = []
    for frac in (0.2, 0.8):
        off = s_min + frac * (s_max - s_min)
        w_test = target_solid.slice(n_vec, off)
        if len(w_test) != len(wires_mid):
            has_variable_section = True
            break
        e_test = [e for w in w_test for e in w.Edges]
        if len(e_test) != len(edges_mid):
            has_variable_section = True
            break
        try:
            p_test = assemble_contours_from_segments(
                [_occ_edge_to_segment2d(e, u_axis, v_axis) for e in e_test]
            )
            b0 = profile_geom.bbox()
            b1 = p_test.bbox()
            if (
                abs(p_test.outer_contour.area - profile_geom.outer_contour.area) > 0.5
                or max(abs(b0[k] - b1[k]) for k in range(4)) > 0.05
            ):
                has_variable_section = True
                break
        except ToolingError:
            has_variable_section = True
            break

    if has_variable_section:
        limitations.append(
            "2.5D LIMITATION: Tool has non-uniform cross-section along its length axis; "
            "a single 2D cross-section is not sufficient for full 3D collision verification."
        )

    return profile_geom, length_mm, has_variable_section, tuple(limitations)


def import_tool_from_step(
    file_path: Union[str, Path],
    kind: Union[ToolKind, str],
    name: Optional[str] = None,
    tool_id: Optional[str] = None,
    length_axis: Optional[Union[str, Tuple[float, float, float]]] = None,
    section_offset_mm: Optional[float] = None,
    solid_index: Optional[int] = None,
    units: str = "mm",
) -> ToolProfile:
    """Import a 3D STEP/STP tool via FreeCAD/OpenCASCADE and extract its exact 2D cross-section."""
    t_start = time.perf_counter()
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise ToolingError(f"STEP file not found: {path}")

    uk = units.strip().lower()
    if uk not in UNIT_SCALE_TO_MM:
        raise ToolingError(f"Unsupported units for STEP import: {units!r}")
    scale_to_mm = UNIT_SCALE_TO_MM[uk]

    try:
        import Part
    except ImportError as exc:
        raise ToolingError("FreeCAD Part module is required for STEP tool import") from exc

    shape = Part.Shape()
    try:
        shape.read(str(path))
    except Exception as exc:
        raise ToolingError(f"Failed to read STEP file {path.name}: {exc}") from exc

    if shape.isNull():
        raise ToolingError(f"STEP file {path.name} produced a null Shape")

    t_after_import = time.perf_counter()
    profile_geom, length_raw, has_var, limits = extract_cross_section_from_shape(
        shape,
        length_axis=length_axis,
        section_offset_mm=section_offset_mm,
        solid_index=solid_index,
    )
    t_after_section = time.perf_counter()

    tool_kind = ToolKind(kind.upper()) if isinstance(kind, str) else kind
    stem = path.stem
    safe_id = tool_id or re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("_") or "step_tool"
    tool_name = name or stem

    tool = ToolProfile(
        tool_id=safe_id,
        name=tool_name,
        kind=tool_kind,
        source_format="STEP",
        source_file=str(path),
        units=uk,
        unit_scale_to_mm=scale_to_mm,
        profile_geometry=profile_geom,
        length_mm=round(length_raw * scale_to_mm, 6) if length_raw > 0.0 else NOT_SPECIFIED,
        validation_status=ValidationStatus.IMPORTED,
        has_variable_section_25d_limitation=has_var,
        limitations_25d=limits,
        metrics=ToolPerformanceMetrics(
            import_time_ms=(t_after_import - t_start) * 1000.0,
            section_time_ms=(t_after_section - t_after_import) * 1000.0,
            memory_bytes=path.stat().st_size,
        ),
    )
    validate_tool_profile(tool)
    return tool


def import_tool_from_freecad_object(
    fc_object: Any,
    kind: Union[ToolKind, str],
    name: Optional[str] = None,
    tool_id: Optional[str] = None,
    length_axis: Optional[Union[str, Tuple[float, float, float]]] = None,
    section_offset_mm: Optional[float] = None,
    solid_index: Optional[int] = None,
    units: str = "mm",
) -> ToolProfile:
    """Import a tool from an open FreeCAD object without modifying the object or its document."""
    t_start = time.perf_counter()
    if fc_object is None or not hasattr(fc_object, "Shape"):
        raise ToolingError("Selected FreeCAD object has no Shape attribute")
    src_shape = fc_object.Shape
    if src_shape is None or src_shape.isNull():
        raise ToolingError("Selected FreeCAD object has a null Shape")

    # Read-only copy of the shape
    shape_copy = src_shape.copy()
    t_after_copy = time.perf_counter()

    profile_geom, length_raw, has_var, limits = extract_cross_section_from_shape(
        shape_copy,
        length_axis=length_axis,
        section_offset_mm=section_offset_mm,
        solid_index=solid_index,
    )
    t_after_section = time.perf_counter()

    uk = units.strip().lower()
    if uk not in UNIT_SCALE_TO_MM:
        raise ToolingError(f"Unsupported units: {units!r}")
    scale_to_mm = UNIT_SCALE_TO_MM[uk]

    obj_name = str(getattr(fc_object, "Name", "FreeCADObject"))
    obj_label = str(getattr(fc_object, "Label", obj_name))
    doc_name = str(getattr(getattr(fc_object, "Document", None), "Name", "ActiveDocument"))
    safe_id = tool_id or re.sub(r"[^A-Za-z0-9_.-]+", "_", obj_label).strip("_") or obj_name

    brep_bytes = len(shape_copy.exportBrepToString().encode("utf-8")) if hasattr(shape_copy, "exportBrepToString") else 0

    tool = ToolProfile(
        tool_id=safe_id,
        name=name or obj_label,
        kind=ToolKind(kind.upper()) if isinstance(kind, str) else kind,
        source_format="FREECAD_SHAPE",
        source_file=f"freecad://{doc_name}/{obj_name}",
        units=uk,
        unit_scale_to_mm=scale_to_mm,
        profile_geometry=profile_geom,
        length_mm=round(length_raw * scale_to_mm, 6) if length_raw > 0.0 else NOT_SPECIFIED,
        validation_status=ValidationStatus.IMPORTED,
        has_variable_section_25d_limitation=has_var,
        limitations_25d=limits,
        metrics=ToolPerformanceMetrics(
            import_time_ms=(t_after_copy - t_start) * 1000.0,
            section_time_ms=(t_after_section - t_after_copy) * 1000.0,
            memory_bytes=brep_bytes,
        ),
    )
    validate_tool_profile(tool)
    return tool


# ---------------------------------------------------------------------------
# Coordinate Systems & Static 2.5D Scene Positioning
# ---------------------------------------------------------------------------

def machine_to_view_point(sz: Point2D) -> Point2D:
    """Convert Machine coordinates `(s_mm, z_mm)` to Qt Canvas View coordinates `(x_view, y_view)`."""
    return (float(sz[0]), -float(sz[1]))


def view_to_machine_point(xy_view: Point2D) -> Point2D:
    """Convert Qt Canvas View coordinates `(x_view, y_view)` to Machine coordinates `(s_mm, z_mm)`."""
    return (float(xy_view[0]), -float(xy_view[1]))


@dataclass
class ToolingSetup2D:
    """Manages selected PUNCH and DIE positioning in Machine coordinates `(s, z)`.

    Coordinate spaces:
    1. `SOURCE`: original file coordinates.
    2. `TOOL_LOCAL`: tool local coordinates (mm) with `working_datum` at `(0, 0)`
       and tool axis aligned vertically (`(0, -1)` for PUNCH, `(0, +1)` for DIE).
    3. `MACHINE`: 2.5D machine scene coordinates `(s, z)` in mm, where `s` is
       horizontal along the sheet cross-section and `z` is vertical along ram motion.
    4. `VIEW`: Qt `QGraphicsScene` coordinates `(x = s, y = -z)`.
    """
    punch: Optional[ToolProfile] = None
    die: Optional[ToolProfile] = None
    visible: bool = True
    aligned: bool = False
    aligned_bend_id: Optional[str] = None
    punch_machine_transform: Transform2D = field(default_factory=Transform2D.identity)
    die_machine_transform: Transform2D = field(default_factory=Transform2D.identity)
    alignment_anchor_sz: Point2D = (0.0, 0.0)
    sheet_top_z_mm: float = 1.0
    sheet_bottom_z_mm: float = 0.0
    punch_clearance_mm: float = DEFAULT_INITIAL_PUNCH_CLEARANCE_MM
    ram_z_mm: float = 16.0
    ram_stroke_mm: float = 0.0
    match_active_bend_length: bool = False
    active_bend_length_mm: Optional[float] = None
    allow_end_zone_collision: bool = False
    end_zone_length_mm: float = 10.0
    # Explicit physical machine-X station assembly; independent from virtual
    # tool-length mode. Empty means a single native source-CAD section.
    punch_section_offsets_xm: Tuple[float, ...] = ()
    die_section_offsets_xm: Tuple[float, ...] = ()

    def set_native_sections(self, punch_offsets_xm=(), die_offsets_xm=()) -> None:
        """Select native FCStd sections; no catalog-length or virtual extrusion."""
        def check(name, values):
            offsets = tuple(float(v) for v in values)
            if len(offsets) == 1:
                raise ValueError(f"{name}: one offset is ambiguous; use no offsets or >=2")
            if any(not math.isfinite(v) for v in offsets):
                raise ValueError(f"{name}: non-finite section offset")
            if len(set(round(v, 8) for v in offsets)) != len(offsets):
                raise ValueError(f"{name}: duplicate machine-X section offset")
            return offsets

        if self.match_active_bend_length and (punch_offsets_xm or die_offsets_xm):
            raise ValueError("physical multi-sections are incompatible with virtual tool length")
        self.punch_section_offsets_xm = check("punch", punch_offsets_xm)
        self.die_section_offsets_xm = check("die", die_offsets_xm)

    def set_match_active_bend_length(
        self,
        enabled: bool,
        active_bend_length_mm: Optional[float] = None,
    ) -> None:
        """Enable or disable development virtual tool length mode (`tool_length == active_bend_length`)."""
        if enabled and (self.punch_section_offsets_xm or self.die_section_offsets_xm):
            raise ValueError("physical multi-sections are incompatible with virtual tool length")
        self.match_active_bend_length = bool(enabled)
        if active_bend_length_mm is not None:
            val = float(active_bend_length_mm)
            self.active_bend_length_mm = val if (math.isfinite(val) and val > 0.0) else None
        elif not self.match_active_bend_length:
            self.active_bend_length_mm = None

    def effective_punch_length_mm(self, fallback_bend_length_mm: Optional[float] = None) -> Optional[float]:
        """Return effective punch length (mm) in current mode (virtual bend length or library fixed)."""
        if self.match_active_bend_length:
            if fallback_bend_length_mm is not None and math.isfinite(float(fallback_bend_length_mm)) and float(fallback_bend_length_mm) > 0.0:
                return float(fallback_bend_length_mm)
            if self.active_bend_length_mm is not None and math.isfinite(float(self.active_bend_length_mm)) and float(self.active_bend_length_mm) > 0.0:
                return float(self.active_bend_length_mm)
            return None
        if self.punch is not None and isinstance(self.punch.length_mm, (int, float)):
            return float(self.punch.length_mm)
        return None

    def effective_die_length_mm(self, fallback_bend_length_mm: Optional[float] = None) -> Optional[float]:
        """Return effective die length (mm) in current mode (virtual bend length or library fixed)."""
        if self.match_active_bend_length:
            if fallback_bend_length_mm is not None and math.isfinite(float(fallback_bend_length_mm)) and float(fallback_bend_length_mm) > 0.0:
                return float(fallback_bend_length_mm)
            if self.active_bend_length_mm is not None and math.isfinite(float(self.active_bend_length_mm)) and float(self.active_bend_length_mm) > 0.0:
                return float(self.active_bend_length_mm)
            return None
        if self.die is not None and isinstance(self.die.length_mm, (int, float)):
            return float(self.die.length_mm)
        return None

    def select_punch(self, punch: Optional[ToolProfile]) -> None:
        if punch is not None:
            if punch.kind != ToolKind.PUNCH:
                raise ToolingError(f"Expected PUNCH tool, got {punch.kind}")
            if punch.validation_status == ValidationStatus.INVALID:
                raise ToolingError(f"Cannot select INVALID punch '{punch.name}'")
        self.punch = punch
        self.reset_position()

    def select_die(self, die: Optional[ToolProfile]) -> None:
        if die is not None:
            if die.kind != ToolKind.DIE:
                raise ToolingError(f"Expected DIE tool, got {die.kind}")
            if die.validation_status == ValidationStatus.INVALID:
                raise ToolingError(f"Cannot select INVALID die '{die.name}'")
        self.die = die
        self.reset_position()

    def reset_position(self) -> None:
        """Reset tooling transforms to default unaligned parking position above/below sheet."""
        self.aligned = False
        self.aligned_bend_id = None
        self.alignment_anchor_sz = (0.0, 0.0)
        self.sheet_top_z_mm = 1.0
        self.sheet_bottom_z_mm = -1.0
        self.punch_clearance_mm = 15.0
        self.ram_z_mm = self.punch_clearance_mm
        self.ram_stroke_mm = 0.0
        self.punch_machine_transform = Transform2D.translation(0.0, self.punch_clearance_mm)
        self.die_machine_transform = Transform2D.translation(0.0, -5.0)

    def align_to_cross_section(
        self,
        cross_section: Optional[Dict[str, Any]] = None,
        clearance_mm: float = DEFAULT_INITIAL_PUNCH_CLEARANCE_MM,
        bend_id: Optional[str] = None,
        bend_zone: Optional[Any] = None,
        thickness_mm: Optional[float] = None,
    ) -> None:
        """Align PUNCH and DIE working axes and datums to the active bend in Machine `(s, z)` space.

        - Requires at least one selected tool (`punch` or `die`) with `GEOMETRY_VERIFIED` datums.
        - Places the Die V-groove center (`working_datum`) directly under the active bend bottom
          surface (`sheet_bottom_z`) so the die lies below the sheet.
        - Aligns the Punch working axis (`s`) coaxially with the Die V-groove center (`s_punch == s_die == anchor_s`).
        - Places the Punch working tip above the sheet top surface by explicit positive `clearance_mm`
          (`z_punch_tip = sheet_top_z + clearance_mm`), so the punch does not touch the sheet in initial state.
        - Requires authoritative sheet thickness from SheetMetal / BendZone; silent 1.0mm defaults are prohibited.
        - Does NOT modify `PanelGraph`, `FoldState`, `BendZone`, or `BendSequenceController`.
        """
        if self.punch is None and self.die is None:
            raise ToolingError("Не выбран ни пуансон (PUNCH), ни матрица (DIE) для выравнивания.")
        if not math.isfinite(float(clearance_mm)) or float(clearance_mm) <= 0.0:
            raise ToolingError(
                f"Initial punch clearance_mm must be a positive number in mm, got {clearance_mm!r}"
            )
        if self.punch is not None:
            if (
                self.punch.validation_status != ValidationStatus.GEOMETRY_VERIFIED
                or self.punch.punch_datums is None
                or self.punch.working_datum == NOT_SPECIFIED
            ):
                raise ToolingError(
                    f"Punch '{self.punch.name}' lacks verified geometric datums "
                    f"(status={self.punch.validation_status.value}); assign datums before aligning tooling."
                )
        if self.die is not None:
            if (
                self.die.validation_status != ValidationStatus.GEOMETRY_VERIFIED
                or self.die.die_datums is None
                or self.die.working_datum == NOT_SPECIFIED
            ):
                raise ToolingError(
                    f"Die '{self.die.name}' lacks verified geometric datums "
                    f"(status={self.die.validation_status.value}); assign datums before aligning tooling."
                )

        # 1. Authoritative sheet thickness derivation from SheetMetal / BendZone
        sheet_thickness: Optional[float] = None
        if thickness_mm is not None:
            sheet_thickness = float(thickness_mm)
        elif bend_zone is not None and getattr(bend_zone, "thickness_mm", None) is not None:
            sheet_thickness = float(bend_zone.thickness_mm)
        elif cross_section is not None and cross_section.get("thickness_mm") is not None:
            sheet_thickness = float(cross_section["thickness_mm"])

        if sheet_thickness is None or sheet_thickness <= 0.0 or not math.isfinite(sheet_thickness):
            raise ToolingError(
                "UNSUPPORTED: Sheet thickness must be derived from SheetMetal / BendZone; "
                "missing authoritative thickness data (silent fallbacks are forbidden)."
            )

        if cross_section is not None and cross_section.get("neutral_curve"):
            nc = cross_section["neutral_curve"]
            mid_idx = len(nc) // 2
            anchor_s = float(nc[mid_idx][0])
            resolved_bend_id = bend_id or cross_section.get("bend_id")

            # max(z) - min(z) is admissible ONLY as a verification for a known flat, properly oriented section
            zone_poly = cross_section.get("zone_polygon") or nc
            zs = [float(p[1]) for p in zone_poly]
            if zs:
                cur_ang = abs(float(cross_section.get("angle_deg", 0.0)))
                if cur_ang < 1e-3:
                    z_span = max(zs) - min(zs)
                    if abs(z_span - sheet_thickness) > 0.05:
                        import logging
                        logging.getLogger(__name__).warning(
                            "Flat section z_span (%.3f mm) differs from authoritative thickness (%.3f mm)",
                            z_span, sheet_thickness,
                        )
        else:
            anchor_s = 0.0
            resolved_bend_id = bend_id

        # Machine coordinate contract:
        # Die top shoulder plane is at Z_m = 0.0.
        # Flat sheet outer/bottom surface rests on die shoulders at Z_m = 0.0.
        # Sheet inner/top surface is at Z_m = sheet_thickness.
        sheet_bottom_z = 0.0
        sheet_top_z = sheet_thickness

        self.alignment_anchor_sz = (anchor_s, sheet_bottom_z)
        self.sheet_top_z_mm = sheet_top_z
        self.sheet_bottom_z_mm = sheet_bottom_z
        self.punch_clearance_mm = float(clearance_mm)
        self.ram_z_mm = sheet_top_z + self.punch_clearance_mm
        self.ram_stroke_mm = 0.0
        # Die V-groove center (local (0,0)) aligned at (anchor_s, sheet_bottom_z) where sheet_bottom_z = 0.0
        self.die_machine_transform = Transform2D.translation(anchor_s, sheet_bottom_z)
        # Punch working tip (local (0,0)) coaxially aligned at (anchor_s, sheet_top_z + clearance_mm)
        self.punch_machine_transform = Transform2D.translation(anchor_s, self.ram_z_mm)
        self.aligned = True
        self.aligned_bend_id = str(resolved_bend_id) if resolved_bend_id else None
        if bend_zone is not None and getattr(bend_zone, "axis_xy", None) is not None:
            ax = bend_zone.axis_xy
            if len(ax) == 2:
                blen = _dist(ax[0], ax[1])
                if math.isfinite(blen) and blen > 0.0:
                    self.active_bend_length_mm = float(blen)
        elif cross_section is not None and cross_section.get("bend_length_mm") is not None:
            try:
                blen = float(cross_section["bend_length_mm"])
                if math.isfinite(blen) and blen > 0.0:
                    self.active_bend_length_mm = blen
            except (TypeError, ValueError):
                pass

    def set_punch_ram_z(self, ram_z_mm: float, stroke_mm: float = 0.0) -> None:
        """Move ONLY the punch vertically along its coaxial Machine `s` axis.

        Never moves the die (`die_machine_transform` remains invariant) and never
        mutates the source `ToolProfile` geometry.
        """
        if not math.isfinite(float(ram_z_mm)):
            raise ToolingError(f"ram_z_mm must be finite, got {ram_z_mm!r}")
        anchor_s = self.alignment_anchor_sz[0] if self.aligned else self.punch_machine_transform.tx
        self.ram_z_mm = float(ram_z_mm)
        self.ram_stroke_mm = float(stroke_mm)
        self.punch_machine_transform = Transform2D.translation(anchor_s, self.ram_z_mm)

    def punch_tip_machine(self) -> Optional[Point2D]:
        if self.punch is None:
            return None
        return self.punch_machine_transform.apply((0.0, 0.0))

    def die_v_center_machine(self) -> Optional[Point2D]:
        if self.die is None:
            return None
        return self.die_machine_transform.apply((0.0, 0.0))

    def punch_polygons_machine(
        self, arc_segments: int = 20
    ) -> Optional[Tuple[Tuple[Point2D, ...], Tuple[Tuple[Point2D, ...], ...]]]:
        if self.punch is None:
            return None
        outer_local, holes_local = self.punch.local_polygons(arc_segments=arc_segments)
        tr = self.punch_machine_transform
        outer_m = tuple(tr.apply(p) for p in outer_local)
        holes_m = tuple(tuple(tr.apply(p) for p in h) for h in holes_local)
        return outer_m, holes_m

    def die_polygons_machine(
        self, arc_segments: int = 20
    ) -> Optional[Tuple[Tuple[Point2D, ...], Tuple[Tuple[Point2D, ...], ...]]]:
        if self.die is None:
            return None
        outer_local, holes_local = self.die.local_polygons(arc_segments=arc_segments)
        tr = self.die_machine_transform
        outer_m = tuple(tr.apply(p) for p in outer_local)
        holes_m = tuple(tuple(tr.apply(p) for p in h) for h in holes_local)
        return outer_m, holes_m

    def punch_bounds_machine(self, arc_segments: int = 20) -> Optional[BBox2D]:
        polys = self.punch_polygons_machine(arc_segments=arc_segments)
        if polys is None or not polys[0]:
            return None
        xs = [p[0] for p in polys[0]]
        zs = [p[1] for p in polys[0]]
        return (min(xs), min(zs), max(xs), max(zs))

    def die_bounds_machine(self, arc_segments: int = 20) -> Optional[BBox2D]:
        polys = self.die_polygons_machine(arc_segments=arc_segments)
        if polys is None or not polys[0]:
            return None
        xs = [p[0] for p in polys[0]]
        zs = [p[1] for p in polys[0]]
        return (min(xs), min(zs), max(xs), max(zs))

    def geometry_status_summary(self) -> str:
        statuses = []
        if self.punch is not None:
            statuses.append(self.punch.validation_status)
        if self.die is not None:
            statuses.append(self.die.validation_status)
        if not statuses:
            return "NONE"
        if any(s == ValidationStatus.INVALID for s in statuses):
            return "INVALID"
        if any(s == ValidationStatus.NEEDS_ALIGNMENT for s in statuses):
            return "NEEDS_ALIGNMENT"
        if any(s == ValidationStatus.IMPORTED for s in statuses):
            return "IMPORTED"
        return "VERIFIED"

    def status_line(self) -> str:
        p_name = self.punch.name if self.punch is not None else "—"
        d_name = self.die.name if self.die is not None else "—"
        g_status = self.geometry_status_summary()
        base = f"Punch: {p_name} | Die: {d_name} | Geometry: {g_status} | Collision: NOT CHECKED"
        if self.match_active_bend_length:
            len_str = f"{self.active_bend_length_mm:.1f} mm" if self.active_bend_length_mm else "ACTIVE_BEND"
            return f"{base} | ToolLen: VIRTUAL={len_str}"
        return base


# ---------------------------------------------------------------------------
# Explicit Scene Bounds Calculation (Sheet vs Punch vs Die vs Combined)
# ---------------------------------------------------------------------------

def _pts_bbox(pts: Sequence[Point2D]) -> Optional[BBox2D]:
    if not pts:
        return None
    xs = [float(p[0]) for p in pts]
    zs = [float(p[1]) for p in pts]
    return (min(xs), min(zs), max(xs), max(zs))


def _union_bboxes(boxes: Sequence[Optional[BBox2D]]) -> Optional[BBox2D]:
    valid = [b for b in boxes if b is not None]
    if not valid:
        return None
    return (
        min(b[0] for b in valid),
        min(b[1] for b in valid),
        max(b[2] for b in valid),
        max(b[3] for b in valid),
    )


@dataclass(frozen=True)
class SceneBounds2D:
    """Explicit separation of `sheet_bounds`, `punch_bounds`, `die_bounds`, and `combined_bounds`
    in Machine `(s, z)` coordinates (mm) and View `(x_screen, y_screen)` rectangle.
    """
    sheet_bounds: Optional[BBox2D]
    punch_bounds: Optional[BBox2D]
    die_bounds: Optional[BBox2D]
    combined_bounds: Optional[BBox2D]
    view_rect: BBox2D  # (x_left, y_top, width, height) in Qt QGraphicsScene coordinates


def compute_scene_bounds_2d(
    sheet_pts: Sequence[Point2D] = (),
    punch_pts: Sequence[Point2D] = (),
    die_pts: Sequence[Point2D] = (),
    focus_sheet_when_tall_tooling: bool = True,
) -> SceneBounds2D:
    """Compute separated `sheet_bounds`, `punch_bounds`, `die_bounds`, `combined_bounds`,
    and the target `view_rect` without mutating any Machine coordinates.

    When both sheet and tall tooling (e.g. a 120–135 mm punch) are visible, if the combined
    vertical span exceeds 2.5x the sheet span, the view window clamps the vertical extent
    around the sheet and tool working zone (tip + V-groove) so the sheet remains clearly
    legible while still showing both tools in place.
    """
    s_box = _pts_bbox(sheet_pts)
    p_box = _pts_bbox(punch_pts)
    d_box = _pts_bbox(die_pts)
    c_box = _union_bboxes((s_box, p_box, d_box))

    if c_box is None:
        return SceneBounds2D(
            sheet_bounds=None,
            punch_bounds=None,
            die_bounds=None,
            combined_bounds=None,
            view_rect=(-120.0, -60.0, 240.0, 120.0),
        )

    target_box = c_box
    if focus_sheet_when_tall_tooling and s_box is not None and (p_box is not None or d_box is not None):
        sheet_w = max(s_box[2] - s_box[0], 10.0)
        sheet_h = max(s_box[3] - s_box[1], 4.0)
        sheet_span = max(sheet_w, sheet_h, 25.0)
        comb_h = c_box[3] - c_box[1]
        if comb_h > 2.0 * sheet_span:
            # Keep full horizontal extent of sheet + working zone of tools, and clamp vertical window
            # so at least 25 mm (or 0.65 * sheet_span) of the punch above and die below the sheet are visible
            tool_pad_z = max(25.0, 0.65 * sheet_span)
            min_s = min(s_box[0], c_box[0])
            max_s = max(s_box[2], c_box[2])
            min_z = max(c_box[1], s_box[1] - tool_pad_z)
            max_z = min(c_box[3], s_box[3] + tool_pad_z)
            target_box = (min_s, min_z, max_s, max_z)

    # Convert Machine (s_min, z_min, s_max, z_max) to View (x = s, y = -z)
    min_x = target_box[0]
    max_x = target_box[2]
    min_y = -target_box[3]
    max_y = -target_box[1]
    span = max(max_x - min_x, max_y - min_y, 10.0)
    pad = 0.15 * span
    view_rect = (
        min_x - pad,
        min_y - pad,
        (max_x - min_x) + 2.0 * pad,
        (max_y - min_y) + 2.0 * pad,
    )
    return SceneBounds2D(
        sheet_bounds=s_box,
        punch_bounds=p_box,
        die_bounds=d_box,
        combined_bounds=c_box,
        view_rect=view_rect,
    )


# ---------------------------------------------------------------------------
# Persistent User Tool Library (Versioned JSON + Exact Geometry, No Pickle)
# ---------------------------------------------------------------------------

def compare_profiles_within_tolerance(
    a: ToolProfile,
    b: ToolProfile,
    tol_mm: float = DEFAULT_GEOMETRY_TOL_MM,
) -> Tuple[bool, str]:
    """Verify that two `ToolProfile` objects match in exact segment types, radii, endpoints, and datums."""
    if a.kind != b.kind:
        return False, f"Kind mismatch: {a.kind} vs {b.kind}"
    sa = a.profile_geometry.all_segments
    sb = b.profile_geometry.all_segments
    if len(sa) != len(sb):
        return False, f"Segment count mismatch: {len(sa)} vs {len(sb)}"

    for idx, (s1, s2) in enumerate(zip(sa, sb)):
        if type(s1) is not type(s2):
            return False, f"Segment #{idx} type mismatch: {type(s1).__name__} vs {type(s2).__name__}"
        if isinstance(s1, LineSegment2D) and isinstance(s2, LineSegment2D):
            if _dist(s1.start, s2.start) > tol_mm or _dist(s1.end, s2.end) > tol_mm:
                return False, f"Line #{idx} endpoint mismatch exceeding {tol_mm} mm"
        elif isinstance(s1, ArcSegment2D) and isinstance(s2, ArcSegment2D):
            if (
                _dist(s1.start, s2.start) > tol_mm
                or _dist(s1.end, s2.end) > tol_mm
                or _dist(s1.center, s2.center) > tol_mm
                or abs(s1.radius - s2.radius) > tol_mm
                or s1.ccw != s2.ccw
            ):
                return False, f"Arc #{idx} parameter mismatch exceeding {tol_mm} mm"
        elif isinstance(s1, Circle2D) and isinstance(s2, Circle2D):
            if _dist(s1.center, s2.center) > tol_mm or abs(s1.radius - s2.radius) > tol_mm:
                return False, f"Circle #{idx} parameter mismatch exceeding {tol_mm} mm"

    for k in range(4):
        if abs(a.bbox[k] - b.bbox[k]) > tol_mm:
            return False, f"BBox mismatch at index {k}: {a.bbox} vs {b.bbox}"

    return True, "Geometry matches within tolerance"


class ToolLibrary25D:
    """Persistent user tool library for PressBrakeSim 2.5D.

    Stores each imported `ToolProfile` as a versioned `.profile25d.json` file
    inside `<library_root>/punch/` or `<library_root>/die/` with exact line/arc
    geometry (never using `pickle` and never degrading arcs to polylines).
    Also reads legacy `.FCStd` + `.tooling.json` entries in `toollibrary/` when
    FreeCAD `Part` is available.
    """

    PROFILE_SUFFIX = ".profile25d.json"

    def __init__(self, library_root: Optional[Union[str, Path]] = None) -> None:
        if library_root is None:
            repo_root = Path(__file__).resolve().parent.parent.parent
            library_root = repo_root / "toollibrary"
        self.library_root = Path(library_root).expanduser().resolve()
        self.punch_dir = self.library_root / "punch"
        self.die_dir = self.library_root / "die"
        self.punch_dir.mkdir(parents=True, exist_ok=True)
        self.die_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[Tuple[str, str], ToolProfile] = {}

    def _kind_dir(self, kind: Union[ToolKind, str]) -> Path:
        tk = ToolKind(kind.upper()) if isinstance(kind, str) else kind
        return self.punch_dir if tk == ToolKind.PUNCH else self.die_dir

    @staticmethod
    def _validate_tool_id(tool_id: str) -> str:
        tid = str(tool_id).strip()
        if not tid or "/" in tid or "\\" in tid or ".." in tid or Path(tid).name != tid:
            raise ToolingError(f"Invalid tool_id: {tool_id!r}")
        return tid

    def profile_path(self, tool_id: str, kind: Union[ToolKind, str]) -> Path:
        tid = self._validate_tool_id(tool_id)
        return self._kind_dir(kind) / f"{tid}{self.PROFILE_SUFFIX}"

    def save_tool(self, profile: ToolProfile, overwrite: bool = True) -> Path:
        """Persist a validated `ToolProfile` to the library atomically."""
        if profile.validation_status == ValidationStatus.INVALID:
            raise ToolingError(
                f"Cannot save INVALID tool profile '{profile.tool_id}' to library: "
                + "; ".join(profile.validation_messages)
            )
        tid = self._validate_tool_id(profile.tool_id)
        dest = self.profile_path(tid, profile.kind)
        if dest.exists() and not overwrite:
            raise ToolingError(f"Tool '{tid}' already exists in {profile.kind.value} library")

        payload = profile.to_dict()
        tmp_path = dest.with_suffix(".json.tmp")
        try:
            tmp_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            tmp_path.replace(dest)
        except Exception as exc:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise ToolingError(f"Failed to save tool profile '{tid}': {exc}") from exc

        self._cache[(profile.kind.value, tid)] = profile
        return dest

    def load_tool(self, tool_id: str, kind: Union[ToolKind, str], use_cache: bool = True) -> ToolProfile:
        """Load a `ToolProfile` from the library by `tool_id` and `kind`."""
        tk = ToolKind(kind.upper()) if isinstance(kind, str) else kind
        tid = self._validate_tool_id(tool_id)
        cache_key = (tk.value, tid)
        if use_cache and cache_key in self._cache:
            return self._cache[cache_key]

        p_path = self.profile_path(tid, tk)
        if p_path.is_file():
            try:
                data = json.loads(p_path.read_text(encoding="utf-8"))
                prof = ToolProfile.from_dict(data)
            except Exception as exc:
                raise ToolingError(f"Corrupt tool profile JSON at {p_path}: {exc}") from exc
            self._cache[cache_key] = prof
            return prof

        # Fallback: check for legacy .FCStd in the same directory
        fcstd_path = self._kind_dir(tk) / f"{tid}.FCStd"
        if fcstd_path.is_file():
            prof = self._load_legacy_fcstd_tool(fcstd_path, tk)
            self._cache[cache_key] = prof
            return prof

        raise ToolingError(f"Tool '{tid}' ({tk.value}) not found in {self.library_root}")

    def _load_legacy_fcstd_tool(self, fcstd_path: Path, kind: ToolKind) -> ToolProfile:
        """Load an existing `.FCStd` tool (e.g. `puancon.FCStd` / `matrica.FCStd`) via FreeCAD.

        Critically preserves `App.ActiveDocument` and `Gui.ActiveDocument` so background
        library scanning never clobbers the user's open SheetMetal document in FreeCAD.
        """
        try:
            import FreeCAD as App
        except ImportError as exc:
            raise ToolingError(
                f"FreeCAD is required to extract 2D profile from {fcstd_path.name}"
            ) from exc

        prev_active_doc = getattr(App, "ActiveDocument", None)
        prev_active_name = getattr(prev_active_doc, "Name", None)
        t0 = time.perf_counter()
        doc = App.openDocument(str(fcstd_path), True)
        try:
            target_obj = doc.getObject("Body") or doc.getObject("Pad") or doc.getObject("Sketch")
            if target_obj is None:
                for o in doc.Objects:
                    if hasattr(o, "Shape") and not o.Shape.isNull() and o.Shape.Solids:
                        target_obj = o
                        break
            if target_obj is None:
                raise ToolingError(f"No valid 3D Solid or Sketch found in {fcstd_path.name}")
            prof = import_tool_from_freecad_object(
                target_obj,
                kind=kind,
                name=fcstd_path.stem,
                tool_id=fcstd_path.stem,
                length_axis="Z",
            )
            prof.source_format = "FCSTD"
            prof.source_file = str(fcstd_path)
            dt_ms = (time.perf_counter() - t0) * 1000.0
            prof.metrics = replace(prof.metrics, import_time_ms=dt_ms)
            return prof
        finally:
            try:
                App.closeDocument(doc.Name)
            except Exception as exc:
                raise ToolingError(f"Failed to close temporary tool document {doc.Name}: {exc}") from exc
            if prev_active_name is not None and prev_active_name in App.listDocuments():
                App.setActiveDocument(prev_active_name)

    def list_tools(self, kind: Optional[Union[ToolKind, str]] = None) -> Tuple[ToolProfile, ...]:
        """List all saved `ToolProfile` entries in the library."""
        kinds = (
            (ToolKind(kind.upper()) if isinstance(kind, str) else kind,)
            if kind is not None
            else (ToolKind.PUNCH, ToolKind.DIE)
        )
        results: List[ToolProfile] = []
        for tk in kinds:
            kdir = self._kind_dir(tk)
            if not kdir.is_dir():
                continue
            seen_ids = set()
            for item in sorted(kdir.iterdir()):
                if item.name.endswith(self.PROFILE_SUFFIX):
                    tid = item.name[: -len(self.PROFILE_SUFFIX)]
                    try:
                        results.append(self.load_tool(tid, tk))
                        seen_ids.add(tid)
                    except ToolingError:
                        continue
            # Also include legacy .FCStd tools if FreeCAD is available and not already saved as .profile25d.json
            for item in sorted(kdir.iterdir()):
                if item.suffix.lower() == ".fcstd" and item.stem not in seen_ids:
                    try:
                        results.append(self.load_tool(item.stem, tk))
                        seen_ids.add(item.stem)
                    except ToolingError:
                        continue
        return tuple(results)

    def delete_tool(self, tool_id: str, kind: Union[ToolKind, str], confirmed: bool = False) -> None:
        """Delete a user tool profile from the library (requires `confirmed=True`)."""
        if not confirmed:
            raise ToolingError("Tool deletion requires explicit confirmation (confirmed=True)")
        tk = ToolKind(kind.upper()) if isinstance(kind, str) else kind
        tid = self._validate_tool_id(tool_id)
        p_path = self.profile_path(tid, tk)
        if not p_path.is_file():
            raise ToolingError(f"Tool profile '{tid}' ({tk.value}) not found at {p_path}")
        p_path.unlink()
        self._cache.pop((tk.value, tid), None)
