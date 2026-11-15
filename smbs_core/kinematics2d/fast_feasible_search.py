"""Bounded hierarchical search for a *sampled* feasible press-brake order.

Existing engines are authoritative:
  - section-only DFS: fast candidate generator, never certifies the machine;
  - native D6 manual replay: selected actual FCStd punch and die, all bends.

No second geometry engine, fake clearance or approval by coarse sampling.
The 60-second budget is BEST EFFORT: an in-flight OpenCASCADE operation may
run beyond a Python deadline, and no safe hard interruption is available.

Even a successful report means sampled punch/die clearance ONLY; backgauge,
frame, clamps, tool change, continuous motion and transfers remain unverified.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from time import perf_counter
from types import SimpleNamespace
from typing import Any, Callable, Optional


class FastSearchStatus(str, Enum):
    SAMPLED_TOOL_CLEAR = "SAMPLED_3D_TOOL_CLEAR_MACHINE_UNVERIFIED"
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
    elapsed_s: float
    budget_s: float
    planar_elapsed_s: float
    planar_status: str
    planar_limit_reason: str
    planar_deepest_prefix: tuple[str, ...]
    message_ru: str

    @property
    def sampled_tool_clear(self) -> bool:
        return self.status is FastSearchStatus.SAMPLED_TOOL_CLEAR

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
            "elapsed_s": round(self.elapsed_s, 4),
            "time_budget_s": self.budget_s,
            "deadline_exceeded": self.deadline_exceeded,
            "planar_elapsed_s": round(self.planar_elapsed_s, 4),
            "planar_status": self.planar_status,
            "planar_limit_reason": self.planar_limit_reason,
            "planar_deepest_prefix": list(self.planar_deepest_prefix),
            "sampled_tool_clear": self.sampled_tool_clear,
            "machine_feasible_certified": self.machine_feasible_certified,
            "continuous_motion_certified": False,
            "backgauge_frame_clamps_transfer_checked": False,
            "message_ru": self.message_ru,
        }


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


def _native_result_is_qualified(physical, sequence):
    """Only a complete, non-coarse, real-tool sampled D6 replay qualifies."""
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
    if physical.get("tool_length_mode") != "SELECTED_FCSTD_REAL_LENGTH":
        return False
    if not physical.get("punch_fcstd_source") or not physical.get("die_fcstd_source"):
        return False
    steps = physical.get("steps") or ()
    if len(steps) != len(sequence):
        return False
    for step, bend in zip(steps, sequence):
        if step.get("bend_id") != bend:
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

    # A limit is per phase, not a hard wall on OCC. Restrict each phase's
    # sampling effort conservatively; exhausted intervals become UNVERIFIED.
    phase_share = remaining_s / max(1, 5 * bend_count)
    return AdaptiveSamplingConfig(
        initial_clearance_mm=clearance_mm,
        max_phase_time_s=max(0.5, min(8.0, phase_share)),
        max_evaluated_states_per_phase=48,
        component_reject_enabled=True,  # reject only: never certifies clear
        coarse_occ_screen=False,
    )


def search_fast_feasible_sequence(
    session: Any,
    tooling: Any,
    *,
    budget_s: float = 60.0,
    max_candidates: int = 8,
    planar_budget_fraction: float = 0.25,
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

    def result(status, sequence=None, orientations=(), candidates=(),
               attempts=(), planar_elapsed=0.0, planar_status="NOT_RUN",
               planar_limit="", deepest=(), message=""):
        return FastSequenceResult(
            status=status,
            sequence=tuple(sequence) if sequence is not None else None,
            orientation_by_bend=tuple(orientations),
            planar_candidates=tuple(candidates),
            attempts=tuple(attempts),
            elapsed_s=max(0.0, clock() - started),
            budget_s=float(budget_s),
            planar_elapsed_s=float(planar_elapsed),
            planar_status=str(planar_status),
            planar_limit_reason=str(planar_limit),
            planar_deepest_prefix=tuple(deepest),
            message_ru=str(message),
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

    if planar_runner is None:
        from .sequence_search_2d import search_bend_sequences_2d
        planar_runner = search_bend_sequences_2d
    if native_runner is None:
        from .manual_physical_validation import check_manual_3d_sequence
        native_runner = check_manual_3d_sequence
    if sampling_config_factory is None:
        sampling_config_factory = _default_sampling_config

    deadline = started + float(budget_s)
    # Keep enough remaining budget for native evidence; do not let the 2D
    # DFS consume the entire search timeout.
    planar_budget = min(float(budget_s) * planar_budget_fraction,
                        max(0.001, deadline - clock()))
    if progress_callback is not None:
        progress_callback("PLANAR_2D", 0, len(order), "")

    report = planar_runner(
        session, tooling,
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
        match_active_bend_length=False,
        allow_end_zone_collision=False,
        allow_retract_warning=False,
        backgauge_model=None,
        weights=weights,
    )
    planar_elapsed = max(0.0, clock() - started)
    planar_status = getattr(getattr(report, "status", None), "value",
                            str(getattr(report, "status", "UNKNOWN")))
    planar_limit = str(getattr(report, "limit_reason", "") or "")
    deepest = tuple(getattr(report, "deepest_prefix", ()) or ())
    seen = set()
    candidates = []
    for candidate in getattr(report, "candidates", ()):
        seq = tuple(getattr(candidate, "sequence", ()))
        if len(seq) != len(order) or set(seq) != set(order):
            continue
        orientation = _candidate_orientation(candidate, seq)
        if orientation is None:
            continue
        key = (seq, orientation)
        if key in seen:
            continue
        seen.add(key)
        candidates.append((seq, orientation, candidate))
    candidates.sort(key=lambda x: (
        getattr(x[2], "evidence", "") != "SAMPLED_2D_CLEAR_OR_TOUCH",
        not bool(getattr(x[2], "cost_complete", False)),
        float(getattr(x[2], "cost", 0.0) or 0.0),
        x[0], x[1],
    ))
    # The operator's current order may be physically valid even when the
    # cheap 2D cross-section model cannot recognize its 3D placement.
    # Use it only as a fallback for native verification, NEVER as a pass.
    # Native D6 checks every phase with the specified orientation.
    if seed_order is not None and seed_orientations is not None:
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
    candidates = candidates[:max_candidates]
    available_sequences = tuple(x[0] for x in candidates)
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
        try:
            def native_progress(position, total, bend):
                if progress_callback is not None:
                    progress_callback("NATIVE_BEND", position, total, bend)

            native = native_runner(
                native_session, tooling, seq,
                match_active_bend_length=False,
                allow_end_zone_collision=False,
                end_zone_length_mm=0.0,
                reverse_axis_by_bend=dict(orientation),
                config=cfg,
                progress_callback=native_progress,
                sequence_geometry_cache=geometry_cache,
                deadline_at=deadline,
            )
            if not isinstance(native, dict):
                raise TypeError("native validator must return a dict")
            status = str(native.get("status", "UNVERIFIED"))
            reason = str(native.get("reason", ""))
            checked = int(native.get("checked_bends", 0))
            qualified = _native_result_is_qualified(native, seq)
        except Exception as exc:
            status = "UNVERIFIED"
            reason = "native_exception:{}:{}".format(type(exc).__name__, exc)
            checked = 0
            qualified = False

        attempts.append(FastNativeAttempt(
            seq, orientation, status, reason, checked,
            max(0.0, clock() - trial_started),
        ))
        if qualified:
            return result(
                FastSearchStatus.SAMPLED_TOOL_CLEAR, sequence=seq,
                orientations=orientation, candidates=available_sequences,
                attempts=attempts, planar_elapsed=planar_elapsed,
                planar_status=planar_status, planar_limit=planar_limit,
                deepest=deepest,
                message="Найден полный порядок: проверены выборочные 3D траектории с реальными пуансоном и матрицей. Непрерывность, упор, рама, зажимы и перекладки не подтверждены.",
            )

    time_out = clock() >= deadline
    code = (FastSearchStatus.TIME_BUDGET_REACHED if time_out
            else FastSearchStatus.NATIVE_REJECTED if attempts
            else FastSearchStatus.PLANAR_ONLY)
    return result(
        code, candidates=available_sequences, attempts=attempts,
        planar_elapsed=planar_elapsed, planar_status=planar_status,
        planar_limit=planar_limit, deepest=deepest,
        message=(
            "Время поиска исчерпано; полного 3D подтверждения пока нет."
            if time_out else
            "Полные 2D варианты получены, но физическая проверка выбранной оснастки не прошла. Другие ориентации и варианты могут существовать."
        ),
    )
