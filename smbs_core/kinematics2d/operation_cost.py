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
    punch_length_change_fixed: float = 4.0
    punch_length_delta_per_100_mm: float = 0.5
    die_length_change_fixed: float = 4.0
    die_length_delta_per_100_mm: float = 0.5
    backgauge_move_fixed: float = 2.0
    backgauge_move_per_100_mm: float = 0.5
    backgauge_height_change_fixed: float = 12.0
    backgauge_height_change_per_10_mm: float = 2.0
    backgauge_height_deadband_mm: float = 0.5
    operator_short_side_fixed: float = 10.0
    operator_short_side_per_100_mm: float = 3.0
    operator_side_deadband_mm: float = 10.0
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
            "punch_length_change_fixed",
            "punch_length_delta_per_100_mm",
            "die_length_change_fixed",
            "die_length_delta_per_100_mm",
            "backgauge_move_fixed",
            "backgauge_move_per_100_mm",
            "backgauge_height_change_fixed",
            "backgauge_height_change_per_10_mm",
            "backgauge_height_deadband_mm",
            "operator_short_side_fixed",
            "operator_short_side_per_100_mm",
            "operator_side_deadband_mm",
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
            "punch_length_change_fixed": float(self.punch_length_change_fixed),
            "punch_length_delta_per_100_mm": float(self.punch_length_delta_per_100_mm),
            "die_length_change_fixed": float(self.die_length_change_fixed),
            "die_length_delta_per_100_mm": float(self.die_length_delta_per_100_mm),
            "backgauge_move_fixed": float(self.backgauge_move_fixed),
            "backgauge_move_per_100_mm": float(self.backgauge_move_per_100_mm),
            "backgauge_height_change_fixed": float(self.backgauge_height_change_fixed),
            "backgauge_height_change_per_10_mm": float(self.backgauge_height_change_per_10_mm),
            "backgauge_height_deadband_mm": float(self.backgauge_height_deadband_mm),
            "operator_short_side_fixed": float(self.operator_short_side_fixed),
            "operator_short_side_per_100_mm": float(self.operator_short_side_per_100_mm),
            "operator_side_deadband_mm": float(self.operator_side_deadband_mm),
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
    punch_length_mm: Optional[float] = None
    die_length_mm: Optional[float] = None
    punch_length_source: str = "NOT_SPECIFIED"
    die_length_source: str = "NOT_SPECIFIED"
    backgauge_s_mm: Optional[float] = None
    backgauge_z_mm: Optional[float] = None
    operator_reach_mm: Optional[float] = None
    rear_reach_mm: Optional[float] = None
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
        for key in ("backgauge_s_mm", "backgauge_z_mm",
                    "operator_reach_mm", "rear_reach_mm"):
            val = getattr(self, key)
            if val is not None and (
                isinstance(val, bool) or not isinstance(val, (int, float))
                or not isfinite(val)
            ):
                raise ValueError(f"{key} must be finite when provided")
        if ((self.backgauge_s_mm is None) != (self.backgauge_z_mm is None)):
            raise ValueError("backgauge s and z must both be provided or both absent")
        if any(getattr(self, name) is not None and getattr(self, name) < 0
               for name in ("operator_reach_mm", "rear_reach_mm")):
            raise ValueError("operator/rear reach must be nonnegative")
        for key in ("punch_length_mm", "die_length_mm"):
            val = getattr(self, key)
            if val is not None and (
                isinstance(val, bool) or not isinstance(val, (int, float))
                or not isfinite(val) or val <= 0
            ):
                raise ValueError(f"{key} must be finite and positive when provided")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bend_id": self.bend_id,
            "rotation_deg": round(float(self.rotation_deg), 2),
            "flipped": self.flipped,
            "shift_xm_mm": round(float(self.shift_xm_mm), 2),
            "bend_length_mm": round(float(self.bend_length_mm), 2),
            "punch_length_mm": self.punch_length_mm,
            "die_length_mm": self.die_length_mm,
            "punch_length_source": self.punch_length_source,
            "die_length_source": self.die_length_source,
            "backgauge_s_mm": self.backgauge_s_mm,
            "backgauge_z_mm": self.backgauge_z_mm,
            "operator_reach_mm": self.operator_reach_mm,
            "rear_reach_mm": self.rear_reach_mm,
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
    punch_length_delta_mm: Optional[float] = None
    die_length_delta_mm: Optional[float] = None
    punch_length_cost: float = 0.0
    die_length_cost: float = 0.0
    tooling_lengths_from_fixed_profiles: bool = False
    backgauge_move_mm: Optional[float] = None
    backgauge_move_cost: float = 0.0
    backgauge_position_known: bool = False
    backgauge_height_delta_mm: Optional[float] = None
    backgauge_height_cost: float = 0.0
    operator_side_cost: float = 0.0

    @property
    def total(self) -> float:
        return (self.turn_cost + self.flip_cost + self.shift_cost
                + self.length_change_cost + self.punch_length_cost
                + self.die_length_cost + self.backgauge_move_cost
                + self.backgauge_height_cost + self.operator_side_cost)

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
            "punch_length_delta_mm": self.punch_length_delta_mm,
            "die_length_delta_mm": self.die_length_delta_mm,
            "punch_length_cost": round(float(self.punch_length_cost), 4),
            "die_length_cost": round(float(self.die_length_cost), 4),
            "backgauge_move_mm": self.backgauge_move_mm,
            "backgauge_move_cost": round(float(self.backgauge_move_cost), 4),
            "backgauge_position_known": self.backgauge_position_known,
            "backgauge_height_delta_mm": self.backgauge_height_delta_mm,
            "backgauge_height_cost": round(float(self.backgauge_height_cost), 4),
            "operator_side_cost": round(float(self.operator_side_cost), 4),
            "tooling_lengths_from_fixed_profiles": self.tooling_lengths_from_fixed_profiles,
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
    rear_panel_vertices_xy=None,
    reverse_axis: bool = False,
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
            reverse_axis=bool(reverse_axis),
            rear_panel_vertices_xy=rear_panel_vertices_xy,
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

        # Project every known panel vertex onto the machine feed axis.
        # Machine +s = -Y points to the rear gauge; -s points to operator.
        # Use complete current folded state, never global unplaced BBox.
        operator_reach = rear_reach = None
        if rear_panel_vertices_xy is not None:
            s_coordinates = []
            for panel_id, vertices in rear_panel_vertices_xy.items():
                tf = fold_state.transforms.get(panel_id)
                if tf is None:
                    continue
                for x, y in vertices:
                    point = plc.apply(tf.point((float(x), float(y), 0.0)))
                    s_coordinates.append(-float(point[1]))
            if s_coordinates:
                operator_reach = max(0.0, -min(s_coordinates))
                rear_reach = max(0.0, max(s_coordinates))
        return WorkpieceMachineSetup(
            bend_id=active_bend_id,
            rotation_deg=float(azimuth),
            flipped=flipped,
            shift_xm_mm=float(desired_midpoint_xm),
            bend_length_mm=float(bend_len),
            operator_reach_mm=operator_reach,
            rear_reach_mm=rear_reach,
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


def operator_side_penalty(
    current: WorkpieceMachineSetup, weights: OperationWeights,
) -> float:
    """Soft ergonomic penalty: excessive rear reach over the operator reach.

    Both reaches are projected distances from active bend line in machine
    coordinates, not panel lengths or proof of loading safety.
    """
    front, rear = current.operator_reach_mm, current.rear_reach_mm
    if front is None or rear is None:
        return 0.0
    excess = rear - front
    return (
        weights.operator_short_side_fixed
        + weights.operator_short_side_per_100_mm * excess / 100.0
        if excess > weights.operator_side_deadband_mm else 0.0
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
            operator_side_cost=operator_side_penalty(current, weights),
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

    # Physical tools with fixed lengths incur no changeover between bends.
    # Virtual axial stretching models planned reconfiguration only. It is
    # never physical verification of a new tool assembly.
    pdelta = (abs(current.punch_length_mm - previous.punch_length_mm)
              if current.punch_length_mm is not None
              and previous.punch_length_mm is not None else None)
    ddelta = (abs(current.die_length_mm - previous.die_length_mm)
              if current.die_length_mm is not None
              and previous.die_length_mm is not None else None)
    punch_cost = (
        weights.punch_length_change_fixed
        + weights.punch_length_delta_per_100_mm * pdelta / 100.0
        if pdelta is not None and pdelta > weights.length_deadband_mm else 0.0
    )
    die_cost = (
        weights.die_length_change_fixed
        + weights.die_length_delta_per_100_mm * ddelta / 100.0
        if ddelta is not None and ddelta > weights.length_deadband_mm else 0.0
    )
    gauge_known = all(
        p.backgauge_s_mm is not None and p.backgauge_z_mm is not None
        for p in (previous, current)
    )
    # Retain physical 2-axis travel for diagnostics but score S and Z separately.
    gauge_distance = (
        math.hypot(
            current.backgauge_s_mm - previous.backgauge_s_mm,
            current.backgauge_z_mm - previous.backgauge_z_mm,
        ) if gauge_known else None
    )
    gauge_s_delta = (
        abs(current.backgauge_s_mm - previous.backgauge_s_mm)
        if gauge_known else None
    )
    gauge_z_delta = (
        abs(current.backgauge_z_mm - previous.backgauge_z_mm)
        if gauge_known else None
    )
    gauge_cost = (
        weights.backgauge_move_fixed
        + weights.backgauge_move_per_100_mm * gauge_s_delta / 100.0
        if gauge_s_delta is not None and gauge_s_delta > weights.shift_deadband_mm
        else 0.0
    )
    gauge_height_cost = (
        weights.backgauge_height_change_fixed
        + weights.backgauge_height_change_per_10_mm * gauge_z_delta / 10.0
        if gauge_z_delta is not None
        and gauge_z_delta > weights.backgauge_height_deadband_mm
        else 0.0
    )
    is_complete = previous.is_complete and current.is_complete
    tooling_verified = all(
        source == "FIXED_PROFILE"
        for source in (previous.punch_length_source,
                       previous.die_length_source,
                       current.punch_length_source,
                       current.die_length_source)
    )
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
        punch_length_delta_mm=pdelta,
        die_length_delta_mm=ddelta,
        punch_length_cost=punch_cost,
        die_length_cost=die_cost,
        tooling_lengths_from_fixed_profiles=tooling_verified,
        backgauge_move_mm=gauge_distance,
        backgauge_move_cost=gauge_cost,
        backgauge_height_delta_mm=gauge_z_delta,
        backgauge_height_cost=gauge_height_cost,
        backgauge_position_known=gauge_known,
        operator_side_cost=operator_side_penalty(current, weights),
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

