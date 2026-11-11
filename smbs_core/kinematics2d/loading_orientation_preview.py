"""Operator-facing orientation for *unverified* manual/canonical playback.

This is a deterministic ergonomic display preference, not a collision,
fixture, rear-face contact or machine-access certification. Search-selected
orientation must always supersede this preview-only fallback.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping, Sequence


@dataclass(frozen=True)
class LoadingOrientationPreview:
    bend_id: str
    reverse_axis: bool
    operator_reach_mm: float | None
    rear_reach_mm: float | None
    status: str

    def to_dict(self) -> dict:
        return {
            "bend_id": self.bend_id,
            "reverse_axis": self.reverse_axis,
            "operator_reach_mm": self.operator_reach_mm,
            "rear_reach_mm": self.rear_reach_mm,
            "status": self.status,
        }


def plan_preview_loading_orientations(
    state, panel_vertices_xy: Mapping[str, Sequence[tuple[float, float]]],
    order: Sequence[str], *, deadband_mm: float = 10.0,
) -> tuple[LoadingOrientationPreview, ...]:
    """Plan 180° machine-Z rotations along the supplied bending sequence.

    Computes each placement with previously completed bends *in an isolated
    FoldState*. The orientation maximising the operator-side projection
    is preferred, provided it is more operator-friendly than the other by
    the configured deadband. No geometric collision or rear stop is checked.

    The result should NEVER be interpreted as an approved setup. Only an
    explicit selected search candidate can supply checked 2D/CAD evidence.
    """
    from .operation_cost import compute_workpiece_machine_setup
    from .panel_kinematics import FoldState

    if not isfinite(deadband_mm) or deadband_mm < 0:
        raise ValueError("nonnegative finite deadband required")
    if len(order) != len(set(order)):
        raise ValueError("duplicate bends in playback order")
    if any(b not in state.bends for b in order):
        raise ValueError("unknown bend in playback order")

    isolated = FoldState(state.graph, state.fixed_panel)
    result: list[LoadingOrientationPreview] = []
    for bend_id in order:
        zone = isolated.bends[bend_id].bend_zone
        z = float(zone.outer_radius_mm) if zone is not None else 0.0
        setups = []
        for reverse in (False, True):
            try:
                setup = compute_workpiece_machine_setup(
                    isolated, bend_id, support_plane_zm=z,
                    align_bisector=False, reverse_axis=reverse,
                    rear_panel_vertices_xy=panel_vertices_xy,
                )
            except (ValueError, TypeError, KeyError, AttributeError):
                # An unavailable pose is NOT evidence that this orientation
                # is physically obstructed. Keep the decision unverified.
                continue
            front, rear = setup.operator_reach_mm, setup.rear_reach_mm
            if (setup.is_complete and front is not None and rear is not None
                    and isfinite(front) and isfinite(rear)):
                setups.append((reverse, float(front), float(rear)))
        if not setups:
            result.append(LoadingOrientationPreview(
                bend_id, False, None, None, "UNVERIFIED_POSE_UNAVAILABLE",
            ))
        else:
            normal = next((v for v in setups if not v[0]), None)
            # Prefer the largest difference (operator projection minus rear).
            # When both orientations are effectively equivalent, retain the
            # normal orientation to avoid gratuitous flip animations.
            best = max(setups, key=lambda v: (v[1] - v[2], not v[0]))
            if (normal is not None and best[0]
                    and (best[1] - best[2]) - (normal[1] - normal[2])
                    <= deadband_mm):
                best = normal
            result.append(LoadingOrientationPreview(
                bend_id, best[0], best[1], best[2],
                "ERGONOMIC_ONLY_NOT_CAD_VALIDATED",
            ))
        isolated.set_angle(bend_id, float(isolated.bends[bend_id].angle_deg))
    return tuple(result)
