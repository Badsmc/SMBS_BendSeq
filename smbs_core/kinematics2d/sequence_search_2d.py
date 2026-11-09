"""Experimental, section-only DFS. NEVER invokes native 3D collision validation.

Uses the existing FoldState, Stage A, Stage B, machine-section calculator,
2D polygon contact oracle and tooling setup. This is sampled planar evidence,
not a certified manufacturing plan. 2D interference prunes only within this
explicitly requested planar model; it does not prove 3D impossibility.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from time import perf_counter
from typing import Any, Mapping, Optional, Sequence

from .contact_oracle_2d import ContactStatus
from .axis_screening import axes_from_fold_state, group_parallel_bends
from .operation_cost import (
    OperationWeights, WorkpieceMachineSetup,
    compute_workpiece_machine_setup, score_sequence,
)
from .preliminary_screening import (
    PreliminaryScreeningReport, ScreeningDecision,
    screen_candidate_stage_a, screen_candidate_stage_b,
)


class PlanarSearchStatus(str, Enum):
    SAMPLED_2D_CANDIDATES = "SAMPLED_2D_CANDIDATES_NOT_3D_VALIDATED"
    UNVERIFIED_CANDIDATES = "UNVERIFIED_2D_CANDIDATES"
    EXHAUSTED = "NO_2D_CANDIDATES_EXHAUSTED"
    LIMIT_REACHED = "SEARCH_LIMIT_REACHED"
    INVALID_INPUT = "INVALID_INPUT"


@dataclass(frozen=True)
class PlanarStep:
    bend_id: str
    evidence: str
    reason: str
    sampled_phases: tuple[str, ...]
    contact_statuses: tuple[str, ...]
    setup: Optional[WorkpieceMachineSetup] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "bend_id": self.bend_id, "evidence": self.evidence,
            "reason": self.reason, "sampled_phases": list(self.sampled_phases),
            "contact_statuses": list(self.contact_statuses),
            "machine_setup": self.setup.to_dict() if self.setup else None,
        }


@dataclass(frozen=True)
class PlanarCandidate:
    sequence: tuple[str, ...]
    steps: tuple[PlanarStep, ...]
    evidence: str
    cost: float = 0.0
    cost_complete: bool = False
    group_transitions: int = 0
    numbered_bends: tuple[tuple[int, str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": list(self.sequence), "evidence": self.evidence,
            "steps": [step.to_dict() for step in self.steps],
            "cost": self.cost,
            "cost_complete": self.cost_complete,
            "group_transitions": self.group_transitions,
            "numbered_bends": [
                {"number": n, "bend_id": bid, "axis_group": group}
                for n, bid, group in self.numbered_bends
            ],
        }


@dataclass(frozen=True)
class PlanarSearchReport:
    status: PlanarSearchStatus
    candidates: tuple[PlanarCandidate, ...]
    evaluated_steps: int
    visited_nodes: int
    elapsed_s: float
    rejected_2d_interferences: int
    unverified_steps: int
    limit_reason: str
    warnings: tuple[str, ...]
    evidence: str = "SAMPLED_PLANAR_SECTIONS_ONLY_NOT_3D_CERTIFIED"
    axis_groups: tuple[tuple[str, tuple[str, ...]], ...] = ()
    complete_candidates_examined: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "evidence": self.evidence,
            "candidates": [c.to_dict() for c in self.candidates],
            "evaluated_steps": self.evaluated_steps,
            "visited_nodes": self.visited_nodes,
            "elapsed_s": self.elapsed_s,
            "rejected_2d_interferences": self.rejected_2d_interferences,
            "unverified_steps": self.unverified_steps,
            "limit_reason": self.limit_reason,
            "warnings": list(self.warnings),
            "axis_groups": [{"group_id": name, "bend_ids": list(ids)}
                            for name, ids in self.axis_groups],
            "complete_candidates_examined": self.complete_candidates_examined,
        }


def _classify_step(report: PreliminaryScreeningReport) -> str:
    """Interpret Stage B as planar evidence, not as its 3D-search decision."""
    if report.stage == "STAGE_B_SECTION_REQUIRES_3D":
        return "2D_INTERFERENCE"
    if report.decision is ScreeningDecision.PROVEN_BLOCKING:
        return "INVALID_INPUT"
    statuses = tuple(report.contact_statuses)
    expected_phases = ("APPROACH", "BENDING", "BOTTOM", "RETRACT")
    if (not statuses or any(p not in report.sampled_phases for p in expected_phases)
            or any(s not in (ContactStatus.CLEAR.value, ContactStatus.TOUCH.value) for s in statuses)
            or report.stage != "STAGE_B_PREVIEW"):
        return "UNVERIFIED_2D"
    if report.station_reports:
        previews = tuple(
            item.get("preview", {}).get("status", "UNVERIFIED")
            for item in report.station_reports
        )
        if any(s == "2D_INTERFERENCE_REQUIRES_3D" for s in previews):
            return "2D_INTERFERENCE"
        if any(s != "SAMPLED_SECTIONS_CLEAR_OR_TOUCH_3D_REQUIRED" for s in previews):
            return "UNVERIFIED_2D"
    return "SAMPLED_2D_CLEAR_OR_TOUCH"


def search_bend_sequences_2d(
    session: Any,
    tooling: Any,
    *,
    max_solutions: int = 10,
    max_complete_candidates: int = 1000,
    group_first: bool = True,
    weights: Optional[OperationWeights] = None,
    max_nodes: int = 10000,
    max_evaluations: int = 50000,
    max_search_time_s: float = 60.0,
    fractions: Sequence[float] = tuple(i / 16 for i in range(17)),
    stroke_fractions: Sequence[float] = tuple(i / 8 for i in range(9)),
    match_active_bend_length: bool = True,
    bend_shifts: Optional[Mapping[str, float]] = None,
    multi_station_enabled: bool = False,
    verified_punch_span_xm: Optional[tuple[float, float]] = None,
    verified_die_span_xm: Optional[tuple[float, float]] = None,
    tolerance_mm: float = 0.02,
) -> PlanarSearchReport:
    """Enumerate up to N sampled 2D sequences without ever running Stage C.

    Unknown geometry is retained as UNVERIFIED, never reported as safe.
    A 2D overlap can exclude a sequence *from this planar preview only*.
    FoldState and tooling are restored after every branch and after search.
    """
    start = perf_counter()
    warnings = (
        "Sampled 2D cross sections do not certify a continuous stroke, 3D tool ends, "
        "backgauge, frame or transfer path.",
        "2D overlaps are planar-model rejection only, not proof of a 3D collision.",
    )
    state = getattr(session, "fold_state", None)
    panel_vertices = getattr(session, "panel_vertices_xy", None)
    order = tuple(getattr(session, "bend_order", ()))
    try:
        if state is None or tooling is None or not panel_vertices or not order:
            raise ValueError("missing FoldState, tooling, panel vertices, or bend order")
        if len(set(order)) != len(order) or set(order) != set(state.bends):
            raise ValueError("bend order must contain every FoldState bend exactly once")
        if max_solutions < 1 or max_complete_candidates < 1 or max_nodes < 1 or max_evaluations < 1:
            raise ValueError("positive solution and search limits required")
        if not isfinite(max_search_time_s) or max_search_time_s <= 0:
            raise ValueError("positive finite time budget required")
        if not isfinite(tolerance_mm) or not 1e-5 <= tolerance_mm <= 1.0:
            raise ValueError("invalid planar tolerance")
        fs = tuple(sorted(set(float(x) for x in fractions)))
        if not fs or fs[0] != 0.0 or fs[-1] != 1.0 or any(not isfinite(x) or x < 0 or x > 1 for x in fs):
            raise ValueError("fractions must include 0 and 1 and remain within [0,1]")
        stroke_fs = tuple(sorted(set(float(x) for x in stroke_fractions)))
        if (not stroke_fs or stroke_fs[0] != 0.0 or stroke_fs[-1] != 1.0
                or any(not isfinite(x) or x < 0 or x > 1 for x in stroke_fs)):
            raise ValueError("stroke_fractions must contain [0,1] endpoints")
        axis_groups_raw = group_parallel_bends(axes_from_fold_state(state))
        axis_groups = tuple((g.group_id, g.bend_ids) for g in axis_groups_raw)
        group_of = {bid: group_id for group_id, ids in axis_groups for bid in ids}
        if set(group_of) != set(order):
            raise ValueError("axis grouping does not cover every bend")
        op_weights = weights if weights is not None else OperationWeights()
        shifts = dict(bend_shifts or {})
        if any(b not in state.bends or not isfinite(float(v)) for b, v in shifts.items()):
            raise ValueError("invalid bend shift")
    except (ValueError, TypeError, OverflowError) as exc:
        return PlanarSearchReport(
            PlanarSearchStatus.INVALID_INPUT, (), 0, 0, perf_counter() - start,
            0, 0, str(exc), warnings,
        )

    original_angles = dict(state.angles)
    original_transforms = dict(state.transforms)
    evaluated = visited = rejected = unverified = 0
    stopped = ""
    candidates: list[PlanarCandidate] = []
    complete_candidates_examined = 0
    step_cache: dict[tuple[frozenset[str], str], PlanarStep] = {}

    def eval_step(prefix: tuple[str, ...], bend_id: str) -> PlanarStep:
        nonlocal evaluated, rejected, unverified
        key = (frozenset(prefix), bend_id)
        if key in step_cache:
            return step_cache[key]
        evaluated += 1
        try:
            state.reset_all()
            for completed in prefix:
                state.set_angle(completed, float(state.bends[completed].angle_deg))
            setup = compute_workpiece_machine_setup(
                state, bend_id, desired_midpoint_xm=float(shifts.get(bend_id, 0.0)),
            )
            stage_a = screen_candidate_stage_a(
                state, bend_id, tooling,
                match_active_bend_length=match_active_bend_length,
                desired_midpoint_xm=float(shifts.get(bend_id, 0.0)),
            )
            if stage_a.decision is ScreeningDecision.PROVEN_BLOCKING:
                step = PlanarStep(bend_id, "2D_INTERFERENCE",
                                  stage_a.reason, (), (), setup)
            else:
                stage_b = screen_candidate_stage_b(
                    state, bend_id, tooling, panel_vertices,
                    desired_midpoint_xm=float(shifts.get(bend_id, 0.0)),
                    match_active_bend_length=match_active_bend_length,
                    tolerance_mm=tolerance_mm, fractions=fs,
                    stroke_fractions=stroke_fs,
                    multi_station_enabled=multi_station_enabled,
                    verified_punch_span_xm=verified_punch_span_xm,
                    verified_die_span_xm=verified_die_span_xm,
                )
                evidence = _classify_step(stage_b)
                step = PlanarStep(
                    bend_id, evidence, stage_b.reason,
                    stage_b.sampled_phases, stage_b.contact_statuses, setup,
                )
        except Exception as exc:
            step = PlanarStep(
                bend_id, "UNVERIFIED_2D",
                "planar_evaluation_error:" + type(exc).__name__ + ":" + str(exc), (), (),
            )
        finally:
            state.angles = dict(original_angles)
            state.transforms = dict(original_transforms)
        if step.evidence in ("2D_INTERFERENCE", "INVALID_INPUT"):
            rejected += 1
        elif step.evidence == "UNVERIFIED_2D":
            unverified += 1
        step_cache[key] = step
        return step

    def dfs(prefix: tuple[str, ...], steps: tuple[PlanarStep, ...]) -> None:
        nonlocal visited, stopped, complete_candidates_examined
        if stopped:
            return
        if complete_candidates_examined >= max_complete_candidates:
            stopped = "MAX_COMPLETE_CANDIDATES_REACHED"
            return
        if perf_counter() - start >= max_search_time_s:
            stopped = "MAX_SEARCH_TIME_REACHED"
            return
        if visited >= max_nodes:
            stopped = "MAX_NODES_REACHED"
            return
        visited += 1
        if len(prefix) == len(order):
            evidence = ("SAMPLED_2D_CLEAR_OR_TOUCH"
                        if all(s.evidence == "SAMPLED_2D_CLEAR_OR_TOUCH" for s in steps)
                        else "UNVERIFIED_2D")
            setups = [step.setup for step in steps]
            if all(setup is not None for setup in setups):
                score = score_sequence(prefix, setups, op_weights)
                cost, cost_complete = score.total, score.is_complete
            else:
                cost, cost_complete = 0.0, False
            numbered = tuple((i + 1, bid, group_of[bid])
                             for i, bid in enumerate(prefix))
            transitions = sum(
                numbered[i - 1][2] != numbered[i][2]
                for i in range(1, len(numbered))
            )
            candidates.append(PlanarCandidate(
                prefix, steps, evidence, cost, cost_complete,
                transitions, numbered,
            ))
            complete_candidates_examined += 1
            return
        remaining = [bid for bid in order if bid not in prefix]
        if group_first and prefix:
            prior_group = group_of[prefix[-1]]
            remaining.sort(key=lambda bid: (group_of[bid] != prior_group,
                                            group_of[bid], bid))
        elif group_first:
            remaining.sort(key=lambda bid: (group_of[bid], bid))
        for bend_id in remaining:
            if evaluated >= max_evaluations:
                stopped = "MAX_EVALUATIONS_REACHED"
                return
            if perf_counter() - start >= max_search_time_s:
                stopped = "MAX_SEARCH_TIME_REACHED"
                return
            step = eval_step(prefix, bend_id)
            if step.evidence in ("2D_INTERFERENCE", "INVALID_INPUT"):
                continue
            dfs(prefix + (bend_id,), steps + (step,))
            if stopped:
                return

    try:
        dfs((), ())
    finally:
        state.angles = original_angles
        state.transforms = original_transforms

    # Cost ranking applies to all completed candidates inspected within bounds.
    # Incomplete setup metrics never outrank complete measured costs.
    candidates.sort(key=lambda c: (
        c.evidence != "SAMPLED_2D_CLEAR_OR_TOUCH",
        not c.cost_complete, c.cost, c.group_transitions, c.sequence,
    ))
    candidates = candidates[:max_solutions]
    fully_sampled = any(c.evidence == "SAMPLED_2D_CLEAR_OR_TOUCH" for c in candidates)
    if stopped:
        status = PlanarSearchStatus.LIMIT_REACHED
    elif fully_sampled:
        status = PlanarSearchStatus.SAMPLED_2D_CANDIDATES
    elif candidates:
        status = PlanarSearchStatus.UNVERIFIED_CANDIDATES
    else:
        status = PlanarSearchStatus.EXHAUSTED
    return PlanarSearchReport(
        status, tuple(candidates), evaluated, visited, perf_counter()-start,
        rejected, unverified, stopped, warnings,
        axis_groups=axis_groups, complete_candidates_examined=complete_candidates_examined,
    )
