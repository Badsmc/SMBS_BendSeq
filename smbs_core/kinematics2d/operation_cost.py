"""Operation cost and heuristic penalty layer for press-brake sequence planning.

Evaluates technological penalties for physical workpiece handling:
- Sheet overturn / flip (relative to machine +Z_m)
- Horizontal workpiece reorientation / turn (azimuth in X_m-Y_m plane)
- Longitudinal shift along the bed (X_m)
- Active bend line length delta

Physical feasibility (collision detection) is strictly decoupled from cost scoring:
a cheaper candidate never overrides a collision, and an unverified candidate
never receives an approval or low-cost certificate.

All penalties are finite, non-negative, and dimensionless heuristic points.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class OperationWeights:
    """Configurable, non-negative heuristic weights for press brake workpiece handling.

    Units: dimensionless penalty points (cost ranking only).
    """
    turn_fixed: float = 2.0
    turn_per_90_deg: float = 1.0
    flip: float = 8.0
    shift_fixed: float = 1.0
    shift_per_100_mm: float = 0.5
    length_change_fixed: float = 2.0
    length_delta_per_100_mm: float = 0.5
    rotation_deadband_deg: float = 1.0
    shift_deadband_mm: float = 1.0
    length_deadband_mm: float = 0.5

    def __post_init__(self):
        for name in (
            "turn_fixed",
            "turn_per_90_deg",
            "flip",
            "shift_fixed",
            "shift_per_100_mm",
            "length_change_fixed",
            "length_delta_per_100_mm",
            "rotation_deadband_deg",
            "shift_deadband_mm",
            "length_deadband_mm",
        ):
            val = getattr(self, name)
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not isfinite(val) or val < 0:
                raise ValueError(f"Weight '{name}' must be a finite, non-negative number: {val}")
        if self.rotation_deadband_deg >= 180.0:
            raise ValueError("rotation_deadband_deg must be less than 180 degrees")

    def to_dict(self) -> Dict[str, float]:
        return {
            "turn_fixed": float(self.turn_fixed),
            "turn_per_90_deg": float(self.turn_per_90_deg),
            "flip": float(self.flip),
            "shift_fixed": float(self.shift_fixed),
            "shift_per_100_mm": float(self.shift_per_100_mm),
            "length_change_fixed": float(self.length_change_fixed),
            "length_delta_per_100_mm": float(self.length_delta_per_100_mm),
            "rotation_deadband_deg": float(self.rotation_deadband_deg),
            "shift_deadband_mm": float(self.shift_deadband_mm),
            "length_deadband_mm": float(self.length_deadband_mm),
        }


@dataclass(frozen=True)
class WorkpieceMachineSetup:
    """Exact spatial placement metrics for an active bend candidate in Machine Space."""
    bend_id: str
    rotation_deg: float
    flipped: bool
    shift_xm_mm: float
    bend_length_mm: float
    is_complete: bool = True
    note: str = ""

    def __post_init__(self):
        if not isinstance(self.bend_id, str) or not self.bend_id:
            raise ValueError("bend_id must be a non-empty string")
        if not isinstance(self.flipped, bool):
            raise ValueError("flipped must be a boolean")
        for name in ("rotation_deg", "shift_xm_mm", "bend_length_mm"):
            val = getattr(self, name)
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not isfinite(val):
                raise ValueError(f"Numeric finite value required for '{name}': {val}")
        if self.bend_length_mm <= 0:
            raise ValueError("bend_length_mm must be positive")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bend_id": self.bend_id,
            "rotation_deg": round(float(self.rotation_deg), 2),
            "flipped": self.flipped,
            "shift_xm_mm": round(float(self.shift_xm_mm), 2),
            "bend_length_mm": round(float(self.bend_length_mm), 2),
            "is_complete": self.is_complete,
            "note": self.note,
        }


@dataclass(frozen=True)
class StepPenalty:
    """Transition penalty incurred when moving from previous bend setup to current."""
    bend_id: str
    previous_bend_id: Optional[str]
    turn_deg: float
    is_flipped: bool
    shift_delta_mm: float
    length_delta_mm: float
    turn_cost: float
    flip_cost: float
    shift_cost: float
    length_change_cost: float
    is_complete: bool = True

    @property
    def total(self) -> float:
        return self.turn_cost + self.flip_cost + self.shift_cost + self.length_change_cost

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bend_id": self.bend_id,
            "previous_bend_id": self.previous_bend_id,
            "turn_deg": round(float(self.turn_deg), 2),
            "flipped_between_bends": self.is_flipped,
            "shift_delta_mm": round(float(self.shift_delta_mm), 2),
            "length_delta_mm": round(float(self.length_delta_mm), 2),
            "turn_cost": round(float(self.turn_cost), 4),
            "flip_cost": round(float(self.flip_cost), 4),
            "shift_cost": round(float(self.shift_cost), 4),
            "length_change_cost": round(float(self.length_change_cost), 4),
            "total": round(float(self.total), 4),
            "is_complete": self.is_complete,
        }


@dataclass(frozen=True)
class SequencePenalty:
    """Total sequence cost composed of step transition penalties."""
    order: Tuple[str, ...]
    steps: Tuple[StepPenalty, ...]
    total: float
    weights: OperationWeights
    is_complete: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order": list(self.order),
            "total": round(float(self.total), 4),
            "steps": [s.to_dict() for s in self.steps],
            "weights": self.weights.to_dict(),
            "is_complete": self.is_complete,
            "note": "Technological cost ranking only; does not replace or certify 3D physical feasibility.",
        }


def smallest_turn_deg(before: float, after: float) -> float:
    """Compute the shortest planar turn angle in [0, 180] degrees between two headings."""
    if not isfinite(before) or not isfinite(after):
        raise ValueError("Non-finite rotation heading")
    return abs((after - before + 180.0) % 360.0 - 180.0)


def compute_workpiece_machine_setup(
    fold_state,
    active_bend_id: str,
    desired_midpoint_xm: float = 0.0,
    support_plane_zm: float = 0.0,
    align_bisector: bool = True,
) -> WorkpieceMachineSetup:
    """Derive actual spatial placement orientation and dimensions for active_bend_id.

    Calculates:
    - flipped: True if workpiece root face top normal points downward (-Z_m).
    - rotation_deg: azimuth of workpiece in machine horizontal plane.
    - bend_length_mm: physical bend axis line length.
    - shift_xm_mm: longitudinal bed placement.
    """
    from .workpiece_placement_3d import WorkpiecePlacement3D

    if active_bend_id not in getattr(fold_state, "bends", {}):
        return WorkpieceMachineSetup(
            bend_id=active_bend_id,
            rotation_deg=0.0,
            flipped=False,
            shift_xm_mm=float(desired_midpoint_xm),
            bend_length_mm=1.0,
            is_complete=False,
            note="unknown_bend_in_fold_state",
        )

    bend = fold_state.bends[active_bend_id]
    axis = getattr(bend, "axis_xyz", None)
    if axis is None or len(axis) < 2:
        return WorkpieceMachineSetup(
            bend_id=active_bend_id,
            rotation_deg=0.0,
            flipped=False,
            shift_xm_mm=float(desired_midpoint_xm),
            bend_length_mm=1.0,
            is_complete=False,
            note="missing_bend_axis_xyz",
        )

    dx = float(axis[1][0]) - float(axis[0][0])
    dy = float(axis[1][1]) - float(axis[0][1])
    dz = float(axis[1][2]) - float(axis[0][2])
    bend_len = math.sqrt(dx * dx + dy * dy + dz * dz)
    if bend_len <= 1e-6:
        bend_len = 1.0

    try:
        plc = WorkpiecePlacement3D.compute_placement(
            fold_state,
            active_bend_id,
            desired_midpoint_xm=float(desired_midpoint_xm),
            support_plane_zm=float(support_plane_zm),
            align_bisector=bool(align_bisector),
        )
        root_panel = getattr(fold_state, "fixed_panel", None)
        if root_panel is None or root_panel not in fold_state.transforms:
            root_panel = bend.fixed_panel

        p_tf = fold_state.transforms[root_panel]
        # Normal of root panel in machine coordinates
        nz = plc.apply(p_tf.point((0.0, 0.0, 1.0)))
        n0 = plc.apply(p_tf.point((0.0, 0.0, 0.0)))
        norm_z = nz[2] - n0[2]
        flipped = bool(norm_z < -1e-4)

        # In-plane reference vector of root panel in machine coordinates
        vx = plc.apply(p_tf.point((1.0, 0.0, 0.0)))
        vec_x = vx[0] - n0[0]
        vec_y = vx[1] - n0[1]
        azimuth = math.degrees(math.atan2(vec_y, vec_x))

        return WorkpieceMachineSetup(
            bend_id=active_bend_id,
            rotation_deg=float(azimuth),
            flipped=flipped,
            shift_xm_mm=float(desired_midpoint_xm),
            bend_length_mm=float(bend_len),
            is_complete=True,
        )
    except Exception as exc:
        return WorkpieceMachineSetup(
            bend_id=active_bend_id,
            rotation_deg=0.0,
            flipped=False,
            shift_xm_mm=float(desired_midpoint_xm),
            bend_length_mm=float(bend_len),
            is_complete=False,
            note=f"placement_error:{exc}",
        )


def transition_penalty(
    previous: Optional[WorkpieceMachineSetup],
    current: WorkpieceMachineSetup,
    weights: OperationWeights = OperationWeights(),
) -> StepPenalty:
    """Calculate step changeover penalty between previous and current bend setups.

    First operation incurs zero changeover penalty (initial loading is not scored).
    """
    if previous is None:
        return StepPenalty(
            bend_id=current.bend_id,
            previous_bend_id=None,
            turn_deg=0.0,
            is_flipped=False,
            shift_delta_mm=0.0,
            length_delta_mm=0.0,
            turn_cost=0.0,
            flip_cost=0.0,
            shift_cost=0.0,
            length_change_cost=0.0,
            is_complete=current.is_complete,
        )

    turn = smallest_turn_deg(previous.rotation_deg, current.rotation_deg)
    flipped = previous.flipped != current.flipped
    shift_delta = abs(current.shift_xm_mm - previous.shift_xm_mm)
    length_delta = abs(current.bend_length_mm - previous.bend_length_mm)

    turn_cost = (
        weights.turn_fixed + weights.turn_per_90_deg * turn / 90.0
        if turn > weights.rotation_deadband_deg
        else 0.0
    )
    flip_cost = weights.flip if flipped else 0.0
    shift_cost = (
        weights.shift_fixed + weights.shift_per_100_mm * shift_delta / 100.0
        if shift_delta > weights.shift_deadband_mm
        else 0.0
    )
    length_cost = (
        weights.length_change_fixed + weights.length_delta_per_100_mm * length_delta / 100.0
        if length_delta > weights.length_deadband_mm
        else 0.0
    )

    is_complete = previous.is_complete and current.is_complete
    return StepPenalty(
        bend_id=current.bend_id,
        previous_bend_id=previous.bend_id,
        turn_deg=turn,
        is_flipped=flipped,
        shift_delta_mm=shift_delta,
        length_delta_mm=length_delta,
        turn_cost=turn_cost,
        flip_cost=flip_cost,
        shift_cost=shift_cost,
        length_change_cost=length_cost,
        is_complete=is_complete,
    )


def score_sequence(
    order: Sequence[str],
    setups: Union[Dict[str, WorkpieceMachineSetup], Sequence[WorkpieceMachineSetup]],
    weights: OperationWeights = OperationWeights(),
) -> SequencePenalty:
    """Compute total sequence penalty across ordered bend steps."""
    if not order:
        return SequencePenalty((), (), 0.0, weights, is_complete=True)

    steps: List[StepPenalty] = []
    is_complete = True
    prev = None

    if isinstance(setups, (list, tuple)):
        # Exact setup sequence corresponding to the path
        for idx, curr in enumerate(setups):
            bid = order[idx] if idx < len(order) else curr.bend_id
            if curr.bend_id != bid:
                # Fallback if indices mismatch
                pass
            step = transition_penalty(prev, curr, weights)
            if not step.is_complete:
                is_complete = False
            steps.append(step)
            prev = curr
    else:
        for bid in order:
            curr = setups.get(bid)
            if curr is None:
                curr = WorkpieceMachineSetup(
                    bend_id=bid,
                    rotation_deg=0.0,
                    flipped=False,
                    shift_xm_mm=0.0,
                    bend_length_mm=1.0,
                    is_complete=False,
                    note="setup_missing_for_scoring",
                )
            step = transition_penalty(prev, curr, weights)
            if not step.is_complete:
                is_complete = False
            steps.append(step)
            prev = curr

    total = sum(s.total for s in steps)
    return SequencePenalty(
        order=tuple(order),
        steps=tuple(steps),
        total=total,
        weights=weights,
        is_complete=is_complete,
    )

