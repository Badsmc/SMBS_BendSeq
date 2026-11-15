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
    places the shorter projected side toward the rear gauge. A deadband can
    suppress a gratuitous flip for nearly equal sides. No collision or
    rear-stop contact is checked.

    The result should NEVER be interpreted as an approved setup. Only an
    explicit selected search candidate can supply checked 2D/CAD evidence.
    """
    from .panel_kinematics import FoldState
    from .workpiece_placement_3d import WorkpiecePlacement3D

    if not isfinite(deadband_mm) or deadband_mm < 0:
        raise ValueError("nonnegative finite deadband required")
    if len(order) != len(set(order)):
        raise ValueError("duplicate bends in playback order")
    if any(b not in state.bends for b in order):
        raise ValueError("unknown bend in playback order")

    isolated = FoldState(state.graph, state.fixed_panel)
    complete_vertices = all(
        len(panel_vertices_xy.get(panel_id, ())) >= 3
        for panel_id in isolated.transforms
    )
    result: list[LoadingOrientationPreview] = []
    for bend_id in order:
        if not complete_vertices:
            result.append(LoadingOrientationPreview(
                bend_id, False, None, None, "UNVERIFIED_POSE_UNAVAILABLE",
            ))
            isolated.set_angle(bend_id, float(isolated.bends[bend_id].angle_deg))
            continue
        zone = isolated.bends[bend_id].bend_zone
        z = float(zone.outer_radius_mm) if zone is not None else 0.0
        setups = []
        for reverse in (False, True):
            try:
                # Match the actual 2D/3D playback pose. The operation-cost
                # helper auto-orients the fixed panel toward the rear when
                # passed vertices; that extra rotation changes the meaning
                # of reverse_axis and can put the LONG side at +s/right.
                placement = WorkpiecePlacement3D.compute_placement(
                    isolated, bend_id, support_plane_zm=z,
                    align_bisector=True, align_bend_arc_center=True,
                    reverse_axis=reverse,
                )
            except (ValueError, TypeError, KeyError, AttributeError):
                # An unavailable pose is NOT evidence that this orientation
                # is physically obstructed. Keep the decision unverified.
                continue
            projected = [
                -placement.apply(isolated.transforms[panel_id].point(
                    (float(x), float(y), 0.0)
                ))[1]
                for panel_id, vertices in panel_vertices_xy.items()
                if panel_id in isolated.transforms
                for x, y in vertices
            ]
            if projected and all(isfinite(s) for s in projected):
                setups.append((reverse, max(0.0, -min(projected)),
                               max(0.0, max(projected))))
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
                ("ERGONOMIC_ONLY_NOT_CAD_VALIDATED"
                 if best[2] <= best[1] + 1e-4 else
                 "UNVERIFIED_SHORT_REAR_POSE_UNAVAILABLE"),
            ))
        isolated.set_angle(bend_id, float(isolated.bends[bend_id].angle_deg))
    return tuple(result)
