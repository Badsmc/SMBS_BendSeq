"""Bounded BFM-lite + bitmask/Beam Search for *candidate generation* only.

No FreeCAD/OCCT/Qt, native collisions, tool motion, or synchronous file I/O.
The geometry matrix encodes axis proximity and dependencies, NOT proven
physical collision feasibility. A candidate is always UNVERIFIED.

The bitmask is the authoritative staged operation state: a completed
PREBEND means its physical fold is at its intermediate angle, FINISH
sets the original final angle; neither state can be conflated with a
different set of completed operation IDs.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees, hypot, isfinite
from time import perf_counter
from typing import Mapping, Sequence

from .staged_bends import BendOperation


class FastSearchError(ValueError):
    pass


@dataclass(frozen=True)
class FastCandidate:
    operation_ids: tuple[str, ...]
    orientation_by_operation: tuple[tuple[str, bool], ...]
    estimated_cost: float
    proximity_risk: float
    evidence: str = "UNVERIFIED_3D_NOT_CHECKED"

    def orientations(self) -> dict[str, bool]:
        return dict(self.orientation_by_operation)

    def to_dict(self) -> dict:
        return {
            "operation_ids": list(self.operation_ids),
            "orientation_by_operation": self.orientations(),
            "estimated_cost": self.estimated_cost,
            "proximity_risk": self.proximity_risk,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class FastSearchReport:
    candidates: tuple[FastCandidate, ...]
    status: str
    elapsed_seconds: float
    explored_states: int
    expanded_transitions: int
    pruned_duplicate_states: int
    geometric_pair_count: int
    dependency_count: int
    limit_seconds: float
    beam_width: int

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "elapsed_seconds": self.elapsed_seconds,
            "explored_states": self.explored_states,
            "expanded_transitions": self.expanded_transitions,
            "pruned_duplicate_states": self.pruned_duplicate_states,
            "geometric_pair_count": self.geometric_pair_count,
            "dependency_count": self.dependency_count,
            "limit_seconds": self.limit_seconds,
            "beam_width": self.beam_width,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "disclaimer": (
                "BFM-lite geometry ranking only: no 2D or 3D continuous "
                "collision certification; candidate orders are UNVERIFIED."
            ),
        }


@dataclass(frozen=True)
class _Node:
    mask: int
    last: int
    reverse: bool
    path: tuple[int, ...]
    reversed_flags: tuple[bool, ...]
    cost: float
    risk: float


def _axis_data(fold_state, operations):
    bends = getattr(fold_state, "bends", None)
    if not isinstance(bends, Mapping):
        raise FastSearchError("FoldState with authoritative bend geometry required")
    geometry = {}
    for step in operations:
        bid = step.bend_id
        if bid in geometry:
            continue
        bend = bends.get(bid)
        if bend is None:
            raise FastSearchError("Unknown bend: " + bid)
        try:
            a, b = bend.axis_xyz
            x1, y1 = float(a[0]), float(a[1])
            x2, y2 = float(b[0]), float(b[1])
            length = hypot(x2-x1, y2-y1)
        except (ValueError, TypeError, IndexError) as exc:
            raise FastSearchError("Invalid bend axis: " + bid) from exc
        if not all(isfinite(v) for v in (x1,y1,x2,y2,length)) or length <= 1e-8:
            raise FastSearchError("Invalid unfolded bend axis: " + bid)
        # Axis direction is bidirectional. Reverse is an explicit 180
        # workpiece-loading choice, not a proven machine-side flip.
        heading = degrees(atan2(y2-y1, x2-x1)) % 180.0
        geometry[bid] = (x1,y1,x2,y2,length,heading)
    return geometry


def _segment_distance(g, h):
    """2D flat-axis segment distance in mm, pure scalar arithmetic."""
    ax,ay,bx,by = g[:4]
    cx,cy,dx,dy = h[:4]

    def orient(x1,y1,x2,y2,x3,y3):
        return (x2-x1)*(y3-y1)-(y2-y1)*(x3-x1)

    ab_c = orient(ax,ay,bx,by,cx,cy)
    ab_d = orient(ax,ay,bx,by,dx,dy)
    cd_a = orient(cx,cy,dx,dy,ax,ay)
    cd_b = orient(cx,cy,dx,dy,bx,by)
    boxes_overlap = (
        max(min(ax,bx),min(cx,dx)) <= min(max(ax,bx),max(cx,dx))
        and max(min(ay,by),min(cy,dy)) <= min(max(ay,by),max(cy,dy))
    )
    if (boxes_overlap
            and (ab_c >= 0 >= ab_d or ab_d >= 0 >= ab_c)
            and (cd_a >= 0 >= cd_b or cd_b >= 0 >= cd_a)):
        return 0.0

    def point_segment(px,py,x1,y1,x2,y2):
        ux,uy = x2-x1,y2-y1
        t = max(0.0,min(1.0,((px-x1)*ux+(py-y1)*uy)/(ux*ux+uy*uy)))
        return hypot(px-x1-t*ux,py-y1-t*uy)

    return min(point_segment(ax,ay,cx,cy,dx,dy),
               point_segment(bx,by,cx,cy,dx,dy),
               point_segment(cx,cy,ax,ay,bx,by),
               point_segment(dx,dy,ax,ay,bx,by))


def _cost(prev, cur, prev_reverse, cur_reverse, *, weights, risk):
    if prev is None:
        return 0.0
    # Pure unfolded-axis heuristic; true physical machine poses are NOT
    # known yet and must not be substituted by these estimated headings.
    heading1 = (prev[5] + (180.0 if prev_reverse else 0.0)) % 360.0
    heading2 = (cur[5] + (180.0 if cur_reverse else 0.0)) % 360.0
    turn = abs((heading2-heading1+180.0)%360.0-180.0)
    angle_weight = float(weights.get("turn_per_90_deg", 1.0))
    turn_cost = (float(weights.get("turn_fixed", 2.0))
                 + angle_weight*turn/90.0) if turn > 1.0 else 0.0
    delta = abs(prev[4]-cur[4])
    length_cost = (float(weights.get("length_change_fixed", 2.0))
                   + float(weights.get("length_delta_per_100_mm", 0.5))
                   *delta/100.0) if delta > 0.5 else 0.0
    # Proximity risk is *only* a low-weight sorting hint, never pruning.
    return turn_cost+length_cost+0.15*risk


def search_fast_candidates(
    fold_state,
    operations: Sequence[BendOperation] | None = None,
    *,
    fixed_orientations: Mapping[str, bool] | None = None,
    weights: Mapping[str, float] | None = None,
    limit_seconds: float = 1.0,
    beam_width: int = 24,
    max_candidates: int = 5,
    clock=perf_counter,
) -> FastSearchReport:
    """Return probable (never physically certified) bend operation orders.

    Any explicit user 0/180 choice is locked. Unspecified orientations are
    considered in both directions. Time budget includes matrix preparation.
    The solver does no native work; rendering and verifying are separate.
    """
    start = clock()
    if not isinstance(limit_seconds, (int,float)) or not isfinite(limit_seconds) or not 0 < limit_seconds <= 30:
        raise FastSearchError("Time limit must be positive and <= 30 seconds")
    if type(beam_width) is not int or not 1 <= beam_width <= 1024:
        raise FastSearchError("Beam width must be in 1..1024")
    if type(max_candidates) is not int or not 1 <= max_candidates <= 32:
        raise FastSearchError("max_candidates must be in 1..32")
    if operations is None:
        operations = tuple(
            BendOperation(b.bend_id, b.bend_id, b.angle_deg)
            for b in getattr(fold_state, "ordered_bends", ())
        )
    else:
        operations = tuple(operations)
    if not operations or len(operations) > 32:
        raise FastSearchError("Supported operation count is 1..32")
    if not all(isinstance(op, BendOperation) for op in operations):
        raise FastSearchError("Expected validated BendOperation entries")
    ids = [op.operation_id for op in operations]
    if len(set(ids)) != len(ids):
        raise FastSearchError("Duplicate operation IDs")
    if any(op.kind not in ("SINGLE","PREBEND","FINISH") for op in operations):
        raise FastSearchError("Invalid staged operation kind")
    geometry = _axis_data(fold_state, operations)
    by_id = {op.operation_id:i for i,op in enumerate(operations)}
    by_bend: dict[str,list] = {}
    for i,op in enumerate(operations):
        if (not isinstance(op.target_angle_deg,(int,float))
                or not isfinite(op.target_angle_deg)):
            raise FastSearchError("Non-finite target angle")
        by_bend.setdefault(op.bend_id,[]).append((i,op))
    if set(by_bend) != set(fold_state.bends):
        raise FastSearchError(
            "All physical bends must have SINGLE or PREBEND/FINISH operations"
        )
    prerequisites = [0]*len(operations)
    for bend_id, steps in by_bend.items():
        final_angle = float(fold_state.bends[bend_id].angle_deg)
        if any(
            op.kind in ("SINGLE","FINISH")
            and abs(float(op.target_angle_deg)-final_angle)>1e-6
            for _,op in steps
        ):
            raise FastSearchError("Operation target differs from SheetMetal for " + bend_id)
        kinds = {op.kind for _,op in steps}
        if kinds == {"SINGLE"} and len(steps) == 1:
            continue
        if kinds != {"PREBEND","FINISH"} or len(steps) != 2:
            raise FastSearchError("Invalid operation stages for " + bend_id)
        pre = next(i for i,op in steps if op.kind == "PREBEND")
        finish = next(i for i,op in steps if op.kind == "FINISH")
        p_angle = operations[pre].target_angle_deg
        f_angle = operations[finish].target_angle_deg
        if not (p_angle*f_angle > 0 and abs(p_angle) < abs(f_angle)-1e-7):
            raise FastSearchError("Invalid pre-bend and finish angles")
        prerequisites[finish] |= 1 << pre

    pins = dict(fixed_orientations or {})
    if not set(pins).issubset(set(ids)) or any(type(v) is not bool for v in pins.values()):
        raise FastSearchError("Orientation pins must be bool and reference existing operation IDs")
    w = dict(weights or {})
    allowed_weights = ("turn_fixed","turn_per_90_deg","length_change_fixed",
                       "length_delta_per_100_mm")
    for name in allowed_weights:
        val = w.get(name, 1.0)
        if isinstance(val,bool) or not isinstance(val,(int,float)) or not isfinite(val) or val < 0:
            raise FastSearchError("Invalid non-negative weight: " + name)

    # BFM-lite: cheap pairwise axis proximity *risk*, NOT collision proofs.
    # No false hard precedence constraints are inferred from mere 2D axes.
    axis = [geometry[op.bend_id] for op in operations]
    risk = [[0.0]*len(operations) for _ in operations]
    matrix_pairs = 0
    for i in range(len(operations)):
        if clock()-start >= limit_seconds:
            return FastSearchReport((),"TIME_BUDGET_UNVERIFIED",clock()-start,
                                    0,0,0,matrix_pairs,sum(bool(p) for p in prerequisites),
                                    limit_seconds,beam_width)
        for j in range(i):
            if operations[i].bend_id == operations[j].bend_id:
                continue
            distance = _segment_distance(axis[i],axis[j])
            threshold = min(15.0,0.15*min(axis[i][4],axis[j][4]))
            r = max(0.0,1.0-distance/max(threshold,0.01))
            risk[i][j] = risk[j][i] = r
            matrix_pairs += 1

    full_mask = (1<<len(operations))-1
    beam = [_Node(0,-1,False,(),(),0.0,0.0)]
    explored = expanded = duplicates = 0
    exhausted = False
    for depth in range(len(operations)):
        if clock()-start >= limit_seconds:
            exhausted = True
            break
        layer = []
        # Key is exact operation-completion mask, last operation and
        # orientation; previous staged angles are implied by the mask.
        seen: dict[tuple[int,int,bool],int] = {}
        for node in beam:
            if clock()-start >= limit_seconds:
                exhausted = True
                break
            explored += 1
            for index,op in enumerate(operations):
                bit = 1<<index
                if node.mask&bit or node.mask&prerequisites[index] != prerequisites[index]:
                    continue
                for reverse in ((pins[op.operation_id],) if op.operation_id in pins
                                else (False,True)):
                    if clock()-start >= limit_seconds:
                        exhausted = True
                        break
                    pair_risk = 0.0 if node.last < 0 else risk[node.last][index]
                    edge_cost = _cost(
                        None if node.last<0 else axis[node.last],axis[index],
                        node.reverse,reverse,weights=w,risk=pair_risk,
                    )
                    candidate = _Node(node.mask|bit,index,reverse,
                                      node.path+(index,),node.reversed_flags+(reverse,),
                                      node.cost+edge_cost,node.risk+pair_risk)
                    layer.append(candidate)
                    expanded += 1
                if exhausted:
                    break
            if exhausted:
                break
        if exhausted:
            break
        layer.sort(key=lambda n:(n.cost,n.risk,n.path,n.reversed_flags))
        next_beam = []
        for n in layer:
            key=(n.mask,n.last,n.reverse)
            if seen.get(key,0)>=2:
                duplicates += 1
                continue
            seen[key]=seen.get(key,0)+1
            next_beam.append(n)
            if len(next_beam)>=beam_width:
                break
        beam=next_beam
        if not beam:
            break

    completed = [n for n in beam if n.mask == full_mask]
    completed.sort(key=lambda n:(n.cost,n.risk,n.path,n.reversed_flags))
    # Use a round-robin across operation orders to retain some diversity:
    # the first candidate is the cheapest, then prefer distinct orders.
    distinct = []
    seen_paths = set()
    for n in completed:
        if n.path not in seen_paths:
            distinct.append(n)
            seen_paths.add(n.path)
    for n in completed:
        if len(distinct)>=max_candidates:
            break
        if n not in distinct:
            distinct.append(n)
    selected = sorted(distinct[:max_candidates],key=lambda n:(n.cost,n.risk,n.path))
    results = tuple(
        FastCandidate(
            tuple(ids[i] for i in n.path),
            tuple((ids[i],reverse) for i,reverse in zip(n.path,n.reversed_flags)),
            round(n.cost,4),
            round(n.risk,4),
        ) for n in selected
    )
    status = ("TIME_BUDGET_UNVERIFIED" if exhausted else
              "CANDIDATES_UNVERIFIED" if results else "NO_CANDIDATES_UNVERIFIED")
    return FastSearchReport(
        results,status,clock()-start,explored,expanded,duplicates,matrix_pairs,
        sum(bool(p) for p in prerequisites),limit_seconds,beam_width,
    )
