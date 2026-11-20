"""FAST-only sampled machine-centre axis rejection for the current sheet pose.

The punch and die centreline is the *same* vertical machine axis Y_m=0
over the current bend's X_m span. We exclude intentional contact around
the active bend zone; other rigid panels cutting this line far from that
zone are rejected from the FAST shortlist.

This is a sampled *axis* filter, NOT a tool-solid collision check:
it cannot certify approach/retract, tool-body clearance, end caps, holes,
backgauge, or continuous motion. Missing/ambiguous geometry never prunes.
Only pure Python FoldState kinematics and machine placement are used.
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


def _face_axis_slice(polygon, xmin, xmax, reference_z, excluded_height,
                     tolerance=1e-5):
    """Intersection of rigid planar polygon interior with machine Y=0.

    For edges straddling Y=0, consecutive X intersections form intervals
    of material (even-odd rule). A single point/tangency is NOT blocking.
    Vertices on the plane and degenerate/concave ambiguous crossings are
    UNKNOWN rather than inventing a collision. Returns (X,Z) or None.
    """
    if len(polygon) < 3:
        return None
    if any(abs(p[1]) <= tolerance for p in polygon):
        return None
    crossings = []
    for p,q in zip(polygon, polygon[1:]+polygon[:1]):
        if (p[1] < -tolerance and q[1] > tolerance
                or p[1] > tolerance and q[1] < -tolerance):
            fraction = -p[1]/(q[1]-p[1])
            crossings.append((
                p[0] + fraction*(q[0]-p[0]),
                p[2] + fraction*(q[2]-p[2]),
            ))
    crossings.sort()
    if len(crossings) % 2:
        return None
    for (ax,az),(bx,bz) in zip(crossings[::2],crossings[1::2]):
        left=max(ax,xmin)
        right=min(bx,xmax)
        if right-left <= tolerance or bx-ax <= tolerance:
            continue
        x=(left+right)*0.5
        z=az+(bz-az)*(x-ax)/(bx-ax)
        if abs(z-reference_z) > excluded_height:
            return x,z
    return None


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
    """Sample the loaded workpiece before/during/after one bend operation.

    The completed bitmask uniquely determines absolute preceding bend
    angles. The check is orientation-independent: 180° about machine Z
    reflects X and Y but preserves the symmetric centreline Y=0.
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
    # Active fold's intentional sheet-contact envelope, conservatively
    # exempted by radius, thickness and a selectable modelling tolerance.
    exclusion=outer_radius+thickness+clearance_mm
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
            for panel_id in graph.panels:
                transform=view.transforms[panel_id]
                poly=panel_vertices_xy[panel_id]
                # Both sheet faces are checked; sidewall/edge contacts
                # and true tool-body overlaps remain outside this screen.
                for surface_z in (0.0,-thickness):
                    machine_poly=[
                        placement.apply(transform.point(
                            (float(x),float(y),surface_z)
                        )) for x,y in poly
                    ]
                    # All tuples returned by placement are deterministic XYZ.
                    result=_face_axis_slice(machine_poly,xmin,xmax,
                                            reference_z,exclusion)
                    if result is not None:
                        return AxisScreenResult(
                            "BLOCKED","NON_ACTIVE_SHEET_CROSSES_TOOL_CENTER_AXIS",
                            panel_id,float(fraction),result[0],result[1],checked+1,
                        )
            checked+=1
    except (ValueError,TypeError,KeyError,AttributeError,
            ZeroDivisionError,OverflowError) as exc:
        return AxisScreenResult("UNKNOWN",type(exc).__name__+":"+str(exc),
                                samples=checked)
    return AxisScreenResult("CLEAR_SAMPLED","no_off_zone_axis_intersection_in_samples",
                            samples=checked)
