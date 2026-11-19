"""Operation-scoped workpiece loading orientation for staged press bends.

A physical bend (B03) may appear as two operations (B03.1/B03.2).
The operation ID owns the explicit 180-degree choice, never the shared
FoldState bend ID. Legacy physical-bend settings remain the fallback.
"""
from __future__ import annotations

from typing import Mapping, Sequence


def expanded_operation_orientations(
    physical: Mapping[str, bool],
    operations: Sequence[object],
    operation_overrides: Mapping[str, bool],
) -> dict[str, bool]:
    """Return physical keys plus independently overrideable operation keys."""
    result = {str(bid): bool(value) for bid, value in physical.items()}
    for step in operations:
        op_id = str(step.operation_id)
        bid = str(step.bend_id)
        result[op_id] = bool(operation_overrides.get(op_id, result.get(bid, False)))
    return result


def reverse_axis_for_operation(
    choices: Mapping[str, bool], bend_id: str, operation_id: str
) -> bool:
    """A staged operation wins over its physical bend's default orientation."""
    return bool(choices.get(operation_id, choices.get(bend_id, False)))
