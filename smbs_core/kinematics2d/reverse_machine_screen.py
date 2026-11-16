"""Machine-coordinate tool AABB ranking for complete reverse-search orders.

Uses the actual selected FCStd native tool envelope where FreeCAD is
available, loaded once per distinct bend length. Only scores candidate
orders / loading orientations; it CANNOT certify collision-free motion.
The independent forward D6 remains authoritative for sampled collisions.

This is a deliberately inexpensive broadphase, not a mesh-to-mesh
machine clearance proof. It never rejects a candidate.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, log1p
from typing import Any


@dataclass(frozen=True)
class ToolAABB:
    punch: tuple[float, ...]  # xmin,ymin,zmin,xmax,ymax,zmax, tip at z=50
    die: tuple[float, ...]


def _box(shape: Any) -> tuple[float, ...]:
    bb = shape.BoundBox
    values = (
        float(bb.XMin), float(bb.YMin), float(bb.ZMin),
        float(bb.XMax), float(bb.YMax), float(bb.ZMax),
    )
    if not all(isfinite(v) for v in values):
        raise ValueError("nonfinite machine tool bounds")
    if any(values[i] >= values[i + 3] for i in range(3)):
        raise ValueError("degenerate machine tool bounds")
    return values


def load_tool_boxes(tooling: Any, bends: dict, *, deadline=None,
                    clock=None) -> tuple[dict, str]:
    """Load native FCStd envelopes using exactly the same tool loader as D6.

    A failed/missing tool means UNKNOWN screening; never substitute a
    guessed punch or die. Virtual lengths match the active bend length.
    """
    if tooling is None:
        return {}, "TOOLING_NOT_GIVEN"
    try:
        from .physical_validation_25d import _source_path
        from .native_tool_loader import load_native_die, load_native_punch
        from math import dist

        p_path = _source_path(getattr(tooling, "punch", None))
        d_path = _source_path(getattr(tooling, "die", None))
        if p_path is None or d_path is None:
            return {}, "NATIVE_SELECTED_TOOL_PATHS_UNAVAILABLE"
        virtual = bool(getattr(tooling, "match_active_bend_length", False))
        p_sections = tuple(getattr(tooling, "punch_section_offsets_xm", ()) or ())
        d_sections = tuple(getattr(tooling, "die_section_offsets_xm", ()) or ())
        if virtual and (p_sections or d_sections):
            return {}, "VIRTUAL_TOOL_LENGTH_INCOMPATIBLE_WITH_SECTIONS"
        cached = {}
        result = {}
        for bid, bend in bends.items():
            if deadline is not None and clock is not None and clock() >= deadline:
                return result, "TOOL_LOADING_TIME_BUDGET_REACHED"
            length = float(dist(*bend.axis_xyz))
            key = (round(length, 5) if virtual else None)
            if key not in cached:
                effective = length if virtual else None
                punch = load_native_punch(
                    p_path, ram_z_mm=50.0,
                    section_offsets_xm=p_sections or None,
                    virtual_length_mm=effective,
                )
                die = load_native_die(
                    d_path, section_offsets_xm=d_sections or None,
                    virtual_length_mm=effective,
                )
                cached[key] = ToolAABB(
                    punch=_box(punch.machine_shape),
                    die=_box(die.machine_shape),
                )
            result[bid] = cached[key]
        return result, "NATIVE_TOOL_ENVELOPES_LOADED"
    except Exception as exc:
        # Failure is explicit and does not affect candidate generation.
        return {}, "NATIVE_TOOL_SCREEN_UNAVAILABLE:{}:{}".format(
            type(exc).__name__, str(exc)[:160]
        )


def _box_overlap(a, b) -> float:
    lengths = [
        min(a[i+3], b[i+3]) - max(a[i], b[i])
        for i in range(3)
    ]
    return 0.0 if any(n <= 0.0 for n in lengths) else (
        lengths[0] * lengths[1] * lengths[2]
    )


def _shift_z(box, dz):
    return (box[0], box[1], box[2] + dz,
            box[3], box[4], box[5] + dz)


def _panel_machine_box(points, panel_tf, placement, thickness):
    cloud = (
        placement.apply(panel_tf.point((x, y, z)))
        for x, y in points for z in (0.0, -thickness)
    )
    cloud = tuple(cloud)
    return tuple(min(p[i] for p in cloud) for i in range(3)) + tuple(
        max(p[i] for p in cloud) for i in range(3)
    )


def score_candidate_machine(
    sequence: tuple[str, ...], source: Any, polygons: dict,
    thickness: float, tool_boxes: dict,
    *, deadline=None, clock=None,
) -> tuple[float, tuple[tuple[str, bool], ...], int, int]:
    """Rank a full order for each bend's two 180-degree Z orientations.

    Uses actual part coordinate transforms and static CAD tool envelopes
    in machine coordinates. Punch z=50 envelope is shifted to two
    representative tip positions (z=6.5 and z=1.5). These are NOT the
    measured machine ram trajectory. Even zero overlap is UNVERIFIED.
    """
    from .panel_kinematics import FoldState
    from .workpiece_placement_3d import WorkpiecePlacement3D

    state = FoldState(source.graph, source.fixed_panel)
    all_names = tuple(source.bends)
    remaining = set(all_names)
    total_penalty = 0.0
    pairs = 0
    overlaps = 0
    orientation = {}
    for bid in reversed(sequence):
        if deadline is not None and clock is not None and clock() >= deadline:
            return total_penalty, tuple(sorted(orientation.items())), pairs, overlaps
        tool = tool_boxes.get(bid)
        if tool is None:
            continue
        remaining.remove(bid)
        bend = source.bends[bid]
        contact_panels = {bend.fixed_panel, bend.moving_panel}
        scores = []
        for reversed_axis in (False, True):
            score = 0.0
            checks = 0
            hits = 0
            for fraction in (0.0, 0.5, 1.0):
                angles = {
                    name: (0.0 if name not in remaining and name != bid
                           else bend.angle_deg * fraction if name == bid
                           else source.bends[name].angle_deg)
                    for name in all_names
                }
                state.angles = angles
                state.transforms = state._compute_transforms(angles)
                placement = WorkpiecePlacement3D.compute_placement(
                    state, bid, reverse_axis=reversed_axis,
                )
                # Tool envelope follows the fixed punch/die machine axes.
                machine_tools = (
                    _shift_z(tool.punch, -43.5),  # tip at z=6.5
                    _shift_z(tool.punch, -48.5),  # tip at z=1.5
                    tool.die,
                )
                for panel_id, vertices in polygons.items():
                    panel_tf = state.transforms[panel_id]
                    machine_box = _panel_machine_box(
                        vertices, panel_tf, placement, thickness,
                    )
                    weight = (0.08 if panel_id in contact_panels else 1.0)
                    for box in machine_tools:
                        checks += 1
                        vol = _box_overlap(machine_box, box)
                        if vol > 0:
                            hits += 1
                            score += weight * log1p(vol)
            scores.append((score, reversed_axis, checks, hits))
        best = min(scores, key=lambda item: (item[0], item[1]))
        total_penalty += best[0]
        orientation[bid] = best[1]
        pairs += best[2]
        overlaps += best[3]
    # Flat/fully folded initial orientation was not assumed to be feasible.
    return total_penalty, tuple(sorted(orientation.items())), pairs, overlaps
