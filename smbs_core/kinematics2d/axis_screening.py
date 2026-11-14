"""Conservative bend-axis grouping and longitudinal probe locations.

Pure geometry only: neither sample intersections nor their absence can
certify a 3D trajectory. Coordinates are authoritative FoldState millimetres.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import acos, degrees, isfinite, sqrt
from typing import Iterable, Mapping, Sequence

Vec3 = tuple[float, float, float]
Axis = tuple[Vec3, Vec3]


def _point(raw: Sequence[float]) -> Vec3:
    if len(raw) != 3:
        raise ValueError("axis endpoint must contain three coordinates")
    p = tuple(float(x) for x in raw)
    if not all(isfinite(x) for x in p):
        raise ValueError("axis endpoint must be finite")
    return p


def _dot(a: Vec3, b: Vec3) -> float:
    return sum(a[i] * b[i] for i in range(3))


def _unit(axis: Axis) -> Vec3:
    a, b = _point(axis[0]), _point(axis[1])
    v = tuple(b[i] - a[i] for i in range(3))
    length = sqrt(_dot(v, v))
    if length <= 1e-9:
        raise ValueError("zero-length bend axis")
    u = tuple(x / length for x in v)
    for component in u:
        if abs(component) > 1e-12:
            if component < 0:
                u = tuple(-x for x in u)
            break
    return u


@dataclass(frozen=True)
class AxisGroup:
    group_id: str
    direction: Vec3
    bend_ids: tuple[str, ...]


def group_parallel_bends(
    axes: Mapping[str, Axis], *, angular_tolerance_deg: float = 1.0
) -> tuple[AxisGroup, ...]:
    """Endpoint-order invariant; compare with all group members to avoid drift."""
    if not isfinite(angular_tolerance_deg) or not 0 <= angular_tolerance_deg < 90:
        raise ValueError("angular_tolerance_deg must be finite and in [0, 90)")
    grouped: list[list[tuple[str, Vec3]]] = []
    for bend_id in sorted(axes):
        unit = _unit(axes[bend_id])
        for group in grouped:
            if all(degrees(acos(min(1.0, abs(_dot(unit, other))))) <= angular_tolerance_deg
                   for _, other in group):
                group.append((bend_id, unit))
                break
        else:
            grouped.append([(bend_id, unit)])
    return tuple(AxisGroup(f"AXIS_{i+1:02d}", group[0][1],
                           tuple(bid for bid, _ in group))
                 for i, group in enumerate(grouped))


def active_bend_frame(axis: Axis) -> tuple[Vec3, Vec3, Vec3]:
    """Return local origin, longitudinal unit and transverse unit in flat XY."""
    a, b = _point(axis[0]), _point(axis[1])
    u = _unit((a, b))
    if abs(u[2]) > 1e-6:
        raise ValueError("active bend is not in unfolded XY plane")
    origin = tuple((a[i] + b[i]) / 2.0 for i in range(3))
    return origin, u, (-u[1], u[0], 0.0)


def longitudinal_coordinate(point: Sequence[float], axis: Axis) -> float:
    origin, longitudinal, _ = active_bend_frame(axis)
    p = _point(point)
    return _dot(tuple(p[i] - origin[i] for i in range(3)), longitudinal)


def longitudinal_probes(
    axis: Axis,
    *,
    tool_end_positions_mm: Iterable[float] = (),
    panel_vertices_xyz: Iterable[Sequence[float]] = (),
    edge_offset_mm: float = 0.5,
    max_probes: int = 128,
) -> tuple[float, ...]:
    """Suggest longitudinal stations, not evidence of global collision freedom."""
    if not isfinite(edge_offset_mm) or edge_offset_mm <= 0:
        raise ValueError("edge_offset_mm must be finite and positive")
    if max_probes < 1:
        raise ValueError("max_probes must be >= 1")
    a, b = _point(axis[0]), _point(axis[1])
    half = sqrt(sum((b[i] - a[i]) ** 2 for i in range(3))) / 2.0
    anchors = [0.0, -half, half]
    anchors.extend(float(x) for x in tool_end_positions_mm)
    anchors.extend(longitudinal_coordinate(p, axis) for p in panel_vertices_xyz)
    if not all(isfinite(x) for x in anchors):
        raise ValueError("probe positions must be finite")
    probes = {0.0}
    for x in anchors:
        probes.update((x, x - edge_offset_mm, x + edge_offset_mm))
    result = tuple(sorted(probes))
    if len(result) > max_probes:
        raise ValueError(
            f"probe limit exceeded: {len(result)} > {max_probes}; "
            "cannot silently skip risk locations"
        )
    return result


def axes_from_fold_state(fold_state: object) -> dict[str, Axis]:
    bends = getattr(fold_state, "bends", None)
    if not isinstance(bends, Mapping):
        raise ValueError("FoldState.bends missing")
    axes: dict[str, Axis] = {}
    for bend_id, bend in bends.items():
        raw = getattr(bend, "axis_xyz", None)
        if raw is None or len(raw) != 2:
            raise ValueError(f"missing authoritative axis for {bend_id}")
        axes[str(bend_id)] = (_point(raw[0]), _point(raw[1]))
    return axes
