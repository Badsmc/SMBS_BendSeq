"""FAST (<1s target): conservative dependency matrix + memoized beam search.

This module is deliberately geometry- and FreeCAD-independent. It enumerates
*kinematically admissible candidates*, NOT collision-free manufacturing plans.
No Step A/B, native OCC, machine transfer or tooling contact is invented by
missing geometry. A caller may inject an independently grounded, cheap 2D
screen returning BLOCK / RISK / UNKNOWN / CLEAR; UNKNOWN is never pruned.

The initial feasibility matrix consists of exact staged PREBEND -> FINISH
precedence and optional EXPLICIT validated precedence and no-go transitions.
It does not infer false constraints from drawing-plane axis intersections.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees, hypot, isfinite
from time import perf_counter
from typing import Callable, Mapping, Sequence


_SCREEN = frozenset(("CLEAR", "BLOCK", "RISK", "UNKNOWN"))


@dataclass(frozen=True)
class FastStep:
    operation_id: str
    bend_id: str
    kind: str
    target_angle_deg: float
    axis_group: str
    heading_deg: float
    line_length_mm: float
    allowed_reverse: tuple[bool, ...] = (False, True)

    def __post_init__(self):
        if not self.operation_id or not self.bend_id:
            raise ValueError("operation and bend IDs are required")
        if self.kind not in ("SINGLE", "PREBEND", "FINISH"):
            raise ValueError("unknown operation kind")
        if (not isfinite(self.target_angle_deg)
                or not isfinite(self.heading_deg)
                or not isfinite(self.line_length_mm)
                or self.line_length_mm <= 0):
            raise ValueError("invalid angle or bend line")
        if (not self.allowed_reverse or len(set(self.allowed_reverse)) != len(self.allowed_reverse)
                or any(type(x) is not bool for x in self.allowed_reverse)):
            raise ValueError("invalid reverse-axis choices")


@dataclass(frozen=True)
class FastCandidate:
    operation_ids: tuple[str, ...]
    orientation_by_operation: tuple[tuple[str, bool], ...]
    heuristic_cost: float

    @property
    def orientation_map(self) -> dict[str, bool]:
        return dict(self.orientation_by_operation)

    def to_dict(self) -> dict:
        return {
            "operation_ids": list(self.operation_ids),
            "orientation_by_operation": self.orientation_map,
            "heuristic_cost": round(self.heuristic_cost, 4),
            "evidence": "UNVERIFIED_FAST_TOPOLOGY",
        }


@dataclass(frozen=True)
class FastSearchResult:
    status: str
    candidates: tuple[FastCandidate, ...]
    elapsed_s: float
    expanded: int
    generated: int
    matrix_rejected: int
    screen_rejected: int
    cache_hits: int
    best_partial: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "candidates": [item.to_dict() for item in self.candidates],
            "elapsed_s": round(self.elapsed_s, 5),
            "expanded": self.expanded,
            "generated": self.generated,
            "matrix_rejected": self.matrix_rejected,
            "screen_rejected": self.screen_rejected,
            "cache_hits": self.cache_hits,
            "best_partial": list(self.best_partial),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class FastProblem:
    steps: tuple[FastStep, ...]
    # Constraints here must come from physical proof or the staged controller.
    # A pair (before, after) enforces order, even if the steps are on distinct bends.
    precedences: tuple[tuple[str, str], ...] = ()
    # A pair (previous, next) is prohibited only with prior verified evidence.
    forbidden_transitions: tuple[tuple[str, str], ...] = ()
    # A preferred orientation is ranking-only; NOT a clearance certificate.
    preferred_orientation: tuple[tuple[str, bool], ...] = ()

    def __post_init__(self):
        ids = [s.operation_id for s in self.steps]
        if not ids or len(ids) > 40 or len(ids) != len(set(ids)):
            raise ValueError("1-40 unique operation IDs required")
        bends: dict[str, list[FastStep]] = {}
        for op in self.steps:
            bends.setdefault(op.bend_id, []).append(op)
        for bid, actions in bends.items():
            if len(actions) == 1:
                if actions[0].kind != "SINGLE":
                    raise ValueError("single physical bend must have SINGLE step: " + bid)
            elif (len(actions) != 2
                  or {a.kind for a in actions} != {"PREBEND", "FINISH"}):
                raise ValueError("physical bend must have one PREBEND and FINISH: " + bid)
        allowed = set(ids)
        for p, q in self.precedences + self.forbidden_transitions:
            if p not in allowed or q not in allowed or p == q:
                raise ValueError("invalid feasibility matrix edge")
        pref = dict(self.preferred_orientation)
        if len(pref) != len(self.preferred_orientation):
            raise ValueError("duplicate orientation preferences")
        if any(k not in allowed or type(v) is not bool
               for k, v in self.preferred_orientation):
            raise ValueError("invalid orientation preference")

    @classmethod
    def from_session(
        cls, session, *,
        forced_orientations: Mapping[str, bool] | None = None,
        preferred_orientations: Mapping[str, bool] | None = None,
        verified_precedences: Sequence[tuple[str, str]] = (),
        verified_forbidden_transitions: Sequence[tuple[str, str]] = (),
    ) -> "FastProblem":
        """Read active SheetMetal bend-axis metadata without changing FoldState.

        No synchronous OCC computations; the existing controller already
        supplies all operation IDs and intermediate cumulative angles.
        """
        controller = session.controller
        raw = tuple(controller.operation_steps)
        state = session.fold_state
        forced = dict(forced_orientations or {})
        preferred = dict(preferred_orientations or {})
        ids = {item.operation_id for item in raw}
        physical = {item.bend_id for item in raw}
        if (set(forced) - (ids | physical) or set(preferred) - (ids | physical)
                or any(type(v) is not bool for v in (*forced.values(), *preferred.values()))):
            raise ValueError("unknown operation ID or invalid 180-degree option")

        result = []
        for item in raw:
            bend = state.bends[item.bend_id]
            p1, p2 = bend.axis_xyz
            dx, dy, dz = (float(p2[i] - p1[i]) for i in range(3))
            length = hypot(hypot(dx, dy), dz)
            if length <= 0:
                raise ValueError("invalid SheetMetal bend axis")
            # Axial direction modulo 180 is ONLY an approximate grouping
            # signal, never a calibrated workpiece machine-frame rotation.
            heading = degrees(atan2(dy, dx)) % 180.0
            if heading >= 179.999:
                heading = 0.0
            # 1-degree bins assist deterministic beam ranking; actual tool
            # collision remains unverified until the separate 3D replay.
            group = "axis:{:.0f}".format(round(heading))
            explicit = forced.get(item.operation_id, forced.get(item.bend_id))
            choices = (explicit,) if explicit is not None else (False, True)
            result.append(FastStep(
                item.operation_id, item.bend_id, item.kind,
                float(item.target_angle_deg), group, heading, length, choices,
            ))

        # The operation controller is authoritative for stage dependencies.
        dependencies = list(verified_precedences)
        for item in raw:
            if item.kind == "FINISH":
                earlier = next(
                    (o for o in raw if o.bend_id == item.bend_id
                     and o.kind == "PREBEND"), None
                )
                if earlier is None:
                    raise ValueError("FINISH without PREBEND")
                dependencies.append((earlier.operation_id, item.operation_id))
        orientation_prefs = tuple(
            (item.operation_id, preferred.get(
                item.operation_id, preferred.get(item.bend_id, False)
            )) for item in raw
        )
        return cls(
            tuple(result), tuple(dependencies),
            tuple(verified_forbidden_transitions), orientation_prefs,
        )


@dataclass(frozen=True)
class _Node:
    completed: int
    last_index: int
    last_reverse: bool
    cost: float
    path: tuple[int, ...]
    reverse_path: tuple[bool, ...]


def search_fast_beam(
    problem: FastProblem, *,
    beam_width: int = 32,
    max_candidates: int = 5,
    time_budget_s: float = 1.0,
    quick_screen: Callable[[int, FastStep, bool], str] | None = None,
    clock: Callable[[], float] = perf_counter,
) -> FastSearchResult:
    """Bounded beam search, returning preview-only proposals.

    Feasibility matrix uses stage/verified precedence; bit-mask identifies
    finished operations. Memoization keys contain last operation and reversal
    because costs and orientation-dependent screens may differ.

    The 1-second limit covers the SEARCH itself. Callbacks must be independently
    bounded/nonblocking; this function cannot interrupt native OCC or 2D calls.
    No callback is wired into the GUI while the cheap 2D oracle is still slow.
    """
    if not isinstance(problem, FastProblem):
        raise TypeError("FastProblem required")
    if (type(beam_width) is not int or beam_width < 1
            or type(max_candidates) is not int or max_candidates < 1
            or not isfinite(time_budget_s) or time_budget_s <= 0):
        raise ValueError("positive beam width, count and finite time budget required")

    start = clock()
    deadline = start + time_budget_s
    steps = problem.steps
    n = len(steps)
    pos = {s.operation_id: i for i, s in enumerate(steps)}
    prereq = [0] * n
    for before, after in problem.precedences:
        prereq[pos[after]] |= 1 << pos[before]
    forbidden = {(pos[a], pos[b]) for a, b in problem.forbidden_transitions}
    prefs = dict(problem.preferred_orientation)
    all_done = (1 << n) - 1
    beams = [_Node(0, -1, False, 0.0, (), ())]
    final = []
    best_partial = ()
    generated = expanded = matrix_rejected = screen_rejected = hits = 0
    cache: dict[tuple[int, int, bool], str] = {}
    timed_out = False
    # No geometry claims can be made without a real quick-screen callback.
    warnings = [
        "Topology, stage dependencies, axis headings and cost are only a fast ranking.",
        "No real 2D tool contact, continuous 3D collision, backgauge, clamps or transfer certified.",
        "UNVERIFIED: run independent 3D/OCC verification before manufacturing.",
    ]
    if quick_screen is None:
        warnings.append("No fast 2D collision callback configured; matrix has no geometric blockers.")

    for _depth in range(n):
        if clock() >= deadline:
            timed_out = True
            break
        next_nodes: dict[tuple[int, int, bool], _Node] = {}
        for node in beams:
            if clock() >= deadline:
                timed_out = True
                break
            expanded += 1
            if len(node.path) > len(best_partial):
                best_partial = tuple(steps[i].operation_id for i in node.path)
            for i, step in enumerate(steps):
                if clock() >= deadline:
                    timed_out = True
                    break
                bit = 1 << i
                if node.completed & bit:
                    continue
                if (prereq[i] & node.completed) != prereq[i]:
                    matrix_rejected += 1
                    continue
                if (node.last_index, i) in forbidden:
                    matrix_rejected += 1
                    continue
                prev = steps[node.last_index] if node.last_index >= 0 else None
                # Grouping by sheet-plane axis heading is an ordering heuristic.
                # It is not equivalent to the actual press machine rotation.
                axis_change = 0.0 if prev is None or prev.axis_group == step.axis_group else 2.0
                length_delta = 0.0 if prev is None else abs(step.line_length_mm - prev.line_length_mm)
                for reverse in step.allowed_reverse:
                    if clock() >= deadline:
                        timed_out = True
                        break
                    key = (node.completed, i, reverse)
                    if quick_screen is None:
                        screening = "UNKNOWN"
                    elif key in cache:
                        hits += 1
                        screening = cache[key]
                    else:
                        screening = quick_screen(node.completed, step, reverse)
                        if screening not in _SCREEN:
                            raise ValueError("quick_screen must return CLEAR/BLOCK/RISK/UNKNOWN")
                        cache[key] = screening
                    if screening == "BLOCK":
                        screen_rejected += 1
                        continue
                    # Do not falsely promote UNKNOWN to proven clearance.
                    reverse_change = 0.0 if prev is None or reverse == node.last_reverse else 8.0
                    mismatch = 0.0 if reverse == prefs.get(step.operation_id, False) else 0.8
                    risk = 2.0 if screening == "RISK" else 0.0
                    cost = (node.cost + axis_change + reverse_change
                            + min(length_delta / 100.0, 100.0) + mismatch + risk)
                    nxt = _Node(
                        node.completed | bit, i, reverse, cost,
                        node.path + (i,), node.reverse_path + (reverse,),
                    )
                    state_key = (nxt.completed, nxt.last_index, nxt.last_reverse)
                    old = next_nodes.get(state_key)
                    if old is None or (nxt.cost, nxt.path, nxt.reverse_path) < (
                            old.cost, old.path, old.reverse_path):
                        next_nodes[state_key] = nxt
                    generated += 1
                if timed_out:
                    break
            if timed_out:
                break
        if timed_out:
            break
        if not next_nodes:
            break
        ranked = sorted(next_nodes.values(), key=lambda x: (x.cost, x.path, x.reverse_path))
        beams = ranked[:beam_width]
        for node in beams:
            if len(node.path) > len(best_partial):
                best_partial = tuple(steps[i].operation_id for i in node.path)
        if beams and beams[0].completed == all_done:
            final = beams
            break

    candidates: list[FastCandidate] = []
    for node in final:
        if node.completed != all_done:
            continue
        ids = tuple(steps[i].operation_id for i in node.path)
        orientations = tuple(zip(ids, node.reverse_path))
        candidates.append(FastCandidate(ids, orientations, node.cost))
        if len(candidates) >= max_candidates:
            break
    return FastSearchResult(
        status=(
            "CANDIDATES_UNVERIFIED" if candidates else
            "BUDGET_EXHAUSTED" if timed_out else "NO_CANDIDATES_UNVERIFIED"
        ),
        candidates=tuple(candidates),
        elapsed_s=clock() - start,
        expanded=expanded,
        generated=generated,
        matrix_rejected=matrix_rejected,
        screen_rejected=screen_rejected,
        cache_hits=hits,
        best_partial=best_partial,
        warnings=tuple(warnings),
    )
