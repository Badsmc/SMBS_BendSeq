"""Experimental axis-staged bend ordering with stateful 2D machine sections.

No native 3D calls here. A section interference rejects only the examined
(state, bend, loading) hypothesis, never every sequence using that bend.
The two-level search retains cross-axis interleavings and does not interpret
axis-only successes as a feasible final press-brake plan.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from math import isfinite
from time import perf_counter
from typing import Any, Callable, Mapping

from .axis_screening import axes_from_fold_state, group_parallel_bends
from .operation_cost import (
    OperationWeights, compute_workpiece_machine_setup, transition_penalty,
    score_sequence,
)
from .preliminary_screening import (
    ScreeningDecision, screen_candidate_stage_a, screen_candidate_stage_b,
)
from .sequence_search_2d import (
    PlanarStep, PlanarCandidate, PlanarSearchReport, PlanarSearchStatus,
    _classify_step, _setup_with_scored_tool_lengths,
)


@dataclass(frozen=True)
class _Path:
    order: tuple[str, ...] = ()
    orientations: tuple[bool, ...] = ()
    steps: tuple[PlanarStep, ...] = ()
    cost: float = 0.0
    unknown: int = 0


@dataclass(frozen=True)
class AxisStagedResult:
    planar_report: PlanarSearchReport
    axis_orders: tuple[tuple[str, tuple[tuple[str, ...], ...]], ...]
    broadphase: tuple[dict, ...]
    elapsed_s: float
    evaluations: int
    cache_hits: int
    rejected: int
    exhausted_budget: bool
    transition_trace: tuple[dict, ...] = ()
    combined_orders: tuple[dict, ...] = ()

    def to_dict(self) -> dict:
        return {
            "schema_version": "smbs.axis_staged_experiment.v1",
            "status": self.planar_report.status.value,
            "axis_orders": [
                {"axis": key, "orders": [list(order) for order in orders]}
                for key, orders in self.axis_orders
            ],
            "broadphase": list(self.broadphase),
            "elapsed_s": self.elapsed_s,
            "evaluations": self.evaluations,
            "cache_hits": self.cache_hits,
            "rejected_2d_hypotheses": self.rejected,
            "exhausted_budget": self.exhausted_budget,
            "transition_trace": list(self.transition_trace),
            "combined_orders": list(self.combined_orders),
            "safety": "UNVERIFIED_2D_AND_3D_PREVIEW_NOT_A_MACHINE_CERTIFICATE",
            "planar": self.planar_report.to_dict(),
        }


def _punch_only_section_interference(report: Any) -> bool:
    """A 2D punch-only interference cannot resolve axial CONDITIONAL ends.

    Never classify DIE/BACKGAUGE/mixed findings as conditional. Return
    UNKNOWN for possible end relief; exact native 3D must decide.
    """
    if getattr(report, "stage", "") != "STAGE_B_SECTION_REQUIRES_3D":
        return False
    interfering = [
        tool for _phase, tool, _component, status, _reason
        in getattr(report, "diagnostic_findings", ())
        if status == "INTERFERENCE"
    ]
    return bool(interfering) and all(tool == "PUNCH" for tool in interfering)


def _bend_weight(bend: Any, override: float | None = None) -> float:
    if override is not None:
        if isinstance(override, bool) or not isfinite(float(override)) or override < 0:
            raise ValueError("bend weights must be finite and non-negative")
        return float(override)
    a, b = bend.axis_xyz
    length = sum((float(b[i]) - float(a[i])) ** 2 for i in range(3)) ** 0.5
    return 0.5 * abs(float(bend.angle_deg)) / 90.0 + length / 500.0


def _build_transition_evaluator(
    session, tooling, *, weights, bend_weights, deadline, tolerance,
    bend_fractions, stroke_fractions, allow_end_zone_collision,
    end_zone_length_mm, allow_retract_warning, match_active_bend_length,
    transition_screen=None, clock=perf_counter,
):
    state = session.fold_state
    panels = session.panel_vertices_xy
    cache = {}
    metrics = {"evaluated": 0, "hits": 0, "rejected": 0,
               "trace": [], "max_depth": 0, "rejection_counts": {}}

    def evaluate(prefix: tuple[str, ...], bend_id: str, reverse: bool):
        key = (frozenset(prefix), bend_id, bool(reverse))
        if key in cache:
            metrics["hits"] += 1
            return cache[key]
        if clock() >= deadline:
            return None
        originals = (dict(state.angles), dict(state.transforms))
        try:
            state.reset_all()
            for bid in prefix:
                state.set_angle(bid, float(state.bends[bid].angle_deg))
            bend = state.bends[bend_id]
            setup = compute_workpiece_machine_setup(
                state, bend_id,
                support_plane_zm=float(
                    getattr(getattr(bend, "bend_zone", None),
                            "outer_radius_mm", 0.0)
                ),
                align_bisector=False,
                reverse_axis=reverse,
                rear_panel_vertices_xy=panels,
            )
            setup = _setup_with_scored_tool_lengths(
                setup, tooling, match_active_bend_length,
            )
            if transition_screen is not None:
                evidence, reason = transition_screen(
                    tuple(prefix), bend_id, bool(reverse)
                )
                phases, statuses = (), ()
            else:
                a = screen_candidate_stage_a(
                    state, bend_id, tooling,
                    match_active_bend_length=match_active_bend_length,
                )
                if a.decision is ScreeningDecision.PROVEN_BLOCKING:
                    evidence = "2D_TOOLING_CONSTRAINT"
                    reason = a.reason
                    phases, statuses = (), ()
                else:
                    b = screen_candidate_stage_b(
                        state, bend_id, tooling, panels,
                        match_active_bend_length=match_active_bend_length,
                        allow_end_zone_collision=allow_end_zone_collision,
                        end_zone_length_mm=end_zone_length_mm,
                        allow_retract_warning=allow_retract_warning,
                        tolerance_mm=tolerance,
                        fractions=bend_fractions,
                        stroke_fractions=stroke_fractions,
                        reverse_axis=reverse,
                        deadline_monotonic=deadline,
                    )
                    evidence, reason = _classify_step(b), b.reason
                    if (evidence == "2D_INTERFERENCE"
                            and allow_end_zone_collision
                            and _punch_only_section_interference(b)):
                        evidence = "UNVERIFIED_2D"
                        reason = (
                            "PUNCH_SECTION_END_ZONE_NEEDS_NATIVE_3D:"
                            + str(b.reason)
                        )
                    phases, statuses = b.sampled_phases, b.contact_statuses
            step = PlanarStep(
                bend_id, str(evidence), str(reason), tuple(phases),
                tuple(statuses), setup=setup,
                orientation_variant=(
                    "REVERSE_MACHINE_Z_180" if reverse else "NORMAL"
                ),
            )
            metrics["evaluated"] += 1
            if step.evidence in (
                "2D_INTERFERENCE", "2D_TOOLING_CONSTRAINT",
                "CAD_3D_INTERFERENCE", "INVALID_INPUT",
            ):
                metrics["rejected"] += 1
            cache[key] = step
            metrics["max_depth"] = max(metrics["max_depth"], len(prefix) + 1)
            if len(metrics["trace"]) < 500:
                metrics["trace"].append({
                    "completed_before": list(prefix), "bend_id": bend_id,
                    "reverse_axis": bool(reverse), "evidence": step.evidence,
                    "reason": step.reason,
                    "sampled_phases": list(step.sampled_phases),
                    "contact_statuses": list(step.contact_statuses),
                })
            if step.evidence != "SAMPLED_2D_CLEAR_OR_TOUCH":
                label = step.evidence + ":" + step.reason[:96]
                counts = metrics["rejection_counts"]
                counts[label] = counts.get(label, 0) + 1
            return step
        except Exception as exc:
            # An exception is not a proven obstacle. Explicitly mark unknown.
            step = PlanarStep(
                bend_id, "UNVERIFIED_2D",
                "stage_exception:{}:{}".format(type(exc).__name__, exc),
                (), (), orientation_variant=(
                    "REVERSE_MACHINE_Z_180" if reverse else "NORMAL"
                ),
            )
            cache[key] = step
            metrics["evaluated"] += 1
            if len(metrics["trace"]) < 500:
                metrics["trace"].append({
                    "completed_before": list(prefix), "bend_id": bend_id,
                    "reverse_axis": bool(reverse), "evidence": step.evidence,
                    "reason": step.reason,
                })
            return step
        finally:
            state.angles, state.transforms = originals

    return evaluate, metrics


def _weighted_transition_cost(previous_id, bend_id, previous_setup,
                              current_setup, weights, per_bend_weights) -> float:
    """Charge BOTH neighboring operation weights on each adjacent transition.

    The additive sum of fixed per-bend weights is constant over complete
    permutations, so it cannot by itself rank bend orders. Weight each edge
    by the mean importance of its two endpoints. The outgoing edge of a
    bend is accounted for when the NEXT operation is appended.
    """
    penalty = transition_penalty(previous_setup, current_setup, weights).total
    if previous_id is None:
        return penalty * (1.0 + per_bend_weights[bend_id])
    importance = 0.5 * (
        per_bend_weights[previous_id] + per_bend_weights[bend_id]
    )
    return penalty * (1.0 + importance)


def _advance(path: _Path, bend: str, reverse: bool, step: PlanarStep,
             *, weights, bend_weight, all_bend_weights) -> _Path | None:
    if step.evidence in (
        "2D_INTERFERENCE", "2D_TOOLING_CONSTRAINT",
        "CAD_3D_INTERFERENCE", "INVALID_INPUT",
    ):
        return None
    prev = path.steps[-1].setup if path.steps else None
    cost = float(bend_weight)
    if step.setup is not None:
        cost += _weighted_transition_cost(
            path.order[-1] if path.order else None,
            bend, prev, step.setup, weights, all_bend_weights,
        )
    else:
        cost += 25.0
    unknown = step.evidence != "SAMPLED_2D_CLEAR_OR_TOUCH"
    # Unknown remains searchable, but never reported as sampled-clear.
    if unknown:
        cost += 25.0
    return _Path(
        path.order + (bend,),
        path.orientations + (reverse,),
        path.steps + (step,),
        path.cost + cost,
        path.unknown + int(unknown),
    )


def _rank(paths, limit):
    # Retain at least one alternative path from each distinct first bend.
    ranked = sorted(paths, key=lambda p: (p.unknown, p.cost, p.order,
                                          p.orientations))
    chosen = []
    seen = set()
    for p in ranked:
        key = (p.order, p.orientations)
        if key not in seen:
            chosen.append(p)
            seen.add(key)
        if len(chosen) == limit:
            break
    return chosen


def search_axis_staged_experiment(
    session: Any,
    tooling: Any,
    *,
    weights: OperationWeights | None = None,
    bend_weights: Mapping[str, float] | None = None,
    max_time_s: float = 90.0,
    beam_width: int = 10,
    per_axis_limit: int = 5,
    combined_pool: int = 15,
    finalists: int = 5,
    allow_end_zone_collision: bool = False,
    end_zone_length_mm: float = 0.0,
    allow_retract_warning: bool = False,
    match_active_bend_length: bool = True,
    tolerance_mm: float = 0.02,
    transition_screen: Callable | None = None,
    broadphase_runner: Callable | None = None,
    clock: Callable = perf_counter,
) -> AxisStagedResult:
    """Group by parallel axes, merge interleavings, recheck all bent states.

    Every expensive stage is bounded. The broadphase only ranks possible
    overlaps; it never proves or rejects a collision. There is no automatic
    native OCC approval in this exploratory UI entrypoint.
    """
    if (not isfinite(max_time_s) or max_time_s <= 0 or beam_width < 1
            or per_axis_limit < 1 or combined_pool < 1 or finalists < 1
            or not isfinite(tolerance_mm) or tolerance_mm <= 0):
        raise ValueError("invalid axis-staged search budgets")
    started = clock()
    deadline = started + max_time_s
    # Preserve the final 30% for the 3D preview-risk stage instead of
    # exhausting the entire soft budget on 2D axis/interleaving expansion.
    search_deadline = started + max_time_s * 0.70
    weights = weights or OperationWeights()
    bend_weights = dict(bend_weights or {})
    state = session.fold_state
    order = tuple(session.bend_order)
    if (not order or len(order) != len(set(order))
            or set(order) != set(state.bends)
            or not session.panel_vertices_xy
            or tooling is None or tooling.punch is None or tooling.die is None):
        raise ValueError("selected SheetMetal part and punch/die required")
    if any(b not in state.bends for b in bend_weights):
        raise ValueError("bend weight refers to an unknown bend")
    cost_for = {
        bid: _bend_weight(state.bends[bid], bend_weights.get(bid))
        for bid in order
    }
    axes = group_parallel_bends(axes_from_fold_state(state))
    evaluate, counts = _build_transition_evaluator(
        session, tooling, weights=weights, bend_weights=bend_weights,
        deadline=search_deadline, tolerance=tolerance_mm,
        bend_fractions=(0.0, 0.25, 0.5, 0.75, 1.0),
        stroke_fractions=(0.0, 0.5, 1.0),
        allow_end_zone_collision=allow_end_zone_collision,
        end_zone_length_mm=end_zone_length_mm,
        allow_retract_warning=allow_retract_warning,
        match_active_bend_length=match_active_bend_length,
        transition_screen=transition_screen, clock=clock,
    )
    axis_paths = []
    for group in axes:
        if clock() >= search_deadline:
            break
        bends = tuple(group.bend_ids)
        beam = [_Path()]
        for _ in bends:
            if clock() >= search_deadline:
                break
            expanded = []
            for path in beam:
                for bid in bends:
                    if bid in path.order:
                        continue
                    for reverse in (False, True):
                        step = evaluate(path.order, bid, reverse)
                        if step is None:
                            break
                        nxt = _advance(
                            path, bid, reverse, step, weights=weights,
                            bend_weight=cost_for[bid],
                            all_bend_weights=cost_for,
                        )
                        if nxt is not None:
                            expanded.append(nxt)
            beam = _rank(expanded, beam_width)
            if not beam:
                break
        complete = [p for p in beam if len(p.order) == len(bends)]
        # An axis-only interference is conditional on OTHER AXES being flat.
        # Retain untested alternative axis orders; the combined search must
        # recheck them with the actual cross-axis folded state.
        if not complete:
            candidates = [
                bends, tuple(reversed(bends)),
                tuple(sorted(bends, key=lambda bid: (cost_for[bid], bid))),
            ]
            complete = [
                _Path(order=tuple(o), cost=1000.0)
                for o in dict.fromkeys(candidates)
            ]
        axis_paths.append((group.group_id, _rank(complete, per_axis_limit)))

    merged = []
    if len(axis_paths) == len(axes):
        # The axis orders are priorities, not a substitute for cross-axis
        # geometry. Every interleaved operation is freshly screened with
        # the entire previously folded set.
        for choices in product(*(paths for _, paths in axis_paths)):
            if clock() >= search_deadline:
                break
            positions_start = tuple(0 for _ in choices)
            frontier = [(_Path(), positions_start)]
            for depth in range(len(order)):
                if clock() >= search_deadline:
                    break
                expanded = []
                for path, pos in frontier:
                    for gi, axis_order in enumerate(choices):
                        if pos[gi] >= len(axis_order.order):
                            continue
                        bend = axis_order.order[pos[gi]]
                        for reverse in (False, True):
                            step = evaluate(path.order, bend, reverse)
                            if step is None:
                                break
                            nxt = _advance(
                                path, bend, reverse, step,
                                weights=weights, bend_weight=cost_for[bend],
                                all_bend_weights=cost_for,
                            )
                            if nxt is not None:
                                newpos = list(pos)
                                newpos[gi] += 1
                                expanded.append((nxt, tuple(newpos)))
                expanded.sort(key=lambda x: (
                    x[0].unknown, x[0].cost, x[0].order, x[0].orientations
                ))
                frontier = []
                seen = set()
                for entry in expanded:
                    key = (entry[0].order, entry[0].orientations)
                    if key not in seen:
                        seen.add(key)
                        frontier.append(entry)
                    if len(frontier) >= beam_width:
                        break
                if not frontier:
                    break
            merged.extend(p for p, _ in frontier if len(p.order) == len(order))
            if len(merged) >= combined_pool * 2:
                break
    merged = _rank(merged, combined_pool)
    broadphase = []
    if broadphase_runner is None and transition_screen is None:
        from .axis_staged_broadphase_3d import preview_mesh_aabb_risk
        broadphase_runner = preview_mesh_aabb_risk
    for p in merged:
        if clock() >= deadline:
            break
        if broadphase_runner is None:
            evaluation = {"status": "NOT_RUN", "possible_overlap_samples": 0}
        else:
            try:
                evaluation = broadphase_runner(
                    session, tooling, p.order,
                    dict(zip(p.order, p.orientations)),
                    match_active_bend_length=match_active_bend_length,
                    deadline=deadline,
                )
            except Exception as exc:
                evaluation = {
                    "status": "UNVERIFIED",
                    "reason": type(exc).__name__ + ":" + str(exc),
                    "possible_overlap_samples": 0,
                }
        broadphase.append({
            "sequence": list(p.order),
            "orientation_by_bend": dict(zip(p.order, p.orientations)),
            **evaluation,
        })
    risks = {
        (tuple(x["sequence"]),
         tuple(bool(x["orientation_by_bend"][bid]) for bid in x["sequence"])): x
        for x in broadphase
    }
    # Unchecked 3D preview is *not* silently preferable to checked risk.
    merged.sort(key=lambda p: (
        p.unknown,
        1 if (p.order, p.orientations) not in risks
        or risks[(p.order, p.orientations)].get("status") == "UNVERIFIED" else 0,
        risks.get((p.order, p.orientations), {}).get(
            "possible_overlap_samples", 0
        ),
        p.cost, p.order, p.orientations,
    ))
    winning = merged[:finalists]
    group_of = {
        bend: a.group_id for a in axes for bend in a.bend_ids
    }
    candidates = []
    for p in winning:
        scored = score_sequence(
            p.order, [step.setup for step in p.steps], weights
        ) if all(step.setup is not None for step in p.steps) else None
        candidates.append(PlanarCandidate(
            sequence=p.order,
            steps=p.steps,
            evidence=("UNVERIFIED_2D_CANDIDATES" if p.unknown
                      else "SAMPLED_2D_CLEAR_OR_TOUCH"),
            cost=p.cost,
            cost_complete=bool(scored and scored.is_complete and not p.unknown),
            group_transitions=sum(
                group_of[p.order[i]] != group_of[p.order[i-1]]
                for i in range(1, len(p.order))
            ),
            numbered_bends=tuple(
                (i+1, bid, group_of[bid]) for i, bid in enumerate(p.order)
            ),
            cost_breakdown=tuple(
                {
                    **part.to_dict(),
                    "individual_bend_weight": cost_for[p.order[i]],
                    "weighted_transition_cost": _weighted_transition_cost(
                        p.order[i - 1] if i else None,
                        p.order[i],
                        p.steps[i - 1].setup if i else None,
                        p.steps[i].setup, weights, cost_for,
                    ),
                }
                for i, part in enumerate(scored.steps)
            ) if scored else (),
        ))
    elapsed = clock() - started
    report = PlanarSearchReport(
        status=(PlanarSearchStatus.SAMPLED_2D_CANDIDATES
                if candidates and all(p.unknown == 0 for p in winning)
                else PlanarSearchStatus.UNVERIFIED_CANDIDATES
                if candidates else PlanarSearchStatus.LIMIT_REACHED
                if clock() >= deadline else PlanarSearchStatus.EXHAUSTED),
        candidates=tuple(candidates),
        evaluated_steps=counts["evaluated"],
        visited_nodes=counts["evaluated"] + counts["hits"],
        elapsed_s=elapsed,
        rejected_2d_interferences=counts["rejected"],
        unverified_steps=sum(p.unknown for p in winning),
        limit_reason=("MAX_SEARCH_TIME_REACHED" if clock() >= deadline
                      else "NO_COMPLETE_INTERLEAVING_WITHIN_2D_BUDGET"
                      if not candidates and clock() >= search_deadline
                      else "NO_COMPLETE_INTERLEAVING" if not candidates
                      else "FINALISTS_PRELIMINARY_ONLY"),
        warnings=(
            "Axis-only geometries are tentative; every interleaving is re-screened.",
            "2D sampled collision rejects only the tested pose and state.",
            "3D preview AABB overlapping faces is ranking only; no physical proof.",
            "No native continuous 3D or backgauge/frame/clamp clearance certified.",
        ),
        axis_groups=tuple((a.group_id, a.bend_ids) for a in axes),
        complete_candidates_examined=len(merged),
        operation_weights=weights.to_dict(),
        conditional_end_zone_requested=allow_end_zone_collision,
        max_depth_reached=(
            len(order) if candidates else counts["max_depth"]
        ),
        deepest_prefix=(
            winning[0].order if candidates else
            tuple(max((p.order for _, paths in axis_paths for p in paths),
                      key=len, default=()))
        ),
        rejection_counts=tuple(sorted(
            counts["rejection_counts"].items(),
            key=lambda item: (-item[1], item[0]),
        )[:30]),
    )
    return AxisStagedResult(
        planar_report=report,
        axis_orders=tuple(
            (name, tuple(p.order for p in paths))
            for name, paths in axis_paths
        ),
        broadphase=tuple(broadphase),
        elapsed_s=elapsed,
        evaluations=counts["evaluated"],
        cache_hits=counts["hits"],
        rejected=counts["rejected"],
        exhausted_budget=clock() >= deadline,
        transition_trace=tuple(counts["trace"]),
        combined_orders=tuple({
            "sequence": list(p.order),
            "orientation_by_bend": dict(zip(p.order, p.orientations)),
            "heuristic_cost": p.cost,
            "unverified_2d_steps": p.unknown,
        } for p in merged),
    )
