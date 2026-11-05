"""Optional D7 weighted forward planner atop the existing D6 physical oracle.

No change to FreeCAD GUI and no additional geometry engine. The optimizer
scores only *physically accepted* branches, and never turns unknown geometry
into a certified solution. It searches the best route for one explicitly
specified machine setup per bend; future setup enumeration is a separate task.

This function does not replace D6 plan_forward; it can be enabled when the
machine placement engine supplies rotation/flip values for every bend.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import inf, isfinite

from .operation_cost import BendSetup, OperationWeights, SequencePenalty, score_sequence, transition_penalty
from .panel_kinematics import FoldState
from .sequence_search import (
    Feasibility, SearchStatus, StepEvidence, StepOracle, _fresh_state,
)


@dataclass(frozen=True)
class WeightedSearchResult:
    status: SearchStatus
    sequence: tuple[str, ...]
    candidate_sequence: tuple[str, ...]
    best_verified_so_far: tuple[str, ...]
    cost: SequencePenalty | None
    optimality_proven: bool
    explored_states: int
    checked_steps: int
    diagnostics: tuple[str, ...]

    @property
    def success(self):
        return self.status is SearchStatus.VERIFIED and bool(self.sequence)


def plan_weighted_forward(
    source_state: FoldState,
    oracle: StepOracle,
    setups: dict[str, BendSetup],
    weights: OperationWeights = OperationWeights(),
    *,
    max_states: int = 5000,
    max_checks: int = 100000,
    allow_partial_preview: bool = False,
) -> WeightedSearchResult:
    """Branch-and-bound search optimizing cost, not just first-found route.

    Only fully certified StepEvidence can produce verified sequences.
    A partial 2.5D preview remains an unverified candidate, even if cheap.
    Pruning is safe because all weights are nonnegative and one fixed setup is
    provided per bend. Invalid/missing setup data is rejected up front.
    """
    if not isinstance(source_state, FoldState) or not callable(oracle):
        raise TypeError("FoldState and step oracle required")
    if not isinstance(weights, OperationWeights):
        raise TypeError("OperationWeights required")
    if not isinstance(max_states, int) or max_states <= 0:
        raise ValueError("max_states must be positive")
    if not isinstance(max_checks, int) or max_checks <= 0:
        raise ValueError("max_checks must be positive")

    bends = tuple(b.bend_id for b in source_state.ordered_bends)
    if not bends or len(set(bends)) != len(bends):
        raise ValueError("invalid_bend_set")
    # Strict input contract; 2D bend angle or sign cannot invent machine
    # flip/rotation. Missing setups fail, not silently incur zero penalties.
    if (not isinstance(setups, dict) or set(setups) != set(bends)
            or any(not isinstance(setups[b], BendSetup) or
                   setups[b].bend_id != b for b in bends)):
        raise ValueError("Complete explicit machine setup required for each bend")
    if any(not isfinite(b.angle_deg) or abs(b.angle_deg) < 1e-10
           for b in source_state.ordered_bends):
        raise ValueError("invalid_target_angle")
    if any(abs(x) > 1e-9 for x in source_state.angles.values()):
        return WeightedSearchResult(
            SearchStatus.UNVERIFIED, (), (), (), None, False, 0, 0,
            ("source_state_not_flat_reset_required",)
        )

    from .sequence_search import REQUIRED_SCOPES
    explored, checked = 0, 0
    unknown = False
    exhausted = False
    best_verified = ()
    best_verified_cost = inf
    best_preview = ()
    best_preview_cost = inf
    memo_best: dict[tuple[frozenset[str], str | None, bool], float] = {}
    evidence_cache: dict[tuple[frozenset[str], str | None, str], StepEvidence] = {}
    diagnostics = []

    def visit(completed, previous, path, cost, partial):
        nonlocal explored, checked, unknown, exhausted
        nonlocal best_verified, best_verified_cost, best_preview, best_preview_cost
        if exhausted:
            return
        if len(completed) == len(bends):
            if partial:
                if cost < best_preview_cost - 1e-9 or (
                    abs(cost-best_preview_cost) <= 1e-9 and
                    (not best_preview or path < best_preview)
                ):
                    best_preview, best_preview_cost = path, cost
            elif cost < best_verified_cost - 1e-9 or (
                abs(cost-best_verified_cost) <= 1e-9 and
                (not best_verified or path < best_verified)
            ):
                best_verified, best_verified_cost = path, cost
            return
        if explored >= max_states:
            exhausted = True
            return

        key = (completed, previous, partial)
        # Preserve lexical deterministic tie-breaking by only discarding a
        # strictly worse route reaching an equivalent state and setup.
        if cost > memo_best.get(key, inf) + 1e-9:
            return
        memo_best[key] = min(cost, memo_best.get(key, inf))
        if not partial and cost > best_verified_cost + 1e-9:
            return
        if partial and cost > best_preview_cost + 1e-9:
            return
        explored += 1
        state = _fresh_state(source_state, completed)

        alternatives = []
        for bid in bends:
            if bid in completed:
                continue
            edge = transition_penalty(setups[previous] if previous else None, setups[bid], weights)
            alternatives.append((edge.total, bid))
        # Lower cost first; all permutations still enumerated until budgets.
        for edge_cost, bid in sorted(alternatives):
            if checked >= max_checks:
                exhausted = True
                return
            cache_key = (completed, previous, bid)
            ev = evidence_cache.get(cache_key)
            if ev is None:
                checked += 1
                try:
                    ev = oracle(state, bid, previous)
                    if not isinstance(ev, StepEvidence):
                        raise TypeError("Expected StepEvidence")
                except Exception as exc:
                    ev = StepEvidence(Feasibility.UNVERIFIED, "oracle_error:" + str(exc))
                evidence_cache[cache_key] = ev
            if ev.status is Feasibility.COLLISION:
                if not ev.verified_scopes:
                    unknown = True
                continue
            certified = ev.is_certified()
            next_partial = partial or not certified
            if not certified:
                unknown = True
                if not (allow_partial_preview and ev.status is Feasibility.CLEAR
                        and bool(ev.verified_scopes)):
                    continue
            visit(completed | {bid}, bid, path + (bid,),
                  cost + edge_cost, next_partial)
            if exhausted:
                return

    visit(frozenset(), None, (), 0.0, False)
    if exhausted:
        status = SearchStatus.BUDGET_EXHAUSTED
        diagnostics.append("Optimality not proven: search budget exhausted.")
    elif best_verified:
        status = SearchStatus.VERIFIED
        diagnostics.append("Best certified sequence for one explicit setup per bend; global optimum proven within this setup model.")
    elif best_preview or unknown:
        status = SearchStatus.UNVERIFIED
        diagnostics.append("No complete machine certification; candidate is preview only.")
    else:
        status = SearchStatus.NO_FEASIBLE_SEQUENCE
        diagnostics.append("Every explored candidate rejected under supplied setup model.")

    selected = best_verified or best_preview
    penalty = score_sequence(selected, setups, weights) if selected else None
    return WeightedSearchResult(
        status,
        best_verified if status is SearchStatus.VERIFIED else (),
        best_preview if status is SearchStatus.UNVERIFIED else (),
        best_verified if status is SearchStatus.BUDGET_EXHAUSTED else (),
        penalty,
        status is SearchStatus.VERIFIED,
        explored, checked, tuple(diagnostics),
    )
