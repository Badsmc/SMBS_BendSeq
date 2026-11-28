"""Validate the exact FAST candidate before touching the live tooling."""
from math import dist
from .shared_strokes import continuous_strokes, spans_cover_interval, stroke_axis


def pinned_orientations(operations, operation_pins, bend_pins, advisory):
    result = {}
    for step in operations:
        if step.operation_id in advisory:
            continue
        if step.operation_id in operation_pins:
            result[step.operation_id] = operation_pins[step.operation_id]
        elif step.bend_id in bend_pins:
            result[step.operation_id] = bend_pins[step.bend_id]
    return result


def verify_candidate_layout(candidate, operations, bends, assembly, pinned):
    """No coverage shortcuts from the previous operation order."""
    operations = tuple(operations)
    by_id = {op.operation_id: op for op in operations}
    order = tuple(candidate.operation_ids)
    orientations = candidate.orientations()
    if len(order) != len(operations) or len(set(order)) != len(order) or set(order) != set(by_id):
        raise ValueError("FAST candidate does not contain the exact current operations")
    if set(orientations) != set(order) or any(type(v) is not bool for v in orientations.values()):
        raise ValueError("FAST candidate has missing or invalid orientations")
    if any(orientations.get(k) != v for k, v in pinned.items()):
        raise ValueError("FAST candidate changes an operator-locked orientation")
    ordered = tuple(by_id[op_id] for op_id in order)
    groups = (tuple((op.bend_id,) for op in ordered)
              if any(op.kind != "SINGLE" for op in ordered)
              else continuous_strokes(ordered, bends, orientations))
    spans = []
    for group in groups:
        length = dist(*stroke_axis(group, bends))
        spans.append((group, length))
        for kind in ("punch", "die"):
            sections = getattr(assembly, kind + "_spans_xm")
            if not spans_cover_interval(sections, -length / 2, length / 2):
                raise ValueError(f"{kind}: installed sections do not cover FAST stroke {group} ({length:g} mm)")
    return tuple(spans)
