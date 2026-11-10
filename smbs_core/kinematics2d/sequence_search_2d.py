"""Experimental, section-only DFS. NEVER invokes native 3D collision validation.

Uses the existing FoldState, Stage A, Stage B, machine-section calculator,
2D polygon contact oracle and tooling setup. This is sampled planar evidence,
not a certified manufacturing plan. 2D interference prunes only within this
explicitly requested planar model; it does not prove 3D impossibility.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
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
    backgauge_checked: bool = False
    elapsed_s: float = 0.0
    timing_ms: tuple[tuple[str, float], ...] = ()
    diagnostic_findings: tuple[tuple[str, str, str, str, str], ...] = ()
    touch_summary: tuple[tuple[str, str, str, str, int], ...] = ()
    orientation_variant: str = "NORMAL"

    def to_dict(self) -> dict[str, Any]:
        return {
            "bend_id": self.bend_id, "evidence": self.evidence,
            "reason": self.reason,
            "orientation_variant": self.orientation_variant,
            "sampled_phases": list(self.sampled_phases),
            "contact_statuses": list(self.contact_statuses),
            "machine_setup": self.setup.to_dict() if self.setup else None,
            "backgauge_checked": self.backgauge_checked,
            "elapsed_s": self.elapsed_s,
            "timing_ms": dict(self.timing_ms),
            "diagnostic_findings": [
                {"phase": p, "tool": t, "component": c, "status": st, "reason": r}
                for p, t, c, st, r in self.diagnostic_findings
            ],
            "touch_summary": [
                {"phase": p, "tool": t, "component": c, "reason": r, "count": n}
                for p, t, c, r, n in self.touch_summary
            ],
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
    cost_breakdown: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": list(self.sequence), "evidence": self.evidence,
            "steps": [step.to_dict() for step in self.steps],
            "cost": self.cost,
            "cost_complete": self.cost_complete,
            "cost_breakdown": list(self.cost_breakdown),
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
    evaluated_step_details: tuple[tuple[tuple[str, ...], PlanarStep], ...] = ()
    max_depth_reached: int = 0
    deepest_prefix: tuple[str, ...] = ()
    rejection_counts: tuple[tuple[str, int], ...] = ()
    slowest_steps: tuple[tuple[str, str, float], ...] = ()
    final_replay_attempts: int = 0
    final_replay_rejections: int = 0
    dead_state_cache_hits: int = 0
    dead_states_proven: int = 0
    deferred_root_probes: int = 0
    paired_first_step_obstructions: tuple[tuple[str, str, str, str], ...] = ()
    partial_candidate: Optional[PlanarCandidate] = None
    partial_remainder: tuple[str, ...] = ()
    conditional_end_zone_requested: bool = False
    alternative_setup_trials: tuple[tuple[tuple[str, ...], str, str, str], ...] = ()
    operation_weights: Optional[dict[str, float]] = None

    def to_dict(self) -> dict[str, Any]:
        # Aggregate the actual per-transition contact findings; keep each
        # tool separate rather than inferring punch/die from a short reason.
        # This is diagnostic only and does not affect DFS pruning.
        per_bend: dict[str, dict[str, Any]] = {}
        for prefix, step in self.evaluated_step_details:
            item = per_bend.setdefault(step.bend_id, {
                "evaluations": 0, "clear_or_touch": 0,
                "interferences": 0, "unverified": 0,
                "contact_findings": {},
            })
            item["evaluations"] += 1
            if step.evidence == "SAMPLED_2D_CLEAR_OR_TOUCH":
                item["clear_or_touch"] += 1
            elif step.evidence == "2D_RETRACT_WARNING_NOT_3D_VALIDATED":
                item.setdefault("retract_warnings", 0)
                item["retract_warnings"] += 1
            elif step.evidence == "2D_INTERFERENCE":
                item["interferences"] += 1
            elif step.evidence == "UNVERIFIED_2D":
                item["unverified"] += 1
            # Count a distinct component/tool finding at most once per
            # transition; a single phase may emit multiple polygon records.
            findings_seen = set()
            for phase, tool, component, status, reason in step.diagnostic_findings:
                if status != "INTERFERENCE":
                    continue
                key = (phase, tool, component, reason)
                findings_seen.add(key)
            for phase, tool, component, reason in findings_seen:
                key = "|".join((phase, tool, component, reason))
                counts = item["contact_findings"]
                counts[key] = counts.get(key, 0) + 1
        for item in per_bend.values():
            item["contact_findings"] = [
                {"phase": parts[0], "tool": parts[1],
                 "component": parts[2], "reason": parts[3],
                 "transitions": count}
                for key, count in sorted(
                    item["contact_findings"].items(),
                    key=lambda kv: (-kv[1], kv[0])
                )
                for parts in [key.split("|", 3)]
            ]
        return {
            "status": self.status.value,
            "operation_weights": self.operation_weights,
            "partial_candidate": (self.partial_candidate.to_dict()
                                  if self.partial_candidate is not None else None),
            "partial_remainder": list(self.partial_remainder),
            "conditional_end_zone_requested": self.conditional_end_zone_requested,
            "alternative_setup_trials": [
                {"completed_prefix": list(prefix), "bend_id": bid,
                 "orientation": orientation, "evidence": evidence}
                for prefix, bid, orientation, evidence in self.alternative_setup_trials
            ],
            "per_bend_contact_summary": per_bend,
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
            "final_replay_attempts": self.final_replay_attempts,
            "final_replay_rejections": self.final_replay_rejections,
            "dead_state_cache_hits": self.dead_state_cache_hits,
            "dead_states_proven": self.dead_states_proven,
            "deferred_root_probes": self.deferred_root_probes,
            "paired_first_step_obstructions": [
                {"first_bend": a, "second_bend": b,
                 "after_first_reason": a_to_b,
                 "after_second_reason": b_to_a}
                for a, b, a_to_b, b_to_a in self.paired_first_step_obstructions
            ],
            "max_depth_reached": self.max_depth_reached,
            "deepest_prefix": list(self.deepest_prefix),
            "rejection_counts": dict(self.rejection_counts),
            "slowest_steps": [
                {"prefix": prefix, "bend_id": bend_id, "elapsed_s": seconds}
                for prefix, bend_id, seconds in self.slowest_steps
            ],
            "evaluated_step_details": [
                {"completed_prefix": list(prefix), **step.to_dict()}
                for prefix, step in self.evaluated_step_details
            ],
        }


def _classify_step(report: PreliminaryScreeningReport) -> str:
    """Interpret Stage B as planar evidence, not as its 3D-search decision."""
    if report.stage == "STAGE_B_RETRACT_WARNING":
        return "2D_RETRACT_WARNING_NOT_3D_VALIDATED"
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



def validate_partial_planar_prefix(
    session: Any,
    tooling: Any,
    sequence: Sequence[str],
    *,
    match_active_bend_length: bool = True,
    tolerance_mm: float = 0.02,
    allow_retract_warning: bool = False,
) -> tuple[bool, str]:
    """Full-density sampled 2D gate before displaying an exploratory prefix.

    This method cannot certify a continuous motion or 3D collision safety.
    Any interference, missing geometry or setup exception fails closed.
    Restores FoldState even if a preview probe raises an exception.
    """
    state = session.fold_state
    originals = (dict(state.angles), dict(state.transforms))
    completed: set[str] = set()
    conditional_warnings: list[str] = []
    try:
        if (not sequence or len(set(sequence)) != len(sequence)
                or any(b not in state.bends for b in sequence)):
            return False, "invalid_partial_bend_order"
        for bid in sequence:
            state.reset_all()
            for prior in sequence:
                if prior in completed:
                    state.set_angle(prior, float(state.bends[prior].angle_deg))
            stage_a = screen_candidate_stage_a(
                state, bid, tooling,
                match_active_bend_length=match_active_bend_length,
            )
            if stage_a.decision is ScreeningDecision.PROVEN_BLOCKING:
                return False, bid + ":" + stage_a.reason
            stage_b = screen_candidate_stage_b(
                state, bid, tooling, session.panel_vertices_xy,
                match_active_bend_length=match_active_bend_length,
                fractions=tuple(i / 16 for i in range(17)),
                stroke_fractions=tuple(i / 8 for i in range(9)),
                tolerance_mm=tolerance_mm,
                allow_retract_warning=allow_retract_warning,
            )
            evidence = _classify_step(stage_b)
            if evidence != "SAMPLED_2D_CLEAR_OR_TOUCH" and not (
                    allow_retract_warning
                    and evidence == "2D_RETRACT_WARNING_NOT_3D_VALIDATED"):
                return False, bid + ":" + stage_b.reason
            if evidence == "2D_RETRACT_WARNING_NOT_3D_VALIDATED":
                conditional_warnings.append(bid)
            completed.add(bid)
        if conditional_warnings:
            return True, (
                "CONDITIONAL_RETRACT_WARNING_NOT_3D_CERTIFIED:" +
                ",".join(conditional_warnings)
            )
        return True, "FULL_DENSITY_SAMPLED_2D_ONLY_NOT_3D_CERTIFIED"
    except Exception as exc:
        return False, "partial_replay_error:" + type(exc).__name__ + ":" + str(exc)
    finally:
        state.angles = originals[0]
        state.transforms = originals[1]



def _setup_with_scored_tool_lengths(
    setup: WorkpieceMachineSetup,
    tooling: Any,
    match_active_bend_length: bool,
) -> WorkpieceMachineSetup:
    """Get planning lengths from the SAME mode used by Stage A/B.

    Virtual axial stretching uses bend-line length as an explicitly planned
    tool length. Fixed tooling uses library profile length only. Do not read
    tooling.match_active_bend_length here: it may differ from the per-search
    override passed to the collision stages.
    """
    punch_profile = getattr(tooling, "punch", None)
    die_profile = getattr(tooling, "die", None)

    def planned_length(profile: Any) -> tuple[Optional[float], str]:
        if profile is None:
            return None, "NOT_SPECIFIED"
        if match_active_bend_length:
            return float(setup.bend_length_mm), "VIRTUAL_MATCH_BEND"
        length = getattr(profile, "length_mm", None)
        if (isinstance(length, (int, float)) and not isinstance(length, bool)
                and isfinite(length) and length > 0):
            return float(length), "FIXED_PROFILE"
        return None, "NOT_SPECIFIED"

    punch_len, punch_source = planned_length(punch_profile)
    die_len, die_source = planned_length(die_profile)
    return replace(
        setup,
        punch_length_mm=punch_len,
        die_length_mm=die_len,
        punch_length_source=punch_source,
        die_length_source=die_source,
    )


def search_bend_sequences_2d(
    session: Any,
    tooling: Any,
    *,
    max_solutions: int = 10,
    max_complete_candidates: int = 1000,
    group_first: bool = True,
    preferred_bend_order: Optional[Sequence[str]] = None,
    root_probe_nodes: int = 0,
    root_probe_seconds: float = 0.0,
    min_partial_bends: int = 0,
    allow_end_zone_collision: bool = False,
    end_zone_length_mm: float = 0.0,
    allow_retract_warning: bool = False,
    alternate_axis_bends: Sequence[str] = (),
    weights: Optional[OperationWeights] = None,
    max_nodes: int = 10000,
    max_evaluations: int = 50000,
    max_search_time_s: float = 60.0,
    fractions: Sequence[float] = tuple(i / 16 for i in range(17)),
    stroke_fractions: Sequence[float] = tuple(i / 8 for i in range(9)),
    exploration_fractions: Optional[Sequence[float]] = None,
    exploration_stroke_fractions: Optional[Sequence[float]] = None,
    match_active_bend_length: bool = True,
    bend_shifts: Optional[Mapping[str, float]] = None,
    multi_station_enabled: bool = False,
    verified_punch_span_xm: Optional[tuple[float, float]] = None,
    verified_die_span_xm: Optional[tuple[float, float]] = None,
    tolerance_mm: float = 0.02,
    backgauge_polygon_machine: Optional[Sequence[tuple[float, float]]] = None,
) -> PlanarSearchReport:
    """Enumerate up to N sampled 2D sequences without ever running Stage C.

    Unknown geometry is retained as UNVERIFIED, never reported as safe.
    A 2D overlap can exclude a sequence *from this planar preview only*.
    FoldState and tooling are restored after every branch and after search.
    Optional reduced exploration sampling NEVER produces a returned candidate
    without replaying all of its steps at the original full sampling density.
    """
    start = perf_counter()
    warnings = (
        "Sampled 2D cross sections do not certify a continuous stroke, 3D tool ends, "
        "backgauge, frame or transfer path.",
        "2D overlaps are planar-model rejection only, not proof of a 3D collision.",
        ("Conditional RETRACT PUNCH interference warnings enabled: "
         "not physical approval; collision findings retained."
         if allow_retract_warning else "RETRACT interference remains blocking in the 2D preview."),
        ("Backgauge machine-space section was provided and sampled."
         if backgauge_polygon_machine is not None
         else "Backgauge geometry missing: no backgauge collision checks performed."),
    )
    state = getattr(session, "fold_state", None)
    panel_vertices = getattr(session, "panel_vertices_xy", None)
    order = tuple(getattr(session, "bend_order", ()))
    try:
        if state is None or tooling is None or not panel_vertices or not order:
            raise ValueError("missing FoldState, tooling, panel vertices, or bend order")
        if len(set(order)) != len(order) or set(order) != set(state.bends):
            raise ValueError("bend order must contain every FoldState bend exactly once")
        if max_solutions < 1 or max_complete_candidates < 1 or max_nodes < 1 or max_evaluations < 1 or root_probe_nodes < 0:
            raise ValueError("positive solution and search limits required")
        if any(bid not in order for bid in alternate_axis_bends):
            raise ValueError("alternate_axis_bends contains unknown bend")
        if min_partial_bends < 0 or min_partial_bends > len(order):
            raise ValueError("min_partial_bends must be between zero and bend count")
        if not isfinite(end_zone_length_mm) or end_zone_length_mm < 0:
            raise ValueError("end_zone_length_mm must be finite and non-negative")
        if not isfinite(root_probe_seconds) or root_probe_seconds < 0:
            raise ValueError("root_probe_seconds must be finite and non-negative")
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
        coarse_fs = (tuple(sorted(set(float(x) for x in exploration_fractions)))
                     if exploration_fractions is not None else fs)
        coarse_stroke_fs = (
            tuple(sorted(set(float(x) for x in exploration_stroke_fractions)))
            if exploration_stroke_fractions is not None else stroke_fs
        )
        if (not coarse_fs or coarse_fs[0] != 0.0 or coarse_fs[-1] != 1.0
                or any(not isfinite(x) or x < 0 or x > 1 for x in coarse_fs)):
            raise ValueError("exploration_fractions must contain 0 and 1")
        if (not coarse_stroke_fs or coarse_stroke_fs[0] != 0.0 or coarse_stroke_fs[-1] != 1.0
                or any(not isfinite(x) or x < 0 or x > 1 for x in coarse_stroke_fs)):
            raise ValueError("exploration_stroke_fractions must contain 0 and 1")
        progressive = coarse_fs != fs or coarse_stroke_fs != stroke_fs
        priority_order = tuple(str(b) for b in preferred_bend_order) if preferred_bend_order is not None else order
        if len(priority_order) != len(order) or len(set(priority_order)) != len(order) or set(priority_order) != set(order):
            raise ValueError("preferred_bend_order must be a permutation of bend IDs")
        priority_index = {bid: i for i, bid in enumerate(priority_order)}
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
    step_cache: dict[tuple[frozenset[str], str, bool], PlanarStep] = {}
    step_diagnostics: list[tuple[tuple[str, ...], PlanarStep]] = []
    # Only states with no coarse complete path whatsoever are memoized dead.
    # A different permutation of the same folded subset may have a different
    # full-density replay outcome, so a replay rejection must NOT memoize it.
    dead_states: set[frozenset[str]] = set()
    dead_state_cache_hits = 0
    probe_cutoffs = 0
    deepest_prefix: tuple[str, ...] = ()
    best_partial_steps: tuple[PlanarStep, ...] = ()
    best_partial_prefix: tuple[str, ...] = ()
    alternative_setup_trials: list[tuple[tuple[str, ...], str, str, str]] = []
    final_replay_attempts = final_replay_rejections = 0

    def eval_step(prefix: tuple[str, ...], bend_id: str, *, detailed: bool = False) -> PlanarStep:
        nonlocal evaluated, rejected, unverified
        # Separate coarse and full-density results: the same subset must
        # never reuse a less thoroughly sampled verdict at final replay.
        key = (frozenset(prefix), bend_id, detailed and progressive)
        if key in step_cache:
            return step_cache[key]
        evaluated += 1
        step_start = perf_counter()
        try:
            state.reset_all()
            for completed in prefix:
                state.set_angle(completed, float(state.bends[completed].angle_deg))
            setup = compute_workpiece_machine_setup(
                state, bend_id, desired_midpoint_xm=float(shifts.get(bend_id, 0.0)),
            )
            # Distinguish physical tool length from bend-line length. Under
            # virtual axial matching the effective lengths are only planned,
            # not a checked machine tooling assembly.
            setup = _setup_with_scored_tool_lengths(
                setup, tooling, match_active_bend_length,
            )
            stage_a = screen_candidate_stage_a(
                state, bend_id, tooling,
                match_active_bend_length=match_active_bend_length,
                desired_midpoint_xm=float(shifts.get(bend_id, 0.0)),
            )
            if stage_a.decision is ScreeningDecision.PROVEN_BLOCKING:
                # Invalid source geometry and insufficient tool length are not
                # evidence of 2D polygon intersection.
                evidence = ("INVALID_INPUT"
                            if stage_a.stage in ("STAGE_A_GEOMETRY", "STAGE_A_INPUT")
                            else "2D_TOOLING_CONSTRAINT")
                step = PlanarStep(bend_id, evidence,
                                  stage_a.reason, (), (), setup)
            else:
                stage_b = screen_candidate_stage_b(
                    state, bend_id, tooling, panel_vertices,
                    desired_midpoint_xm=float(shifts.get(bend_id, 0.0)),
                    match_active_bend_length=match_active_bend_length,
                    allow_end_zone_collision=allow_end_zone_collision,
                    end_zone_length_mm=end_zone_length_mm,
                    allow_retract_warning=allow_retract_warning,
                    tolerance_mm=tolerance_mm,
                    fractions=fs if detailed else coarse_fs,
                    stroke_fractions=stroke_fs if detailed else coarse_stroke_fs,
                    backgauge_polygon_machine=backgauge_polygon_machine,
                    deadline_monotonic=start + max_search_time_s,
                    multi_station_enabled=multi_station_enabled,
                    verified_punch_span_xm=verified_punch_span_xm,
                    verified_die_span_xm=verified_die_span_xm,
                )
                evidence = _classify_step(stage_b)
                orientation_variant = "NORMAL"
                # Try a distinct 180-degree Z-axis loading orientation only
                # for explicitly selected bends and only if the normal pose
                # is rejected by sampled 2D interference. The same complete
                # Stage B contact oracle is applied to the alternative.
                if (evidence == "2D_INTERFERENCE"
                        and bend_id in alternate_axis_bends
                        and not detailed
                        # Bound diagnostic experiments so they do not consume
                        # the entire 30-second sequence-search budget.
                        and len(alternative_setup_trials) < 12
                        and perf_counter() - start < max_search_time_s):
                    alt = screen_candidate_stage_b(
                        state, bend_id, tooling, panel_vertices,
                        desired_midpoint_xm=float(shifts.get(bend_id, 0.0)),
                        match_active_bend_length=match_active_bend_length,
                        allow_end_zone_collision=allow_end_zone_collision,
                        end_zone_length_mm=end_zone_length_mm,
                        allow_retract_warning=allow_retract_warning,
                        reverse_axis=True,
                        tolerance_mm=tolerance_mm,
                        fractions=fs if detailed else coarse_fs,
                        stroke_fractions=stroke_fs if detailed else coarse_stroke_fs,
                        backgauge_polygon_machine=backgauge_polygon_machine,
                        deadline_monotonic=start + max_search_time_s,
                        multi_station_enabled=multi_station_enabled,
                        verified_punch_span_xm=verified_punch_span_xm,
                        verified_die_span_xm=verified_die_span_xm,
                    )
                    alt_evidence = _classify_step(alt)
                    if not detailed:
                        alternative_setup_trials.append((
                            tuple(prefix), bend_id, "REVERSE_MACHINE_Z_180",
                            alt_evidence,
                        ))
                    # The GUI's live machine-cycle placement is still based
                    # on the normal orientation. Record alternative feasibility
                    # as a diagnostic but DO NOT silently treat it as a safe
                    # sequence transition until playback/transfer can use the
                    # identical physical pose.
                step = PlanarStep(
                    bend_id, evidence, stage_b.reason,
                    stage_b.sampled_phases, stage_b.contact_statuses, setup,
                    stage_b.backgauge_checked,
                    timing_ms=stage_b.timing_ms,
                    diagnostic_findings=stage_b.diagnostic_findings,
                    touch_summary=stage_b.touch_summary,
                    orientation_variant=orientation_variant,
                )
        except Exception as exc:
            step = PlanarStep(
                bend_id, "UNVERIFIED_2D",
                "planar_evaluation_error:" + type(exc).__name__ + ":" + str(exc), (), (),
            )
        finally:
            state.angles = dict(original_angles)
            state.transforms = dict(original_transforms)
        if step.evidence in ("2D_INTERFERENCE", "INVALID_INPUT", "2D_TOOLING_CONSTRAINT"):
            rejected += 1
        elif step.evidence == "UNVERIFIED_2D":
            unverified += 1
        step = PlanarStep(
            step.bend_id, step.evidence, step.reason,
            step.sampled_phases, step.contact_statuses,
            step.setup, step.backgauge_checked,
            perf_counter() - step_start, step.timing_ms, step.diagnostic_findings,
            step.touch_summary, step.orientation_variant,
        )
        step_cache[key] = step
        step_diagnostics.append((tuple(prefix), step))
        return step

    def dfs(prefix: tuple[str, ...], steps: tuple[PlanarStep, ...],
            node_ceiling: Optional[int] = None,
            root_deadline: Optional[float] = None) -> None:
        nonlocal visited, stopped, complete_candidates_examined, deepest_prefix
        nonlocal final_replay_attempts, final_replay_rejections, dead_state_cache_hits
        nonlocal probe_cutoffs, best_partial_steps, best_partial_prefix
        if len(prefix) > len(deepest_prefix):
            deepest_prefix = prefix
            if (min_partial_bends > 0
                    and min_partial_bends <= len(prefix) <= min(8, len(order) - 1)):
                best_partial_steps = steps
                best_partial_prefix = prefix
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
        # Local limits are scheduling hints, never proof of exhaustion.
        # Always retry their deferred subtrees in the unrestricted pass.
        if ((root_deadline is not None and perf_counter() >= root_deadline)
                or (node_ceiling is not None and visited >= node_ceiling)):
            probe_cutoffs += 1
            return
        mask = frozenset(prefix)
        if mask in dead_states:
            dead_state_cache_hits += 1
            return
        visited += 1
        # Subtree exhaustion is proof of deadness only if no *coarse*
        # complete order was seen and no search resource limit intervened.
        completed_before = final_replay_attempts
        cutoff_before = probe_cutoffs
        candidate_count_before = len(candidates)
        if len(prefix) == len(order):
            if progressive:
                final_replay_attempts += 1
                replay_steps = []
                for i, bend_id in enumerate(prefix):
                    if perf_counter() - start >= max_search_time_s:
                        stopped = "MAX_SEARCH_TIME_REACHED"
                        return
                    replay_step = eval_step(prefix[:i], bend_id, detailed=True)
                    if replay_step.evidence in (
                        "2D_INTERFERENCE", "INVALID_INPUT", "2D_TOOLING_CONSTRAINT"
                    ):
                        final_replay_rejections += 1
                        return
                    replay_steps.append(replay_step)
                steps = tuple(replay_steps)
            evidence = ("SAMPLED_2D_CLEAR_OR_TOUCH"
                        if all(s.evidence == "SAMPLED_2D_CLEAR_OR_TOUCH" for s in steps)
                        else "CONDITIONAL_RETRACT_WARNING_NOT_3D_VALIDATED"
                        if all(s.evidence in (
                            "SAMPLED_2D_CLEAR_OR_TOUCH",
                            "2D_RETRACT_WARNING_NOT_3D_VALIDATED",
                        ) for s in steps)
                        else "UNVERIFIED_2D")
            setups = [step.setup for step in steps]
            if all(setup is not None for setup in setups):
                score = score_sequence(prefix, setups, op_weights)
                cost, cost_complete = score.total, score.is_complete
                breakdown = tuple(p.to_dict() for p in score.steps)
            else:
                cost, cost_complete = 0.0, False
                breakdown = ()
            numbered = tuple((i + 1, bid, group_of[bid])
                             for i, bid in enumerate(prefix))
            transitions = sum(
                numbered[i - 1][2] != numbered[i][2]
                for i in range(1, len(numbered))
            )
            candidates.append(PlanarCandidate(
                prefix, steps, evidence, cost, cost_complete,
                transitions, numbered, cost_breakdown=breakdown,
            ))
            complete_candidates_examined += 1
            # Stop immediately after reaching the requested number of
            # complete candidates: do not screen another sibling first.
            if complete_candidates_examined >= max_complete_candidates:
                stopped = "MAX_COMPLETE_CANDIDATES_REACHED"
            return
        remaining = [bid for bid in priority_order if bid not in prefix]
        if group_first and prefix:
            prior_group = group_of[prefix[-1]]
            remaining.sort(key=lambda bid: (group_of[bid] != prior_group,
                                            group_of[bid], priority_index[bid]))
        elif group_first:
            remaining.sort(key=lambda bid: (group_of[bid], priority_index[bid]))
        for bend_id in remaining:
            if root_deadline is not None and perf_counter() >= root_deadline:
                probe_cutoffs += 1
                return
            if evaluated >= max_evaluations:
                stopped = "MAX_EVALUATIONS_REACHED"
                return
            if perf_counter() - start >= max_search_time_s:
                stopped = "MAX_SEARCH_TIME_REACHED"
                return
            step = eval_step(prefix, bend_id)
            if step.evidence in ("2D_INTERFERENCE", "INVALID_INPUT", "2D_TOOLING_CONSTRAINT"):
                continue
            dfs(prefix + (bend_id,), steps + (step,), node_ceiling, root_deadline)
            if stopped:
                return
        if (not stopped and probe_cutoffs == cutoff_before
                and final_replay_attempts == completed_before
                and len(candidates) == candidate_count_before):
            dead_states.add(mask)

    try:
        if (root_probe_nodes or root_probe_seconds) and len(order) >= 3:
            # Each first bend receives a time slice as well as a node budget.
            # Caching may keep the visited-node counter below its ceiling,
            # so node-count-only scheduling is not fair in practice.
            # After the bounded probes, retry deferred branches in full DFS.
            for first_bend in priority_order:
                if stopped:
                    break
                if perf_counter() - start >= max_search_time_s:
                    stopped = "MAX_SEARCH_TIME_REACHED"
                    break
                root_deadline = (
                    min(start + max_search_time_s, perf_counter() + root_probe_seconds)
                    if root_probe_seconds else None
                )
                step = eval_step((), first_bend)
                if step.evidence in ("2D_INTERFERENCE", "INVALID_INPUT", "2D_TOOLING_CONSTRAINT"):
                    continue
                dfs(
                    (first_bend,), (step,),
                    visited + root_probe_nodes if root_probe_nodes else None,
                    root_deadline,
                )
        if not stopped:
            dfs((), ())
    finally:
        state.angles = original_angles
        state.transforms = original_transforms

    # The deepest safe *sampled* prefix is a separate preview artifact.
    # Never return it as a complete candidate, even if a resource limit fired.
    partial = None
    partial_remainder = ()
    if best_partial_steps and not candidates:
        partial_seq = best_partial_prefix
        partial_remainder = tuple(b for b in order if b not in partial_seq)
        partial = PlanarCandidate(
            partial_seq, best_partial_steps,
            ("PARTIAL_COARSE_2D_WITH_RETRACT_WARNINGS_NOT_FULLY_REPLAYED"
             if any(s.evidence == "2D_RETRACT_WARNING_NOT_3D_VALIDATED"
                    for s in best_partial_steps)
             else "PARTIAL_COARSE_2D_NOT_FULLY_REPLAYED"),
            numbered_bends=tuple((i + 1, bid, group_of[bid])
                                 for i, bid in enumerate(partial_seq)),
        )
    # Cost ranking applies to all completed candidates inspected within bounds.
    # Incomplete setup metrics never outrank complete measured costs.
    candidates.sort(key=lambda c: (
        c.evidence != "SAMPLED_2D_CLEAR_OR_TOUCH",
        not c.cost_complete, c.cost, c.group_transitions, c.sequence,
    ))
    candidates = candidates[:max_solutions]
    fully_sampled = any(c.evidence == "SAMPLED_2D_CLEAR_OR_TOUCH" for c in candidates)
    # Reaching the requested number of completed candidates is a normal,
    # successful termination, not a resource/time-limit failure. Preserve
    # MAX_COMPLETE_CANDIDATES_REACHED in limit_reason for diagnostics only.
    if stopped and stopped != "MAX_COMPLETE_CANDIDATES_REACHED":
        status = PlanarSearchStatus.LIMIT_REACHED
    elif fully_sampled:
        status = PlanarSearchStatus.SAMPLED_2D_CANDIDATES
    elif candidates:
        status = PlanarSearchStatus.UNVERIFIED_CANDIDATES
    else:
        status = PlanarSearchStatus.EXHAUSTED
    # Diagnose mutual first-step obstructions based on actually performed
    # pairwise trials. These are *not* proof of global order impossibility:
    # an intervening bend, alternative setup or full 3D pose might differ.
    first_step_rejections = {
        (prefix[0], step.bend_id): step.reason
        for prefix, step in step_diagnostics
        if len(prefix) == 1 and step.evidence == "2D_INTERFERENCE"
    }
    mutually_blocked = tuple(
        (a, b, first_step_rejections[(a, b)],
         first_step_rejections[(b, a)])
        for i, a in enumerate(sorted(order))
        for b in sorted(order)[i + 1:]
        if (a, b) in first_step_rejections
        and (b, a) in first_step_rejections
    )
    return PlanarSearchReport(
        status, tuple(candidates), evaluated, visited, perf_counter()-start,
        rejected, unverified, stopped, warnings,
        axis_groups=axis_groups, complete_candidates_examined=complete_candidates_examined,
        evaluated_step_details=tuple(step_diagnostics),
        final_replay_attempts=final_replay_attempts,
        final_replay_rejections=final_replay_rejections,
        dead_state_cache_hits=dead_state_cache_hits,
        dead_states_proven=len(dead_states),
        deferred_root_probes=probe_cutoffs,
        paired_first_step_obstructions=mutually_blocked,
        partial_candidate=partial,
        partial_remainder=partial_remainder,
        conditional_end_zone_requested=bool(allow_end_zone_collision),
        alternative_setup_trials=tuple(alternative_setup_trials),
        operation_weights=op_weights.to_dict(),
        max_depth_reached=len(deepest_prefix),
        deepest_prefix=deepest_prefix,
        rejection_counts=tuple(sorted(
            (label, sum(step.evidence == label for _, step in step_diagnostics))
            for label in sorted({step.evidence for _, step in step_diagnostics})
        )),
        slowest_steps=tuple(
            ((" -> ".join(prefix)), step.bend_id, round(step.elapsed_s, 3))
            for prefix, step in sorted(
                step_diagnostics, key=lambda entry: entry[1].elapsed_s, reverse=True
            )[:5]
        ),
    )
