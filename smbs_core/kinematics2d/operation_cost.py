"""D7 operation-cost layer: deterministic weights for press-brake setups.

Physical feasibility is NOT part of these scores. A cheaper route is never
permitted to override a collision. Machine orientation and flip are explicit
inputs supplied by a future setup planner, not inferred from bend sign or
a 2D projected axis. Bend length comes from SheetMetal bend axis geometry.

All costs are non-negative, finite, and dimensionless penalty points.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt


@dataclass(frozen=True)
class OperationWeights:
    """Initial *tunable* heuristic weights, not measured machine seconds."""
    turn_fixed: float = 2.0
    turn_per_90_deg: float = 1.0
    flip: float = 8.0
    length_change_fixed: float = 3.0
    length_delta_per_100_mm: float = 0.5
    rotation_deadband_deg: float = 1.0
    length_deadband_mm: float = 0.5

    def __post_init__(self):
        for field in self.__dataclass_fields__:
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not isfinite(value) or value < 0:
                raise ValueError("Weight must be finite, numeric, non-negative: " + field)
        if self.rotation_deadband_deg >= 180:
            raise ValueError("rotation_deadband_deg must be less than 180")


@dataclass(frozen=True)
class BendSetup:
    """Machine setup required for a particular bend candidate.

    rotation_deg: signed azimuth of longitudinal bend axis *in the machine
                  frame after selecting the actual placement*, not bend angle.
    flipped: whether the sheet is overturned relative to its chosen front
             face/datum. Does not follow from positive/negative bend sign.
    line_length_mm: physical bend line length (not unfolded flange length).
    """
    bend_id: str
    rotation_deg: float
    flipped: bool
    line_length_mm: float

    def __post_init__(self):
        if not isinstance(self.bend_id, str) or not self.bend_id:
            raise ValueError("bend_id required")
        if not isinstance(self.flipped, bool):
            raise ValueError("flipped must be an explicit boolean")
        for name in ("rotation_deg", "line_length_mm"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not isfinite(value):
                raise ValueError("finite numeric " + name + " required")
        if self.line_length_mm <= 0:
            raise ValueError("line_length_mm must be positive")


@dataclass(frozen=True)
class StepPenalty:
    bend_id: str
    previous_bend_id: str | None
    turn_deg: float
    is_flipped: bool
    length_delta_mm: float
    turn_cost: float
    flip_cost: float
    length_change_cost: float

    @property
    def total(self) -> float:
        return self.turn_cost + self.flip_cost + self.length_change_cost

    def to_dict(self) -> dict:
        return {
            "bend_id": self.bend_id,
            "previous_bend_id": self.previous_bend_id,
            "turn_deg": self.turn_deg,
            "flipped_between_bends": self.is_flipped,
            "length_delta_mm": self.length_delta_mm,
            "turn_cost": self.turn_cost,
            "flip_cost": self.flip_cost,
            "length_change_cost": self.length_change_cost,
            "total": self.total,
        }


@dataclass(frozen=True)
class SequencePenalty:
    order: tuple[str, ...]
    steps: tuple[StepPenalty, ...]
    total: float
    weights: OperationWeights

    def to_dict(self) -> dict:
        return {
            "order": list(self.order),
            "total": self.total,
            "steps": [x.to_dict() for x in self.steps],
            "weights": {k: getattr(self.weights, k) for k in self.weights.__dataclass_fields__},
            "note": "Cost ranking only; not evidence of physically valid setup or motion.",
        }


def bend_length_mm(bend) -> float:
    """Measure the actual SheetMetal bend axis, not flange or sheet length."""
    try:
        a, b = bend.axis_xyz
        d = sqrt(sum((float(b[i])-float(a[i]))**2 for i in range(3)))
    except (TypeError, ValueError, IndexError, AttributeError) as exc:
        raise ValueError("invalid_bend_axis") from exc
    if not isfinite(d) or d <= 0:
        raise ValueError("invalid_bend_axis_length")
    return d


def smallest_turn_deg(before: float, after: float) -> float:
    """Shortest rotation between two *machine-frame* azimuth headings."""
    if not isfinite(before) or not isfinite(after):
        raise ValueError("nonfinite_rotation")
    return abs((after - before + 180.0) % 360.0 - 180.0)


def transition_penalty(
    previous: BendSetup | None,
    current: BendSetup,
    weights: OperationWeights = OperationWeights(),
) -> StepPenalty:
    """First bend has zero changeover penalty: loading cost is not modeled."""
    if not isinstance(current, BendSetup) or not isinstance(weights, OperationWeights):
        raise TypeError("Expected BendSetup and OperationWeights")
    if previous is not None and not isinstance(previous, BendSetup):
        raise TypeError("Expected previous BendSetup or None")
    turn = smallest_turn_deg(previous.rotation_deg, current.rotation_deg) if previous else 0.0
    flipped = previous.flipped != current.flipped if previous else False
    length_delta = abs(current.line_length_mm-previous.line_length_mm) if previous else 0.0
    turn_cost = (weights.turn_fixed + weights.turn_per_90_deg * turn / 90.0
                 if turn > weights.rotation_deadband_deg else 0.0)
    flip_cost = weights.flip if flipped else 0.0
    length_cost = (weights.length_change_fixed
                   + weights.length_delta_per_100_mm * length_delta / 100.0
                   if length_delta > weights.length_deadband_mm else 0.0)
    return StepPenalty(current.bend_id, previous.bend_id if previous else None,
                       turn, flipped, length_delta, turn_cost, flip_cost, length_cost)


def score_sequence(
    order: tuple[str, ...],
    setups: dict[str, BendSetup],
    weights: OperationWeights = OperationWeights(),
) -> SequencePenalty:
    """Strict, deterministic total; missing setup data fails, never guesses."""
    if not order or len(order) != len(set(order)):
        raise ValueError("Nonempty sequence with unique bends required")
    if any(bid not in setups or setups[bid].bend_id != bid for bid in order):
        raise ValueError("Explicit machine setup missing or bend_id mismatch")
    penalties = []
    for i, bid in enumerate(order):
        prev = setups[order[i-1]] if i else None
        penalties.append(transition_penalty(prev, setups[bid], weights))
    return SequencePenalty(tuple(order), tuple(penalties),
                           sum(p.total for p in penalties), weights)
