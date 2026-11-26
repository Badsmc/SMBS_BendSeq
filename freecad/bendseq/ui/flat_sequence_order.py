"""Authoritative operation order for the flat-pattern sequence view.

A manual prefix is a selection scope, not a second independent order.
A FAST result updates controller.operation_steps, and the view must display
that order immediately. Never number remaining unselected bends as if they
belonged to a partial plan.
"""


def displayed_operations(controller, *, manual_prefix=(), editing=False, catalog_prefix=()):
    steps = tuple(controller.operation_steps)
    if controller.has_stages:
        return steps
    prefix = tuple(manual_prefix or catalog_prefix)
    if prefix or editing:
        actual = tuple(step.bend_id for step in steps[:len(prefix)])
        # Stale prefixes cannot override the controller's current plan.
        if actual == prefix:
            return steps[:len(prefix)]
    return steps


def operation_numbers(steps):
    labels = {}
    for number, step in enumerate(steps, 1):
        labels.setdefault(step.bend_id, []).append((number, step))
    return labels
