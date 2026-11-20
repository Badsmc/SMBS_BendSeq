"""Operation-level pre-bend and finish-bend sequencing.

A physical bend ID remains the single FoldState key. Operations have distinct IDs
but never create duplicate bend zones or geometry. Each operation sets a cumulative
absolute angle, not an additional angle to add.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Sequence

from .fold_sequence import (
    ANGLE_EPS, BendSequenceController, PlaybackStatus, SequenceError,
)


@dataclass(frozen=True)
class BendOperation:
    operation_id: str
    bend_id: str
    target_angle_deg: float
    kind: str = "SINGLE"  # SINGLE, PREBEND, FINISH

    def to_dict(self) -> dict:
        return {
            "operation_id": self.operation_id,
            "bend_id": self.bend_id,
            "target_angle_deg": self.target_angle_deg,
            "kind": self.kind,
        }


class StagedBendSequenceController(BendSequenceController):
    """Playback over operation steps, with a single authoritative physical FoldState.

    The inherited 'order' remains the unique physical bend order for older
    FreeCAD data contracts. 'operation_steps' is the real simulation timeline.
    Physical validators must explicitly support repeated operations before they
    can claim to validate a staged plan.
    """

    def __init__(self, fold_state, order=None, speed_deg_per_sec=90.0):
        self._stage_steps = None
        self._completed_steps = 0
        super().__init__(fold_state, order=order, speed_deg_per_sec=speed_deg_per_sec)
        self._stage_steps = tuple(
            BendOperation(bid, bid, self.target_for(bid))
            for bid in self.order
        )

    @property
    def operation_steps(self) -> tuple[BendOperation, ...]:
        return self._stage_steps

    @property
    def has_stages(self) -> bool:
        return any(step.kind != "SINGLE" for step in self._stage_steps)

    @property
    def completed_step_count(self) -> int:
        return self._completed_steps

    @property
    def total_steps(self) -> int:
        return len(self._stage_steps) if self._stage_steps is not None else len(self.order)

    def operation_at(self, index: int) -> BendOperation:
        if not 0 <= index < self.total_steps:
            raise SequenceError("Operation index out of range")
        return self._stage_steps[index]

    @property
    def active_operation_id(self) -> str:
        return self.operation_at(self.active_index).operation_id

    @property
    def active_bend_id(self) -> str:
        if self._stage_steps is None:
            return super().active_bend_id
        return self.operation_at(self.active_index).bend_id

    @property
    def target_angle_deg(self) -> float:
        if self._stage_steps is None:
            return super().target_angle_deg
        return self.operation_at(self.active_index).target_angle_deg

    def operation_start_deg(self, index: int) -> float:
        step = self.operation_at(index)
        for prev in reversed(self._stage_steps[:index]):
            if prev.bend_id == step.bend_id:
                return prev.target_angle_deg
        return 0.0

    def _rebuild_single_steps(self) -> None:
        self._stage_steps = tuple(
            BendOperation(bid, bid, self.target_for(bid))
            for bid in self.order
        )
        self._completed_steps = 0

    def set_bend_order(self, order: Sequence[str]) -> None:
        if self._stage_steps is not None and self.has_stages:
            raise SequenceError(
                "Для предгибов изменяйте порядок операций, а не исходных гибов."
            )
        super().set_bend_order(order)
        if self._stage_steps is not None:
            self._rebuild_single_steps()

    def move_up(self, index=None) -> int:
        if not self.has_stages:
            idx = super().move_up(index)
            self._rebuild_single_steps()
            return idx
        idx = self.active_index if index is None else int(index)
        if idx <= 0:
            return idx
        order = [s.operation_id for s in self._stage_steps]
        order[idx - 1], order[idx] = order[idx], order[idx - 1]
        self.reorder_operations(order)
        self.active_index = idx - 1
        return self.active_index

    def move_down(self, index=None) -> int:
        if not self.has_stages:
            idx = super().move_down(index)
            self._rebuild_single_steps()
            return idx
        idx = self.active_index if index is None else int(index)
        if idx >= self.total_steps - 1:
            return idx
        order = [s.operation_id for s in self._stage_steps]
        order[idx], order[idx + 1] = order[idx + 1], order[idx]
        self.reorder_operations(order)
        self.active_index = idx + 1
        return self.active_index

    def restore_canonical_order(self) -> None:
        if not self.has_stages:
            super().restore_canonical_order()
            self._rebuild_single_steps()
            return
        sorted_steps = sorted(
            self._stage_steps,
            key=lambda s: (
                self.canonical_order.index(s.bend_id),
                0 if s.kind == "PREBEND" else 1,
            ),
        )
        self.reorder_operations([s.operation_id for s in sorted_steps])
        self.active_index = 0

    def add_prebend(self, bend_id: str, intermediate_angle_deg: float) -> None:
        """Create/update the PREBEND then FINISH operations for one bend."""
        self._ensure_can_reorder()
        if bend_id not in self.canonical_order:
            raise SequenceError("Неизвестный физический гиб: " + bend_id)
        try:
            intermediate = float(intermediate_angle_deg)
        except (ValueError, TypeError) as exc:
            raise SequenceError("Введите числовой промежуточный угол.") from exc
        final = self.target_for(bend_id)
        if not isfinite(intermediate) or (
            intermediate * final <= 0.0
            or abs(intermediate) <= ANGLE_EPS
            or abs(intermediate) >= abs(final) - ANGLE_EPS
        ):
            raise SequenceError(
                "Предварительный угол должен быть одного знака с конечным "
                "и строго между 0° и заданным конечным углом."
            )
        steps = list(self._stage_steps)
        existing = next(
            (i for i, s in enumerate(steps)
             if s.bend_id == bend_id and s.kind == "PREBEND"), None
        )
        if existing is None:
            index = next(i for i, s in enumerate(steps) if s.bend_id == bend_id)
            steps[index] = BendOperation(bend_id + ".2", bend_id, final, "FINISH")
            steps.insert(index, BendOperation(
                bend_id + ".1", bend_id, intermediate, "PREBEND"
            ))
            self.active_index = index
        else:
            steps[existing] = BendOperation(
                bend_id + ".1", bend_id, intermediate, "PREBEND"
            )
            self.active_index = existing
        self._stage_steps = tuple(steps)
        self._completed_steps = 0
        self.status = PlaybackStatus.IDLE

    def remove_prebend(self, bend_id: str) -> None:
        self._ensure_can_reorder()
        steps = list(self._stage_steps)
        if not any(s.bend_id == bend_id and s.kind == "PREBEND" for s in steps):
            return
        steps = [s for s in steps if not (
            s.bend_id == bend_id and s.kind == "PREBEND"
        )]
        for i, step in enumerate(steps):
            if step.bend_id == bend_id:
                steps[i] = BendOperation(bend_id, bend_id, self.target_for(bend_id))
                self.active_index = i
                break
        self._stage_steps = tuple(steps)
        self._completed_steps = 0
        self.status = PlaybackStatus.IDLE

    def reorder_operations(self, operation_ids: Sequence[str]) -> None:
        self._ensure_can_reorder()
        ids = tuple(operation_ids)
        by_id = {step.operation_id: step for step in self._stage_steps}
        if (len(ids) != len(by_id) or len(set(ids)) != len(ids)
                or set(ids) != set(by_id)):
            raise SequenceError("Порядок операций содержит пропуски или дубликаты.")
        steps = tuple(by_id[op_id] for op_id in ids)
        seen: set[str] = set()
        for op in steps:
            if op.kind == "FINISH" and op.bend_id not in seen:
                raise SequenceError(
                    f"Догиб {op.bend_id} нельзя ставить раньше предварительного гиба."
                )
            if op.kind == "PREBEND":
                seen.add(op.bend_id)
        active_id = self.active_operation_id
        self._stage_steps = steps
        self.active_index = ids.index(active_id)
        self._completed_steps = 0
        self.status = PlaybackStatus.IDLE

    def operation_snapshot(self) -> list[dict]:
        return [step.to_dict() for step in self._stage_steps]

    def restore_operation_snapshot(self, data: Sequence[dict]) -> None:
        self._ensure_can_reorder()
        if not isinstance(data, (list, tuple)) or len(data) > 2 * len(self.canonical_order):
            raise SequenceError("Некорректный список операций предгиба.")
        from collections import Counter
        counts = Counter()
        staged = []
        for entry in data:
            if not isinstance(entry, dict):
                raise SequenceError("Некорректная операция.")
            bid = entry.get("bend_id")
            kind = entry.get("kind")
            op_id = entry.get("operation_id")
            if bid not in self.canonical_order or kind not in ("SINGLE", "PREBEND", "FINISH"):
                raise SequenceError("Неизвестный гиб или этап операции.")
            counts[(bid, kind)] += 1
            target = entry.get("target_angle_deg")
            if isinstance(target, bool) or not isinstance(target, (int, float)) or not isfinite(target):
                raise SequenceError("Недопустимый угол этапа.")
            final = self.target_for(bid)
            if kind in ("SINGLE", "FINISH") and abs(target - final) > ANGLE_EPS:
                raise SequenceError("Конечный угол изменён относительно модели.")
            if kind == "PREBEND" and not (target * final > 0 and ANGLE_EPS < abs(target) < abs(final) - ANGLE_EPS):
                raise SequenceError("Недопустимый угол предгиба.")
            expected_id = bid if kind == "SINGLE" else bid + (".1" if kind == "PREBEND" else ".2")
            if op_id != expected_id:
                raise SequenceError("Нарушена идентификация операции.")
            staged.append(BendOperation(op_id, bid, float(target), kind))
        for bid in self.canonical_order:
            kinds = [s.kind for s in staged if s.bend_id == bid]
            if sorted(kinds) not in (["SINGLE"], ["FINISH", "PREBEND"]):
                raise SequenceError("Каждый гиб должен иметь один финальный этап.")
        if len({s.operation_id for s in staged}) != len(staged):
            raise SequenceError("Дублирование идентификатора операции.")
        seen = set()
        for step in staged:
            if step.kind == "PREBEND":
                seen.add(step.bend_id)
            if step.kind == "FINISH" and step.bend_id not in seen:
                raise SequenceError("Догиб указан раньше предгиба.")
        self._stage_steps = tuple(staged)
        self.active_index = 0
        self._completed_steps = 0
        self.status = PlaybackStatus.IDLE

    def _state_for_prefix(self, count: int) -> dict[str, float]:
        angles = {bid: 0.0 for bid in self.canonical_order}
        for step in self._stage_steps[:count]:
            angles[step.bend_id] = step.target_angle_deg
        return angles

    def _sync_status_when_stopped(self) -> None:
        if self._stage_steps is None or not self.has_stages:
            return super()._sync_status_when_stopped()
        if self._completed_steps >= self.total_steps:
            self.status = PlaybackStatus.COMPLETED
        elif self._completed_steps == 0 and all(
            abs(a) < ANGLE_EPS for a in self.fold_state.angles.values()
        ):
            self.status = PlaybackStatus.IDLE
        else:
            self.status = PlaybackStatus.PAUSED

    def reset(self) -> None:
        super().reset()
        self._completed_steps = 0

    def is_valid_sequential_prefix(self) -> tuple[bool, int]:
        if not self.has_stages:
            return super().is_valid_sequential_prefix()
        count = self._completed_steps
        expected = self._state_for_prefix(count)
        active = self._stage_steps[count] if count < self.total_steps else None
        for bid, expected_angle in expected.items():
            actual = self.fold_state.angles[bid]
            if active is not None and active.bend_id == bid:
                lo = min(expected_angle, active.target_angle_deg)
                hi = max(expected_angle, active.target_angle_deg)
                if not lo - ANGLE_EPS <= actual <= hi + ANGLE_EPS:
                    return False, count
            elif abs(actual - expected_angle) > ANGLE_EPS:
                return False, count
        return True, min(count, self.total_steps - 1)

    def set_manual_angle(self, signed_angle_deg: float) -> None:
        if not self.has_stages:
            return super().set_manual_angle(signed_angle_deg)
        if self.active_index != self._completed_steps:
            raise SequenceError(
                "Для изменения угла выберите следующий этап или выполните Reset."
            )
        angle = float(signed_angle_deg)
        start = self.operation_start_deg(self.active_index)
        end = self.target_angle_deg
        if not isfinite(angle) or not min(start, end) - ANGLE_EPS <= angle <= max(start, end) + ANGLE_EPS:
            raise SequenceError("Угол выходит за границы выбранного этапа.")
        if self.status == PlaybackStatus.PLAYING:
            self.status = PlaybackStatus.PAUSED
        self.fold_state.set_angle(self.active_bend_id, angle)
        self._sync_status_when_stopped()

    def play_all(self) -> None:
        if not self.has_stages:
            return super().play_all()
        if self.status == PlaybackStatus.PLAYING:
            return
        if self.status == PlaybackStatus.COMPLETED:
            self.reset()
        valid, _ = self.is_valid_sequential_prefix()
        if not valid:
            raise SequenceError("Нарушен порядок углов. Выполните Reset.")
        self.active_index = min(self._completed_steps, self.total_steps - 1)
        self.status = PlaybackStatus.PLAYING

    def mark_operation_completed(self) -> None:
        """Called by the machine cycle AFTER finishing its retract for this step."""
        if not self.has_stages:
            return
        if self._completed_steps >= self.total_steps:
            return
        step = self._stage_steps[self._completed_steps]
        if abs(self.fold_state.angles[step.bend_id] - step.target_angle_deg) > ANGLE_EPS:
            raise SequenceError("Фактический угол не достиг целевого для этапа.")
        self._completed_steps += 1
        if self._completed_steps == self.total_steps:
            self.status = PlaybackStatus.COMPLETED

    def advance_time(self, dt_sec: float) -> bool:
        if not self.has_stages:
            return super().advance_time(dt_sec)
        if self.status != PlaybackStatus.PLAYING or not isfinite(dt_sec) or dt_sec <= 0:
            return False
        if self._completed_steps >= self.total_steps:
            self.status = PlaybackStatus.COMPLETED
            return False
        self.active_index = self._completed_steps
        op = self._stage_steps[self.active_index]
        current = self.fold_state.angles[op.bend_id]
        diff = op.target_angle_deg - current
        rate = self.speed_deg_per_sec * min(float(dt_sec), 0.1)
        if abs(diff) <= rate + ANGLE_EPS:
            self.fold_state.set_angle(op.bend_id, op.target_angle_deg)
            self.mark_operation_completed()
            self.active_index = min(self._completed_steps, self.total_steps - 1)
        else:
            self.fold_state.set_angle(op.bend_id, current + (rate if diff > 0 else -rate))
        return True

    def next_step(self) -> None:
        if not self.has_stages:
            return super().next_step()
        if self.status == PlaybackStatus.PLAYING:
            self.pause()
        valid, _ = self.is_valid_sequential_prefix()
        if not valid:
            raise SequenceError("Нарушен порядок этапов. Выполните Reset.")
        if self._completed_steps < self.total_steps:
            op = self._stage_steps[self._completed_steps]
            self.fold_state.set_angle(op.bend_id, op.target_angle_deg)
            self.mark_operation_completed()
        self.active_index = min(self._completed_steps, self.total_steps - 1)
        self._sync_status_when_stopped()

    def seek_staged_operation_start(self, index: int) -> None:
        """Replay the completed prefix so a selected stage can be scrubbed."""
        self.operation_at(index)  # Validate before changing the pose.
        self.fold_state.reset_all()
        for bid, angle in self._state_for_prefix(index).items():
            if abs(angle) > ANGLE_EPS:
                self.fold_state.set_angle(bid, angle)
        self._completed_steps = self.active_index = index
        self._sync_status_when_stopped()

    def previous_step(self) -> None:
        if not self.has_stages:
            return super().previous_step()
        if self.status == PlaybackStatus.PLAYING:
            self.pause()
        valid, _ = self.is_valid_sequential_prefix()
        if not valid:
            raise SequenceError("Нарушен порядок этапов. Выполните Reset.")
        if self._completed_steps < self.total_steps:
            current = self._stage_steps[self._completed_steps]
            start = self.operation_start_deg(self._completed_steps)
            if abs(self.fold_state.angles[current.bend_id] - start) > ANGLE_EPS:
                self.fold_state.set_angle(current.bend_id, start)
                self.active_index = self._completed_steps
                self._sync_status_when_stopped()
                return
        self.seek_staged_operation_start(max(0, self._completed_steps - 1))

    def status_line(self) -> str:
        if not self.has_stages:
            return super().status_line()
        step = self.operation_at(self.active_index)
        return (
            f"Этап {self.active_index+1}/{self.total_steps} | "
            f"{step.operation_id} ({step.kind}) | "
            f"{self.current_angle_deg:+.1f}° / {step.target_angle_deg:+.1f}° | "
            f"{self.status.value}"
        )
