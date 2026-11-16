"""Experimental reverse (unbending) DFS using conservative 3D AABB ranking.

This module is a candidate generator, NOT a physical collision validator.
Overlap of bounding boxes is ambiguous, so it must never prune a transition
or certify a bend. Actual tool/workpiece clearance is checked by the
independent native D6 validator in the forward sequence.
"""
from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
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
    mesh_triangles: int = 0
    mesh_panels: int = 0
    mesh_overlap_tests: int = 0
    search_mode: str = "ASTAR_BEST_FIRST"
    machine_screen_status: str = "NOT_RUN"
    machine_screen_pairs: int = 0
    machine_screen_overlaps: int = 0
    machine_screen_candidates: int = 0

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
            "mesh_triangles": self.mesh_triangles,
            "mesh_panels": self.mesh_panels,
            "mesh_overlap_tests": self.mesh_overlap_tests,
            "search_mode": self.search_mode,
            "machine_screen_status": self.machine_screen_status,
            "machine_screen_pairs": self.machine_screen_pairs,
            "machine_screen_overlaps": self.machine_screen_overlaps,
            "machine_screen_candidates": self.machine_screen_candidates,
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
    tooling: Any = None,
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
    from .reverse_mesh_screen import (
        extract_panel_triangles, transformed_prisms,
        approximate_overlap_count, build_bvh,
    )
    # Bound one-time triangulation memory for FreeCAD's UI process.
    panel_meshes = extract_panel_triangles(
        panel_build, max_triangles_per_panel=256,
    )
    mesh_triangles = sum(len(tris) for tris in panel_meshes.values())
    from collections import OrderedDict
    mesh_scene_cache = OrderedDict()
    mesh_tests = 0
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

    def mesh_scene(mask, active, fraction):
        key = (mask, active, fraction)
        if key in mesh_scene_cache:
            mesh_scene_cache.move_to_end(key)
        else:
            angles = {
                name: (0.0 if mask & (1 << indexed[name])
                       else (source.bends[name].angle_deg * fraction
                             if name == active else source.bends[name].angle_deg))
                for name in order
            }
            transforms = state._compute_transforms(angles)
            mesh_scene_cache[key] = {}
            for pid, tris in panel_meshes.items():
                prisms = transformed_prisms(tris, transforms[pid], thickness)
                mesh_scene_cache[key][pid] = (prisms, build_bvh(prisms))
            if len(mesh_scene_cache) > 48:
                mesh_scene_cache.popitem(last=False)
        return mesh_scene_cache[key]

    def transition_score(mask, bid):
        nonlocal mesh_tests
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
                    volume = _overlap(scene[p], scene[q])
                    if volume <= 0.0:
                        continue
                    score += volume
                    if p not in panel_meshes or q not in panel_meshes:
                        continue
                    fine = mesh_scene(mask, bid, fraction)
                    mesh_tests += 1
                    # The SAT hit is an approximate ranking penalty only.
                    score += 50.0 * approximate_overlap_count(
                        fine[p][0], fine[q][0], limit=20,
                        deadline=deadline, clock=clock,
                        bvh_a=fine[p][1], bvh_b=fine[q][1],
                    )
        transition_cache[key] = score
        return score

    # Bounded A* with admissible h = number of remaining bends.
    # Each edge costs 1 + nonnegative coarse-interference penalty.
    # h is a lower bound; rankings are NOT physical feasibility verdicts.
    heap = []
    serial = 0
    heappush(heap, (len(order), 0.0, serial, 0, (), None))
    max_nodes = 12000
    discovery_target = min(len(order), max(max_candidates * 3, max_candidates))
    # Preserve up to 30s for loading/ranking selected machine tooling.
    discovery_deadline = (deadline - min(30.0, float(budget_s) * 0.35)
                          if tooling is not None else deadline)
    while (heap and clock() < discovery_deadline
           and len(candidates) < discovery_target):
        _, penalty, _, mask, path, root = heappop(heap)
        cache_key = (root, mask)
        count = visit_counts.get(cache_key, 0)
        if count >= 3:
            continue
        visit_counts[cache_key] = count + 1
        if len(path) > len(deepest):
            deepest = path
            if progress is not None:
                progress(len(path), len(order), path[-1] if path else "")
        if mask == all_mask:
            seq = tuple(reversed(path))
            if seq not in candidate_orders and seq[0] not in first_bends_seen:
                candidate_orders.add(seq)
                first_bends_seen.add(seq[0])
                candidates.append(ReverseCandidate(
                    sequence=seq,
                    orientation_by_bend=tuple((bid, False) for bid in seq),
                    bbox_overlap_score=penalty,
                    evidence=("REVERSE_ASTAR_MESH_RANKED_UNVERIFIED"
                              if panel_meshes
                              else "REVERSE_ASTAR_AABB_RANKED_UNVERIFIED"),
                ))
            continue
        if len(visit_counts) >= max_nodes:
            break
        for bid in order:
            if clock() >= discovery_deadline:
                break
            bit = 1 << indexed[bid]
            if mask & bit:
                continue
            try:
                increment = transition_score(mask, bid)
            except (ValueError, ArithmeticError, KeyError):
                continue
            transitions += 1
            new_path = path + (bid,)
            new_mask = mask | bit
            new_root = root if root is not None else bid
            if visit_counts.get((new_root, new_mask), 0) >= 3:
                continue
            new_penalty = penalty + increment
            serial += 1
            g = len(new_path) + new_penalty
            h = len(order) - len(new_path)
            heappush(heap, (g+h, new_penalty, serial, new_mask, new_path, new_root))

    # Rank complete permutations with genuine native tool envelopes at
    # machine-space placements. It remains a heuristic; never a rejection.
    machine_status = "NO_CANDIDATES"
    machine_pairs = 0
    machine_overlaps = 0
    machine_checked = 0
    if candidates and tooling is not None and clock() < deadline:
        from .reverse_machine_screen import (
            load_tool_boxes, score_candidate_machine,
        )
        tools_by_bend, machine_status = load_tool_boxes(
            tooling, source.bends, deadline=deadline, clock=clock,
        )
        if tools_by_bend:
            ranked = []
            for original in candidates:
                if clock() >= deadline:
                    machine_status = "MACHINE_SCREEN_PARTIAL_TIMEOUT"
                    break
                try:
                    risk, pose, pairs, overlaps = score_candidate_machine(
                        original.sequence, source, polygons, thickness,
                        tools_by_bend, deadline=deadline, clock=clock,
                    )
                    if (len(pose) != len(original.sequence)
                            or set(dict(pose)) != set(original.sequence)):
                        machine_status = "MACHINE_SCREEN_PARTIAL_POSES_UNVERIFIED"
                        ranked.append(original)
                        continue
                    machine_pairs += pairs
                    machine_overlaps += overlaps
                    machine_checked += 1
                    ranked.append(ReverseCandidate(
                        original.sequence, pose,
                        original.bbox_overlap_score + risk,
                        "REVERSE_ASTAR_MACHINE_AABB_RANKED_UNVERIFIED",
                    ))
                except Exception as exc:
                    # A failed fast heuristic must not abort exact D6 search.
                    machine_status = "MACHINE_SCREEN_PARTIAL_ERROR:" + type(exc).__name__
                    ranked.append(original)
            known = {c.sequence for c in ranked}
            ranked.extend(c for c in candidates if c.sequence not in known)
            candidates = ranked
    candidates = sorted(candidates, key=lambda c: (
        c.bbox_overlap_score, c.sequence
    ))[:max_candidates]
    return ReverseSearchReport(
        candidates=tuple(candidates),
        deepest_unbent=deepest,
        visited_states=len(visit_counts),
        evaluated_transitions=transitions,
        elapsed_s=max(0., clock()-started),
        timed_out=clock() >= deadline,
        geometry_available=True,
        reason="MESH_AABB_RANKING_ONLY_REQUIRES_FORWARD_NATIVE_D6",
        mesh_triangles=mesh_triangles,
        mesh_panels=len(panel_meshes),
        mesh_overlap_tests=mesh_tests,
        machine_screen_status=machine_status,
        machine_screen_pairs=machine_pairs,
        machine_screen_overlaps=machine_overlaps,
        machine_screen_candidates=machine_checked,
    )
