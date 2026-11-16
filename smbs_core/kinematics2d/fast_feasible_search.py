"""Bounded hierarchical search for a *sampled* feasible press-brake order.

Existing engines are authoritative:
  - section-only DFS: fast candidate generator, never certifies the machine;
  - native D6 manual replay: selected actual FCStd punch and die, all bends.

No second geometry engine, fake clearance or approval by coarse sampling.
The 600-second experimental budget is BEST EFFORT: an in-flight OpenCASCADE operation may
run beyond a Python deadline, and no safe hard interruption is available.

Even a successful report means sampled punch/die clearance ONLY; backgauge,
frame, clamps, tool change, continuous motion and transfers remain unverified.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import dist, isfinite
from time import perf_counter
from types import SimpleNamespace
from typing import Any, Callable, Optional


class FastSearchStatus(str, Enum):
    SAMPLED_TOOL_CLEAR = "SAMPLED_3D_TOOL_CLEAR_MACHINE_UNVERIFIED"
    VIRTUAL_SAMPLED_CLEAR = "VIRTUAL_SAMPLED_3D_CLEAR_PHYSICAL_TOOL_LENGTH_UNVERIFIED"
    TIME_BUDGET_REACHED = "TIME_BUDGET_REACHED_UNVERIFIED"
    PLANAR_ONLY = "PLANAR_CANDIDATES_NOT_NATIVE_VALIDATED"
    NATIVE_REJECTED = "NATIVE_CANDIDATES_REJECTED_OR_UNVERIFIED"
    NO_2D_CANDIDATES = "NO_2D_CANDIDATES_NOT_PROOF_OF_IMPOSSIBILITY"
    INVALID_INPUT = "INVALID_INPUT"


@dataclass(frozen=True)
class FastNativeAttempt:
    sequence: tuple[str, ...]
    orientation_by_bend: tuple[tuple[str, bool], ...]
    status: str
    reason: str
    checked_bends: int
    elapsed_s: float

    def to_dict(self) -> dict:
        return {
            "sequence": list(self.sequence),
            "orientation_by_bend": dict(self.orientation_by_bend),
            "status": self.status,
            "reason": self.reason,
            "checked_bends": self.checked_bends,
            "elapsed_s": round(self.elapsed_s, 4),
        }


@dataclass(frozen=True)
class FastSequenceResult:
    status: FastSearchStatus
    sequence: Optional[tuple[str, ...]]
    orientation_by_bend: tuple[tuple[str, bool], ...]
    planar_candidates: tuple[tuple[str, ...], ...]
    attempts: tuple[FastNativeAttempt, ...]
    winning_steps: tuple[dict, ...]
    elapsed_s: float
    budget_s: float
    planar_elapsed_s: float
    planar_status: str
    planar_limit_reason: str
    planar_deepest_prefix: tuple[str, ...]
    message_ru: str
    preview_sequence: Optional[tuple[str, ...]] = None
    preview_orientations: tuple[tuple[str, bool], ...] = ()
    reverse_search: Optional[dict] = None
    preview_checked_bends: int = 0
    preview_origin: str = "NONE"

    @property
    def sampled_tool_clear(self) -> bool:
        return self.status is FastSearchStatus.SAMPLED_TOOL_CLEAR

    @property
    def virtual_tool_clear(self) -> bool:
        return self.status is FastSearchStatus.VIRTUAL_SAMPLED_CLEAR

    @property
    def machine_feasible_certified(self) -> bool:
        # The existing physical validator excludes frame, clamps, backgauge
        # travel and continuous collision certification.
        return False

    @property
    def deadline_exceeded(self) -> bool:
        return self.elapsed_s > self.budget_s

    def to_dict(self) -> dict:
        return {
            "schema_version": "smbs.fast_feasible.v1",
            "status": self.status.value,
            "sequence": list(self.sequence) if self.sequence else None,
            "orientation_by_bend": dict(self.orientation_by_bend),
            "planar_candidates": [list(x) for x in self.planar_candidates],
            "attempts": [a.to_dict() for a in self.attempts],
            "winning_steps": [dict(item) for item in self.winning_steps],
            "elapsed_s": round(self.elapsed_s, 4),
            "time_budget_s": self.budget_s,
            "deadline_exceeded": self.deadline_exceeded,
            "planar_elapsed_s": round(self.planar_elapsed_s, 4),
            "planar_status": self.planar_status,
            "planar_limit_reason": self.planar_limit_reason,
            "planar_deepest_prefix": list(self.planar_deepest_prefix),
            "sampled_tool_clear": self.sampled_tool_clear,
            "virtual_tool_clear": self.virtual_tool_clear,
            "machine_feasible_certified": self.machine_feasible_certified,
            "continuous_motion_certified": False,
            "backgauge_frame_clamps_transfer_checked": False,
            "message_ru": self.message_ru,
            "preview_sequence": list(self.preview_sequence) if self.preview_sequence else None,
            "preview_orientations": dict(self.preview_orientations),
            "reverse_search": self.reverse_search,
            "preview_checked_bends": self.preview_checked_bends,
            "preview_origin": self.preview_origin,
        }


def _best_unverified_preview(order, initial_sequence, initial_orientations,
                             attempts):
    """Select best *displayable* order, not a false machine clearance.

    Native attempts contain richer evidence than a cheap initial order.
    Prefer a greater checked prefix, then UNVERIFIED to a reported
    rejection, retaining stable first-attempt ordering on ties.
    The checked_bends count is *not* proof of physical clearance.
    """
    target = set(order)
    def valid(sequence, orientations):
        seq = tuple(sequence or ())
        poses = dict(orientations or ())
        return (len(seq) == len(order) and set(seq) == target
                and set(poses) == target)

    rejected = {
        (tuple(a.sequence), tuple(sorted(a.orientation_by_bend)))
        for a in attempts
        if a.status not in ("UNVERIFIED",)
    }
    chosen = None
    if (valid(initial_sequence, initial_orientations)
            and (tuple(initial_sequence),
                 tuple(sorted(dict(initial_orientations).items())))
            not in rejected):
        chosen = (tuple(initial_sequence),
                  tuple(sorted(dict(initial_orientations).items())),
                  0, "REVERSE_RANKED_UNVERIFIED")
    best_rank = (-1, -1)
    for attempt in attempts:
        if (attempt.status != "UNVERIFIED"
                or not valid(attempt.sequence, attempt.orientation_by_bend)):
            # An actually rejected physical order must never win the
            # preview merely because it reached many checked positions.
            continue
        checked = max(0, min(int(attempt.checked_bends), len(order)))
        rank = (checked, 1)
        if rank > best_rank:
            best_rank = rank
            chosen = (
                tuple(attempt.sequence),
                tuple(sorted(attempt.orientation_by_bend)),
                checked,
                "NATIVE_ATTEMPT_UNVERIFIED",
            )
    return chosen


def _candidate_orientation(candidate, order):
    steps = tuple(getattr(candidate, "steps", ()))
    if len(steps) != len(order):
        return None
    orientation = {}
    for step in steps:
        bend = str(getattr(step, "bend_id", ""))
        label = str(getattr(step, "orientation_variant", ""))
        if bend not in order or bend in orientation:
            return None
        orientation[bend] = label.startswith("REVERSE_MACHINE_Z_180")
    if set(orientation) != set(order):
        return None
    return tuple(sorted(orientation.items()))


def _native_result_is_qualified(physical, sequence, expected_orientation=None, *, virtual_length=False):
    """Only a complete real-tool sampled D6 replay of the SAME poses qualifies."""
    if not isinstance(physical, dict):
        return False
    if physical.get("status") != "SAMPLED_3D_CLEAR_NOT_CONTINUOUS":
        return False
    if not physical.get("complete_part_order_requested"):
        return False
    if tuple(physical.get("requested_bends") or ()) != tuple(sequence):
        return False
    if physical.get("checked_bends") != len(sequence):
        return False
    if physical.get("stopped_early", True):
        return False
    if physical.get("validation_mode") != "FULL_ADAPTIVE_D6":
        return False
    expected_length_mode = ("VIRTUAL_MATCH_BEND" if virtual_length
                            else "SELECTED_FCSTD_REAL_LENGTH")
    if physical.get("tool_length_mode") != expected_length_mode:
        return False
    if not physical.get("punch_fcstd_source") or not physical.get("die_fcstd_source"):
        return False
    steps = physical.get("steps") or ()
    if len(steps) != len(sequence):
        return False
    if expected_orientation is not None:
        if set(expected_orientation) != set(sequence):
            return False
    for step, bend in zip(steps, sequence):
        if step.get("bend_id") != bend:
            return False
        if expected_orientation is not None:
            # A correct bend ID with the wrong 180-degree workpiece pose
            # can be a collision in the actual machine. Never substitute.
            if (step.get("reverse_axis") is None
                    or bool(step["reverse_axis"])
                    != bool(expected_orientation[bend])):
                return False
        if step.get("status") != "SAMPLED_3D_CLEAR_NOT_CONTINUOUS":
            return False
        if (step.get("punch_die_pair") or {}).get("status") != "SAMPLED_CLEAR":
            return False
        if (step.get("retract") or {}).get("status") != "SAMPLED_CLEAR":
            return False
    return True


def _default_sampling_config(remaining_s, bend_count, clearance_mm):
    from .motion_trajectory_3d import AdaptiveSamplingConfig

    # Each phase may borrow time from the shared deadline. Dividing by
    # 5*bend_count previously gave ~0.9s per phase on ten-bend parts.
    # Incomplete trajectories remain UNVERIFIED, never silently cleared.
    # The 600s experimental mode must not keep the old 6s cap: BENDING
    # repeatedly needed more than 9s even for the first bend. Bound the
    # per-phase allowance by the global deadline, never certify timeout.
    # First pass is breadth-oriented. The 09:43 benchmark spent 260s
    # on one candidate, while other permutations failed at a different
    # fourth bend. Keep more of the 600s budget for genuinely distinct
    # orders. This affects UNKNOWN verdicts only, not correctness.
    phase_limit = max(2.0, min(18.0, remaining_s * 0.045))
    return AdaptiveSamplingConfig(
        initial_clearance_mm=clearance_mm,
        max_phase_time_s=phase_limit,
        max_evaluated_states_per_phase=72,
        max_recursion_depth=7,
        component_reject_enabled=True,  # reject only: never certifies clear
        coarse_occ_screen=False,
    )


def _real_tool_length_issue(session, tooling) -> str:
    """Reject impossible single-hit axial coverage before any expensive DFS.

    Only applies to *one real unsectioned FCStd tool*. If multi-section
    assemblies are configured, native D6 owns their actual span/overlap.
    Missing or unknown catalog lengths are left to native qualification.
    """
    state = getattr(session, "fold_state", None)
    if state is None:
        return ""
    lengths = []
    for bend_id in getattr(session, "bend_order", ()) or ():
        bend = getattr(state, "bends", {}).get(bend_id)
        axis = getattr(bend, "axis_xyz", None)
        try:
            if axis is not None:
                length = float(dist(axis[0], axis[1]))
                if isfinite(length) and length > 0:
                    lengths.append((str(bend_id), length))
        except (TypeError, ValueError, IndexError, OverflowError):
            continue
    if not lengths:
        return ""
    largest_id, largest_mm = max(lengths, key=lambda x: x[1])
    for label, attr, sections_attr in (
        ("пуансон", "punch", "punch_section_offsets_xm"),
        ("матрица", "die", "die_section_offsets_xm"),
    ):
        if getattr(tooling, sections_attr, ()):
            continue
        source = getattr(tooling, attr, None)
        raw_length = getattr(source, "length_mm", None)
        if isinstance(raw_length, bool) or not isinstance(raw_length, (int, float)):
            continue
        length = float(raw_length)
        if isfinite(length) and length > 0 and length + 0.02 < largest_mm:
            return (
                "Реальный {} имеет длину {:.2f} мм, но гиб {} требует "
                "{:.2f} мм в одном ходе. Выберите достаточно длинный "
                "FCStd-инструмент или настройте реальные секции оснастки. "
                "Виртуальное удлинение не подтверждает физическую возможность."
            ).format(label, length, largest_id, largest_mm)
    return ""


def search_fast_feasible_sequence(
    session: Any,
    tooling: Any,
    *,
    budget_s: float = 600.0,
    max_candidates: int = 8,
    planar_budget_fraction: float = 0.45,
    initial_clearance_mm: float = 40.0,
    weights: Any = None,
    planar_runner: Optional[Callable[..., Any]] = None,
    native_runner: Optional[Callable[..., dict]] = None,
    sampling_config_factory: Optional[Callable[[float, int, float], Any]] = None,
    progress_callback: Optional[Callable[[str, int, int, str], None]] = None,
    seed_order: Optional[tuple[str, ...]] = None,
    seed_orientations: Optional[dict[str, bool]] = None,
    clock: Callable[[], float] = perf_counter,
) -> FastSequenceResult:
    """Find a complete candidate with native sampled punch/die clearance.

    Search is bounded by a monotonic *soft* deadline and runs synchronously
    in FreeCAD's own Python thread. No OCC calls in workers, no approximate
    verdict promotion, no mutation of the operator's session order/angles.
    """
    started = clock()
    reverse_data = None

    def result(status, sequence=None, orientations=(), candidates=(),
               attempts=(), winning_steps=(), planar_elapsed=0.0,
               planar_status="NOT_RUN",
               planar_limit="", deepest=(), message="",
               preview_sequence=None, preview_orientations=(),
               preview_checked_bends=0, preview_origin="NONE"):
        return FastSequenceResult(
            status=status,
            sequence=tuple(sequence) if sequence is not None else None,
            orientation_by_bend=tuple(orientations),
            planar_candidates=tuple(candidates),
            attempts=tuple(attempts),
            winning_steps=tuple(winning_steps),
            elapsed_s=max(0.0, clock() - started),
            budget_s=float(budget_s),
            planar_elapsed_s=float(planar_elapsed),
            planar_status=str(planar_status),
            planar_limit_reason=str(planar_limit),
            planar_deepest_prefix=tuple(deepest),
            message_ru=str(message),
            preview_sequence=(tuple(preview_sequence)
                              if preview_sequence is not None else None),
            preview_orientations=tuple(preview_orientations),
            reverse_search=reverse_data,
            preview_checked_bends=int(preview_checked_bends),
            preview_origin=str(preview_origin),
        )

    order = tuple(getattr(session, "bend_order", ()) or ())
    fold_state = getattr(session, "fold_state", None)
    if (fold_state is None or not order or len(set(order)) != len(order)
            or set(order) != set(getattr(fold_state, "bends", {}))
            or getattr(session, "panel_build", None) is None
            or tooling is None or getattr(tooling, "punch", None) is None
            or getattr(tooling, "die", None) is None
            or not isfinite(budget_s) or budget_s <= 0
            or not 0.05 <= planar_budget_fraction <= 0.75
            or not isinstance(max_candidates, int) or max_candidates < 1
            or not isfinite(initial_clearance_mm) or initial_clearance_mm <= 0):
        return result(FastSearchStatus.INVALID_INPUT,
                      message="Недостаточно данных для поиска: проверьте деталь и реальные пуансон/матрицу.")

    # Unified sequencing mode: real punch/die cross sections matter,
    # but their installed axial lengths are deliberately out of scope.
    # Force per-bend virtual extrusion WITHOUT mutating user tooling.
    virtual_length = True
    from copy import copy
    # All pipeline stages see identical virtual-length tool settings.
    # Preserve original selected profiles; do not mutate the operator UI.
    search_tooling = copy(tooling)
    search_tooling.match_active_bend_length = True
    search_tooling.punch_section_offsets_xm = ()
    search_tooling.die_section_offsets_xm = ()

    if planar_runner is None:
        from .sequence_search_2d import search_bend_sequences_2d
        planar_runner = search_bend_sequences_2d
    supports_verified_step_cache = native_runner is None
    if native_runner is None:
        from .manual_physical_validation import check_manual_3d_sequence
        native_runner = check_manual_3d_sequence
    if sampling_config_factory is None:
        sampling_config_factory = _default_sampling_config

    deadline = started + float(budget_s)
    # First generate orders by reverse-unbending, ranking 3D AABB overlap.
    # AABB is never treated as verified clearance.
    from .reverse_aabb_search import search_reverse_aabb
    reverse_budget = min(90.0, max(0.0, deadline - clock()) * 0.2)
    reverse = search_reverse_aabb(
        session, budget_s=reverse_budget,
        max_candidates=max(1, (max_candidates + 1) // 2),
        progress=(lambda done, total, bend:
                  progress_callback("REVERSE_AABB", done, total, bend))
                 if progress_callback else None,
        clock=clock,
        tooling=search_tooling,
        virtual_tool_length=True,
    )
    reverse_data = reverse.to_dict()
    seen = set()
    candidates = []
    for item in reverse.candidates:
        seq = tuple(item.sequence)
        orientation = tuple(sorted(item.orientation_by_bend))
        if len(seq) != len(order) or set(seq) != set(order):
            continue
        pair = (seq, orientation)
        if pair not in seen:
            seen.add(pair)
            candidates.append((
                seq, orientation,
                SimpleNamespace(evidence=item.evidence,
                                cost=item.bbox_overlap_score,
                                cost_complete=False),
            ))
    if candidates:
        planar_status = "SKIPPED_REVERSE_AABB_CANDIDATES_AVAILABLE"
        planar_limit = ""
        deepest = tuple(reversed(reverse.deepest_unbent))
    else:
        # Existing 2D search remains an isolated fallback.
        planar_budget = min(float(budget_s) * 0.25,
                            max(0.001, deadline - clock()))
        if progress_callback is not None:
            progress_callback("PLANAR_2D", 0, len(order), "")
        report = planar_runner(
            session, search_tooling,
            max_solutions=max_candidates,
            max_complete_candidates=max_candidates * 3,
            max_nodes=4000,
            max_evaluations=10000,
            max_search_time_s=planar_budget,
            root_probe_nodes=40 if len(order) >= 10 else 0,
            root_probe_seconds=0.6 if len(order) >= 10 else 0.0,
            preferred_bend_order=tuple(reversed(order)) if len(order) >= 6 else order,
            group_first=len(order) < 6,
            exploration_fractions=(0.0, 0.25, 0.5, 0.75, 1.0),
            exploration_stroke_fractions=(0.0, 0.5, 1.0),
            match_active_bend_length=virtual_length,
            allow_end_zone_collision=False,
            allow_retract_warning=False,
            backgauge_model=None,
            weights=weights,
        )
        planar_status = getattr(getattr(report, "status", None), "value",
                                str(getattr(report, "status", "UNKNOWN")))
        planar_limit = str(getattr(report, "limit_reason", "") or "")
        deepest = tuple(getattr(report, "deepest_prefix", ()) or ())
        for candidate in getattr(report, "candidates", ()):
            seq = tuple(getattr(candidate, "sequence", ()))
            if len(seq) != len(order) or set(seq) != set(order):
                continue
            orientation = _candidate_orientation(candidate, seq)
            if orientation is None:
                continue
            pair = (seq, orientation)
            if pair not in seen:
                seen.add(pair)
                candidates.append((seq, orientation, candidate))
    planar_elapsed = max(0.0, clock() - started)
    candidates.sort(key=lambda x: (
        getattr(x[2], "evidence", "") not in (
            "SAMPLED_2D_CLEAR_OR_TOUCH",
            "REVERSE_AABB_RANKED_UNVERIFIED",
            "REVERSE_ASTAR_MESH_RANKED_UNVERIFIED",
            "REVERSE_ASTAR_AABB_RANKED_UNVERIFIED",
            "REVERSE_ASTAR_MACHINE_AABB_RANKED_UNVERIFIED"),
        not bool(getattr(x[2], "cost_complete", False)),
        float(getattr(x[2], "cost", 0.0) or 0.0),
        x[0], x[1],
    ))
    # The operator's current order may be physically valid even when the
    # cheap 2D cross-section model cannot recognize its 3D placement.
    # Use it only as a fallback for native verification, NEVER as a pass.
    # Native D6 checks every phase with the specified orientation.
    if not reverse.candidates and seed_order is not None and seed_orientations is not None:
        seed = tuple(seed_order)
        directions = tuple(sorted(
            (str(bid), bool(value))
            for bid, value in seed_orientations.items()
        ))
        if (len(seed) == len(order) and set(seed) == set(order)
                and len(directions) == len(order)
                and set(dict(directions)) == set(order)):
            pair = (seed, directions)
            if pair not in seen:
                seen.add(pair)
                candidates.append((
                    seed, directions,
                    SimpleNamespace(
                        evidence="OPERATOR_SEED_NOT_PLANAR_VERIFIED",
                        cost=float("inf"), cost_complete=False,
                    ),
                ))
    # Reserve remaining slots for a pose flip at the *actual* unresolved
    # bend reported by native D6. Preflipping B08/first bends consumed the
    # whole budget before reaching later blockers (e.g. B07).
    candidates = candidates[:max_candidates]
    available_sequences = tuple(x[0] for x in candidates)
    # Preview only: preserve corresponding 2D orientation choices.
    preview_candidates = [
        item for item in candidates
        if getattr(item[2], "evidence", "") != "OPERATOR_SEED_NOT_PLANAR_VERIFIED"
    ]
    preview_sequence = preview_candidates[0][0] if preview_candidates else None
    preview_orientations = preview_candidates[0][1] if preview_candidates else ()
    if not candidates:
        code = (FastSearchStatus.TIME_BUDGET_REACHED
                if clock() >= deadline else FastSearchStatus.NO_2D_CANDIDATES)
        return result(
            code, candidates=available_sequences,
            planar_elapsed=planar_elapsed, planar_status=planar_status,
            planar_limit=planar_limit, deepest=deepest,
            message="Полный порядок за бюджет 2D-поиска не найден. Это не доказывает невозможность гибки.",
        )

    # Per-invocation cache shares immutable OCC geometry across native
    # replays. All collision decisions still come from fresh D6 checking.
    from .native_sequence_geometry_cache import NativeSequenceGeometryCache
    geometry_cache = NativeSequenceGeometryCache(max_full_sheets=16)
    verified_step_cache = {}
    attempts = []
    for i, (seq, orientation, _) in enumerate(candidates):
        remaining = deadline - clock()
        if remaining <= 0:
            break
        if progress_callback is not None:
            progress_callback("NATIVE_START", i + 1, len(candidates),
                              " → ".join(seq))
        native_session = SimpleNamespace(
            bend_order=seq,
            fold_state=fold_state,
            panel_build=session.panel_build,
            part_label=str(getattr(session, "part_label", "")),
        )
        cfg = sampling_config_factory(remaining, len(seq), initial_clearance_mm)
        trial_started = clock()
        native = None
        try:
            def native_progress(position, total, bend):
                if progress_callback is not None:
                    progress_callback("NATIVE_BEND", position, total, bend)

            native = native_runner(
                native_session, search_tooling, seq,
                match_active_bend_length=virtual_length,
                allow_end_zone_collision=False,
                end_zone_length_mm=0.0,
                reverse_axis_by_bend=dict(orientation),
                config=cfg,
                progress_callback=native_progress,
                sequence_geometry_cache=geometry_cache,
                # Candidate rejection needs the D6 collision verdict, not
                # up to 30s of optional panel-by-panel OCC attribution.
                include_collision_attribution=False,
                deadline_at=deadline,
                **({"verified_step_cache": verified_step_cache}
                   if supports_verified_step_cache else {}),
            )
            if not isinstance(native, dict):
                raise TypeError("native validator must return a dict")
            status = str(native.get("status", "UNVERIFIED"))
            reason = str(native.get("reason", ""))
            checked = int(native.get("checked_bends", 0))
            qualified = _native_result_is_qualified(
                native, seq, dict(orientation), virtual_length=virtual_length
            )
            # One bounded retry on a sampling-timeout, with the SAME
            # geometry, sequence and poses. Re-run full D6 rather than
            # promoting incomplete intervals to a pass.
            timed_out_phase = any(
                "MAX_TIME_BUDGET_EXCEEDED" in str(step.get("reason", ""))
                for step in (native.get("steps") or ())
            )
            # Do not immediately recheck the identical unresolved prefix
            # when other geometric orders remain unexplored. A longer
            # native retry is useful only after all queued variants were
            # tried and the remaining budget can support it.
            if (not qualified and status == "UNVERIFIED"
                    and timed_out_phase
                    and i == len(candidates) - 1
                    and checked >= len(seq) - 1
                    and deadline - clock() >= 95.0):
                from dataclasses import replace
                retry_remaining = deadline - clock()
                retry_cfg = sampling_config_factory(
                    retry_remaining, len(seq), initial_clearance_mm
                )
                retry_cfg = replace(
                    retry_cfg,
                    max_phase_time_s=min(
                        60.0, max(
                            retry_cfg.max_phase_time_s,
                            float(cfg.max_phase_time_s) * 1.5,
                        ),
                    ),
                )
                if progress_callback is not None:
                    progress_callback("NATIVE_RETRY", i + 1, len(candidates), seq[0])
                native = native_runner(
                    native_session, search_tooling, seq,
                    match_active_bend_length=virtual_length,
                    allow_end_zone_collision=False,
                    end_zone_length_mm=0.0,
                    reverse_axis_by_bend=dict(orientation),
                    config=retry_cfg,
                    progress_callback=native_progress,
                    sequence_geometry_cache=geometry_cache,
                    include_collision_attribution=False,
                    deadline_at=deadline,
                    **({"verified_step_cache": verified_step_cache}
                       if supports_verified_step_cache else {}),
                )
                if not isinstance(native, dict):
                    raise TypeError("native retry validator must return a dict")
                status = str(native.get("status", "UNVERIFIED"))
                reason = str(native.get("reason", ""))
                checked = int(native.get("checked_bends", 0))
                qualified = _native_result_is_qualified(
                    native, seq, dict(orientation), virtual_length=virtual_length
                )
        except Exception as exc:
            status = "UNVERIFIED"
            reason = "native_exception:{}:{}".format(type(exc).__name__, exc)
            checked = 0
            qualified = False

        attempts.append(FastNativeAttempt(
            seq, orientation, status, reason, checked,
            max(0.0, clock() - trial_started),
        ))
        # The next physically different loading must target the actual
        # unresolved bend. Never treat a timeout as a collision or proof
        # of clearance. Revalidate the whole modified candidate with D6.
        if (not qualified and status == "UNVERIFIED"
                and isinstance(native, dict)
                and len(candidates) < max_candidates
                and deadline - clock() >= 45.0):
            failed_bend = None
            for step in native.get("steps") or ():
                if str(step.get("status", "")) not in (
                    "SAMPLED_3D_CLEAR_NOT_CONTINUOUS", "SAMPLED_CLEAR",
                ):
                    failed_bend = step.get("bend_id")
                    break
            if failed_bend not in seq and "native_3d_not_verified_at:" in reason:
                failed_bend = reason.split("native_3d_not_verified_at:", 1)[1].split()[0]
            if failed_bend in seq:
                # A timeout is not a geometric rejection: try a different
                # *ordering* before spending another full phase budget on
                # the same orientation at the identical prefix. Move the
                # unresolved bend two places later (if possible). Each
                # permutation is independently replayed from the start.
                new_variants = []
                failed_index = seq.index(failed_bend)
                if failed_index < len(seq) - 1:
                    shifted = list(seq)
                    shifted.pop(failed_index)
                    shifted.insert(min(failed_index + 2, len(shifted)),
                                   failed_bend)
                    new_variants.append((
                        tuple(shifted), orientation,
                        "D6_UNRESOLVED_BEND_DELAYED_UNVERIFIED",
                        "NATIVE_ORDER_VARIANT",
                    ))
                flipped = dict(orientation)
                flipped[failed_bend] = not flipped.get(failed_bend, False)
                new_variants.append((
                    seq, tuple(sorted(flipped.items())),
                    "D6_UNRESOLVED_BEND_POSE_RETRY_UNVERIFIED",
                    "NATIVE_POSE_VARIANT",
                ))
                added = []
                for next_seq, next_orientation, evidence, event_name in new_variants:
                    if len(candidates) + len(added) >= max_candidates:
                        break
                    key = (next_seq, next_orientation)
                    if key in seen:
                        continue
                    seen.add(key)
                    added.append((
                        next_seq, next_orientation,
                        SimpleNamespace(evidence=evidence,
                                        cost=0.0, cost_complete=False),
                    ))
                    if progress_callback is not None:
                        progress_callback(
                            event_name, i + 2 + len(added) - 1,
                            len(candidates) + len(added), str(failed_bend),
                        )
                if added:
                    candidates[i + 1:i + 1] = added
                    available_sequences = tuple(x[0] for x in candidates)
        if qualified:
            return result(
                (FastSearchStatus.VIRTUAL_SAMPLED_CLEAR if virtual_length
                 else FastSearchStatus.SAMPLED_TOOL_CLEAR), sequence=seq,
                orientations=orientation, candidates=available_sequences,
                attempts=attempts, winning_steps=tuple(native["steps"]),
                planar_elapsed=planar_elapsed,
                planar_status=planar_status, planar_limit=planar_limit,
                deepest=deepest,
                message=(
                    "Найден полный порядок с ВИРТУАЛЬНОЙ длиной оснастки: "
                    "выборочные 3D траектории не подтверждают реальную длину "
                    "инструментов. Непрерывность, упор, рама, зажимы и перекладки "
                    "не проверены." if virtual_length else
                    "Найден полный порядок: проверены выборочные 3D траектории "
                    "с реальными пуансоном и матрицей. Непрерывность, упор, "
                    "рама, зажимы и перекладки не подтверждены."
                ),
            )

    display_candidate = _best_unverified_preview(
        order, preview_sequence, preview_orientations, attempts,
    )
    preview_checked_bends = 0
    preview_origin = "NONE"
    if display_candidate is not None:
        (preview_sequence, preview_orientations,
         preview_checked_bends, preview_origin) = display_candidate

    time_out = clock() >= deadline
    code = (FastSearchStatus.TIME_BUDGET_REACHED if time_out
            else FastSearchStatus.NATIVE_REJECTED if attempts
            else FastSearchStatus.PLANAR_ONLY)
    if preview_sequence is None:
        detail = (
            "Полных новых 2D-кандидатов не найдено. Глубина поиска: "
            "{}/{} гибов. Для 3D использовался только исходный "
            "порядок оператора без 2D-подтверждения; он не прошёл "
            "3D-проверку. Предварительный 2D-порядок не создан."
        ).format(len(deepest), len(order))
    else:
        detail = (
            "Кандидат обратного A*/Mesh/BVH или 2D доступен для предварительной симуляции "
            "(UNVERIFIED). Точная 3D-проверка не завершена или "
            "обнаружила препятствие; другие варианты могут существовать."
        )
    return result(
        code, candidates=available_sequences, attempts=attempts,
        preview_sequence=preview_sequence,
        preview_orientations=preview_orientations,
        preview_checked_bends=preview_checked_bends,
        preview_origin=preview_origin,
        planar_elapsed=planar_elapsed, planar_status=planar_status,
        planar_limit=planar_limit, deepest=deepest,
        message=("Общий лимит времени исчерпан. " if time_out else "") + detail,
    )
