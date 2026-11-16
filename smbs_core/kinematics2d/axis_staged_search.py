"""Experimental axis-staged, weighted 2D-first press-brake planner.

Unlike a DFS over full permutations, this planner first retains several
sampled section-feasible orders per parallel-axis group, then interleaves
them and RECHECKS every operation against the whole folded workpiece.
A local axis plan alone never establishes cross-axis feasibility.

Final BBox scoring is deliberately reject-NONE: overlap of bounding boxes
is not proof of sheet/tool penetration, and the polygonal bend zones are
not represented by the simple proxy. Only a native OCC verdict can prove a
3D collision. No sparse or adaptive sampled result certifies a machine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from math import dist, isfinite
from time import perf_counter
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Optional, Sequence


@dataclass(frozen=True)
class AxisStageConfig:
    axis_beam: int = 8
    axis_plans_per_group: int = 4
    merge_beam: int = 24
    axis_combinations: int = 16
    full_candidates: int = 24
    finalists: int = 5
    planar_budget_s: float = 180.0
    max_step_evaluations: int = 800
    exploratory_fractions: tuple[float, ...] = (0.0, 0.5, 1.0)
    exploratory_strokes: tuple[float, ...] = (0.0, 0.5, 1.0)
    full_fractions: tuple[float, ...] = tuple(i / 16 for i in range(17))
    full_strokes: tuple[float, ...] = tuple(i / 8 for i in range(9))
    match_active_bend_length: bool = True
    allow_end_zone_collision: bool = False
    end_zone_length_mm: float = 0.0
    allow_retract_warning: bool = False
    native_max_phase_time_s: float = 20.0

    def __post_init__(self):
        for key in ("axis_beam", "axis_plans_per_group", "merge_beam",
                    "axis_combinations", "full_candidates", "finalists",
                    "max_step_evaluations"):
            val = getattr(self, key)
            if type(val) is not int or val < 1:
                raise ValueError(key + " must be a positive integer")
        if (not isfinite(self.planar_budget_s) or self.planar_budget_s <= 0
                or not isfinite(self.native_max_phase_time_s)
                or self.native_max_phase_time_s <= 0
                or not isfinite(self.end_zone_length_mm)
                or self.end_zone_length_mm < 0):
            raise ValueError("invalid search time or end-zone length")
        for values in (self.exploratory_fractions, self.exploratory_strokes,
                       self.full_fractions, self.full_strokes):
            if (not values or values[0] != 0.0 or values[-1] != 1.0
                    or any(not isfinite(v) or not 0 <= v <= 1 for v in values)
                    or list(values) != sorted(set(values))):
                raise ValueError("samples must be sorted, unique, within [0,1]")


@dataclass(frozen=True)
class AxisLocalPlan:
    axis_group: str
    sequence: tuple[str, ...]
    orientations: tuple[bool, ...]
    cost: float
    unverified_steps: int

    def to_dict(self):
        return {"axis_group": self.axis_group, "sequence": list(self.sequence),
                "orientations": list(self.orientations), "cost": self.cost,
                "unverified_steps": self.unverified_steps}


@dataclass(frozen=True)
class AxisStagedReport:
    status: str
    groups: tuple[tuple[str, tuple[str, ...]], ...]
    axis_plans: tuple[tuple[AxisLocalPlan, ...], ...]
    candidates: tuple[Any, ...]
    finalists: tuple[Any, ...]
    bbox_results: tuple[dict, ...]
    occ_results: tuple[dict, ...]
    evaluated_steps: int
    step_cache_hits: int
    blocked_2d: int
    elapsed_s: float
    limit_reason: str = ""
    warnings: tuple[str, ...] = (
        "Section intersections reject only that sampled 2D loading, not the bend in every 3D setup.",
        "The BBox proxy ranks possible interference; overlap alone never proves penetration.",
        "Any native 3D result is sampled, not continuous or machine-certified.",
        "No inter-bend transfer, real backgauge/clamps/frame motion is certified.",
    )

    def to_dict(self) -> dict:
        return {
            "schema_version": "smbs.axis_staged_experiment.v1",
            "status": self.status,
            "axis_groups": [{"group_id": g, "bend_ids": list(b)}
                            for g, b in self.groups],
            "axis_plans": [[p.to_dict() for p in plans]
                           for plans in self.axis_plans],
            "candidates": [c.to_dict() for c in self.candidates],
            "finalists": [c.to_dict() for c in self.finalists],
            "bbox_results": list(self.bbox_results),
            "occ_results": list(self.occ_results),
            "evaluated_steps": self.evaluated_steps,
            "step_cache_hits": self.step_cache_hits,
            "blocked_2d": self.blocked_2d,
            "elapsed_s": round(self.elapsed_s, 4),
            "limit_reason": self.limit_reason,
            "warnings": list(self.warnings),
            "physically_certified": False,
        }

    def as_planar_report(self):
        """Existing sequence-selection UI accepts the standard 2D report."""
        from .sequence_search_2d import PlanarSearchReport, PlanarSearchStatus
        status = (PlanarSearchStatus.SAMPLED_2D_CANDIDATES
                  if any(c.evidence == "SAMPLED_2D_CLEAR_OR_TOUCH"
                         for c in self.finalists)
                  else PlanarSearchStatus.UNVERIFIED_CANDIDATES
                  if self.finalists else PlanarSearchStatus.LIMIT_REACHED
                  if self.limit_reason else PlanarSearchStatus.EXHAUSTED)
        return PlanarSearchReport(
            status=status, candidates=self.finalists,
            evaluated_steps=self.evaluated_steps, visited_nodes=0,
            elapsed_s=self.elapsed_s,
            rejected_2d_interferences=self.blocked_2d,
            unverified_steps=sum(
                c.evidence != "SAMPLED_2D_CLEAR_OR_TOUCH"
                for c in self.finalists
            ), limit_reason=self.limit_reason, warnings=self.warnings,
            axis_groups=self.groups,
            complete_candidates_examined=len(self.candidates),
        )


def _valid_costs(values: Mapping, label: str):
    for key, value in values.items():
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not isfinite(value) or value < 0):
            raise ValueError(label + ": finite nonnegative costs required")
        if label == "pair" and (not isinstance(key, tuple) or len(key) != 2):
            raise ValueError("pair costs require (previous_bend, next_bend) keys")


def _is_2d_blocked(step) -> bool:
    return step.evidence in (
        "2D_INTERFERENCE", "CAD_3D_INTERFERENCE", "INVALID_INPUT",
        "2D_TOOLING_CONSTRAINT", "BACKGAUGE_Z_OUT_OF_RANGE",
        "CAD_CONTACT_UNAVAILABLE",
    )


def _bbox_intersects(a, b, eps=0.001):
    return all(a[i][0] <= b[i][1] + eps
               and b[i][0] <= a[i][1] + eps for i in range(3))


def _bbox_of_points(points):
    return tuple((min(p[i] for p in points),
                  max(p[i] for p in points)) for i in range(3))


def _proxy_bbox_risk(session, tooling, candidate) -> dict:
    """Conservative ranking-only 3D proxy from machine-placed panel vertices.

    Does NOT include curved bend-zone solids, exact ram trajectory, tool
    discontinuities or holes. Zero overlaps can never be a clearance PASS.
    """
    from copy import copy
    from .workpiece_placement_3d import WorkpiecePlacement3D
    state = session.fold_state
    original_angles = dict(state.angles)
    original_transforms = dict(state.transforms)
    order = tuple(candidate.sequence)
    pose = {
        step.bend_id: step.orientation_variant.startswith("REVERSE_MACHINE_Z_180")
        for step in candidate.steps
    }
    checked = overlaps = 0
    try:
        for index, bend_id in enumerate(order):
            state.reset_all()
            for prior in order[:index]:
                state.set_angle(prior, float(state.bends[prior].angle_deg))
            bend = state.bends[bend_id]
            zone = bend.bend_zone
            if zone is None:
                raise ValueError("no bend zone for " + bend_id)
            t = float(zone.thickness_mm)
            section = copy(tooling)
            section.align_to_cross_section(
                bend_id=bend_id, bend_zone=zone, thickness_mm=t,
            )
            length = dist(bend.axis_xyz[0], bend.axis_xyz[1])
            tool_boxes = []
            for kind in ("punch", "die"):
                polys = (section.punch_polygons_machine()
                         if kind == "punch"
                         else section.die_polygons_machine())
                if polys is None or not polys[0]:
                    raise ValueError("unavailable tool polygon")
                # The effective axial length can be virtual; record this
                # as a proxy, NEVER evidence of a real fitted tool station.
                effective = length if getattr(tooling, "match_active_bend_length", False) \
                    else float(getattr(getattr(tooling, kind), "length_mm", length) or length)
                pts = [(-effective / 2, -s, z) for s, z in polys[0]]
                pts += [(effective / 2, -s, z) for s, z in polys[0]]
                tool_boxes.append(_bbox_of_points(pts))
            for fraction in (0.0, 0.5, 1.0):
                state.set_angle(bend_id, float(bend.angle_deg) * fraction)
                placement = WorkpiecePlacement3D.compute_placement(
                    state, bend_id, reverse_axis=pose[bend_id],
                    align_bisector=True,
                    rear_panel_vertices_xy=None,
                )
                for pid, poly in session.panel_vertices_xy.items():
                    tf = state.transforms.get(pid)
                    if tf is None or not poly:
                        continue
                    pts = [
                        placement.apply(tf.point((float(x), float(y), z)))
                        for x, y in poly for z in (0.0, t)
                    ]
                    box = _bbox_of_points(pts)
                    for tool_box in tool_boxes:
                        checked += 1
                        if _bbox_intersects(box, tool_box):
                            overlaps += 1
                state.set_angle(bend_id, 0.0)
        return {
            "sequence": list(order), "status": "BBOX_PROXY_RANKING_ONLY",
            "overlapping_pairs": overlaps, "tested_pairs": checked,
            "proven_collision": False, "clearance_certified": False,
        }
    except Exception as exc:
        return {
            "sequence": list(order), "status": "UNVERIFIED_BBOX_PROXY",
            "reason": type(exc).__name__ + ":" + str(exc),
            "overlapping_pairs": None, "tested_pairs": checked,
            "proven_collision": False, "clearance_certified": False,
        }
    finally:
        state.angles = original_angles
        state.transforms = original_transforms


def search_axis_staged(
    session: Any,
    tooling: Any,
    *,
    config: AxisStageConfig = AxisStageConfig(),
    weights: Any = None,
    per_bend_cost: Optional[Mapping[str, float]] = None,
    pair_cost: Optional[Mapping[tuple[str, str], float]] = None,
    axis_change_cost: float = 0.0,
    step_checker: Optional[Callable[[tuple[str, ...], str, bool, bool], Any]] = None,
    bbox_checker: Optional[Callable[[Any], dict]] = None,
    run_occ: bool = False,
    native_runner: Optional[Callable[..., dict]] = None,
    progress_callback: Optional[Callable[[str, int, int, str], None]] = None,
) -> AxisStagedReport:
    """Beam-search axis orders; interleave and rescreen globally; rank; OCC.

    step_checker is an injectable Stage-A/B adapter for deterministic unit
    tests. Its fourth argument requests full-density section samples.
    Unknown geometry is never relabelled as clear or physically verified.
    """
    from .axis_screening import axes_from_fold_state, group_parallel_bends
    from .operation_cost import (
        OperationWeights, compute_workpiece_machine_setup, transition_penalty,
    )
    from .preliminary_screening import (
        screen_candidate_stage_a, screen_candidate_stage_b, ScreeningDecision,
    )
    from .sequence_search_2d import (
        PlanarStep, PlanarCandidate, _classify_step,
    )
    started = perf_counter()
    deadline = started + config.planar_budget_s
    bend_ids = tuple(getattr(session, "bend_order", ()))
    state = getattr(session, "fold_state", None)
    if (not bend_ids or len(set(bend_ids)) != len(bend_ids)
            or state is None or set(bend_ids) != set(state.bends)
            or tooling is None or not getattr(tooling, "punch", None)
            or not getattr(tooling, "die", None)
            or not getattr(session, "panel_vertices_xy", None)):
        raise ValueError("valid FoldState, panel vertices and real punch/die required")
    groups_obj = group_parallel_bends(axes_from_fold_state(state))
    groups = tuple((g.group_id, g.bend_ids) for g in groups_obj)
    if not groups or set(b for _, ids in groups for b in ids) != set(bend_ids):
        raise ValueError("axis groups do not cover the part")
    bend_group = {bid: gid for gid, bids in groups for bid in bids}
    w = weights if weights is not None else OperationWeights()
    singles, pairs = dict(per_bend_cost or {}), dict(pair_cost or {})
    _valid_costs(singles, "bend")
    _valid_costs(pairs, "pair")
    if not set(singles) <= set(bend_ids):
        raise ValueError("unknown per-bend weight ID")
    if any(a not in bend_ids or b not in bend_ids for a, b in pairs):
        raise ValueError("unknown pair cost ID")
    if not isfinite(axis_change_cost) or axis_change_cost < 0:
        raise ValueError("invalid axis-change cost")

    original_angles = dict(state.angles)
    original_transforms = dict(state.transforms)
    cache = {}
    evals = hits = blocks = 0
    limit = ""
    rank_counter = 0

    def log(stage, done, total, detail=""):
        if progress_callback:
            progress_callback(stage, done, total, detail)

    def checked(prefix, bid, reverse, detailed=False):
        nonlocal evals, hits, blocks, limit
        key = (frozenset(prefix), bid, bool(reverse), bool(detailed))
        if key in cache:
            hits += 1
            return cache[key]
        if perf_counter() >= deadline:
            limit = "PLANAR_TIME_LIMIT"
            return None
        if evals >= config.max_step_evaluations:
            limit = "PLANAR_STEP_LIMIT"
            return None
        evals += 1
        try:
            if step_checker is not None:
                step = step_checker(tuple(prefix), bid, reverse, detailed)
            else:
                state.reset_all()
                for prev in prefix:
                    state.set_angle(prev, float(state.bends[prev].angle_deg))
                setup = compute_workpiece_machine_setup(
                    state, bid, align_bisector=True, reverse_axis=reverse,
                )
                a = screen_candidate_stage_a(
                    state, bid, tooling,
                    match_active_bend_length=config.match_active_bend_length,
                )
                if a.decision is ScreeningDecision.PROVEN_BLOCKING:
                    step = PlanarStep(
                        bid, "2D_TOOLING_CONSTRAINT", a.reason, (), (),
                        setup=setup,
                        orientation_variant=(
                            "REVERSE_MACHINE_Z_180" if reverse else "NORMAL"),
                    )
                else:
                    b = screen_candidate_stage_b(
                        state, bid, tooling, session.panel_vertices_xy,
                        match_active_bend_length=config.match_active_bend_length,
                        allow_end_zone_collision=config.allow_end_zone_collision,
                        end_zone_length_mm=config.end_zone_length_mm,
                        allow_retract_warning=config.allow_retract_warning,
                        reverse_axis=reverse,
                        fractions=(config.full_fractions if detailed
                                   else config.exploratory_fractions),
                        stroke_fractions=(config.full_strokes if detailed
                                          else config.exploratory_strokes),
                        deadline_monotonic=deadline,
                    )
                    step = PlanarStep(
                        bid, _classify_step(b), b.reason,
                        tuple(b.sampled_phases), tuple(b.contact_statuses),
                        setup=setup, backgauge_checked=b.backgauge_checked,
                        diagnostic_findings=b.diagnostic_findings,
                        orientation_variant=(
                            "REVERSE_MACHINE_Z_180" if reverse else "NORMAL"),
                    )
            if step is None or step.bend_id != bid:
                raise ValueError("step checker returned mismatched bend")
            cache[key] = step
            if _is_2d_blocked(step):
                blocks += 1
            return step
        finally:
            # Even an injected checker must not leave an altered FoldState.
            state.angles = dict(original_angles)
            state.transforms = dict(original_transforms)

    def extension_cost(prev_step, step):
        current = step.setup
        prev = prev_step.setup if prev_step is not None else None
        measured = (
            transition_penalty(prev, current, w).total
            if current is not None and current.is_complete
            and (prev is None or prev.is_complete)
            else 0.0
        )
        prior_id = prev_step.bend_id if prev_step is not None else None
        return (measured + float(singles.get(step.bend_id, 0.0))
                + float(pairs.get((prior_id, step.bend_id), 0.0))
                + (axis_change_cost if prior_id is not None
                   and bend_group[prior_id] != bend_group[step.bend_id]
                   else 0.0))

    # Local stage checks the actual sheet with only same-axis predecessors
    # bent. These checks are necessary candidates, NEVER sufficient for
    # cross-axis interleavings.
    axis_plans = []
    all_candidates = []
    try:
        for index, (gid, ids) in enumerate(groups):
            log("AXIS_SEARCH", index + 1, len(groups), gid)
            # (sequence, orientations, steps, total_cost)
            frontier = [((), (), (), 0.0)]
            for depth in range(len(ids)):
                next_frontier = []
                for seq, poses, steps, cost in frontier:
                    for bid in ids:
                        if bid in seq:
                            continue
                        for reverse in (False, True):
                            step = checked(seq, bid, reverse)
                            if step is None:
                                break
                            if _is_2d_blocked(step):
                                continue
                            next_frontier.append((
                                seq + (bid,), poses + (reverse,),
                                steps + (step,), cost +
                                extension_cost(steps[-1] if steps else None, step),
                            ))
                next_frontier.sort(key=lambda e: (
                    sum(s.evidence != "SAMPLED_2D_CLEAR_OR_TOUCH"
                        for s in e[2]), e[3], e[0], e[1],
                ))
                frontier = next_frontier[:config.axis_beam]
                if not frontier or limit:
                    break
            plans = tuple(
                AxisLocalPlan(
                    gid, seq, poses, cost,
                    sum(s.evidence != "SAMPLED_2D_CLEAR_OR_TOUCH"
                        for s in steps),
                )
                for seq, poses, steps, cost in frontier
                if len(seq) == len(ids)
            )[:config.axis_plans_per_group]
            axis_plans.append(plans)
            if limit:
                break
        if len(axis_plans) == len(groups) and all(axis_plans):
            selections = sorted(
                product(*axis_plans),
                key=lambda items: (
                    sum(i.unverified_steps for i in items),
                    sum(i.cost for i in items),
                    tuple(i.sequence for i in items),
                ),
            )[:config.axis_combinations]
            seen_orders = set()
            for selected_index, selected in enumerate(selections):
                if limit or len(all_candidates) >= config.full_candidates:
                    break
                log("AXES_COMBINE", selected_index + 1,
                    len(selections), "interleavings")
                frontier = [(tuple(0 for _ in selected), (), (), 0.0)]
                for depth in range(len(bend_ids)):
                    expanded = []
                    for positions, seq, steps, cost in frontier:
                        for axis, choice in enumerate(selected):
                            pos = positions[axis]
                            if pos >= len(choice.sequence):
                                continue
                            bid = choice.sequence[pos]
                            reverse = choice.orientations[pos]
                            step = checked(seq, bid, reverse)
                            if step is None:
                                break
                            if _is_2d_blocked(step):
                                continue
                            next_positions = list(positions)
                            next_positions[axis] += 1
                            expanded.append((
                                tuple(next_positions), seq + (bid,),
                                steps + (step,),
                                cost + extension_cost(
                                    steps[-1] if steps else None, step
                                ),
                            ))
                    expanded.sort(key=lambda e: (
                        sum(s.evidence != "SAMPLED_2D_CLEAR_OR_TOUCH"
                            for s in e[2]), e[3], e[1],
                    ))
                    # Preserve a few different histories per intermediate
                    # state: last machine setup affects next transition cost.
                    unique = {}
                    for item in expanded:
                        key = (item[0], item[1][-1],
                               item[2][-1].orientation_variant)
                        if key not in unique:
                            unique[key] = item
                    frontier = list(unique.values())[:config.merge_beam]
                    if not frontier or limit:
                        break
                for positions, seq, steps, _cost in frontier:
                    if len(seq) != len(bend_ids) or seq in seen_orders:
                        continue
                    # Full-density replay is required before a candidate
                    # may be offered to the 3D stage.
                    replay = []
                    for pos, bid in enumerate(seq):
                        reverse = steps[pos].orientation_variant.startswith(
                            "REVERSE_MACHINE_Z_180"
                        )
                        check = checked(seq[:pos], bid, reverse, detailed=True)
                        if check is None or _is_2d_blocked(check):
                            break
                        replay.append(check)
                    if len(replay) != len(seq):
                        continue
                    seen_orders.add(seq)
                    unverified = sum(
                        s.evidence != "SAMPLED_2D_CLEAR_OR_TOUCH"
                        for s in replay
                    )
                    score = sum(
                        extension_cost(replay[i - 1] if i else None, step)
                        for i, step in enumerate(replay)
                    )
                    all_candidates.append(PlanarCandidate(
                        seq, tuple(replay),
                        ("SAMPLED_2D_CLEAR_OR_TOUCH" if unverified == 0
                         else "UNVERIFIED_2D"),
                        cost=score,
                        cost_complete=all(
                            s.setup is not None and s.setup.is_complete
                            for s in replay
                        ),
                        group_transitions=sum(
                            bend_group[seq[i - 1]] != bend_group[seq[i]]
                            for i in range(1, len(seq))
                        ),
                        numbered_bends=tuple(
                            (i + 1, bid, bend_group[bid])
                            for i, bid in enumerate(seq)
                        ),
                    ))
                    if len(all_candidates) >= config.full_candidates:
                        break
    finally:
        state.angles = original_angles
        state.transforms = original_transforms

    all_candidates.sort(key=lambda c: (
        c.evidence != "SAMPLED_2D_CLEAR_OR_TOUCH",
        not c.cost_complete, c.cost, c.group_transitions, c.sequence,
    ))
    # BBox is a soft ranking layer, NOT an exclusion test. It uses machine
    # 3D coordinates and selected punch/die section extrusions but ignores
    # curved zone solids and exact press motion.
    bbox = []
    ranked = []
    for candidate in all_candidates:
        detail = (bbox_checker(candidate) if bbox_checker is not None
                  else _proxy_bbox_risk(session, tooling, candidate))
        if not isinstance(detail, dict):
            raise TypeError("bbox_checker must return a dict")
        detail = {**detail, "sequence": list(candidate.sequence)}
        bbox.append(detail)
        overlaps = detail.get("overlapping_pairs")
        risk = float(overlaps) if isinstance(overlaps, int) else float("inf")
        ranked.append((candidate, risk))
    ranked.sort(key=lambda item: (
        item[0].evidence != "SAMPLED_2D_CLEAR_OR_TOUCH",
        not item[0].cost_complete, item[0].cost, item[1],
        item[0].sequence,
    ))
    finalists = tuple(c for c, _risk in ranked[:config.finalists])

    native_results = []
    if run_occ and finalists:
        if native_runner is None:
            from .manual_physical_validation import check_manual_3d_sequence
            native_runner = check_manual_3d_sequence
        from .motion_trajectory_3d import AdaptiveSamplingConfig
        native_config = AdaptiveSamplingConfig(
            max_phase_time_s=config.native_max_phase_time_s,
            coarse_occ_screen=False,
        )
        for i, candidate in enumerate(finalists):
            log("NATIVE_3D", i + 1, len(finalists), " → ".join(candidate.sequence))
            orientation = {
                s.bend_id: s.orientation_variant.startswith(
                    "REVERSE_MACHINE_Z_180"
                ) for s in candidate.steps
            }
            native_session = SimpleNamespace(
                bend_order=tuple(candidate.sequence),
                fold_state=session.fold_state,
                panel_build=session.panel_build,
                part_label=getattr(session, "part_label", ""),
            )
            try:
                result = native_runner(
                    native_session, tooling, candidate.sequence,
                    match_active_bend_length=config.match_active_bend_length,
                    allow_end_zone_collision=config.allow_end_zone_collision,
                    end_zone_length_mm=config.end_zone_length_mm,
                    reverse_axis_by_bend=orientation,
                    config=native_config,
                )
                if not isinstance(result, dict):
                    raise TypeError("native validator must return dict")
                native_results.append({
                    "sequence": list(candidate.sequence),
                    "status": str(result.get("status", "UNVERIFIED")),
                    "checked_bends": int(result.get("checked_bends", 0)),
                    "reason": str(result.get("reason", "")),
                    "continuous_certified": False,
                })
            except Exception as exc:
                native_results.append({
                    "sequence": list(candidate.sequence),
                    "status": "UNVERIFIED",
                    "checked_bends": 0,
                    "reason": type(exc).__name__ + ":" + str(exc),
                    "continuous_certified": False,
                })
    return AxisStagedReport(
        status=("SAMPLED_2D_SHORTLIST_NOT_MACHINE_CERTIFIED"
                if finalists else "NO_FULL_PLANAR_CANDIDATES"),
        groups=groups, axis_plans=tuple(axis_plans),
        candidates=tuple(all_candidates), finalists=finalists,
        bbox_results=tuple(bbox), occ_results=tuple(native_results),
        evaluated_steps=evals, step_cache_hits=hits,
        blocked_2d=blocks, elapsed_s=perf_counter() - started,
        limit_reason=limit,
    )
