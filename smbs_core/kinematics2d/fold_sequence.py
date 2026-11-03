"""Deterministic cumulative fold sequence playback.

Rooted traversal is a visualization order, not a manufacturing plan.
Each seek recomputes the pose from flat to avoid state drift.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite
from .panel_kinematics import FoldState

class SequenceError(ValueError):
    pass

@dataclass(frozen=True)
class SequenceFrame:
    step: int
    fraction: float
    active_bend: str
    angles: tuple
    transforms: dict

class FoldSequenceController:
    def __init__(self, graph, fixed_panel, order=None):
        self.graph = graph
        self.fixed_panel = fixed_panel
        rooted = graph.rooted(fixed_panel)
        known = {bend.bend_id for bend in rooted}
        self.order = tuple(order) if order is not None else tuple(bend.bend_id for bend in rooted)
        if len(self.order) != len(known) or set(self.order) != known:
            raise SequenceError("Sequence must include every bend exactly once")
        self.targets = {bend.bend_id: bend.angle_deg for bend in rooted}

    @property
    def steps(self):
        return len(self.order)

    def frame(self, position):
        if not isfinite(position) or not 0 <= position <= self.steps:
            raise SequenceError("Position outside sequence")
        state = FoldState(self.graph, self.fixed_panel)
        full = min(int(position), self.steps)
        fraction = position - full
        for bend_id in self.order[:full]:
            state.set_angle(bend_id, self.targets[bend_id])
        active = None
        if full < self.steps:
            active = self.order[full]
            if fraction > 1e-12:
                state.set_angle(active, self.targets[active] * fraction)
        return SequenceFrame(full, fraction, active,
                             tuple((bend, state.angles[bend]) for bend in self.order),
                             state.snapshot())
