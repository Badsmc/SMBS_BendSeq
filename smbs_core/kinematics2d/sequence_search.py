"""Physical 2.5D + D6 bend sequence search with backtracking and forward replay.

Searches for a valid bend sequence using the authoritative 2.5D + D6 physical
collision validator (`check_current_25d_pose` + `check_active_25d_bend_trajectory`),
supporting:
- Temporary development mode where virtual punch & die lengths equal the active bend length;
- Controlled conditional allowance for punch end-zone-only collisions (`CONDITIONAL`);
- Strict cache key isolation and invalidation across completed subsets, tool lengths,
  placements, validator tolerances, and end-zone policies;
- Independent forward replay verification from the flat blank (`0°` -> step 1 -> ... -> step N).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .motion_trajectory_3d import AdaptiveSamplingConfig
from .physical_validation_25d import (
    CurrentPosePhysicalReport,
    PhysicalEvidence,
    check_active_25d_bend_trajectory,
    check_current_25d_pose,
)


class SearchStatus(str, Enum):
    STRICT_VALIDATED = "STRICT_VALIDATED"
    CONDITIONAL_VALIDATED = "CONDITIONAL_VALIDATED"
    NOT_FOUND_WITHIN_SEARCH_LIMITS = "NOT_FOUND_WITHIN_SEARCH_LIMITS"
    NO_FEASIBLE_SEQUENCE = "NO_FEASIBLE_SEQUENCE"
    UNVERIFIED = "UNVERIFIED"
    CANCELLED = "CANCELLED"


@dataclass
class StepEvaluationRecord:
    """Record of a single bend step evaluation during sequence search or replay."""
    bend_id: str
    step_index: int
    completed_prefix: Tuple[str, ...]
    pose_status: PhysicalEvidence
    trajectory_status: Optional[PhysicalEvidence]
    effective_status: PhysicalEvidence
    reason: str
    active_bend_length_mm: float = 0.0
    virtual_punch_length_mm: Optional[float] = None
    virtual_die_length_mm: Optional[float] = None
    is_virtual_tool_geometry: bool = False
    desired_midpoint_xm: float = 0.0
    is_conditional: bool = False
    end_zone_classification: Optional[str] = None
    end_zone_affected_side: Optional[str] = None
    end_zone_overlap_volume_mm3: float = 0.0
    end_zone_length_mm: float = 0.0
    punch_overlap_volume_mm3: float = 0.0
    die_overlap_volume_mm3: float = 0.0
    elapsed_ms: float = 0.0
    from_cache: bool = False
    tool_length_info: Optional[Dict[str, Any]] = None
    pose_report: Optional[CurrentPosePhysicalReport] = None
    trajectory_report: Optional[CurrentPosePhysicalReport] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bend_id": self.bend_id,
            "step_index": self.step_index,
            "completed_prefix": list(self.completed_prefix),
            "pose_status": self.pose_status.value if isinstance(self.pose_status, PhysicalEvidence) else str(self.pose_status),
            "trajectory_status": (
                self.trajectory_status.value
                if isinstance(self.trajectory_status, PhysicalEvidence)
                else (str(self.trajectory_status) if self.trajectory_status is not None else None)
            ),
            "effective_status": self.effective_status.value if isinstance(self.effective_status, PhysicalEvidence) else str(self.effective_status),
            "reason": self.reason,
            "active_bend_length_mm": round(float(self.active_bend_length_mm), 4),
            "virtual_punch_length_mm": (
                round(float(self.virtual_punch_length_mm), 4)
                if self.virtual_punch_length_mm is not None
                else None
            ),
            "virtual_die_length_mm": (
                round(float(self.virtual_die_length_mm), 4)
                if self.virtual_die_length_mm is not None
                else None
            ),
            "is_virtual_tool_geometry": bool(self.is_virtual_tool_geometry),
            "desired_midpoint_xm": round(float(self.desired_midpoint_xm), 4),
            "is_conditional": bool(self.is_conditional),
            "end_zone_classification": self.end_zone_classification,
            "end_zone_affected_side": self.end_zone_affected_side,
            "end_zone_overlap_volume_mm3": round(float(self.end_zone_overlap_volume_mm3), 6),
            "end_zone_length_mm": round(float(self.end_zone_length_mm), 4),
            "punch_overlap_volume_mm3": round(float(self.punch_overlap_volume_mm3), 6),
            "die_overlap_volume_mm3": round(float(self.die_overlap_volume_mm3), 6),
            "elapsed_ms": round(float(self.elapsed_ms), 2),
            "from_cache": bool(self.from_cache),
            "tool_length_info": self.tool_length_info,
        }


@dataclass
class ForwardReplayReport:
    """Result of independent forward replay verification from flat sheet (0°)."""
    sequence: Tuple[str, ...]
    overall_status: PhysicalEvidence
    is_verified: bool
    is_conditional: bool
    steps: List[StepEvaluationRecord] = field(default_factory=list)
    conditional_bends: List[str] = field(default_factory=list)
    blocking_bend_id: Optional[str] = None
    blocking_reason: str = ""
    elapsed_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sequence": list(self.sequence),
            "overall_status": self.overall_status.value,
            "is_verified": bool(self.is_verified),
            "is_conditional": bool(self.is_conditional),
            "conditional_bends": list(self.conditional_bends),
            "blocking_bend_id": self.blocking_bend_id,
            "blocking_reason": self.blocking_reason,
            "elapsed_ms": round(float(self.elapsed_ms), 2),
            "steps": [s.to_dict() for s in self.steps],
        }


@dataclass
class SequenceSearchReport:
    """Comprehensive report from 2.5D + D6 backtracking sequence search."""
    status: SearchStatus
    overall_evidence: PhysicalEvidence
    found_sequence: Optional[Tuple[str, ...]]
    initial_sequence: Tuple[str, ...]
    best_partial_sequence: Tuple[str, ...]
    max_depth_reached: int
    total_bends: int
    states_visited: int
    step_evaluations: int
    cache_hits: int
    dead_end_hits: int
    backtracks_count: int
    elapsed_s: float
    match_active_bend_length: bool
    allow_end_zone_collision: bool
    end_zone_length_mm: float
    strict_search_attempted: bool = True
    strict_search_exhausted: bool = False
    strict_search_limit_reached: bool = False
    strict_search_note: str = ""
    conditional_bends: List[str] = field(default_factory=list)
    winning_steps: List[StepEvaluationRecord] = field(default_factory=list)
    forward_replay: Optional[ForwardReplayReport] = None
    rejection_summary: Dict[str, List[str]] = field(default_factory=dict)
    search_limit_reason: str = ""
    message_ru: str = ""

    @property
    def is_strict_pass(self) -> bool:
        return (
            self.status is SearchStatus.STRICT_VALIDATED
            and self.overall_evidence is PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS
            and self.forward_replay is not None
            and self.forward_replay.is_verified
            and not self.forward_replay.is_conditional
        )

    @property
    def is_conditional_pass(self) -> bool:
        return (
            self.status is SearchStatus.CONDITIONAL_VALIDATED
            and self.overall_evidence is PhysicalEvidence.CONDITIONAL
            and self.forward_replay is not None
            and self.forward_replay.is_verified
            and self.forward_replay.is_conditional
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "overall_evidence": self.overall_evidence.value,
            "is_strict_pass": self.is_strict_pass,
            "is_conditional_pass": self.is_conditional_pass,
            "found_sequence": list(self.found_sequence) if self.found_sequence is not None else None,
            "initial_sequence": list(self.initial_sequence),
            "best_partial_sequence": list(self.best_partial_sequence),
            "max_depth_reached": int(self.max_depth_reached),
            "total_bends": int(self.total_bends),
            "states_visited": int(self.states_visited),
            "step_evaluations": int(self.step_evaluations),
            "cache_hits": int(self.cache_hits),
            "dead_end_hits": int(self.dead_end_hits),
            "backtracks_count": int(self.backtracks_count),
            "elapsed_s": round(float(self.elapsed_s), 3),
            "match_active_bend_length": bool(self.match_active_bend_length),
            "allow_end_zone_collision": bool(self.allow_end_zone_collision),
            "end_zone_length_mm": round(float(self.end_zone_length_mm), 4),
            "strict_search_attempted": bool(self.strict_search_attempted),
            "strict_search_exhausted": bool(self.strict_search_exhausted),
            "strict_search_limit_reached": bool(self.strict_search_limit_reached),
            "strict_search_note": self.strict_search_note,
            "conditional_bends": list(self.conditional_bends),
            "winning_steps": [s.to_dict() for s in self.winning_steps],
            "forward_replay": self.forward_replay.to_dict() if self.forward_replay is not None else None,
            "rejection_summary": {k: list(v) for k, v in self.rejection_summary.items()},
            "search_limit_reason": self.search_limit_reason,
            "message_ru": self.message_ru,
        }


class StepEvaluationCache:
    """Cache of step evaluations and dead-end subsets with full parameter keys.

    Keys strictly incorporate:
    1. Completed bend IDs and signed angles (`frozenset`)
    2. Candidate bend ID
    3. Selected punch & die IDs and source files
    4. Virtual tool length mode and effective punch/die lengths
    5. Longitudinal placement (`desired_midpoint_xm`)
    6. Validator config & tolerances
    7. End-zone collision policy (`allow_end_zone_collision`, `end_zone_length_mm`)
    """

    def __init__(self) -> None:
        self._step_cache: Dict[Tuple[Any, ...], StepEvaluationRecord] = {}
        self._dead_end_subsets: set[Tuple[Any, ...]] = set()

    def clear(self) -> None:
        self._step_cache.clear()
        self._dead_end_subsets.clear()

    @staticmethod
    def build_config_signature(config: Optional[AdaptiveSamplingConfig]) -> Tuple[Any, ...]:
        cfg = config or AdaptiveSamplingConfig()
        return (
            int(cfg.arc_segments),
            round(float(cfg.near_tolerance_mm), 6),
            round(float(cfg.volume_tolerance_mm3), 6),
            round(float(cfg.contact_distance_tolerance_mm), 6),
            round(float(cfg.max_bend_angle_step_deg), 4),
            round(float(cfg.max_ram_displacement_step_mm), 4),
            int(cfg.max_recursion_depth),
            int(cfg.max_evaluated_states_per_phase),
        )

    @staticmethod
    def build_tool_signature(
        tooling,
        match_active_bend_length: bool,
        effective_punch_len_mm: float,
        effective_die_len_mm: float,
        desired_midpoint_xm: float,
    ) -> Tuple[Any, ...]:
        punch = getattr(tooling, "punch", None)
        die = getattr(tooling, "die", None)
        return (
            str(getattr(punch, "tool_id", "")),
            str(getattr(punch, "source_file", "")),
            str(getattr(die, "tool_id", "")),
            str(getattr(die, "source_file", "")),
            bool(match_active_bend_length),
            round(float(effective_punch_len_mm), 5),
            round(float(effective_die_len_mm), 5),
            round(float(desired_midpoint_xm), 5),
        )

    @staticmethod
    def build_policy_signature(
        allow_end_zone_collision: bool,
        end_zone_length_mm: float,
    ) -> Tuple[Any, ...]:
        return (
            bool(allow_end_zone_collision),
            round(float(end_zone_length_mm), 5),
        )

    @classmethod
    def make_step_key(
        cls,
        completed_angles: Dict[str, float],
        candidate_bend_id: str,
        tooling,
        match_active_bend_length: bool,
        effective_punch_len_mm: float,
        effective_die_len_mm: float,
        desired_midpoint_xm: float,
        config: Optional[AdaptiveSamplingConfig],
        allow_end_zone_collision: bool,
        end_zone_length_mm: float,
    ) -> Tuple[Any, ...]:
        completed_sig = frozenset(
            (str(bid), round(float(ang), 5))
            for bid, ang in completed_angles.items()
            if abs(float(ang)) > 1e-6
        )
        return (
            completed_sig,
            str(candidate_bend_id),
            cls.build_tool_signature(
                tooling,
                match_active_bend_length,
                effective_punch_len_mm,
                effective_die_len_mm,
                desired_midpoint_xm,
            ),
            cls.build_config_signature(config),
            cls.build_policy_signature(allow_end_zone_collision, end_zone_length_mm),
        )

    @classmethod
    def make_subset_key(
        cls,
        completed_angles: Dict[str, float],
        tooling,
        match_active_bend_length: bool,
        bend_shifts: Dict[str, float],
        config: Optional[AdaptiveSamplingConfig],
        allow_end_zone_collision: bool,
        end_zone_length_mm: float,
    ) -> Tuple[Any, ...]:
        completed_sig = frozenset(
            (str(bid), round(float(ang), 5))
            for bid, ang in completed_angles.items()
            if abs(float(ang)) > 1e-6
        )
        shifts_sig = tuple(sorted((str(k), round(float(v), 5)) for k, v in (bend_shifts or {}).items()))
        punch = getattr(tooling, "punch", None)
        die = getattr(tooling, "die", None)
        return (
            completed_sig,
            str(getattr(punch, "tool_id", "")),
            str(getattr(die, "tool_id", "")),
            bool(match_active_bend_length),
            shifts_sig,
            cls.build_config_signature(config),
            cls.build_policy_signature(allow_end_zone_collision, end_zone_length_mm),
        )

    def get_step(self, key: Tuple[Any, ...]) -> Optional[StepEvaluationRecord]:
        return self._step_cache.get(key)

    def put_step(self, key: Tuple[Any, ...], record: StepEvaluationRecord) -> None:
        self._step_cache[key] = record

    def is_dead_end(self, subset_key: Tuple[Any, ...]) -> bool:
        return subset_key in self._dead_end_subsets

    def mark_dead_end(self, subset_key: Tuple[Any, ...]) -> None:
        self._dead_end_subsets.add(subset_key)


def _select_bend_index_compat(session, idx: int) -> None:
    if hasattr(session, "select_bend_index"):
        session.select_bend_index(int(idx))
    elif hasattr(session, "select_bend"):
        session.select_bend(int(idx))
    else:
        session.active_bend_index = int(idx)
        order = list(getattr(session, "bend_order", ()))
        if 0 <= int(idx) < len(order):
            session.active_bend_id = order[int(idx)]


def _reset_session_compat(session) -> None:
    if hasattr(session, "reset_all_bends"):
        session.reset_all_bends()
    elif hasattr(session, "reset"):
        session.reset()
    elif hasattr(getattr(session, "fold_state", None), "reset_all"):
        session.fold_state.reset_all()
        _select_bend_index_compat(session, 0)


def _apply_prefix_to_session(
    session,
    press_cycle,
    tooling,
    completed_prefix: Tuple[str, ...],
    candidate_bend_id: str,
    remaining_after_candidate: Tuple[str, ...],
    match_active_bend_length: bool,
    allow_end_zone_collision: bool,
    end_zone_length_mm: float,
) -> Dict[str, float]:
    """Deterministically set session order and FoldState to represent `completed_prefix`
    followed by `candidate_bend_id` as the active bend at 0.0 deg."""
    order = list(completed_prefix) + [candidate_bend_id] + list(remaining_after_candidate)
    session.set_bend_order(order)
    state = session.fold_state
    if hasattr(state, "reset_all"):
        state.reset_all()
    else:
        for k in list(state.angles.keys()):
            state.angles[k] = 0.0
    completed_angles: Dict[str, float] = {}
    for bid in completed_prefix:
        target = float(state.bends[bid].angle_deg)
        state.set_angle(bid, target)
        completed_angles[bid] = target
    state.set_angle(candidate_bend_id, 0.0)
    _select_bend_index_compat(session, len(completed_prefix))

    bend = state.bends[candidate_bend_id]
    axis = getattr(bend, "axis_xyz", None)
    bend_len = math.dist(axis[0], axis[1]) if axis is not None else 0.0
    if hasattr(tooling, "set_match_active_bend_length"):
        tooling.set_match_active_bend_length(match_active_bend_length, bend_len)
    else:
        tooling.match_active_bend_length = bool(match_active_bend_length)
        tooling.active_bend_length_mm = float(bend_len)
    tooling.allow_end_zone_collision = bool(allow_end_zone_collision)
    tooling.end_zone_length_mm = float(end_zone_length_mm)

    if press_cycle is not None:
        if hasattr(press_cycle, "on_tooling_changed"):
            press_cycle.on_tooling_changed()
        elif hasattr(press_cycle, "sync_to_active_bend"):
            press_cycle.sync_to_active_bend()
    return completed_angles


def _sync_tooling_and_cycle_for_active_bend(
    session,
    press_cycle,
    tooling,
    match_active_bend_length: bool,
    allow_end_zone_collision: bool,
    end_zone_length_mm: float,
) -> float:
    """Synchronize tooling virtual length, alignment, and press_cycle for `session.active_bend_id`."""
    state = session.fold_state
    active_id = session.active_bend_id
    bend = state.bends[active_id]
    axis = getattr(bend, "axis_xyz", None)
    bend_len = math.dist(axis[0], axis[1]) if axis is not None else 0.0

    if hasattr(tooling, "set_match_active_bend_length"):
        tooling.set_match_active_bend_length(match_active_bend_length, bend_len)
    else:
        tooling.match_active_bend_length = bool(match_active_bend_length)
        tooling.active_bend_length_mm = float(bend_len)
    tooling.allow_end_zone_collision = bool(allow_end_zone_collision)
    tooling.end_zone_length_mm = float(end_zone_length_mm)

    if press_cycle is not None:
        if hasattr(press_cycle, "on_tooling_changed"):
            press_cycle.on_tooling_changed()
        elif hasattr(press_cycle, "sync_to_active_bend"):
            press_cycle.sync_to_active_bend()
    return bend_len


def evaluate_candidate_bend_step(
    session,
    press_cycle,
    tooling,
    completed_prefix: Tuple[str, ...],
    candidate_bend_id: str,
    remaining_after_candidate: Tuple[str, ...],
    *,
    config: Optional[AdaptiveSamplingConfig] = None,
    desired_midpoint_xm: float = 0.0,
    match_active_bend_length: bool = False,
    allow_end_zone_collision: bool = False,
    end_zone_length_mm: float = 0.0,
    cache: Optional[StepEvaluationCache] = None,
    pose_checker: Callable[..., CurrentPosePhysicalReport] = check_current_25d_pose,
    trajectory_checker: Callable[..., CurrentPosePhysicalReport] = check_active_25d_bend_trajectory,
) -> StepEvaluationRecord:
    """Evaluate a single candidate bend step (initial contact pose + 5-phase trajectory)."""
    t0 = time.perf_counter()
    state = session.fold_state
    order = list(completed_prefix) + [candidate_bend_id] + list(remaining_after_candidate)
    session.set_bend_order(order)
    if hasattr(state, "reset_all"):
        state.reset_all()
    else:
        for k in list(state.angles.keys()):
            state.angles[k] = 0.0
    completed_angles: Dict[str, float] = {}
    for bid in completed_prefix:
        target = float(state.bends[bid].angle_deg)
        state.set_angle(bid, target)
        completed_angles[bid] = target
    state.set_angle(candidate_bend_id, 0.0)
    _select_bend_index_compat(session, len(completed_prefix))

    bend_len = _sync_tooling_and_cycle_for_active_bend(
        session,
        press_cycle,
        tooling,
        match_active_bend_length=match_active_bend_length,
        allow_end_zone_collision=allow_end_zone_collision,
        end_zone_length_mm=end_zone_length_mm,
    )
    eff_p_len = (
        tooling.effective_punch_length_mm(bend_len)
        if hasattr(tooling, "effective_punch_length_mm")
        else (bend_len if match_active_bend_length else float(getattr(getattr(tooling, "punch", None), "length_mm", 0.0)))
    )
    eff_d_len = (
        tooling.effective_die_length_mm(bend_len)
        if hasattr(tooling, "effective_die_length_mm")
        else (bend_len if match_active_bend_length else float(getattr(getattr(tooling, "die", None), "length_mm", 0.0)))
    )

    step_key = None
    if cache is not None:
        step_key = cache.make_step_key(
            completed_angles=completed_angles,
            candidate_bend_id=candidate_bend_id,
            tooling=tooling,
            match_active_bend_length=match_active_bend_length,
            effective_punch_len_mm=eff_p_len,
            effective_die_len_mm=eff_d_len,
            desired_midpoint_xm=desired_midpoint_xm,
            config=config,
            allow_end_zone_collision=allow_end_zone_collision,
            end_zone_length_mm=end_zone_length_mm,
        )
        cached = cache.get_step(step_key)
        if cached is None and allow_end_zone_collision:
            strict_key = cache.make_step_key(
                completed_angles=completed_angles,
                candidate_bend_id=candidate_bend_id,
                tooling=tooling,
                match_active_bend_length=match_active_bend_length,
                effective_punch_len_mm=eff_p_len,
                effective_die_len_mm=eff_d_len,
                desired_midpoint_xm=desired_midpoint_xm,
                config=config,
                allow_end_zone_collision=False,
                end_zone_length_mm=end_zone_length_mm,
            )
            strict_cached = cache.get_step(strict_key)
            if strict_cached is not None and (
                strict_cached.effective_status is PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS
                or (
                    strict_cached.effective_status is PhysicalEvidence.COLLISION
                    and strict_cached.end_zone_classification in ("CENTRAL_ZONE", "MIXED_CENTRAL_AND_END_ZONE")
                )
            ):
                cached = strict_cached
        if cached is not None:
            return StepEvaluationRecord(
                bend_id=cached.bend_id,
                step_index=len(completed_prefix),
                completed_prefix=completed_prefix,
                pose_status=cached.pose_status,
                trajectory_status=cached.trajectory_status,
                effective_status=cached.effective_status,
                reason=cached.reason,
                active_bend_length_mm=cached.active_bend_length_mm,
                virtual_punch_length_mm=cached.virtual_punch_length_mm,
                virtual_die_length_mm=cached.virtual_die_length_mm,
                is_virtual_tool_geometry=cached.is_virtual_tool_geometry,
                desired_midpoint_xm=cached.desired_midpoint_xm,
                is_conditional=cached.is_conditional,
                end_zone_classification=cached.end_zone_classification,
                end_zone_affected_side=cached.end_zone_affected_side,
                end_zone_overlap_volume_mm3=cached.end_zone_overlap_volume_mm3,
                end_zone_length_mm=cached.end_zone_length_mm,
                punch_overlap_volume_mm3=cached.punch_overlap_volume_mm3,
                die_overlap_volume_mm3=cached.die_overlap_volume_mm3,
                elapsed_ms=0.0,
                from_cache=True,
                tool_length_info=cached.tool_length_info,
                pose_report=cached.pose_report,
                trajectory_report=cached.trajectory_report,
            )

    # 1. Initial contact pose check
    pose_rep = pose_checker(
        session,
        press_cycle,
        tooling,
        native_3d=True,
        desired_midpoint_xm=desired_midpoint_xm,
        match_active_bend_length=match_active_bend_length,
        allow_end_zone_collision=allow_end_zone_collision,
        end_zone_length_mm=end_zone_length_mm,
        classify_end_zone_length_mm=end_zone_length_mm,
    )
    pose_ok = pose_rep.status in (
        PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
        PhysicalEvidence.CONDITIONAL,
    )

    traj_rep: Optional[CurrentPosePhysicalReport] = None
    if not pose_ok:
        eff_status = pose_rep.status
        reason = f"initial_pose:{pose_rep.reason}"
    else:
        # 2. Full 5-phase bend trajectory check
        traj_rep = trajectory_checker(
            session,
            tooling,
            config=config,
            desired_midpoint_xm=desired_midpoint_xm,
            match_active_bend_length=match_active_bend_length,
            allow_end_zone_collision=allow_end_zone_collision,
            end_zone_length_mm=end_zone_length_mm,
            classify_end_zone_length_mm=end_zone_length_mm,
        )
        if traj_rep.status is PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS:
            if pose_rep.status is PhysicalEvidence.CONDITIONAL:
                eff_status = PhysicalEvidence.CONDITIONAL
                reason = f"pose_conditional_traj_clear:{pose_rep.reason}"
            else:
                eff_status = PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS
                reason = traj_rep.reason
        elif traj_rep.status is PhysicalEvidence.CONDITIONAL:
            eff_status = PhysicalEvidence.CONDITIONAL
            reason = traj_rep.reason
        else:
            eff_status = traj_rep.status
            reason = f"trajectory:{traj_rep.reason}"

    tool_info = (
        (traj_rep.tool_length_info if traj_rep and traj_rep.tool_length_info else None)
        or pose_rep.tool_length_info
        or {}
    )
    ez_analysis = (
        (traj_rep.end_zone_analysis if traj_rep and traj_rep.end_zone_analysis is not None else None)
        or pose_rep.end_zone_analysis
    )

    p_ov = 0.0
    d_ov = 0.0
    if traj_rep is not None and traj_rep.raw_report is not None:
        raw_t = traj_rep.raw_report
        for ph in getattr(raw_t, "phase_results", []) or []:
            p_ov = max(p_ov, float(getattr(ph, "max_punch_overlap_volume_mm3", 0.0)))
            d_ov = max(d_ov, float(getattr(ph, "max_die_overlap_volume_mm3", 0.0)))
    elif pose_rep.raw_report is not None:
        raw_p = pose_rep.raw_report
        if getattr(raw_p, "punch_result", None) is not None:
            p_ov = float(getattr(raw_p.punch_result, "overlap_volume_mm3", 0.0))
        if getattr(raw_p, "die_result", None) is not None:
            d_ov = float(getattr(raw_p.die_result, "overlap_volume_mm3", 0.0))

    ez_class = None
    ez_side = None
    ez_vol = 0.0
    if ez_analysis is not None:
        c_obj = getattr(ez_analysis, "classification", None)
        ez_class = getattr(c_obj, "value", str(c_obj)) if c_obj is not None else None
        ez_side = getattr(ez_analysis, "affected_side", None)
        ez_vol = float(getattr(ez_analysis, "total_overlap_volume_mm3", 0.0))

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    record = StepEvaluationRecord(
        bend_id=candidate_bend_id,
        step_index=len(completed_prefix),
        completed_prefix=completed_prefix,
        pose_status=pose_rep.status,
        trajectory_status=traj_rep.status if traj_rep is not None else None,
        effective_status=eff_status,
        reason=reason,
        active_bend_length_mm=float(tool_info.get("active_bend_length_mm", bend_len)),
        virtual_punch_length_mm=tool_info.get("punch_virtual_length_mm", bend_len if match_active_bend_length else None),
        virtual_die_length_mm=tool_info.get("die_virtual_length_mm", bend_len if match_active_bend_length else None),
        is_virtual_tool_geometry=bool(tool_info.get("is_virtual_tool_geometry", match_active_bend_length)),
        desired_midpoint_xm=float(desired_midpoint_xm),
        is_conditional=(eff_status is PhysicalEvidence.CONDITIONAL),
        end_zone_classification=ez_class,
        end_zone_affected_side=ez_side,
        end_zone_overlap_volume_mm3=ez_vol,
        end_zone_length_mm=float(end_zone_length_mm),
        punch_overlap_volume_mm3=p_ov,
        die_overlap_volume_mm3=d_ov,
        elapsed_ms=elapsed_ms,
        from_cache=False,
        tool_length_info=tool_info if tool_info else None,
        pose_report=pose_rep,
        trajectory_report=traj_rep,
    )
    if cache is not None and step_key is not None:
        cache.put_step(step_key, record)
    return record


def verify_sequence_forward_replay(
    session,
    press_cycle,
    tooling,
    sequence: Tuple[str, ...],
    *,
    config: Optional[AdaptiveSamplingConfig] = None,
    bend_shifts: Optional[Dict[str, float]] = None,
    match_active_bend_length: bool = False,
    allow_end_zone_collision: bool = False,
    end_zone_length_mm: float = 0.0,
    pose_checker: Callable[..., CurrentPosePhysicalReport] = check_current_25d_pose,
    trajectory_checker: Callable[..., CurrentPosePhysicalReport] = check_active_25d_bend_trajectory,
) -> ForwardReplayReport:
    """Independently verify a complete bend sequence by replaying from flat sheet (0°).

    Does NOT use the search cache — re-evaluates every step from 0° -> step 1 -> ... -> step N.
    Restores the session to the start of `sequence` (0° on all bends) upon completion.
    """
    t0 = time.perf_counter()
    shifts = dict(bend_shifts or {})
    steps: List[StepEvaluationRecord] = []
    conditional_bends: List[str] = []
    blocking_bend_id: Optional[str] = None
    blocking_reason: str = ""

    session.set_bend_order(list(sequence))
    _reset_session_compat(session)

    for idx, bid in enumerate(sequence):
        prefix = tuple(sequence[:idx])
        remaining = tuple(sequence[idx + 1:])
        shift_mm = float(shifts.get(bid, 0.0))
        rec = evaluate_candidate_bend_step(
            session,
            press_cycle,
            tooling,
            completed_prefix=prefix,
            candidate_bend_id=bid,
            remaining_after_candidate=remaining,
            config=config,
            desired_midpoint_xm=shift_mm,
            match_active_bend_length=match_active_bend_length,
            allow_end_zone_collision=allow_end_zone_collision,
            end_zone_length_mm=end_zone_length_mm,
            cache=None,  # Independent replay never uses cached search evaluations
            pose_checker=pose_checker,
            trajectory_checker=trajectory_checker,
        )
        steps.append(rec)
        if rec.effective_status is PhysicalEvidence.CONDITIONAL:
            conditional_bends.append(bid)
        elif rec.effective_status is not PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS:
            blocking_bend_id = bid
            blocking_reason = rec.reason
            break

    # Leave session ordered by `sequence` and reset to flat state (0° on all bends)
    session.set_bend_order(list(sequence))
    _reset_session_compat(session)
    _sync_tooling_and_cycle_for_active_bend(
        session,
        press_cycle,
        tooling,
        match_active_bend_length=match_active_bend_length,
        allow_end_zone_collision=allow_end_zone_collision,
        end_zone_length_mm=end_zone_length_mm,
    )

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    if blocking_bend_id is not None:
        first_bad = steps[-1].effective_status if steps else PhysicalEvidence.UNVERIFIED
        return ForwardReplayReport(
            sequence=tuple(sequence),
            overall_status=first_bad,
            is_verified=False,
            is_conditional=False,
            steps=steps,
            conditional_bends=conditional_bends,
            blocking_bend_id=blocking_bend_id,
            blocking_reason=blocking_reason,
            elapsed_ms=elapsed_ms,
        )

    if conditional_bends:
        return ForwardReplayReport(
            sequence=tuple(sequence),
            overall_status=PhysicalEvidence.CONDITIONAL,
            is_verified=True,
            is_conditional=True,
            steps=steps,
            conditional_bends=conditional_bends,
            elapsed_ms=elapsed_ms,
        )

    return ForwardReplayReport(
        sequence=tuple(sequence),
        overall_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
        is_verified=True,
        is_conditional=False,
        steps=steps,
        conditional_bends=[],
        elapsed_ms=elapsed_ms,
    )


def search_bend_sequence_25d(
    session,
    press_cycle,
    tooling,
    *,
    config: Optional[AdaptiveSamplingConfig] = None,
    bend_shifts: Optional[Dict[str, float]] = None,
    match_active_bend_length: Optional[bool] = None,
    allow_end_zone_collision: Optional[bool] = None,
    end_zone_length_mm: Optional[float] = None,
    max_states: int = 500,
    max_checks: int = 1000,
    max_search_time_s: float = 600.0,
    cache: Optional[StepEvaluationCache] = None,
    progress_callback: Optional[Callable[[int, int, Tuple[str, ...]], bool]] = None,
    pose_checker: Callable[..., CurrentPosePhysicalReport] = check_current_25d_pose,
    trajectory_checker: Callable[..., CurrentPosePhysicalReport] = check_active_25d_bend_trajectory,
) -> SequenceSearchReport:
    """Search for a physically valid bend sequence using DFS backtracking and memoization.

    Priority rules:
    1. Stage 1 searches strictly without end-zone collisions (`allow_end_zone_collision=False`).
    2. If Stage 1 finds a strict sequence, it is verified via independent forward replay and
       returned as `SearchStatus.STRICT_VALIDATED`.
    3. If Stage 1 does not find a strict sequence and `allow_end_zone_collision=True` was
       explicitly enabled, Stage 2 searches allowing `PhysicalEvidence.CONDITIONAL` steps
       (`END_ZONE_ONLY` punch overlaps), prioritizing strictly clear steps over conditional
       steps at every branch.
    4. Any found sequence is independently verified via forward replay from `0°` and loaded
       into `session` ready for playback. If no sequence is found, `session` is restored to
       its original order and state.
    """
    t_start = time.perf_counter()
    if session is None or getattr(session, "fold_state", None) is None:
        return SequenceSearchReport(
            status=SearchStatus.UNVERIFIED,
            overall_evidence=PhysicalEvidence.UNVERIFIED,
            found_sequence=None,
            initial_sequence=(),
            best_partial_sequence=(),
            max_depth_reached=0,
            total_bends=0,
            states_visited=0,
            step_evaluations=0,
            cache_hits=0,
            dead_end_hits=0,
            backtracks_count=0,
            elapsed_s=0.0,
            match_active_bend_length=False,
            allow_end_zone_collision=False,
            end_zone_length_mm=0.0,
            message_ru="Деталь не загружена (UNVERIFIED).",
        )

    eff_match_len = (
        bool(getattr(tooling, "match_active_bend_length", False))
        if match_active_bend_length is None
        else bool(match_active_bend_length)
    )
    eff_allow_end_zone = (
        bool(getattr(tooling, "allow_end_zone_collision", False))
        if allow_end_zone_collision is None
        else bool(allow_end_zone_collision)
    )
    eff_end_zone_len = (
        float(getattr(tooling, "end_zone_length_mm", 0.0))
        if end_zone_length_mm is None
        else float(end_zone_length_mm)
    )
    shifts = dict(bend_shifts or {})
    eval_cache = cache if cache is not None else StepEvaluationCache()

    initial_order = tuple(session.bend_order)
    initial_active_index = int(getattr(session, "active_bend_index", 0))
    initial_angles = dict(session.fold_state.angles)
    initial_transforms = dict(session.fold_state.transforms)
    total_bends = len(initial_order)

    states_visited = 0
    step_evaluations = 0
    cache_hits = 0
    dead_end_hits = 0
    backtracks_count = 0
    best_partial: Tuple[str, ...] = ()
    rejection_summary: Dict[str, List[str]] = {}
    cancelled = False

    def _record_rejection(bid: str, reason: str) -> None:
        lst = rejection_summary.setdefault(bid, [])
        if len(lst) < 10 and reason not in lst:
            lst.append(reason)

    def _run_dfs_stage(
        stage_allow_end_zone: bool,
        stage_time_limit_s: float,
        stage_max_states: int,
        stage_max_checks: int,
    ) -> Tuple[Optional[Tuple[str, ...]], List[StepEvaluationRecord], bool, str]:
        """Run one stage of DFS search.
        Returns (found_sequence, winning_steps, limit_reached, limit_reason).
        """
        nonlocal states_visited, step_evaluations, cache_hits, dead_end_hits, backtracks_count, best_partial, cancelled
        stage_limit_reached = False
        stage_limit_reason = ""
        winning_steps_out: List[StepEvaluationRecord] = []

        def dfs(
            completed_prefix: Tuple[str, ...],
            remaining: Tuple[str, ...],
            path_records: List[StepEvaluationRecord],
        ) -> Optional[Tuple[str, ...]]:
            nonlocal states_visited, step_evaluations, cache_hits, dead_end_hits, backtracks_count, best_partial, stage_limit_reached, stage_limit_reason, cancelled

            if len(completed_prefix) > len(best_partial):
                best_partial = completed_prefix

            if not remaining:
                winning_steps_out.extend(path_records)
                return completed_prefix

            if progress_callback is not None:
                if not progress_callback(states_visited, step_evaluations, completed_prefix):
                    cancelled = True
                    return None

            elapsed = time.perf_counter() - t_start
            if elapsed >= stage_time_limit_s:
                stage_limit_reached = True
                stage_limit_reason = f"MAX_SEARCH_TIME_REACHED:{elapsed:.2f}s>={stage_time_limit_s:.2f}s"
                return None
            if states_visited >= stage_max_states:
                stage_limit_reached = True
                stage_limit_reason = f"MAX_SEARCH_STATES_REACHED:{states_visited}>={stage_max_states}"
                return None
            if step_evaluations >= stage_max_checks:
                stage_limit_reached = True
                stage_limit_reason = f"MAX_SEARCH_CHECKS_REACHED:{step_evaluations}>={stage_max_checks}"
                return None

            completed_angles = {
                bid: float(session.fold_state.bends[bid].angle_deg)
                for bid in completed_prefix
            }
            subset_key = eval_cache.make_subset_key(
                completed_angles=completed_angles,
                tooling=tooling,
                match_active_bend_length=eff_match_len,
                bend_shifts=shifts,
                config=config,
                allow_end_zone_collision=stage_allow_end_zone,
                end_zone_length_mm=eff_end_zone_len,
            )
            if eval_cache.is_dead_end(subset_key):
                dead_end_hits += 1
                return None

            states_visited += 1

            # Evaluate or lookup candidates at this branch
            strict_candidates: List[Tuple[str, Tuple[str, ...], StepEvaluationRecord]] = []
            conditional_candidates: List[Tuple[str, Tuple[str, ...], StepEvaluationRecord]] = []

            for idx, cand_id in enumerate(remaining):
                if cancelled:
                    return None
                elapsed = time.perf_counter() - t_start
                if elapsed >= stage_time_limit_s:
                    stage_limit_reached = True
                    stage_limit_reason = f"MAX_SEARCH_TIME_REACHED:{elapsed:.2f}s>={stage_time_limit_s:.2f}s"
                    break
                if step_evaluations >= stage_max_checks:
                    stage_limit_reached = True
                    stage_limit_reason = f"MAX_SEARCH_CHECKS_REACHED:{step_evaluations}>={stage_max_checks}"
                    break

                rem_after = remaining[:idx] + remaining[idx + 1:]
                shift_mm = float(shifts.get(cand_id, 0.0))
                rec = evaluate_candidate_bend_step(
                    session,
                    press_cycle,
                    tooling,
                    completed_prefix=completed_prefix,
                    candidate_bend_id=cand_id,
                    remaining_after_candidate=rem_after,
                    config=config,
                    desired_midpoint_xm=shift_mm,
                    match_active_bend_length=eff_match_len,
                    allow_end_zone_collision=stage_allow_end_zone,
                    end_zone_length_mm=eff_end_zone_len,
                    cache=eval_cache,
                    pose_checker=pose_checker,
                    trajectory_checker=trajectory_checker,
                )
                if rec.from_cache:
                    cache_hits += 1
                else:
                    step_evaluations += 1

                if rec.effective_status is PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS:
                    strict_candidates.append((cand_id, rem_after, rec))
                    # In Stage 1 (strict search), we can immediately recurse depth-first on the first valid candidate
                    if not stage_allow_end_zone:
                        res = dfs(completed_prefix + (cand_id,), rem_after, path_records + [rec])
                        if res is not None:
                            return res
                        backtracks_count += 1
                elif stage_allow_end_zone and rec.effective_status is PhysicalEvidence.CONDITIONAL:
                    conditional_candidates.append((cand_id, rem_after, rec))
                else:
                    _record_rejection(cand_id, f"prefix={list(completed_prefix)}:{rec.effective_status.value}:{rec.reason}")

            if stage_allow_end_zone:
                # Prioritize strictly clear candidates before conditional candidates
                for cand_id, rem_after, rec in strict_candidates + conditional_candidates:
                    if cancelled or stage_limit_reached:
                        break
                    res = dfs(completed_prefix + (cand_id,), rem_after, path_records + [rec])
                    if res is not None:
                        return res
                    backtracks_count += 1

            if not stage_limit_reached and not cancelled:
                eval_cache.mark_dead_end(subset_key)
            return None

        found = dfs((), initial_order, [])
        return found, winning_steps_out, stage_limit_reached, stage_limit_reason

    found_seq: Optional[Tuple[str, ...]] = None
    winning_steps: List[StepEvaluationRecord] = []
    strict_exhausted = False
    strict_limit_reached = False
    strict_note = ""
    limit_reason = ""
    used_conditional_stage = False

    try:
        # Stage 1: Always search for a strict collision-free sequence first
        stage1_time_budget = (
            max_search_time_s * 0.6 if eff_allow_end_zone else max_search_time_s
        )
        stage1_states_budget = (
            max(1, int(max_states * 0.6)) if (eff_allow_end_zone and max_states > 1) else max_states
        )
        stage1_checks_budget = (
            max(1, int(max_checks * 0.6)) if (eff_allow_end_zone and max_checks > 1) else max_checks
        )
        found_seq, winning_steps, s1_limit, s1_reason = _run_dfs_stage(
            stage_allow_end_zone=False,
            stage_time_limit_s=stage1_time_budget,
            stage_max_states=stage1_states_budget,
            stage_max_checks=stage1_checks_budget,
        )
        if found_seq is None and not cancelled:
            if s1_limit:
                strict_limit_reached = True
                strict_note = f"Strict search reached resource limit ({s1_reason})."
                limit_reason = s1_reason
            else:
                strict_exhausted = True
                strict_note = "Strict search exhausted all reachable branches without finding a collision-free sequence."

            # Stage 2: Conditional search (only if explicitly enabled by user/caller)
            if eff_allow_end_zone and eff_end_zone_len > 0.0:
                used_conditional_stage = True
                found_seq, winning_steps, s2_limit, s2_reason = _run_dfs_stage(
                    stage_allow_end_zone=True,
                    stage_time_limit_s=max_search_time_s,
                    stage_max_states=max_states,
                    stage_max_checks=max_checks,
                )
                if s2_limit:
                    limit_reason = s2_reason

        # If a sequence was found, run independent forward replay from flat sheet (0°)
        replay_report: Optional[ForwardReplayReport] = None
        if found_seq is not None and not cancelled:
            replay_report = verify_sequence_forward_replay(
                session,
                press_cycle,
                tooling,
                found_seq,
                config=config,
                bend_shifts=shifts,
                match_active_bend_length=eff_match_len,
                allow_end_zone_collision=(eff_allow_end_zone if used_conditional_stage else False),
                end_zone_length_mm=eff_end_zone_len,
                pose_checker=pose_checker,
                trajectory_checker=trajectory_checker,
            )

        elapsed_s = time.perf_counter() - t_start

        if cancelled:
            return SequenceSearchReport(
                status=SearchStatus.CANCELLED,
                overall_evidence=PhysicalEvidence.UNVERIFIED,
                found_sequence=None,
                initial_sequence=initial_order,
                best_partial_sequence=best_partial,
                max_depth_reached=len(best_partial),
                total_bends=total_bends,
                states_visited=states_visited,
                step_evaluations=step_evaluations,
                cache_hits=cache_hits,
                dead_end_hits=dead_end_hits,
                backtracks_count=backtracks_count,
                elapsed_s=elapsed_s,
                match_active_bend_length=eff_match_len,
                allow_end_zone_collision=eff_allow_end_zone,
                end_zone_length_mm=eff_end_zone_len,
                strict_search_attempted=True,
                strict_search_exhausted=strict_exhausted,
                strict_search_limit_reached=strict_limit_reached,
                strict_search_note=strict_note,
                rejection_summary=rejection_summary,
                message_ru="Поиск последовательности отменён пользователем.",
            )

        if found_seq is not None and replay_report is not None:
            if not replay_report.is_verified:
                return SequenceSearchReport(
                    status=SearchStatus.UNVERIFIED,
                    overall_evidence=PhysicalEvidence.UNVERIFIED,
                    found_sequence=found_seq,
                    initial_sequence=initial_order,
                    best_partial_sequence=best_partial,
                    max_depth_reached=total_bends,
                    total_bends=total_bends,
                    states_visited=states_visited,
                    step_evaluations=step_evaluations,
                    cache_hits=cache_hits,
                    dead_end_hits=dead_end_hits,
                    backtracks_count=backtracks_count,
                    elapsed_s=elapsed_s,
                    match_active_bend_length=eff_match_len,
                    allow_end_zone_collision=eff_allow_end_zone,
                    end_zone_length_mm=eff_end_zone_len,
                    strict_search_attempted=True,
                    strict_search_exhausted=strict_exhausted,
                    strict_search_limit_reached=strict_limit_reached,
                    strict_search_note=strict_note,
                    winning_steps=winning_steps,
                    forward_replay=replay_report,
                    rejection_summary=rejection_summary,
                    message_ru=(
                        f"Найденная последовательность {' -> '.join(found_seq)} не прошла независимый "
                        f"прямой прогон с нуля на шаге {replay_report.blocking_bend_id}: {replay_report.blocking_reason} (UNVERIFIED)."
                    ),
                )

            if replay_report.is_conditional:
                cond_bends = list(replay_report.conditional_bends)
                strict_prefix_ru = (
                    "Строгий план без коллизий не найден в пределах лимита поиска; "
                    if strict_limit_reached
                    else "Строгий план без коллизий отсутствует для текущего инструмента; "
                )
                return SequenceSearchReport(
                    status=SearchStatus.CONDITIONAL_VALIDATED,
                    overall_evidence=PhysicalEvidence.CONDITIONAL,
                    found_sequence=found_seq,
                    initial_sequence=initial_order,
                    best_partial_sequence=found_seq,
                    max_depth_reached=total_bends,
                    total_bends=total_bends,
                    states_visited=states_visited,
                    step_evaluations=step_evaluations,
                    cache_hits=cache_hits,
                    dead_end_hits=dead_end_hits,
                    backtracks_count=backtracks_count,
                    elapsed_s=elapsed_s,
                    match_active_bend_length=eff_match_len,
                    allow_end_zone_collision=eff_allow_end_zone,
                    end_zone_length_mm=eff_end_zone_len,
                    strict_search_attempted=True,
                    strict_search_exhausted=strict_exhausted,
                    strict_search_limit_reached=strict_limit_reached,
                    strict_search_note=strict_note,
                    conditional_bends=cond_bends,
                    winning_steps=winning_steps,
                    forward_replay=replay_report,
                    rejection_summary=rejection_summary,
                    message_ru=(
                        f"{strict_prefix_ru}найдена условная последовательность (CONDITIONAL): "
                        f"{' -> '.join(found_seq)}. Операции с концевым пересечением пуансона: "
                        f"{', '.join(cond_bends)}. Требуется пуансон с вырезом на конце (геометрия ещё не проверена)."
                    ),
                )

            return SequenceSearchReport(
                status=SearchStatus.STRICT_VALIDATED,
                overall_evidence=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                found_sequence=found_seq,
                initial_sequence=initial_order,
                best_partial_sequence=found_seq,
                max_depth_reached=total_bends,
                total_bends=total_bends,
                states_visited=states_visited,
                step_evaluations=step_evaluations,
                cache_hits=cache_hits,
                dead_end_hits=dead_end_hits,
                backtracks_count=backtracks_count,
                elapsed_s=elapsed_s,
                match_active_bend_length=eff_match_len,
                allow_end_zone_collision=eff_allow_end_zone,
                end_zone_length_mm=eff_end_zone_len,
                strict_search_attempted=True,
                strict_search_exhausted=False,
                strict_search_limit_reached=False,
                strict_search_note="Strict collision-free sequence found and verified by forward replay.",
                conditional_bends=[],
                winning_steps=winning_steps,
                forward_replay=replay_report,
                rejection_summary=rejection_summary,
                message_ru=(
                    f"Найдена и подтверждена прямым прогоном последовательность без коллизий: "
                    f"{' -> '.join(found_seq)}."
                ),
            )

        # No sequence found: distinguish resource limit vs exhaustive search
        if limit_reason:
            return SequenceSearchReport(
                status=SearchStatus.NOT_FOUND_WITHIN_SEARCH_LIMITS,
                overall_evidence=PhysicalEvidence.UNVERIFIED,
                found_sequence=None,
                initial_sequence=initial_order,
                best_partial_sequence=best_partial,
                max_depth_reached=len(best_partial),
                total_bends=total_bends,
                states_visited=states_visited,
                step_evaluations=step_evaluations,
                cache_hits=cache_hits,
                dead_end_hits=dead_end_hits,
                backtracks_count=backtracks_count,
                elapsed_s=elapsed_s,
                match_active_bend_length=eff_match_len,
                allow_end_zone_collision=eff_allow_end_zone,
                end_zone_length_mm=eff_end_zone_len,
                strict_search_attempted=True,
                strict_search_exhausted=strict_exhausted,
                strict_search_limit_reached=strict_limit_reached,
                strict_search_note=strict_note,
                rejection_summary=rejection_summary,
                search_limit_reason=limit_reason,
                message_ru=(
                    f"Решение не найдено в пределах заданного поиска ({limit_reason}). "
                    f"Лучший частичный префикс ({len(best_partial)}/{total_bends}): "
                    f"{' -> '.join(best_partial) if best_partial else 'нет'}."
                ),
            )

        return SequenceSearchReport(
            status=SearchStatus.NO_FEASIBLE_SEQUENCE,
            overall_evidence=PhysicalEvidence.COLLISION,
            found_sequence=None,
            initial_sequence=initial_order,
            best_partial_sequence=best_partial,
            max_depth_reached=len(best_partial),
            total_bends=total_bends,
            states_visited=states_visited,
            step_evaluations=step_evaluations,
            cache_hits=cache_hits,
            dead_end_hits=dead_end_hits,
            backtracks_count=backtracks_count,
            elapsed_s=elapsed_s,
            match_active_bend_length=eff_match_len,
            allow_end_zone_collision=eff_allow_end_zone,
            end_zone_length_mm=eff_end_zone_len,
            strict_search_attempted=True,
            strict_search_exhausted=True,
            strict_search_limit_reached=False,
            strict_search_note=strict_note,
            rejection_summary=rejection_summary,
            message_ru=(
                f"Все достижимые варианты проверены ({states_visited} состояний, {step_evaluations} проверок); "
                f"допустимая последовательность для выбранного инструмента не найдена. "
                f"Лучший частичный префикс ({len(best_partial)}/{total_bends}): "
                f"{' -> '.join(best_partial) if best_partial else 'нет'}."
            ),
        )

    finally:
        # If no valid sequence was found and verified, restore original session state
        if found_seq is None or cancelled:
            try:
                session.set_bend_order(list(initial_order))
                session.fold_state.angles.clear()
                session.fold_state.angles.update(initial_angles)
                session.fold_state.transforms.clear()
                session.fold_state.transforms.update(initial_transforms)
                _select_bend_index_compat(session, initial_active_index)
                _sync_tooling_and_cycle_for_active_bend(
                    session,
                    press_cycle,
                    tooling,
                    match_active_bend_length=eff_match_len,
                    allow_end_zone_collision=eff_allow_end_zone,
                    end_zone_length_mm=eff_end_zone_len,
                )
            except Exception:
                pass
