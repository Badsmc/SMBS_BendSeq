"""Experimental reverse (unbending) DFS using conservative 3D AABB ranking.

This module is a candidate generator, NOT a physical collision validator.
Overlap of bounding boxes is ambiguous, so it must never prune a transition
or certify a bend. Actual tool/workpiece clearance is checked by the
independent native D6 validator in the forward sequence.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from time import perf_counter
from typing import Any, Callable


@dataclass(frozen=True)
class ReverseCandidate:
    sequence: tuple[str, ...]
    orientation_by_bend: tuple[tuple[str, bool], ...]
    bbox_overlap_score: float
    evidence: str = "REVERSE_AABB_RANKED_UNVERIFIED"


@dataclass(frozen=True)
class ReverseSearchReport:
    candidates: tuple[ReverseCandidate, ...]
    deepest_unbent: tuple[str, ...]
    visited_states: int
    evaluated_transitions: int
    elapsed_s: float
    timed_out: bool
    geometry_available: bool
    reason: str

    def to_dict(self) -> dict:
        return {
            "candidates": [
                {"sequence": list(c.sequence),
                 "orientation_by_bend": dict(c.orientation_by_bend),
                 "bbox_overlap_score": c.bbox_overlap_score,
                 "evidence": c.evidence}
                for c in self.candidates
            ],
            "deepest_unbent": list(self.deepest_unbent),
            "visited_states": self.visited_states,
            "evaluated_transitions": self.evaluated_transitions,
            "elapsed_s": round(self.elapsed_s, 4),
            "timed_out": self.timed_out,
            "geometry_available": self.geometry_available,
            "reason": self.reason,
        }


def _vertices(panel: Any):
    for attr in ("vertices_xy", "polygon_xy", "outline_xy", "boundary_xy",
                 "vertices", "polygon", "points_xy"):
        data = getattr(panel, attr, None)
        if callable(data):
            continue
        if data is None:
            continue
        if hasattr(data, "points"):
            data = data.points
        try:
            coords = tuple((float(pt[0]), float(pt[1])) for pt in data)
            if len(coords) >= 3 and all(
                isfinite(v) for p in coords for v in p
            ):
                return coords
        except (TypeError, ValueError, IndexError, KeyError):
            pass
    return None


def _panel_polygons(panel_build: Any, graph: Any) -> dict:
    result = {}
    raw = getattr(panel_build, "panel_vertices_xy", None)
    if isinstance(raw, dict):
        for pid, coords in raw.items():
            try:
                result[str(pid)] = tuple(
                    (float(x), float(y)) for x, y in coords
                )
            except (TypeError, ValueError):
                continue
    # FreeCAD PanelBuildResult stores exact panel outlines in panel_faces,
    # rather than in its Panel objects. Use the same vertices as GUI loader.
    for panel, face in zip(
        getattr(panel_build, "panels", ()) or (),
        getattr(panel_build, "panel_faces", ()) or (),
    ):
        pid = str(getattr(panel, "panel_id", ""))
        if not pid or pid in result:
            continue
        try:
            wire = getattr(face, "OuterWire", None)
            verts = getattr(wire, "OrderedVertexes", None) or face.Vertexes
            points = tuple(
                (float(v.Point.x), float(v.Point.y)) for v in verts
            )
            if len(points) >= 3 and all(
                isfinite(v) for point in points for v in point
            ):
                result[pid] = points
        except (AttributeError, TypeError, ValueError):
            continue
    for panel in getattr(panel_build, "panels", ()) or ():
        pid = str(getattr(panel, "panel_id", ""))
        if pid and pid not in result:
            points = _vertices(panel)
            if points:
                result[pid] = points
    return result if set(graph.panels).issubset(result) else {}


def _aabb(poly, transform, thickness):
    pts = [transform.point((x, y, z)) for x, y in poly
           for z in (0.0, -thickness)]
    return tuple(min(p[k] for p in pts) for k in range(3)) + tuple(
        max(p[k] for p in pts) for k in range(3)
    )


def _overlap(a, b, margin=0.0):
    lengths = [
        min(a[k + 3], b[k + 3]) - max(a[k], b[k])
        for k in range(3)
    ]
    # Conservative broadphase: positive volume only. AABB intersections
    # alone never imply a real collision.
    if any(v <= margin for v in lengths):
        return 0.0
    return lengths[0] * lengths[1] * lengths[2]


def search_reverse_aabb(
    session: Any, *, budget_s: float = 90.0, max_candidates: int = 12,
    progress: Callable[[int, int, str], None] | None = None,
    clock: Callable[[], float] = perf_counter,
) -> ReverseSearchReport:
    """Find reverse unbend permutations; prioritize lower swept AABB overlap.

    States are subsets of already-unbent hinges, and their geometry is
    deterministic from that subset. No native OCC call takes place here.
    The returned forward orders require independent D6 revalidation.
    """
    started = clock()
    deadline = started + max(0.0, float(budget_s))
    source = getattr(session, "fold_state", None)
    panel_build = getattr(session, "panel_build", None)
    if source is None or panel_build is None:
        return ReverseSearchReport((), (), 0, 0, 0., False, False, "NO_GEOMETRY")
    graph = source.graph
    polygons = _panel_polygons(panel_build, graph)
    if not polygons:
        return ReverseSearchReport((), (), 0, 0, clock()-started, False,
                                   False, "PANEL_POLYGONS_UNAVAILABLE")
    order = tuple(source.bends)
    if not order:
        return ReverseSearchReport((), (), 0, 0, clock()-started, False,
                                   False, "NO_BENDS")
    first_zone = next((getattr(b, "bend_zone", None)
                       for b in source.bends.values()
                       if getattr(b, "bend_zone", None) is not None), None)
    thickness = float(getattr(first_zone, "thickness_mm", 1.5) or 1.5)
    if not isfinite(thickness) or thickness <= 0:
        return ReverseSearchReport((), (), 0, 0, clock()-started, False,
                                   False, "INVALID_THICKNESS")
    # Build fresh state; never change the user's animation or angles.
    from .panel_kinematics import FoldState
    state = FoldState(graph, source.fixed_panel)
    all_mask = (1 << len(order)) - 1
    indexed = {bid: i for i, bid in enumerate(order)}
    adjacency = {
        frozenset((b.fixed_panel, b.moving_panel))
        for b in source.bends.values()
    }
    extent_cache = {}
    transition_cache = {}
    visit_counts = {}
    candidates = []
    candidate_orders = set()
    first_bends_seen = set()
    root_candidate_counts = {}
    deepest = ()
    transitions = 0

    def boxes(mask, active=None, fraction=0.0):
        key = (mask, active, fraction)
        if key not in extent_cache:
            angles = {}
            for bid in order:
                if mask & (1 << indexed[bid]):
                    angles[bid] = 0.0
                elif bid == active:
                    angles[bid] = source.bends[bid].angle_deg * fraction
                else:
                    angles[bid] = source.bends[bid].angle_deg
            transforms = state._compute_transforms(angles)
            extent_cache[key] = {
                pid: _aabb(polygons[pid], transforms[pid], thickness)
                for pid in graph.panels
            }
        return extent_cache[key]

    def transition_score(mask, bid):
        key = (mask, bid)
        if key in transition_cache:
            return transition_cache[key]
        bend = source.bends[bid]
        moving = set(bend.moving_subtree)
        other = set(graph.panels) - moving
        score = 0.0
        # Moving swept AABBs are checked at several samples. This is a
        # heuristic ONLY: absent continuous proof, never return 3D_CLEAR.
        for fraction in (0.75, 0.5, 0.25, 0.0):
            scene = boxes(mask, bid, fraction)
            for p in moving:
                for q in other:
                    if frozenset((p, q)) in adjacency:
                        continue
                    score += _overlap(scene[p], scene[q])
        transition_cache[key] = score
        return score

    def dfs(mask, path, score, root):
        nonlocal deepest, transitions
        if clock() >= deadline or len(candidates) >= max_candidates:
            return
        # Allow a few different paths to the same geometric mask;
        # BBox ranking alone cannot justify discarding all alternatives.
        # Keep separate traversal histories for different root unbends.
        # The prior global mask cache suppressed all but three variants.
        cache_key = (root, mask)
        count = visit_counts.get(cache_key, 0)
        if count >= 3:
            return
        visit_counts[cache_key] = count + 1
        if len(path) > len(deepest):
            deepest = path
            if progress is not None:
                progress(len(path), len(order), path[-1] if path else "")
        if mask == all_mask:
            seq = tuple(reversed(path))
            # Spend exact OCC on different first forward bends rather than
            # on many permutations whose first step is identical.
            if (seq not in candidate_orders and seq[0] not in first_bends_seen
                    and root_candidate_counts.get(root, 0) < 2):
                candidate_orders.add(seq)
                first_bends_seen.add(seq[0])
                root_candidate_counts[root] = root_candidate_counts.get(root, 0) + 1
                candidates.append(ReverseCandidate(
                    sequence=seq,
                    orientation_by_bend=tuple((bid, False) for bid in seq),
                    bbox_overlap_score=score,
                ))
            return
        ranking = []
        for bid in order:
            bit = 1 << indexed[bid]
            if mask & bit:
                continue
            if clock() >= deadline:
                break
            try:
                metric = transition_score(mask, bid)
            except (ValueError, ArithmeticError, KeyError):
                continue
            transitions += 1
            ranking.append((metric, bid, bit))
        for metric, bid, bit in sorted(ranking):
            if clock() >= deadline or len(candidates) >= max_candidates:
                break
            next_root = root if root is not None else bid
            if root_candidate_counts.get(next_root, 0) >= 2:
                continue
            dfs(mask | bit, path + (bid,), score + metric, next_root)

    dfs(0, (), 0.0, None)
    return ReverseSearchReport(
        candidates=tuple(sorted(candidates, key=lambda c: (
            c.bbox_overlap_score, c.sequence
        ))),
        deepest_unbent=deepest,
        visited_states=len(visit_counts),
        evaluated_transitions=transitions,
        elapsed_s=max(0., clock()-started),
        timed_out=clock() >= deadline,
        geometry_available=True,
        reason="AABB_RANKING_ONLY_REQUIRES_FORWARD_NATIVE_D6",
    )
