"""D6.1: deterministic, fail-closed forward sequence search for 2.5D FoldState.

Reuses the authoritative kinematics2d FoldState and existing BendSequenceController
order contract. This is NOT a physical collision oracle: the caller must supply
a verifier of the complete machine operation including setup and transfer.

The older smbs_core.Solvers.DFSBackwardPlanner uses Core.FoldState + B-Rep and
cannot consume kinematics2d.FoldState directly. This module isolates the
state-contract boundary rather than pretending that the two are interchangeable.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Callable, Protocol

from .panel_kinematics import FoldState


REQUIRED_SCOPES = frozenset((
    "SETUP", "APPROACH", "BENDING", "RETRACT",
    "TRANSFER", "TOOL", "DIE", "MACHINE", "SELF", "SUPPORT",
))


class Feasibility(str, Enum):
    CLEAR = "CLEAR"
    COLLISION = "COLLISION"
    UNVERIFIED = "UNVERIFIED"


class SearchStatus(str, Enum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    NO_FEASIBLE_SEQUENCE = "NO_FEASIBLE_SEQUENCE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


@dataclass(frozen=True)
class StepEvidence:
    status: Feasibility
    reason: str
    verified_scopes: frozenset[str] = frozenset()

    def is_certified(self) -> bool:
        return (self.status is Feasibility.CLEAR
                and REQUIRED_SCOPES <= self.verified_scopes)


class StepOracle(Protocol):
    def __call__(self, state: FoldState, bend_id: str,
                 previous_bend_id: str | None) -> StepEvidence: ...


@dataclass(frozen=True)
class SequenceSearchResult:
    status: SearchStatus
    sequence: tuple[str, ...]
    explored_states: int
    checked_steps: int
    rejected: tuple[tuple[str, str], ...]
    diagnostics: tuple[str, ...]
    evidence_level: str = "PHYSICAL_SEQUENCE_NOT_APPROVED_UNLESS_VERIFIED"

    @property
    def success(self) -> bool:
        return self.status is SearchStatus.VERIFIED


def _fresh_state(template: FoldState, completed: frozenset[str]) -> FoldState:
    """Recompute state from flat for every branch, never mutate GUI FoldState."""
    state = FoldState(template.graph, template.fixed_panel)
    for bend in template.ordered_bends:
        if bend.bend_id in completed:
            state.set_angle(bend.bend_id, bend.angle_deg)
    return state


def plan_forward(
    source_state: FoldState,
    oracle: StepOracle,
    *,
    max_states: int = 5000,
    max_checks: int = 100000,
) -> SequenceSearchResult:
    """Search verified step sequences using DFS, memoization and backtracking.

    An oracle must validate the actual whole machine step, not just endpoint
    angles. Only CLEAR with all REQUIRED_SCOPES can advance a certified branch.
    Missing scope evidence becomes UNVERIFIED. No passed-in angle, transform
    or GUI playback state is ever changed.

    A NO_FEASIBLE_SEQUENCE result is only legitimate when *every* candidate
    branch is conclusively COLLISION, not when evidence is missing.
    """
    if not isinstance(source_state, FoldState):
        raise TypeError("source_state must be kinematics2d.FoldState")
    if not callable(oracle):
        raise TypeError("an explicit physical step oracle is required")
    if not isinstance(max_states, int) or max_states <= 0:
        raise ValueError("max_states must be positive integer")
    if not isinstance(max_checks, int) or max_checks <= 0:
        raise ValueError("max_checks must be positive integer")

    order = tuple(b.bend_id for b in source_state.ordered_bends)
    targets = {b.bend_id: b.angle_deg for b in source_state.ordered_bends}
    if (not order or len(set(order)) != len(order)
            or any(not isfinite(v) or abs(v) < 1e-10 for v in targets.values())):
        raise ValueError("invalid rooted bend graph or target angles")

    explored = 0
    checked = 0
    budget_hit = False
    any_unknown = False
    rejected: list[tuple[str, str]] = []
    diagnostics: list[str] = []
    # Last bend is part of the state key: TRANSFER and setup feasibility
    # can depend on the previous station operation.
    dead: set[tuple[frozenset[str], str | None]] = set()
    cached: dict[tuple[frozenset[str], str | None, str], StepEvidence] = {}

    def search(completed: frozenset[str], previous: str | None,
               path: tuple[str, ...]) -> tuple[str, ...] | None:
        nonlocal explored, checked, budget_hit, any_unknown
        if len(completed) == len(order):
            return path
        if explored >= max_states:
            budget_hit = True
            return None
        key = (completed, previous)
        if key in dead:
            return None
        explored += 1
        state = _fresh_state(source_state, completed)

        # Stable ordering; no unsupported symmetry pruning.
        for bid in order:
            if bid in completed:
                continue
            if checked >= max_checks:
                budget_hit = True
                break
            cache_key = (completed, previous, bid)
            evidence = cached.get(cache_key)
            if evidence is None:
                checked += 1
                try:
                    evidence = oracle(state, bid, previous)
                    if not isinstance(evidence, StepEvidence):
                        raise TypeError("oracle must return StepEvidence")
                except Exception as exc:
                    evidence = StepEvidence(Feasibility.UNVERIFIED,
                                            "oracle_error: " + str(exc))
                cached[cache_key] = evidence

            if evidence.status is Feasibility.COLLISION:
                rejected.append((bid, evidence.reason))
                continue
            if not evidence.is_certified():
                any_unknown = True
                reason = (evidence.reason if evidence.status is Feasibility.UNVERIFIED
                          else "missing_scopes:" +
                          ",".join(sorted(REQUIRED_SCOPES - evidence.verified_scopes)))
                rejected.append((bid, reason))
                continue
            result = search(completed | {bid}, bid, path + (bid,))
            if result is not None:
                return result
            if budget_hit:
                break
        if not budget_hit:
            dead.add(key)
        return None

    # A nonflat input is not a valid initial sheet for a forward search.
    if any(abs(v) > 1e-9 for v in source_state.angles.values()):
        return SequenceSearchResult(
            SearchStatus.UNVERIFIED, (), 0, 0, (),
            ("source_state_not_flat_reset_required",),
        )

    found = search(frozenset(), None, ())
    if found is not None:
        status = SearchStatus.VERIFIED
        diagnostics.append("Each step reported complete physical-scope evidence.")
    elif budget_hit:
        status = SearchStatus.BUDGET_EXHAUSTED
        diagnostics.append("Search budget reached; feasibility not determined.")
    elif any_unknown:
        status = SearchStatus.UNVERIFIED
        diagnostics.append("At least one candidate lacks required physical evidence.")
    else:
        status = SearchStatus.NO_FEASIBLE_SEQUENCE
        diagnostics.append("Every explored candidate was conclusively rejected.")
    return SequenceSearchResult(
        status, found or (), explored, checked, tuple(rejected),
        tuple(diagnostics),
    )


def apply_verified_order(controller, result: SequenceSearchResult) -> None:
    """Reuse BendSequenceController order API; never start playback implicitly."""
    if not isinstance(result, SequenceSearchResult) or not result.success:
        raise ValueError("Only a fully verified search result may be installed")
    controller.set_bend_order(result.sequence)
