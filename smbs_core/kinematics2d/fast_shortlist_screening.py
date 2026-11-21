"""Bounded, advisory 2D checking of a FAST shortlist.

Run ONLY after the one-second combinatorial FAST search. Reuse the existing
Stage A / Stage B machine-section checks, not native OCC or Qt. A 2D
interference is evidence for ranking, never proof that a bend is impossible.

The checker temporarily changes FoldState angles and restores them in finally.
It works on SINGLE operations only; staged PREBEND/FINISH need a distinct
partial-angle trajectory and are left explicitly UNVERIFIED.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from time import perf_counter
from typing import Callable, Mapping, Sequence

from .fast_bfm_beam_search import FastCandidate
from .staged_bends import BendOperation


@dataclass(frozen=True)
class ShortlistCandidateCheck:
    operation_ids: tuple[str, ...]
    status: str
    sampled_steps: int = 0
    cached_steps: int = 0
    first_issue: str = ""
    elapsed_seconds: float = 0.0
    # Exact staged-state mask in the initial FAST operation ordering.
    # The mask is meaningful only with that same operations tuple.
    failed_transition: tuple[int, str, bool] | None = None

    def to_dict(self) -> dict:
        return {
            "operation_ids": list(self.operation_ids),
            "status": self.status,
            "sampled_steps": self.sampled_steps,
            "cached_steps": self.cached_steps,
            "first_issue": self.first_issue,
            "failed_transition": (
                {"completed_mask": self.failed_transition[0],
                 "operation_id": self.failed_transition[1],
                 "reverse_axis": self.failed_transition[2]}
                if self.failed_transition is not None else None
            ),
            "elapsed_seconds": round(self.elapsed_seconds, 5),
            "evidence": "ADVISORY_SAMPLED_2D_NOT_3D_VERIFIED",
        }


@dataclass(frozen=True)
class ShortlistScreenReport:
    checks: tuple[ShortlistCandidateCheck, ...]
    ranked_indices: tuple[int, ...]
    elapsed_seconds: float
    budget_seconds: float
    actual_probes: int
    cache_hits: int
    status: str

    def to_dict(self) -> dict:
        return {
            "checks": [entry.to_dict() for entry in self.checks],
            "ranked_indices": list(self.ranked_indices),
            "elapsed_seconds": round(self.elapsed_seconds, 5),
            "budget_seconds": self.budget_seconds,
            "actual_probes": self.actual_probes,
            "cache_hits": self.cache_hits,
            "status": self.status,
            "disclaimer": (
                "2D samples are not continuous collision verification. "
                "Backgauge CAD, clamps, tool ends, press frame and 3D "
                "loading/retract remain UNVERIFIED."
            ),
        }


# A sampled 2D obstruction should rank below untested candidates but must
# never be erased. This avoids false hard rejections from central slices.
_RANK = {
    "SAMPLED_2D_TOOLS_GAUGE_CLEAR": 0,
    "SAMPLED_2D_TOOLS_ONLY": 1,
    "PARTIAL_UNVERIFIED": 2,
    "STAGED_UNSUPPORTED": 3,
    "NO_TOOLING": 3,
    "BUDGET_EXCEEDED": 3,
    "NOT_CHECKED": 3,
    "POTENTIAL_2D_INTERFERENCE": 4,
    "STATIC_TOOLING_BLOCK": 5,
}


def _probe_existing_stage_b(
    state,
    op: BendOperation,
    reverse_axis: bool,
    tooling,
    outlines: Mapping,
    deadline: float,
    *,
    backgauge_model=None,
) -> tuple[str, str]:
    """One candidate transition in its *actual already folded* state.

    Stage B checks tool cross-sections at sampled approach, bending,
    bottom and retract phases. It restores tooling and FoldState itself.
    The optional simple 2D gauge is a preview, NOT an OCC stop contact.
    """
    from .preliminary_screening import (
        ScreeningDecision, screen_candidate_stage_a, screen_candidate_stage_b,
    )

    if perf_counter() >= deadline:
        return ("BUDGET_EXCEEDED", "before_stage_a")
    if tooling is None or getattr(tooling, "punch", None) is None or getattr(tooling, "die", None) is None:
        return ("NO_TOOLING", "selected_punch_die_missing")
    match = bool(getattr(tooling, "match_active_bend_length", False))
    stage_a = screen_candidate_stage_a(
        state, op.bend_id, tooling, match_active_bend_length=match,
    )
    if stage_a.decision is ScreeningDecision.PROVEN_BLOCKING:
        return ("STATIC_TOOLING_BLOCK", str(stage_a.reason))
    if stage_a.decision is ScreeningDecision.UNVERIFIED_NEEDS_3D:
        return ("PARTIAL_UNVERIFIED", "stage_a:" + str(stage_a.reason))
    if perf_counter() >= deadline:
        return ("BUDGET_EXCEEDED", "after_stage_a")

    # Synthetic 2D rear-gauge contact is only used when CAD gauge mode is
    # disabled. In CAD mode never invent a substitute physical contact.
    gauge_polygon = None
    if backgauge_model is not None and not bool(getattr(backgauge_model, "cad_geometry_enabled", False)):
        try:
            target, _depth, _source = backgauge_model.resolve_for_state(
                state, outlines, op.bend_id, desired_midpoint_xm=0.0,
            )
            backgauge_model.validate_z_travel(target, op.bend_id)
            gauge_polygon = backgauge_model.polygon_at(target)
        except (ValueError, TypeError, AttributeError, KeyError) as exc:
            # A failed gauge approximation is not a 3D blocker. Continue
            # checking the actual punch/die geometry independently.
            gauge_polygon = None
    if perf_counter() >= deadline:
        return ("BUDGET_EXCEEDED", "before_stage_b")
    report = screen_candidate_stage_b(
        state, op.bend_id, tooling, dict(outlines),
        match_active_bend_length=match,
        reverse_axis=bool(reverse_axis),
        allow_end_zone_collision=bool(
            getattr(tooling, "allow_end_zone_collision", False)
        ),
        end_zone_length_mm=float(
            getattr(tooling, "end_zone_length_mm", 0.0) or 0.0
        ),
        tolerance_mm=0.1,
        fractions=(0.0, 0.5, 1.0),
        stroke_fractions=(0.0, 1.0),
        enforce_rear_frame=True,
        backgauge_polygon_machine=gauge_polygon,
        deadline_monotonic=deadline,
    )
    if report.stage == "STAGE_B_SECTION_REQUIRES_3D":
        return ("POTENTIAL_2D_INTERFERENCE", str(report.reason))
    if report.stage == "STAGE_B_BUDGET":
        return ("BUDGET_EXCEEDED", str(report.reason))
    if report.stage != "STAGE_B_PREVIEW":
        return ("PARTIAL_UNVERIFIED", str(report.stage) + ":" + str(report.reason))
    phases = set(report.sampled_phases)
    statuses = tuple(report.contact_statuses)
    if (not {"APPROACH", "BENDING", "BOTTOM", "RETRACT"}.issubset(phases)
            or not statuses
            or any(status not in ("CLEAR", "TOUCH") for status in statuses)):
        return ("PARTIAL_UNVERIFIED", "incomplete_or_ambiguous_2d_samples")
    if gauge_polygon is None:
        return ("SAMPLED_2D_TOOLS_ONLY", "rear_gauge_not_cad_verified")
    return ("SAMPLED_2D_TOOLS_GAUGE_CLEAR", "synthetic_2d_gauge_only")


def screen_fast_shortlist(
    fold_state,
    operations: Sequence[BendOperation],
    candidates: Sequence[FastCandidate],
    panel_vertices_xy: Mapping,
    tooling,
    *,
    backgauge_model=None,
    budget_seconds: float = 3.0,
    probe: Callable | None = None,
    clock=perf_counter,
) -> ShortlistScreenReport:
    """Rerank only FAST's existing candidates using sampled 2D evidence.

    Returns each original candidate by index; no mutation of candidates,
    operation order or user orientation pins. This is a *cooperative* budget:
    an individual Stage B call cannot be forcibly interrupted, so timing
    overruns are reported instead of being concealed.
    """
    if (not isinstance(budget_seconds, (int, float))
            or isinstance(budget_seconds, bool)
            or not isfinite(budget_seconds) or not 0 < budget_seconds <= 30):
        raise ValueError("budget_seconds must be finite in (0, 30]")
    if not isinstance(panel_vertices_xy, Mapping):
        raise ValueError("panel outlines required")
    candidates = tuple(candidates)
    operations = tuple(operations)
    by_id = {op.operation_id: op for op in operations}
    positions = {op.operation_id: index for index, op in enumerate(operations)}
    if len(by_id) != len(operations):
        raise ValueError("operation IDs must be unique")
    if any(not isinstance(candidate, FastCandidate) for candidate in candidates):
        raise TypeError("Expected FAST candidates")
    start = clock()
    deadline = start + budget_seconds
    checks = []
    memo = {}
    probes = cache_hits = 0
    original_angles = dict(fold_state.angles)
    original_transforms = dict(fold_state.transforms)
    try:
        for candidate in candidates:
            c_start = clock()
            sampled = cached = 0
            status = "SAMPLED_2D_TOOLS_GAUGE_CLEAR"
            issue = ""
            first_blocker = None
            done_angles = {}
            any_tool_only = False
            if (len(candidate.operation_ids) != len(operations)
                    or set(candidate.operation_ids) != set(by_id)
                    or set(candidate.orientations()) != set(by_id)):
                status, issue = "PARTIAL_UNVERIFIED", "invalid_candidate_operation_set"
            elif any(by_id[oid].kind != "SINGLE" for oid in candidate.operation_ids):
                status, issue = "STAGED_UNSUPPORTED", "prebend_finish_requires_partial_angle_screen"
            elif (tooling is None or getattr(tooling, "punch", None) is None
                  or getattr(tooling, "die", None) is None):
                status, issue = "NO_TOOLING", "select_punch_and_die_for_2d_screen"
            elif clock() >= deadline:
                status, issue = "BUDGET_EXCEEDED", "shortlist_budget_exhausted"
            else:
                for oid in candidate.operation_ids:
                    if clock() >= deadline:
                        status, issue = "BUDGET_EXCEEDED", "shortlist_budget_exhausted"
                        break
                    op = by_id[oid]
                    reverse = candidate.orientations()[oid]
                    cache_key = (tuple(sorted(done_angles.items())), op.bend_id, reverse)
                    if cache_key in memo:
                        step_status, step_reason = memo[cache_key]
                        cached += 1
                        cache_hits += 1
                    else:
                        fold_state.reset_all()
                        for bend_id, angle in done_angles.items():
                            fold_state.set_angle(bend_id, angle)
                        try:
                            if probe is None:
                                step_status, step_reason = _probe_existing_stage_b(
                                    fold_state, op, reverse, tooling, panel_vertices_xy,
                                    deadline, backgauge_model=backgauge_model,
                                )
                            else:
                                step_status, step_reason = probe(
                                    fold_state, op, reverse, tooling,
                                    panel_vertices_xy, deadline,
                                )
                        except Exception as exc:
                            step_status = "PARTIAL_UNVERIFIED"
                            step_reason = "2d_probe_error:" + type(exc).__name__ + ":" + str(exc)
                        memo[cache_key] = (step_status, step_reason)
                        sampled += 1
                        probes += 1
                    if step_status == "SAMPLED_2D_TOOLS_ONLY":
                        any_tool_only = True
                    elif step_status != "SAMPLED_2D_TOOLS_GAUGE_CLEAR":
                        status, issue = step_status, oid + ":" + step_reason
                        if step_status in (
                                "POTENTIAL_2D_INTERFERENCE", "STATIC_TOOLING_BLOCK"):
                            completed_mask = sum(
                                1 << positions[prior] for prior in done_angles
                            )
                            first_blocker = (completed_mask, oid, reverse)
                        break
                    done_angles[op.bend_id] = float(op.target_angle_deg)
                else:
                    if any_tool_only:
                        status = "SAMPLED_2D_TOOLS_ONLY"
            checks.append(ShortlistCandidateCheck(
                tuple(candidate.operation_ids), status, sampled, cached,
                issue, clock() - c_start, first_blocker,
            ))
    finally:
        fold_state.angles.clear()
        fold_state.angles.update(original_angles)
        fold_state.transforms.clear()
        fold_state.transforms.update(original_transforms)
    ranked = tuple(sorted(
        range(len(candidates)),
        key=lambda i: (
            _RANK.get(checks[i].status, 3),
            candidates[i].estimated_cost,
            i,
        ),
    ))
    elapsed = clock() - start
    return ShortlistScreenReport(
        tuple(checks), ranked, elapsed, budget_seconds, probes, cache_hits,
        "BUDGET_EXCEEDED" if elapsed >= budget_seconds else "FINISHED_UNVERIFIED_3D",
    )
