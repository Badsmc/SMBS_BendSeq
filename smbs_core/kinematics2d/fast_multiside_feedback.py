"""Forced exploration of every outer side after an advisory FAST 2D screen.

The normal 1 s FAST generation remains untouched. The recovery pass schedules
at least one bounded Beam Search from each detected exterior edge and injects
a *soft* cost for observed 2D obstructions in an exact folded-state mask.

A side seed is an approximate first-bend location, not a real 90-degree
machine setup. All returned plans remain UNVERIFIED_3D.
"""
from __future__ import annotations

from dataclasses import dataclass
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
    sides=forced_side_seeds(outer_outline_xy,operations,fold_state)
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
    for number,side in enumerate(sides):
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
        ) else "FORCED_SIDE_CANDIDATES_UNVERIFIED",
    )
