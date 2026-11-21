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
    ergonomic_penalty: float = 0.0
    nested_fold_penalty: float = 0.0
    axis_penalty: float = 0.0
    feedback_penalty: float = 0.0
    evidence: str = "UNVERIFIED_3D_NOT_CHECKED"

    def orientations(self) -> dict[str, bool]:
        return dict(self.orientation_by_operation)

    def to_dict(self) -> dict:
        return {
            "operation_ids": list(self.operation_ids),
            "orientation_by_operation": self.orientations(),
            "estimated_cost": self.estimated_cost,
            "proximity_risk": self.proximity_risk,
            "ergonomic_penalty": self.ergonomic_penalty,
            "nested_fold_penalty": self.nested_fold_penalty,
            "axis_penalty": self.axis_penalty,
            "feedback_penalty": self.feedback_penalty,
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
    axis_screen_checks: int = 0
    axis_screen_rejected: int = 0
    axis_screen_unknown: int = 0
    axis_screen_samples: int = 0
    axis_screen_available: bool = False
    axis_screen_penalized: int = 0
    axis_screen_findings: tuple[dict, ...] = ()

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
            "axis_screen_checks": self.axis_screen_checks,
            "axis_screen_rejected": self.axis_screen_rejected,
            "axis_screen_unknown": self.axis_screen_unknown,
            "axis_screen_samples": self.axis_screen_samples,
            "axis_screen_available": self.axis_screen_available,
            "axis_screen_penalized": self.axis_screen_penalized,
            "axis_screen_findings": [
                dict(item) for item in self.axis_screen_findings
            ],
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "disclaimer": (
                "FAST soft-penalizes persistent gross *dominant-panel* "
                "misplacement across machine Y=0; local axis crossings "
                "are allowed. A suspect pose may originate in simulator "
                "placement, not bend order. No continuous 3D or tool-solid "
                "collision check; all candidates remain UNVERIFIED."
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
    ergonomic_penalty: float = 0.0
    nested_fold_penalty: float = 0.0
    axis_penalty: float = 0.0
    feedback_penalty: float = 0.0


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



def _flat_short_rear_preferences(fold_state, geometry, panel_vertices_xy):
    """Flat-space preview of which loading direction keeps short side rear.

    Coordinate contract matches WorkpiecePlacement3D *at flat state*:
    machine s = -Y_m; a positive bend uses n_ref = +Z, negative uses
    -Z, and v_transverse = n_ref cross directed bend axis. No folded
    machine placement is inferred; in an already folded state the actual
    accessible side can differ. Therefore preference is never a hard gate.
    """
    if not isinstance(panel_vertices_xy, Mapping):
        return {}
    if any(len(panel_vertices_xy.get(panel, ())) < 3
           for panel in fold_state.graph.panels):
        # Incomplete/unmapped panel contours must not be used to infer
        # which side of the sheet is short; unmodelled material may
        # reverse the preference.
        return {}
    try:
        vertices = [
            (float(x),float(y))
            for name, polygon in panel_vertices_xy.items()
            if name in fold_state.graph.panels
            for x,y in polygon
        ]
    except (TypeError,ValueError):
        return {}
    if len(vertices)<3 or not all(
        isfinite(v) for point in vertices for v in point
    ):
        return {}
    result={}
    for bid,axis in geometry.items():
        x1,y1,x2,y2,length,_heading=axis
        mx,my=(x1+x2)*0.5,(y1+y2)*0.5
        ux,uy=(x2-x1)/length,(y2-y1)/length
        up=1.0 if fold_state.bends[bid].angle_deg>=0 else -1.0
        s=[up*(uy*(x-mx)-ux*(y-my)) for x,y in vertices]
        rear=max(0.0,max(s))
        operator=max(0.0,-min(s))
        delta=rear-operator
        # Arbitrarily close / ambiguous contours cannot identify a
        # meaningful short rear side. Preserve the default 0° in a tie.
        if min(rear,operator)<0.01 or abs(delta)<=10.0:
            continue
        result[bid]=(delta>0.0,abs(delta))
    return result


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
    panel_vertices_xy: Mapping | None = None,
    weights: Mapping[str, float] | None = None,
    screen_tool_center_axis: bool = True,
    axis_clearance_mm: float = 1.0,
    limit_seconds: float = 1.0,
    beam_width: int = 24,
    max_candidates: int = 5,
    forced_first_operation_ids: Sequence[str] | None = None,
    forced_first_reverse: Mapping[str, bool] | None = None,
    transition_penalties: Mapping[tuple[int, str, bool], float] | None = None,
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
    if (type(screen_tool_center_axis) is not bool
            or isinstance(axis_clearance_mm,bool)
            or not isinstance(axis_clearance_mm,(int,float))
            or not isfinite(axis_clearance_mm)
            or not 0 <= axis_clearance_mm <= 10):
        raise FastSearchError("Axis screen requires bool and 0..10 mm clearance")
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
    allowed_starts = (set(forced_first_operation_ids)
                      if forced_first_operation_ids is not None else None)
    if (allowed_starts is not None and (
            not allowed_starts or
            any(not isinstance(item, str) for item in allowed_starts) or
            not allowed_starts.issubset(by_id))):
        raise FastSearchError("Forced first operations must be known nonempty IDs")
    first_reverse = dict(forced_first_reverse or {})
    if (any(key not in by_id or type(reverse) is not bool
            for key, reverse in first_reverse.items())
            or (allowed_starts is not None
                and not set(first_reverse).issubset(allowed_starts))):
        raise FastSearchError("Forced first orientation requires valid start operations")
    feedback_costs = {}
    for key, value in dict(transition_penalties or {}).items():
        if (not isinstance(key, tuple) or len(key) != 3
                or type(key[0]) is not int or key[0] < 0
                or key[0] >> len(operations)
                or key[1] not in by_id or type(key[2]) is not bool
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value) or value < 0):
            raise FastSearchError("Invalid per-transition 2D feedback penalty")
        feedback_costs[(key[0], by_id[key[1]], key[2])] = float(value)
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
                       "length_delta_per_100_mm","operator_short_side_fixed",
                       "operator_short_side_per_100_mm")
    for name in allowed_weights:
        val = w.get(name, 1.0)
        if isinstance(val,bool) or not isinstance(val,(int,float)) or not isfinite(val) or val < 0:
            raise FastSearchError("Invalid non-negative weight: " + name)

    # BFM-lite: cheap pairwise axis proximity *risk*, NOT collision proofs.
    # No false hard precedence constraints are inferred from mere 2D axes.
    axis = [geometry[op.bend_id] for op in operations]
    preferences = _flat_short_rear_preferences(
        fold_state, geometry, panel_vertices_xy,
    )
    # A folded ancestor may make descendant bends awkward to reach. This is
    # a SOFT ranking term only; some real parts require the opposite order.
    nested_pending_masks = [0] * len(operations)
    for i, op in enumerate(operations):
        moving_subtree = getattr(fold_state.bends[op.bend_id],
                                 "moving_subtree", ())
        for bid, members in by_bend.items():
            if bid == op.bend_id:
                continue
            other = fold_state.bends[bid]
            if other.moving_panel in moving_subtree:
                # The last completed operation of a physical bend is the
                # only indication that the descendant is fully formed.
                last = next(index for index, stage in members
                            if stage.kind in ("FINISH", "SINGLE"))
                nested_pending_masks[i] |= 1 << last
    risk = [[0.0]*len(operations) for _ in operations]
    matrix_pairs = 0
    # The centre-axis filter runs only when every panel has an outline
    # and every physical bend has a real BendZone. Missing data is NOT
    # evidence of clearance and never causes a hard rejection.
    axis_screen_available = (
        screen_tool_center_axis
        and isinstance(panel_vertices_xy, Mapping)
        and all(len(panel_vertices_xy.get(pid, ())) >= 3
                for pid in fold_state.graph.panels)
        and all(getattr(b,"bend_zone",None) is not None
                for b in fold_state.bends.values())
    )
    axis_screen_checks = axis_screen_rejected = axis_screen_unknown = 0
    axis_screen_penalized = 0
    gross_pose_penalty = 1000.0
    axis_screen_samples = 0
    axis_screen_findings = []
    axis_cache = {}
    axis_deadline = start+limit_seconds

    def axis_accept(node):
        nonlocal axis_screen_checks,axis_screen_rejected,axis_screen_unknown
        nonlocal axis_screen_penalized
        nonlocal axis_screen_samples,exhausted
        if not axis_screen_available:
            return 0.0
        # 180° rotates the whole workpiece about Machine Z; Y=0 and
        # the symmetric current bend span are invariant. Screen once
        # per completed-operations mask and prospective operation.
        key=(node.mask & ~(1<<node.last),node.last)
        result=axis_cache.get(key)
        if result is None:
            from .fast_center_axis_screen import screen_transition_center_axis
            result=screen_transition_center_axis(
                fold_state,panel_vertices_xy,operations,
                key[0],key[1],
                clearance_mm=axis_clearance_mm,
                fractions=(0.0,0.5,1.0),
                deadline=axis_deadline,clock=clock,
            )
            axis_cache[key]=result
            axis_screen_checks+=1
            axis_screen_samples+=result.samples
            if result.decision=="UNKNOWN":
                axis_screen_unknown+=1
            if (result.decision in ("BLOCKED", "UNKNOWN")
                    and len(axis_screen_findings) < 16):
                axis_screen_findings.append({
                    "bend_id": operations[key[1]].bend_id,
                    "operation_id": operations[key[1]].operation_id,
                    "preceding_operations": [
                        step.operation_id for j, step in enumerate(operations)
                        if key[0] & (1 << j)
                    ],
                    "decision": result.decision,
                    "soft_penalty": (gross_pose_penalty if result.decision == "BLOCKED" else 0.0),
                    "reason": result.reason,
                    "panel_id": result.panel_id,
                    "fraction": result.fraction,
                    "machine_x_mm": result.machine_x_mm,
                    "machine_z_mm": result.machine_z_mm,
                    "samples": result.samples,
                })
        if result.decision=="TIME_BUDGET":
            exhausted=True
            return None
        if result.decision=="BLOCKED":
            # The dominant-panel screen can mistake a valid machine loading
            # pose for an impossible bend. Keep it with a large ranking cost.
            # Do not claim a verified collision here.
            axis_screen_penalized+=1
            return gross_pose_penalty
        return 0.0
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
                if (node.mask == 0 and allowed_starts is not None
                        and op.operation_id not in allowed_starts):
                    continue
                if node.mask&bit or node.mask&prerequisites[index] != prerequisites[index]:
                    continue
                for reverse in ((pins[op.operation_id],) if op.operation_id in pins
                                else (False,True)):
                    if (node.mask == 0 and op.operation_id in first_reverse
                            and reverse != first_reverse[op.operation_id]):
                        continue
                    if clock()-start >= limit_seconds:
                        exhausted = True
                        break
                    pair_risk = 0.0 if node.last < 0 else risk[node.last][index]
                    edge_cost = _cost(
                        None if node.last<0 else axis[node.last],axis[index],
                        node.reverse,reverse,weights=w,risk=pair_risk,
                    )
                    prefer = preferences.get(op.bend_id)
                    ergonomic = 0.0
                    if prefer is not None and reverse != prefer[0]:
                        # Rear must be the short side. No penalty on unknown
                        # or nearly symmetric outlines; this is not a gauge
                        # contact guarantee.
                        ergonomic = (
                            float(w.get("operator_short_side_fixed", 10.0))
                            + float(w.get("operator_short_side_per_100_mm", 3.0))
                            * prefer[1] / 100.0
                        )
                    feedback = feedback_costs.get(
                        (node.mask,index,reverse), 0.0
                    )
                    pending = (nested_pending_masks[index] & ~node.mask)
                    stage_fraction = min(
                        1.0, abs(float(op.target_angle_deg)
                                 / float(fold_state.bends[op.bend_id].angle_deg)),
                    )
                    nested = 1.5 * pending.bit_count() * stage_fraction
                    candidate = _Node(
                        node.mask|bit,index,reverse,
                        node.path+(index,),node.reversed_flags+(reverse,),
                        node.cost+edge_cost+ergonomic+nested+feedback,
                        node.risk+pair_risk,
                        node.ergonomic_penalty+ergonomic,
                        node.nested_fold_penalty+nested,
                        node.axis_penalty,
                        node.feedback_penalty+feedback,
                    )
                    layer.append(candidate)
                    expanded += 1
                if exhausted:
                    break
            if exhausted:
                break
        if exhausted:
            break
        layer.sort(key=lambda n:(n.cost,n.risk,n.path,n.reversed_flags))
        deduped = []
        for n in layer:
            key=(n.mask,n.last,n.reverse)
            if seen.get(key,0)>=2:
                duplicates += 1
                continue
            seen[key]=seen.get(key,0)+1
            deduped.append(n)
        # Screen a bounded oversample *before* choosing beam winners.
        # Otherwise an early blocked candidate could occupy a beam slot
        # merely because its soft penalty was added after initial sorting.
        scored = []
        for n in deduped[:beam_width * 4]:
            if clock()-start >= limit_seconds:
                exhausted = True
                break
            penalty = axis_accept(n)
            if exhausted or penalty is None:
                break
            scored.append(_Node(
                n.mask, n.last, n.reverse, n.path, n.reversed_flags,
                n.cost + penalty, n.risk, n.ergonomic_penalty,
                n.nested_fold_penalty, n.axis_penalty + penalty,
                n.feedback_penalty,
            ))
        if exhausted:
            break
        scored.sort(key=lambda n:(n.cost,n.risk,n.path,n.reversed_flags))
        next_beam = scored[:max(1, beam_width//2)]
        remaining = scored[max(1, beam_width//2):]
        prefix_counts = {}
        for n in next_beam:
            prefix = n.path[:min(2,len(n.path))]
            prefix_counts[prefix] = prefix_counts.get(prefix,0)+1
        while remaining and len(next_beam)<beam_width:
            chosen = min(
                range(len(remaining)),
                key=lambda k: (
                    remaining[k].cost
                    + 5.0*prefix_counts.get(
                        remaining[k].path[:min(2,len(remaining[k].path))],0
                    ),
                    remaining[k].cost, remaining[k].path,
                ),
            )
            n = remaining.pop(chosen)
            next_beam.append(n)
            prefix=n.path[:min(2,len(n.path))]
            prefix_counts[prefix]=prefix_counts.get(prefix,0)+1
        if not next_beam:
            break
        beam=next_beam
        if not beam:
            break

    completed = [n for n in beam if n.mask == full_mask]
    completed.sort(key=lambda n:(n.cost,n.risk,n.path,n.reversed_flags))
    # Do not consume five slots on the same order with only a different
    # orientation of one bend. Reserve the first for the true cheapest,
    # then select other *orderings* with useful prefix variation.
    distinct = []
    seen_paths = set()
    for n in completed:
        if n.path not in seen_paths:
            distinct.append(n)
            seen_paths.add(n.path)
    selected = []
    pool = distinct
    if pool:
        selected.append(pool.pop(0))
    while pool and len(selected) < max_candidates:
        # Preference for different early operations, without promoting a
        # wildly costly route just for variety. Budget stretch is heuristic.
        best = selected[0].cost
        stretch = max(12.0, 0.7*max(1.0,best))
        reasonable = [
            (i,n) for i,n in enumerate(pool) if n.cost <= best+stretch
        ]
        if not reasonable:
            break
        def candidate_key(entry):
            i,n=entry
            similarity = max(
                sum(a==b for a,b in zip(n.path[:4],s.path[:4]))
                for s in selected
            )
            same_start = sum(n.path[0]==s.path[0] for s in selected)
            return (
                similarity*4.0 + same_start*3.0 + (n.cost-best),
                n.cost,n.risk,n.path,
            )
        chosen = min(reasonable,key=candidate_key)[0]
        selected.append(pool.pop(chosen))
    # If few distinct paths survived, use differing loading orientations
    # only as explicit fallbacks, never as five claimed independent orders.
    for n in completed:
        if len(selected) >= max_candidates:
            break
        if n not in selected and n.path not in {x.path for x in selected}:
            selected.append(n)
    results = tuple(
        FastCandidate(
            tuple(ids[i] for i in n.path),
            tuple((ids[i],reverse) for i,reverse in zip(n.path,n.reversed_flags)),
            round(n.cost,4),
            round(n.risk,4),
            round(n.ergonomic_penalty,4),
            round(n.nested_fold_penalty,4),
            round(n.axis_penalty,4),
            round(n.feedback_penalty,4),
        ) for n in selected
    )
    status = ("TIME_BUDGET_UNVERIFIED" if exhausted else
              "CANDIDATES_UNVERIFIED" if results else "NO_CANDIDATES_UNVERIFIED")
    return FastSearchReport(
        results,status,clock()-start,explored,expanded,duplicates,matrix_pairs,
        sum(bool(p) for p in prerequisites),limit_seconds,beam_width,
        axis_screen_checks,axis_screen_rejected,axis_screen_unknown,
        axis_screen_samples,axis_screen_available,
        axis_screen_penalized,tuple(axis_screen_findings),
    )
