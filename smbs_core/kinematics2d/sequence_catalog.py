"""Read-only diagnostic sequence catalog for automatic and manual SMBS JSON.

Files are never treated as physical certification. The FreeCAD controller must
replay any imported sequence with the currently loaded part/tooling before
assigning it to the simulation. No Qt or FreeCAD dependency.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
import json


@dataclass(frozen=True)
class CatalogEntry:
    sequence: tuple[str, ...]
    origin: str
    source: str
    evidence: str
    cost: Optional[float]
    cost_complete: bool
    warning_count: int
    warning_bends: tuple[str, ...]
    warnings: tuple[str, ...]
    weights: Optional[dict[str, float]]
    cost_breakdown: tuple[dict[str, Any], ...]
    model_label: str = ""
    partial: bool = False

    @property
    def key(self) -> tuple[str, tuple[str, ...], str]:
        return (self.origin, self.sequence, self.source)


def _sequence(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("Sequence must be a non-empty list")
    if any(not isinstance(v, str) or not v.strip() for v in value):
        raise ValueError("Sequence bend IDs must be nonempty strings")
    seq = tuple(value)
    if len(set(seq)) != len(seq):
        raise ValueError("Duplicate bend IDs in sequence")
    return seq


def _cost(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if isfinite(value) and value >= 0 else None


def _weights(value: Any) -> Optional[dict[str, float]]:
    if not isinstance(value, dict):
        return None
    result = {}
    for key, val in value.items():
        if isinstance(key, str) and _cost(val) is not None:
            result[key] = float(val)
    return result or None


def _warning_bends(steps: Any, explicit: Any = ()) -> tuple[str, ...]:
    result = []
    for item in explicit if isinstance(explicit, list) else ():
        if isinstance(item, dict) and isinstance(item.get("bend_id"), str):
            result.append(item["bend_id"])
    for item in steps if isinstance(steps, list) else ():
        if not isinstance(item, dict):
            continue
        e = str(item.get("evidence", ""))
        if ("WARNING" in e or item.get("conditional_retract_warning")
                or item.get("diagnostic_findings")
                and e not in ("SAMPLED_2D_CLEAR_OR_TOUCH", "")):
            bid = item.get("bend_id")
            if isinstance(bid, str):
                result.append(bid)
    return tuple(dict.fromkeys(result))


def parse_diagnostic(data: Mapping[str, Any], source: str) -> list[CatalogEntry]:
    """Extract all full/partial candidates, or one operator-defined sequence.

    Never infer a score from absent costs, or a clean status from missing
    validation. Corrupt candidates are rejected individually.
    """
    if not isinstance(data, dict):
        raise ValueError("Diagnostic JSON root must be an object")
    entries = []
    if isinstance(data.get("candidates"), list):
        weights = _weights(data.get("operation_weights"))
        for cand in data["candidates"]:
            if not isinstance(cand, dict):
                continue
            try:
                seq = _sequence(cand.get("sequence"))
            except ValueError:
                continue
            steps = cand.get("steps", [])
            wb = _warning_bends(steps)
            evidence = str(cand.get("evidence", "UNVERIFIED_IMPORTED"))
            cost = _cost(cand.get("cost"))
            breakdown = cand.get("cost_breakdown", [])
            entries.append(CatalogEntry(
                seq, "AUTO", source, evidence, cost,
                bool(cand.get("cost_complete", False)) and cost is not None,
                len(wb), wb, tuple(
                    f"{bend}: RETRACT/условная 2D-проверка" for bend in wb
                ), weights,
                tuple(v for v in breakdown if isinstance(v, dict))
                if isinstance(breakdown, list) else (),
                partial="PARTIAL" in evidence,
            ))
        partial = data.get("partial_candidate")
        if isinstance(partial, dict) and partial.get("sequence"):
            try:
                seq = _sequence(partial["sequence"])
                if not any(e.sequence == seq for e in entries):
                    wb = _warning_bends(partial.get("steps", []))
                    entries.append(CatalogEntry(
                        seq, "AUTO", source,
                        str(partial.get("evidence", "PARTIAL_UNVERIFIED")),
                        _cost(partial.get("cost")),
                        bool(partial.get("cost_complete", False)),
                        len(wb), wb,
                        tuple(f"{bid}: частичный маршрут" for bid in wb),
                        weights, (), partial=True,
                    ))
            except ValueError:
                pass
        return entries

    if "selected_sequence" in data and "steps" in data:
        seq = _sequence(data["selected_sequence"])
        accepted = tuple(data.get("accepted_prefix", ()))
        if any(b not in seq for b in accepted) or len(set(accepted)) != len(accepted):
            raise ValueError("Invalid accepted manual prefix")
        plan = data.get("plan_cost") or {}
        score = _cost(plan.get("total")) if isinstance(plan, dict) else None
        complete_cost = bool(
            isinstance(plan, dict) and plan.get("is_complete")
            and data.get("plan_cost_selected_order_complete")
            and len(accepted) == len(seq)
        ) and score is not None
        warn_bends = _warning_bends(data.get("steps"), data.get("retract_warnings"))
        blocker = data.get("first_blocker")
        msgs = [f"{bid}: RETRACT/условная 2D-проверка" for bid in warn_bends]
        if isinstance(blocker, dict):
            msgs.append(
                "STOP: " + str(blocker.get("bend_id", "?")) +
                " / " + str(blocker.get("reason", "2D-ошибка"))
            )
        model = data.get("model", {})
        return [CatalogEntry(
            seq, "MANUAL", source,
            str(data.get("status", "UNVERIFIED_IMPORTED")),
            score, complete_cost, len(warn_bends), warn_bends,
            tuple(msgs),
            _weights(data.get("operation_weights") or
                     (plan.get("weights") if isinstance(plan, dict) else None)),
            tuple(v for v in plan.get("steps", []) if isinstance(v, dict))
            if isinstance(plan, dict) else (),
            model_label=str(model.get("part_label", "")) if isinstance(model, dict)
            else "",
            partial=(len(seq) < int(data.get("total_bends", len(seq)))),
        )]
    raise ValueError("Unsupported SMBS diagnostic JSON format")


def load_catalog_file(path: str | Path) -> list[CatalogEntry]:
    p = Path(path)
    if p.stat().st_size > 25 * 1024 * 1024:
        raise ValueError("Diagnostic exceeds 25 MiB safety limit")
    with p.open("r", encoding="utf-8") as stream:
        payload = json.load(stream)
    return parse_diagnostic(payload, str(p))
