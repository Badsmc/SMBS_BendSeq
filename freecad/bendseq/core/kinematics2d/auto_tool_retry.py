"""Bounded retry policy for selected, already reviewed tool pairs.

Never infer clearance from an empty, failed, conditional or incomplete report.
A collision rejects this sequence/pose, not every use of the physical tool.
"""
from .auto_tool_orientation import variant_key

MAX_ATTEMPTS = 3
TOTAL_MESH_BUDGET_S = 120.0


def attempt_order(selected, ranked, *, retry=False):
    """Entries are (tool candidate, FAST candidate); selected always first."""
    if not retry:
        return (selected,)
    result = [selected]
    seen = {variant_key(selected[0])}
    for entry in ranked:
        tool, candidate = entry
        key = variant_key(tool)
        if key not in seen and candidate is not None:
            result.append(entry)
            seen.add(key)
        if len(result) >= MAX_ATTEMPTS:
            break
    return tuple(result)


def mesh_outcome(report, order):
    if not isinstance(report, dict):
        return "ERROR"
    keys = ("findings", "conditional_findings", "unresolved", "pending", "completed")
    if any(not isinstance(report.get(k), (tuple, list)) for k in keys):
        return "ERROR"
    reason = report.get("reason", "")
    if reason not in ("", "TIME_BUDGET"):
        return "ERROR"
    expected = tuple(order)
    completed = tuple(report["completed"])
    pending = tuple(report["pending"])
    if completed + pending != expected:
        return "ERROR"
    if report["findings"]:
        if any(not isinstance(hit, dict) or hit.get("operation") not in expected
               for hit in report["findings"]):
            return "ERROR"
        return "CONFIRMED_COLLISION"
    if pending or report["unresolved"] or reason:
        return "INCOMPLETE"
    if report["conditional_findings"]:
        return "CONDITIONAL"
    if not isinstance(report.get("samples"), int) or report["samples"] <= 0:
        return "INCOMPLETE"
    return "SAMPLED_NO_COLLISION"


def next_action(outcome, remaining_count, remaining_seconds):
    if outcome != "CONFIRMED_COLLISION":
        return outcome
    if remaining_count <= 0:
        return "ALTERNATIVES_EXHAUSTED"
    if remaining_seconds <= 0:
        return "TOTAL_BUDGET_EXCEEDED"
    return "RETRY"
