"""Bounded cooperative operation search. Collision evidence belongs to its oracle.

AABB overlap alone must never prune a path. Unknown paths remain candidates,
with explicit unresolved evidence. Per-bend stage precedence is mandatory.
"""
from dataclasses import dataclass
from math import isfinite
from time import monotonic


@dataclass(frozen=True)
class SearchOperation:
    operation_id: str
    bend_id: str
    target_angle_deg: float
    predecessor: str | None = None


def search(operations, evaluate, *, max_nodes=1000, max_results=5,
           seconds=60, cancelled=lambda: False):
    """Yield progress and results; evaluate receives full prefix and pose map.

    Each orientation is explored independently; there is no unsafe merging of
    histories that happen to have equal sets of completed bends.
    """
    ops = tuple(operations)
    ids = {op.operation_id for op in ops}
    if (not ops or len(ids) != len(ops) or max_nodes < 1 or max_results < 1
            or not isfinite(seconds) or seconds <= 0
            or any(op.predecessor is not None and op.predecessor not in ids for op in ops)):
        raise ValueError('Invalid search input')
    started = monotonic()
    stack = [((), {}, 0.0)]
    nodes = results = 0
    while stack:
        if cancelled() or nodes >= max_nodes or monotonic() - started >= seconds:
            yield {'kind': 'finished', 'reason': 'CANCELLED' if cancelled() else 'LIMIT',
                   'nodes': nodes, 'results': results}
            return
        prefix, poses, cost = stack.pop()
        done = {o.operation_id for o in prefix}
        children = []
        for op in ops:
            if op.operation_id in done or (op.predecessor and op.predecessor not in done):
                continue
            for reverse in (False, True):
                if cancelled() or nodes >= max_nodes or monotonic() - started >= seconds:
                    break
                path = prefix + (op,)
                orientation = dict(poses, **{op.operation_id: reverse})
                report = evaluate(path, orientation, len(path) == len(ops))
                nodes += 1
                yield {'kind': 'progress', 'nodes': nodes, 'depth': len(path)}
                if report.get('status') == 'COLLISION':
                    continue
                value = float(report.get('cost', cost))
                if not isfinite(value) or value < 0:
                    raise ValueError('Invalid operation cost')
                if len(path) == len(ops):
                    results += 1
                    yield {'kind': 'result', 'operations': path, 'poses': orientation,
                           'cost': value, 'report': report}
                    if results >= max_results:
                        yield {'kind': 'finished', 'reason': 'RESULT_LIMIT', 'nodes': nodes,
                               'results': results}
                        return
                else:
                    children.append((path, orientation, value))
        # Cheapest evaluated prefix is expanded first, without claiming global optimality.
        stack.extend(sorted(children, key=lambda p: p[2], reverse=True))
    yield {'kind': 'finished', 'reason': 'EXHAUSTED', 'nodes': nodes, 'results': results}
