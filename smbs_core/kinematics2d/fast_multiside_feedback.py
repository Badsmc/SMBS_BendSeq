"""Forced exploration of every outer side after an advisory FAST 2D screen.

The normal 1 s FAST generation remains untouched. The recovery pass schedules
at least one bounded Beam Search from each detected exterior edge and injects
a *soft* cost for observed 2D obstructions in an exact folded-state mask.

A side seed is an approximate first-bend location, not a real 90-degree
machine setup. All returned plans remain UNVERIFIED_3D.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
from math import isfinite
from time import perf_counter

from .fast_bfm_beam_search import search_fast_candidates
from .fast_side_seeds import forced_side_seeds


@dataclass(frozen=True)
class SideSearchCandidate:
    candidate: object
    side_name: str
    axis_aligned: bool

    def to_dict(self):
        return {
            "side": self.side_name,
            "axis_aligned": self.axis_aligned,
            "candidate": self.candidate.to_dict(),
        }


@dataclass(frozen=True)
class ForcedSideSearchReport:
    new_candidates: tuple[SideSearchCandidate, ...]
    side_findings: tuple[dict, ...]
    elapsed_seconds: float
    budget_seconds: float
    feedback_penalties: tuple[dict, ...]
    status: str

    def to_dict(self):
        return {
            "new_candidates": [candidate.to_dict()
                               for candidate in self.new_candidates],
            "side_findings": [dict(item) for item in self.side_findings],
            "detected_sides": len(self.side_findings),
            "searched_sides": sum(
                item.get("status") not in (
                    "BUDGET_EXCEEDED", "REUSED_EQUIVALENT_START",
                    "NO_FIRST_BEND_AVAILABLE"
                ) for item in self.side_findings
            ),
            "reused_equivalent_sides": sum(
                item.get("status") == "REUSED_EQUIVALENT_START"
                for item in self.side_findings
            ),
            "budget_skipped_sides": sum(
                item.get("status") == "BUDGET_EXCEEDED"
                for item in self.side_findings
            ),
            "elapsed_seconds": round(self.elapsed_seconds, 5),
            "budget_seconds": self.budget_seconds,
            "feedback_penalties": [dict(item) for item in self.feedback_penalties],
            "status": self.status,
            "disclaimer": (
                "Forced starts are relative to the *unfolded outer sides*; "
                "no new certified machine loading orientation is created. "
                "A 2D finding only adds a soft transition penalty."
            ),
        }


def transition_feedback_penalties(screen, *, interference_weight=1500.0):
    """Create state-specific penalties; never blacklist all occurrences of a bend."""
    penalties = {}
    for entry in getattr(screen, "checks", ()):
        transition = getattr(entry, "failed_transition", None)
        if transition is None:
            continue
        if entry.status not in ("POTENTIAL_2D_INTERFERENCE",
                                "STATIC_TOOLING_BLOCK"):
            continue
        penalties[transition] = max(
            penalties.get(transition, 0.0), interference_weight,
        )
    return penalties


def search_all_outer_sides(
    fold_state,
    operations,
    initial_candidates,
    outer_outline_xy,
    panel_vertices_xy,
    *,
    fixed_orientations=None,
    weights=None,
    screen=None,
    budget_seconds=2.5,
    outer_edge_polylines_xy=None,
    beam_width=24,
    max_per_side=2,
    clock=perf_counter,
    search_fn=search_fast_candidates,
):
    """Every exterior edge gets a chance to seed a *separate* beam.

    Budget is shared across sides; a skipped/timed-out side is reported.
    Small 2D feedback penalties apply to the EXACT completed operation
    bitmask and 0/180 flag. Even a blocked transition remains searchable.
    """
    if (not isinstance(budget_seconds, (int,float))
            or isinstance(budget_seconds, bool)
            or not isfinite(budget_seconds) or not 0 < budget_seconds <= 30):
        raise ValueError("Invalid forced-side time budget")
    start=clock()
    operations=tuple(operations)
    existing=tuple(initial_candidates)
    sides=forced_side_seeds(
        outer_outline_xy,operations,fold_state,
        outer_edge_polylines_xy=outer_edge_polylines_xy,
    )
    if not sides:
        return ForcedSideSearchReport(
            (),(),clock()-start,budget_seconds,(),"OUTER_CONTOUR_UNAVAILABLE",
        )
    penalties=transition_feedback_penalties(screen)
    penalty_details=tuple({
        "completed_mask": key[0],
        "operation_id": key[1],
        "reverse_axis": key[2],
        "penalty": value,
    } for key,value in sorted(penalties.items()))
    seen = {
        (c.operation_ids,c.orientation_by_operation) for c in existing
    }
    new_candidates=[]
    findings=[]
    # Identical first-operation/orientation constraints define the same
    # FAST search problem. Reuse its results for other actual CAD edges.
    # Every real side still receives its own diagnostic record.
    equivalent_starts={}
    for number,side in enumerate(sides):
        setup_key=(side.first_operation_ids,side.first_reverse)
        if setup_key in equivalent_starts:
            findings.append({
                **side.to_dict(),
                "status":"REUSED_EQUIVALENT_START",
                "same_search_as":equivalent_starts[setup_key],
                "new_candidates":0,
            })
            continue
        remaining = budget_seconds - (clock() - start)
        if remaining <= 0.015:
            findings.append({
                **side.to_dict(),"status":"BUDGET_EXCEEDED",
                "new_candidates":0,
            })
            continue
        if not side.first_operation_ids:
            findings.append({
                **side.to_dict(),"status":"NO_FIRST_BEND_AVAILABLE",
                "new_candidates":0,
            })
            continue
        time_slice = min(1.0, remaining / (len(sides) - number))
        if time_slice <= 0:
            findings.append({
                **side.to_dict(),"status":"BUDGET_EXCEEDED",
                "new_candidates":0,
            })
            continue
        equivalent_starts[setup_key]=side.name
        try:
            report = search_fn(
                fold_state, operations,
                fixed_orientations=fixed_orientations,
                panel_vertices_xy=panel_vertices_xy,
                weights=weights,
                forced_first_operation_ids=side.first_operation_ids,
                forced_first_reverse=dict(side.first_reverse),
                transition_penalties=penalties,
                limit_seconds=time_slice,
                beam_width=beam_width,
                max_candidates=max_per_side,
                # The normal FAST pass already evaluated this symmetric
                # heuristic. Side recovery is time-limited and gets its
                # actual collision feedback from Stage B; do not resample
                # the same expensive centreline on every alternative start.
                screen_tool_center_axis=False,
                axis_clearance_mm=1.0,
            )
        except (TypeError, ValueError) as exc:
            findings.append({
                **side.to_dict(),
                "status":"SEARCH_ERROR",
                "reason":type(exc).__name__+":"+str(exc),
                "new_candidates":0,
            })
            continue
        added=0
        for candidate in report.candidates:
            key=(candidate.operation_ids,candidate.orientation_by_operation)
            if key in seen:
                continue
            seen.add(key)
            new_candidates.append(SideSearchCandidate(
                candidate,side.name,side.axis_aligned,
            ))
            added+=1
        findings.append({
            **side.to_dict(),
            "status":report.status,
            "explored_states":report.explored_states,
            "elapsed_seconds":round(report.elapsed_seconds,5),
            "returned_candidates":len(report.candidates),
            "new_candidates":added,
        })
    elapsed=clock()-start
    return ForcedSideSearchReport(
        tuple(new_candidates),tuple(findings),elapsed,budget_seconds,
        penalty_details,
        "BUDGET_EXCEEDED" if any(
            item["status"]=="BUDGET_EXCEEDED" for item in findings
        ) else ("FORCED_SIDE_CANDIDATES_UNVERIFIED"
                if new_candidates else "NO_NEW_SIDE_CANDIDATES_UNVERIFIED"),
    )


@dataclass(frozen=True)
class FeedbackRecoveryReport:
    """One bounded rerun driven by actual 2D findings and opposite 180 poses."""
    new_candidates: tuple[SideSearchCandidate, ...]
    attempted_cases: tuple[dict, ...]
    elapsed_seconds: float
    budget_seconds: float
    penalty_count: int
    status: str
    coarse_gated_transition_count: int = 0
    coarse_pruned_expansions: int = 0

    def to_dict(self):
        return {
            "new_candidates": [entry.to_dict() for entry in self.new_candidates],
            "attempted_cases": [dict(case) for case in self.attempted_cases],
            "elapsed_seconds": round(self.elapsed_seconds, 5),
            "budget_seconds": self.budget_seconds,
            "penalty_count": self.penalty_count,
            "coarse_gated_transition_count": self.coarse_gated_transition_count,
            "coarse_pruned_expansions": self.coarse_pruned_expansions,
            "status": self.status,
            "evidence": "UNVERIFIED_3D_NOT_CHECKED",
        }


def search_feedback_recovery(
    fold_state,
    operations,
    existing_candidates,
    panel_vertices_xy,
    screen,
    *,
    coarse_gate=None,
    fixed_orientations=None,
    weights=None,
    budget_seconds=1.5,
    max_cases=12,
    clock=perf_counter,
    search_fn=search_fast_candidates,
):
    """Explore alternatives after expanded-list Stage B reports obstructions.

    Each failed 2D transition contributes an exact-state soft penalty.
    Retry some single-bend 180 flips and also synchronized flip pairs/
    triples from the most frequently obstructed operations. This is
    important when every single-flip solution merely fails on the NEXT
    unchanged 0-degree operation. User-pinned choices are never overridden.
    The search is an advisory bounded experiment, not a 3D safety pass.

    All solutions remain provisional, as a 2D finding may be false-positive
    and a sampled-clear path cannot prove continuous machine clearance.
    """
    if (isinstance(budget_seconds, bool) or
            not isinstance(budget_seconds, (int, float)) or
            not isfinite(budget_seconds) or
            not 0 < budget_seconds <= 30):
        raise ValueError("Invalid feedback recovery budget")
    if type(max_cases) is not int or not 1 <= max_cases <= 64:
        raise ValueError("Invalid feedback recovery case count")
    start = clock()
    ops = tuple(operations)
    candidates = tuple(existing_candidates)
    pins = dict(fixed_orientations or {})
    penalties = transition_feedback_penalties(screen)
    forbidden = tuple(
        getattr(coarse_gate, "forbidden_transitions", ()) or ()
    )
    if not penalties:
        return FeedbackRecoveryReport(
            (), (), clock()-start, budget_seconds, 0,
            "NO_2D_OBSTRUCTION_FEEDBACK",
        )
    cases = []
    used = set()
    for index in getattr(screen, "ranked_indices", range(len(candidates))):
        if not 0 <= index < len(candidates):
            continue
        check = screen.checks[index]
        transition = getattr(check, "failed_transition", None)
        if transition is None:
            continue
        if check.status not in (
                "POTENTIAL_2D_INTERFERENCE", "STATIC_TOOLING_BLOCK"):
            continue
        _mask, failed_op, previous_reverse = transition
        if failed_op in pins:
            # A manual 0/180 lock is authoritative, including if it creates
            # an obstruction. Do not quietly unlock or invert it.
            continue
        if not candidates[index].operation_ids:
            continue
        first = candidates[index].operation_ids[0]
        new_reverse = not previous_reverse
        case_key = (first, failed_op, new_reverse)
        if case_key in used:
            continue
        used.add(case_key)
        cases.append(case_key)
    if not cases:
        return FeedbackRecoveryReport(
            (), (), clock()-start, budget_seconds, len(penalties),
            "NO_UNPINNED_FLIP_CASES",
        )
    # First diversify the starts rather than spending all the budget on
    # several variants of the same initial bending operation.
    cases.sort(key=lambda x: (x[0], x[1], x[2]))
    by_start = {}
    for case in cases:
        by_start.setdefault(case[0], []).append(case)
    diverse = []
    while by_start:
        for start_id in tuple(by_start):
            diverse.append(by_start[start_id].pop(0))
            if not by_start[start_id]:
                del by_start[start_id]
    # Multiple different already formed flanges may collide in
    # successive stages. Reserve a few bounded searches for combined
    # 0/180 repairs rather than always flipping only the first blocker.
    counts = Counter((failed,reverse) for _,failed,reverse in cases)
    ranked_failures = []
    used_bends = set()
    for (failed,reverse),count in sorted(
            counts.items(),
            key=lambda item:(-item[1], item[0][0], item[0][1])):
        if failed not in used_bends and failed not in pins:
            ranked_failures.append((failed,reverse))
            used_bends.add(failed)
    combos = []
    if len(ranked_failures) >= 2:
        leaders = ranked_failures[:5]
        for i in range(min(3,len(leaders)-1)):
            combos.append((
                diverse[i % len(diverse)][0],
                tuple(leaders[:i+2]),
            ))
    reserved = min(len(combos),max(0,max_cases-1))
    singles = [
        (first,((failed,reverse),))
        for first,failed,reverse in diverse[:max_cases-reserved]
    ]
    cases = []
    if singles:
        cases.append(singles.pop(0))
    for combined in combos[:reserved]:
        cases.append(combined)
        if singles and len(cases) < max_cases:
            cases.append(singles.pop(0))
    cases.extend(singles[:max_cases-len(cases)])
    seen = {
        (c.operation_ids, c.orientation_by_operation)
        for c in candidates
    }
    new = []
    attempted = []
    pruned_total = 0
    for i, (first, flips) in enumerate(cases):
        failed, reverse = flips[0]
        flip_info = [
            {"operation_id": op, "reverse_axis": pose}
            for op,pose in flips
        ]
        remaining = budget_seconds - (clock()-start)
        if remaining <= 0.012:
            attempted.append({
                "first": first, "flipped_operation": failed,
                "new_reverse_axis": reverse,
                "coordinated_flips": flip_info,
                "status": "BUDGET_EXCEEDED", "new_candidates": 0,
            })
            continue
        per_case = min(0.75, remaining / (len(cases)-i))
        if per_case <= 0:
            attempted.append({
                "first": first, "flipped_operation": failed,
                "new_reverse_axis": reverse, "status": "BUDGET_EXCEEDED",
                "new_candidates": 0,
            })
            continue
        extra_pins = {**pins, **dict(flips)}
        try:
            result = search_fn(
                fold_state, ops,
                fixed_orientations=extra_pins,
                panel_vertices_xy=panel_vertices_xy,
                weights=weights,
                transition_penalties=penalties,
                forbidden_transitions=forbidden,
                forced_first_operation_ids=(first,),
                limit_seconds=per_case,
                beam_width=24,
                max_candidates=3,
                screen_tool_center_axis=False,
            )
        except (ValueError, TypeError) as exc:
            attempted.append({
                "first": first, "flipped_operation": failed,
                "new_reverse_axis": reverse,
                "coordinated_flips": flip_info,
                "status": "SEARCH_ERROR",
                "reason": type(exc).__name__ + ":" + str(exc),
                "new_candidates": 0,
            })
            continue
        pruned_total += int(
            getattr(result, "coarse_2d_pruned_expansions", 0)
        )
        count = 0
        for candidate in result.candidates:
            key = (candidate.operation_ids, candidate.orientation_by_operation)
            if key in seen:
                continue
            seen.add(key)
            count += 1
            new.append(SideSearchCandidate(
                candidate,
                "FLIP_" + "+".join(op for op,_ in flips), False,
            ))
        attempted.append({
            "first": first,
            "flipped_operation": failed,
            "new_reverse_axis": reverse,
            "coordinated_flips": flip_info,
            "status": result.status,
            "elapsed_seconds": round(result.elapsed_seconds, 5),
            "new_candidates": count,
            "coarse_2d_pruned_expansions": getattr(
                result, "coarse_2d_pruned_expansions", 0
            ),
        })
    status = (
        "BUDGET_EXCEEDED" if any(
            entry["status"] == "BUDGET_EXCEEDED" for entry in attempted
        ) else
        "FEEDBACK_NEW_CANDIDATES_UNVERIFIED" if new else
        "FEEDBACK_NO_DISTINCT_CANDIDATES"
    )
    return FeedbackRecoveryReport(
        tuple(new), tuple(attempted), clock()-start,
        budget_seconds, len(penalties), status,
        len(forbidden), pruned_total,
    )
