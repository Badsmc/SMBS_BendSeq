"""Deterministic cumulative fold sequence playback.

Rooted traversal is a visualization order, not a manufacturing plan.
Each seek recomputes the pose from flat to avoid state drift.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Sequence
from .panel_kinematics import FoldState, KinematicsError

ANGLE_EPS = 1e-6


class SequenceError(ValueError):
    pass


class PlaybackStatus(str, Enum):
    IDLE = "IDLE"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"


@dataclass(frozen=True)
class SequenceFrame:
    step: int
    fraction: float
    active_bend: str
    angles: tuple
    transforms: dict


class BendSequenceController:
    """Headless state machine for sequential bend playback over an authoritative FoldState.

    Contains no FreeCAD, Qt, or OpenCASCADE dependencies and does not duplicate
    angle storage: `fold_state.angles` is the single authoritative source of truth.
    """

    def __init__(
        self,
        fold_state: FoldState,
        order: tuple[str, ...] | list[str] | None = None,
        speed_deg_per_sec: float = 90.0,
    ) -> None:
        if not isinstance(fold_state, FoldState):
            raise SequenceError("fold_state must be a FoldState instance")
        if not isfinite(speed_deg_per_sec) or speed_deg_per_sec <= 0.0:
            raise SequenceError("speed_deg_per_sec must be positive and finite")

        self.fold_state: FoldState = fold_state
        rooted = fold_state.ordered_bends
        if not rooted:
            raise SequenceError("FoldState has no bends")
        self._canonical_order: tuple[str, ...] = tuple(b.bend_id for b in rooted)
        self._bends_by_id = {b.bend_id: b for b in rooted}
        self.speed_deg_per_sec: float = float(speed_deg_per_sec)
        self.order: tuple[str, ...] = self._canonical_order
        self.active_index: int = 0
        self.status: PlaybackStatus = PlaybackStatus.IDLE
        if order is not None:
            self.order = self._validate_order_tuple(order)
        self._sync_status_when_stopped()

    @property
    def canonical_order(self) -> tuple[str, ...]:
        return self._canonical_order

    @property
    def bend_order(self) -> tuple[str, ...]:
        return self.order

    def _validate_order_tuple(self, order: Sequence[str]) -> tuple[str, ...]:
        if isinstance(order, (str, bytes)):
            raise SequenceError("bend_order must be a sequence of bend IDs, not a string")
        candidate = tuple(str(bid) for bid in order)
        known = set(self._canonical_order)
        if len(candidate) != len(self._canonical_order):
            raise SequenceError(
                "Некорректная длина последовательности: ожидалось {}, получено {}.".format(
                    len(self._canonical_order), len(candidate)
                )
            )
        if len(set(candidate)) != len(candidate):
            raise SequenceError("Последовательность содержит дублирующиеся ID гибов.")
        if set(candidate) != known:
            unknown = sorted(set(candidate) - known)
            missing = sorted(known - set(candidate))
            raise SequenceError(
                "Некорректный состав гибов (неизвестные: {}, пропущенные: {}).".format(
                    unknown, missing
                )
            )
        return candidate

    def can_reorder(self) -> bool:
        """True only when simulation is stopped and all bends are at 0 deg (after Reset)."""
        if self.status == PlaybackStatus.PLAYING:
            return False
        return all(self.is_bend_flat(bid) for bid in self.order)

    def _ensure_can_reorder(self) -> None:
        if self.status == PlaybackStatus.PLAYING:
            raise SequenceError(
                "Нельзя менять порядок гибки во время воспроизведения. Нажмите Pause и Reset (|<)."
            )
        if not all(self.is_bend_flat(bid) for bid in self.order):
            raise SequenceError(
                "Изменение порядка гибки допускается только для плоской заготовки (все углы 0°). Нажмите Reset (|<)."
            )

    def set_bend_order(self, order: Sequence[str]) -> None:
        """Atomically set a new bend execution order without altering PanelGraph or FoldState.

        Allowed only when stopped and all bends are at 0 deg. Preserves the
        currently selected bend ID as the active operation.
        """
        candidate = self._validate_order_tuple(order)
        self._ensure_can_reorder()
        selected_bid = self.active_bend_id
        self.order = candidate
        self.active_index = self.order.index(selected_bid)
        self.status = PlaybackStatus.IDLE

    def move_up(self, index: int | None = None) -> int:
        """Move the bend at `index` (or active_index) one position up. Returns new index."""
        self._ensure_can_reorder()
        idx = self.active_index if index is None else int(index)
        if not 0 <= idx < self.total_steps:
            raise SequenceError(f"Operation index out of range: {idx}")
        if idx == 0:
            return idx
        lst = list(self.order)
        lst[idx - 1], lst[idx] = lst[idx], lst[idx - 1]
        self.order = tuple(lst)
        self.active_index = idx - 1
        return self.active_index

    def move_down(self, index: int | None = None) -> int:
        """Move the bend at `index` (or active_index) one position down. Returns new index."""
        self._ensure_can_reorder()
        idx = self.active_index if index is None else int(index)
        if not 0 <= idx < self.total_steps:
            raise SequenceError(f"Operation index out of range: {idx}")
        if idx >= self.total_steps - 1:
            return idx
        lst = list(self.order)
        lst[idx], lst[idx + 1] = lst[idx + 1], lst[idx]
        self.order = tuple(lst)
        self.active_index = idx + 1
        return self.active_index

    def restore_canonical_order(self) -> None:
        """Restore the canonical SheetMetal / PanelGraph bend order (requires flat state)."""
        self.set_bend_order(self._canonical_order)

    def bend_for(self, bend_id: str):
        if bend_id not in self._bends_by_id:
            raise SequenceError(f"Unknown bend_id: {bend_id}")
        return self._bends_by_id[bend_id]

    @property
    def total_steps(self) -> int:
        return len(self.order)

    @property
    def active_bend_id(self) -> str:
        return self.order[self.active_index]

    @property
    def active_bend(self):
        return self._bends_by_id[self.active_bend_id]

    @property
    def current_angle_deg(self) -> float:
        return float(self.fold_state.angles[self.active_bend_id])

    @property
    def target_angle_deg(self) -> float:
        return float(self.active_bend.angle_deg)

    def target_for(self, bend_id: str) -> float:
        if bend_id not in self._bends_by_id:
            raise SequenceError(f"Unknown bend_id: {bend_id}")
        return float(self._bends_by_id[bend_id].angle_deg)

    def angle_for(self, bend_id: str) -> float:
        if bend_id not in self.fold_state.angles:
            raise SequenceError(f"Unknown bend_id: {bend_id}")
        return float(self.fold_state.angles[bend_id])

    def is_bend_completed(self, bend_id: str) -> bool:
        target = self.target_for(bend_id)
        cur = self.angle_for(bend_id)
        return abs(cur - target) <= ANGLE_EPS

    def is_bend_flat(self, bend_id: str) -> bool:
        return abs(self.angle_for(bend_id)) <= ANGLE_EPS

    def is_valid_sequential_prefix(self) -> tuple[bool, int]:
        """Check whether current angles form a valid sequential prefix:
        bends [0 .. k-1] at target, bend k in [0, target], bends [k+1 .. N-1] at 0.
        Returns (is_valid, first_incomplete_index).
        """
        first_incomplete = self.total_steps
        for idx, bid in enumerate(self.order):
            if not self.is_bend_completed(bid):
                first_incomplete = idx
                break

        if first_incomplete == self.total_steps:
            return True, self.total_steps - 1

        # All subsequent bends after first_incomplete must be flat (0 deg)
        for idx in range(first_incomplete + 1, self.total_steps):
            if not self.is_bend_flat(self.order[idx]):
                return False, first_incomplete

        return True, first_incomplete

    def _sync_status_when_stopped(self) -> None:
        if all(self.is_bend_flat(bid) for bid in self.order):
            self.status = PlaybackStatus.IDLE
        elif all(self.is_bend_completed(bid) for bid in self.order):
            self.status = PlaybackStatus.COMPLETED
        else:
            self.status = PlaybackStatus.PAUSED

    def reset(self) -> None:
        """Stop playback, reset all bends to 0 deg, set active_index=0 and status=IDLE."""
        self.fold_state.reset_all()
        self.active_index = 0
        self.status = PlaybackStatus.IDLE

    def select_operation(self, index: int) -> None:
        """Select operation index without resetting or auto-folding any bend.

        If currently PLAYING, transitions to PAUSED first.
        """
        if not 0 <= index < self.total_steps:
            raise SequenceError(f"Operation index out of range: {index}")
        if self.status == PlaybackStatus.PLAYING:
            self.status = PlaybackStatus.PAUSED
        self.active_index = int(index)

    def set_manual_angle(self, signed_angle_deg: float) -> None:
        """Set the angle of the currently selected bend manually.

        If currently PLAYING, stops automatic playback first so the timer cannot
        overwrite the user's manual change. Preserves all other bend angles.
        """
        if self.status == PlaybackStatus.PLAYING:
            self.status = PlaybackStatus.PAUSED
        self.fold_state.set_angle(self.active_bend_id, float(signed_angle_deg))
        self._sync_status_when_stopped()

    def step_manual_angle(self, delta_mag_deg: float) -> None:
        """Step the active bend magnitude by delta_mag_deg in its target sign direction."""
        target = self.target_angle_deg
        sign = 1.0 if target >= 0 else -1.0
        new_mag = min(abs(target), max(0.0, abs(self.current_angle_deg) + float(delta_mag_deg)))
        self.set_manual_angle(0.0 if new_mag < 1e-9 else sign * new_mag)

    def play_all(self) -> None:
        """Start automatic sequential playback from a valid sequential state.

        - If IDLE: starts from first bend (index 0).
        - If COMPLETED: resets all bends to 0 deg first, then starts from index 0.
        - If PAUSED on a valid sequential prefix: resumes from the first incomplete bend.
        - If manual edits broke the sequential prefix (e.g. B01=0, B02=-45): raises
          SequenceError instructing the caller to Reset first rather than silently
          overwriting or continuing an invalid prefix.
        """
        if self.status == PlaybackStatus.COMPLETED or all(
            self.is_bend_completed(bid) for bid in self.order
        ):
            self.fold_state.reset_all()
            self.active_index = 0
            self.status = PlaybackStatus.PLAYING
            return

        valid, resume_idx = self.is_valid_sequential_prefix()
        if not valid:
            raise SequenceError(
                "Углы гибов изменены вручную вне порядка последовательности. "
                "Нажмите Reset (|<) для запуска Play All с плоской заготовки."
            )
        self.active_index = resume_idx
        self.status = PlaybackStatus.PLAYING

    def pause(self) -> None:
        """Pause automatic playback without altering any angles."""
        if self.status == PlaybackStatus.PLAYING:
            self.status = PlaybackStatus.PAUSED

    def resume(self) -> None:
        """Resume automatic playback from PAUSED state without re-bending completed steps."""
        if self.status == PlaybackStatus.PAUSED:
            self.play_all()

    def advance_time(self, dt_sec: float) -> bool:
        """Advance automatic playback by elapsed monotonic time `dt_sec` (in seconds).

        To prevent jumping across multiple operations after a UI pause or lag spike,
        at most ONE bend is advanced per tick and it is clamped at its exact target
        angle before transitioning to the next bend on the subsequent tick.
        Returns True if state changed.
        """
        if self.status != PlaybackStatus.PLAYING:
            return False
        if not isfinite(dt_sec) or dt_sec <= 0.0:
            return False

        # Cap single-tick dt to prevent huge jumps if the UI thread was suspended
        effective_dt = min(float(dt_sec), 0.1)

        # Advance active_index if current bend is already at target
        while self.active_index < self.total_steps and self.is_bend_completed(self.active_bend_id):
            if self.active_index + 1 < self.total_steps:
                self.active_index += 1
            else:
                self.status = PlaybackStatus.COMPLETED
                return True

        bid = self.active_bend_id
        target = self.target_for(bid)
        sign = 1.0 if target >= 0.0 else -1.0
        cur_mag = abs(self.angle_for(bid))
        target_mag = abs(target)
        next_mag = cur_mag + self.speed_deg_per_sec * effective_dt

        if next_mag >= target_mag - ANGLE_EPS:
            # Clamp to exact target angle — zero cumulative error between bends
            self.fold_state.set_angle(bid, target)
            if self.active_index + 1 < self.total_steps:
                self.active_index += 1
            else:
                self.status = PlaybackStatus.COMPLETED
            return True

        self.fold_state.set_angle(bid, sign * next_mag)
        return True

    def next_step(self) -> None:
        """When stopped, transition instantaneously to the next completed prefix state."""
        if self.status == PlaybackStatus.PLAYING:
            self.pause()

        valid, first_inc = self.is_valid_sequential_prefix()
        if not valid:
            raise SequenceError(
                "Состояние изменено вручную вне порядка. Нажмите Reset (|<) перед пошаговым переходом."
            )

        if all(self.is_bend_completed(bid) for bid in self.order):
            self.active_index = self.total_steps - 1
            self.status = PlaybackStatus.COMPLETED
            return

        # Complete `first_inc` bend to its exact target angle
        bid = self.order[first_inc]
        self.fold_state.set_angle(bid, self.target_for(bid))
        if first_inc + 1 < self.total_steps:
            self.active_index = first_inc + 1
            self.status = PlaybackStatus.PAUSED
        else:
            self.active_index = first_inc
            self.status = PlaybackStatus.COMPLETED

    def previous_step(self) -> None:
        """When stopped, return instantaneously to the previous completed prefix state."""
        if self.status == PlaybackStatus.PLAYING:
            self.pause()

        valid, first_inc = self.is_valid_sequential_prefix()
        if not valid:
            raise SequenceError(
                "Состояние изменено вручную вне порядка. Нажмите Reset (|<) перед пошаговым переходом."
            )

        if all(self.is_bend_flat(bid) for bid in self.order):
            self.active_index = 0
            self.status = PlaybackStatus.IDLE
            return

        if all(self.is_bend_completed(bid) for bid in self.order):
            # From COMPLETED: unfold the last bend to 0 deg
            last_idx = self.total_steps - 1
            self.fold_state.set_angle(self.order[last_idx], 0.0)
            self.active_index = last_idx
            self._sync_status_when_stopped()
            return

        # If first_inc bend is partially bent (> 0), reset it to 0 deg and stay on first_inc
        cur_bid = self.order[first_inc]
        if not self.is_bend_flat(cur_bid):
            self.fold_state.set_angle(cur_bid, 0.0)
            self.active_index = first_inc
            self._sync_status_when_stopped()
            return

        # Otherwise first_inc is at 0 deg and first_inc > 0: unfold (first_inc - 1) to 0 deg
        prev_idx = max(0, first_inc - 1)
        self.fold_state.set_angle(self.order[prev_idx], 0.0)
        self.active_index = prev_idx
        self._sync_status_when_stopped()

    def status_line(self) -> str:
        """Format informational status line:
        `Step i/N | Bxx | current° / target° | STATUS`
        """
        return "Step {}/{} | {} | {:+.1f}° / {:+.1f}° | {}".format(
            self.active_index + 1,
            self.total_steps,
            self.active_bend_id,
            self.current_angle_deg,
            self.target_angle_deg,
            self.status.value,
        )


class FoldSequenceController:
    def __init__(self, graph, fixed_panel, order=None):
        self.graph = graph
        self.fixed_panel = fixed_panel
        rooted = graph.rooted(fixed_panel)
        known = {bend.bend_id for bend in rooted}
        self.order = tuple(order) if order is not None else tuple(bend.bend_id for bend in rooted)
        if len(self.order) != len(known) or set(self.order) != known:
            raise SequenceError("Sequence must include every bend exactly once")
        self.targets = {bend.bend_id: bend.angle_deg for bend in rooted}

    @property
    def steps(self):
        return len(self.order)

    def frame(self, position):
        if not isfinite(position) or not 0 <= position <= self.steps:
            raise SequenceError("Position outside sequence")
        state = FoldState(self.graph, self.fixed_panel)
        full = min(int(position), self.steps)
        fraction = position - full
        for bend_id in self.order[:full]:
            state.set_angle(bend_id, self.targets[bend_id])
        active = None
        if full < self.steps:
            active = self.order[full]
            if fraction > 1e-12:
                state.set_angle(active, self.targets[active] * fraction)
        return SequenceFrame(full, fraction, active,
                             tuple((bend, state.angles[bend]) for bend in self.order),
                             state.snapshot())
