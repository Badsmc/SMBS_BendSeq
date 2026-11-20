"""Conservative FAST placement sanity screening (not general collision!).

Detect the *gross misplacement* in which the sheet's dominant rigid panel
passes across the punch/die centre axis Y_m=0 at an obviously elevated or
depressed Z over most of the active bend length. Local crossings and
other small panels are NORMAL on many manufacturable parts and MUST NOT
cause pruning. Even a gross crossing is a sampled suspect-POSE heuristic,
not a proven physical tool collision or proof of infeasible bend order.

No FreeCAD, native OCC, tool solids, self-collision or continuous motion.
Missing/ambiguous geometry remains UNKNOWN; never reject on that basis.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from time import perf_counter
from types import SimpleNamespace
from typing import Mapping

from .workpiece_placement_3d import WorkpiecePlacement3D


@dataclass(frozen=True)
class AxisScreenResult:
    decision: str  # BLOCKED, CLEAR_SAMPLED, UNKNOWN, TIME_BUDGET
    reason: str = ""
    panel_id: str = ""
    fraction: float = 0.0
    machine_x_mm: float = 0.0
    machine_z_mm: float = 0.0
    samples: int = 0


def _face_axis_intervals(polygon, xmin, xmax, reference_z,
                         excluded_height, tolerance=1e-5):
    """Material intervals crossing Y_m=0 outside the active bend zone.

    Return None for ambiguous geometry (vertex on axis, odd intersections);
    otherwise return half-open X spans and Z at their midpoints. This is
    an interior 2D-plane test, not 3D solid intersection.
    """
    if len(polygon) < 3 or any(abs(p[1]) <= tolerance for p in polygon):
        return None
    crossings = []
    for p, q in zip(polygon, polygon[1:] + polygon[:1]):
        if ((p[1] < -tolerance and q[1] > tolerance)
                or (p[1] > tolerance and q[1] < -tolerance)):
            t = -p[1] / (q[1] - p[1])
            crossings.append((
                p[0] + t * (q[0] - p[0]),
                p[2] + t * (q[2] - p[2]),
            ))
    crossings.sort()
    if len(crossings) % 2:
        return None
    spans = []
    for (ax, az), (bx, bz) in zip(crossings[::2], crossings[1::2]):
        left, right = max(ax, xmin), min(bx, xmax)
        if right - left <= tolerance or bx - ax <= tolerance:
            continue
        mid_x = (left + right) * 0.5
        mid_z = az + (bz - az) * (mid_x - ax) / (bx - ax)
        if abs(mid_z - reference_z) > excluded_height:
            spans.append((left, right, mid_z))
    return tuple(spans)


def _face_axis_slice(polygon, xmin, xmax, reference_z, excluded_height,
                     tolerance=1e-5):
    """Legacy local-crossing diagnostic; NOT a reason to discard a plan."""
    spans = _face_axis_intervals(
        polygon, xmin, xmax, reference_z, excluded_height, tolerance,
    )
    if spans:
        left, right, z = spans[0]
        return (left + right) * 0.5, z
    return None


def _dominant_panel_axis_intrusion(polygon, xmin, xmax, reference_z,
                                   excluded_height, *, span_fraction=0.65):
    """Find only a broad intrusion, spanning >=65% of active tool length.

    Small flange crossings, a narrow tab and grazing touch are allowed.
    Return (X, Z, coverage) or None; never claim physical collision.
    """
    spans = _face_axis_intervals(
        polygon, xmin, xmax, reference_z, excluded_height,
    )
    if spans is None or xmax <= xmin:
        return None
    required_span = (xmax - xmin) * span_fraction
    for left, right, z in spans:
        if right - left >= required_span:
            return (left + right) * 0.5, z, (right-left)/(xmax-xmin)
    return None


def _dominant_panel(panel_vertices_xy, panel_ids):
    """Only screen a recognisable *main body* occupying >=50% of sheet area.

    If panels are similarly sized, we cannot identify the main sheet
    reliably and do not discard the sequence. Polygon holes are not
    represented here, so this remains a heuristic.
    """
    areas = {}
    for panel_id in panel_ids:
        outline = panel_vertices_xy.get(panel_id, ())
        if len(outline) < 3:
            return None
        try:
            area = abs(sum(
                float(p[0]) * float(q[1]) - float(q[0]) * float(p[1])
                for p, q in zip(outline, outline[1:] + outline[:1])
            )) * 0.5
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
        if not isfinite(area):
            return None
        areas[panel_id] = area
    total = sum(areas.values())
    if total <= 1e-8:
        return None
    best = max(panel_ids, key=lambda pid: (areas[pid], pid))
    if areas[best] < 0.5 * total:
        return None
    return best


def screen_transition_center_axis(
    fold_state,
    panel_vertices_xy: Mapping,
    operations,
    prefix_mask: int,
    active_index: int,
    *,
    clearance_mm: float = 1.0,
    fractions=(0.0, 0.5, 1.0),
    deadline: float | None = None,
    clock=perf_counter,
) -> AxisScreenResult:
    """Look for *persistent gross main-panel misplacement*, not all overlaps.

    The completed mask determines preceding bend angles; screening reflects
    the same Y_m=0 centreline for 0° and 180° rotation around machine Z.
    Such rotation cannot repair an above/below Z misplacement.
    """
    if (not isfinite(clearance_mm) or clearance_mm < 0.0
            or prefix_mask < 0
            or not 0 <= active_index < len(operations)):
        raise ValueError("Invalid centreline screening parameters")
    if not isinstance(panel_vertices_xy, Mapping):
        return AxisScreenResult("UNKNOWN","panel_outline_unavailable")
    graph = fold_state.graph
    for panel in graph.panels:
        poly=panel_vertices_xy.get(panel)
        if not poly or len(poly)<3:
            return AxisScreenResult("UNKNOWN","incomplete_panel_outline")
        try:
            if not all(isfinite(float(value)) for pt in poly for value in pt):
                return AxisScreenResult("UNKNOWN","nonfinite_panel_outline")
        except (TypeError, ValueError):
            return AxisScreenResult("UNKNOWN","invalid_panel_outline")
    main_panel = _dominant_panel(panel_vertices_xy, graph.panels)
    if main_panel is None:
        return AxisScreenResult("UNKNOWN","no_single_dominant_panel")
    active=operations[active_index]
    bend=fold_state.bends[active.bend_id]
    zone=getattr(bend,"bend_zone",None)
    if zone is None:
        return AxisScreenResult("UNKNOWN","authoritative_bend_zone_missing")
    try:
        thickness=float(zone.thickness_mm)
        outer_radius=float(zone.outer_radius_mm)
        if (not all(isfinite(v) and v>0.0 for v in (thickness,outer_radius))
                or len(fractions)<2
                or any(not isfinite(f) or not 0 <= f <= 1 for f in fractions)):
            return AxisScreenResult("UNKNOWN","invalid_zone_or_samples")
    except (AttributeError,ValueError,TypeError):
        return AxisScreenResult("UNKNOWN","invalid_zone_or_samples")
    by_bend={}
    for idx,step in enumerate(operations):
        by_bend.setdefault(step.bend_id,[]).append((idx,step))
    angles={}
    for bid,steps in by_bend.items():
        angle=0.0
        for idx,op in steps:
            if (prefix_mask & (1<<idx)):
                angle=float(op.target_angle_deg)
        angles[bid]=angle
    from_angle=angles[active.bend_id]
    to_angle=float(active.target_angle_deg)
    if (to_angle-from_angle)*float(bend.angle_deg)<-1e-8:
        return AxisScreenResult("UNKNOWN","nonmonotonic_stage_angle")
    checked=0
    # Active bend contact remains allowed. Requiring *substantial* vertical
    # displacement further prevents rejection of legitimate nearby flange
    # crossings. Two distinct sampled phases on the same Z side must show
    # the gross intrusion, not just one transient local intersection.
    exclusion=max(outer_radius+thickness+clearance_mm, 12.0)
    crossing_by_side = {"ABOVE": [], "BELOW": []}
    try:
        for fraction in fractions:
            if deadline is not None and clock() >= deadline:
                return AxisScreenResult("TIME_BUDGET","axis_screen_deadline",
                                        samples=checked)
            sample_angles=dict(angles)
            sample_angles[active.bend_id]=from_angle+(to_angle-from_angle)*float(fraction)
            view=SimpleNamespace(
                graph=graph,
                fixed_panel=fold_state.fixed_panel,
                bends=fold_state.bends,
                angles=sample_angles,
                transforms=fold_state._compute_transforms(sample_angles),
            )
            placement=WorkpiecePlacement3D.compute_placement(
                view,active.bend_id,
                support_plane_zm=outer_radius,
                align_bisector=True,
                align_bend_arc_center=True,
                reverse_axis=False,
            )
            p0,p1,_=WorkpiecePlacement3D.get_world_bend_axis(view,active.bend_id)
            a=placement.apply(p0)
            b=placement.apply(p1)
            # Only the span actually covered by the active bend line is
            # screened; protruding segments of a longer tool need 3D.
            xmin,xmax=sorted((a[0],b[0]))
            xmin+=clearance_mm
            xmax-=clearance_mm
            if xmax-xmin <= 1e-5:
                return AxisScreenResult("UNKNOWN","bend_too_short_for_axis_screen",
                                        samples=checked)
            reference_z=(a[2]+b[2])*0.5
            transform = view.transforms[main_panel]
            poly = panel_vertices_xy[main_panel]
            phase_hit = None
            # Both surfaces of the main panel must be considered, but
            # count each phase only once; one face cannot satisfy the
            # required two independent phase observations by itself.
            for surface_z in (0.0, -thickness):
                machine_poly = [
                    placement.apply(transform.point(
                        (float(x), float(y), surface_z)
                    )) for x, y in poly
                ]
                result = _dominant_panel_axis_intrusion(
                    machine_poly, xmin, xmax, reference_z, exclusion,
                )
                if result is not None:
                    x, z, coverage = result
                    if phase_hit is None or coverage > phase_hit[2]:
                        phase_hit = (x, z, coverage)
            if phase_hit is not None:
                x, z, _coverage = phase_hit
                side = "ABOVE" if z > reference_z else "BELOW"
                crossing_by_side[side].append((float(fraction), x, z))
                if len(crossing_by_side[side]) >= 2:
                    return AxisScreenResult(
                        "BLOCKED",
                        "GROSS_MAIN_BODY_POSE_CROSSES_TOOL_AXIS_" + side,
                        main_panel, float(fraction), x, z, checked + 1,
                    )
            checked += 1
    except (ValueError,TypeError,KeyError,AttributeError,
            ZeroDivisionError,OverflowError) as exc:
        return AxisScreenResult("UNKNOWN",type(exc).__name__+":"+str(exc),
                                samples=checked)
    if any(crossing_by_side.values()):
        return AxisScreenResult(
            "UNKNOWN", "single_sample_gross_crossing_not_conclusive",
            main_panel, samples=checked,
        )
    return AxisScreenResult(
        "CLEAR_SAMPLED", "no_persistent_gross_main_panel_axis_crossing",
        samples=checked,
    )
