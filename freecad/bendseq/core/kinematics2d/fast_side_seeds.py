"""Select independent FAST start seeds from the real unfolded outer perimeter.

These are search *directions*, not certified machine loading poses. For
rectangular/quad outlines expose RIGHT/LEFT/TOP/BOTTOM; for hexagons use
SIDE_1..SIDE_6. Only the original, external outline is accepted: panel
bounding boxes or holes must not be mistaken for outer perimeter sides.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations
from math import hypot, isfinite, cos, radians
from typing import Sequence


@dataclass(frozen=True)
class OuterSideSeed:
    name: str
    first_operation_ids: tuple[str, ...]
    midpoint_xy: tuple[float, float]
    normal_xy: tuple[float, float]
    axis_aligned: bool
    # For geometrically aligned first bends, put the chosen outer side
    # towards the operator (machine -s), not the rear stop (+s).
    first_reverse: tuple[tuple[str, bool], ...] = ()

    def to_dict(self):
        return {
            "side": self.name,
            "first_operation_ids": list(self.first_operation_ids),
            "midpoint_xy": list(self.midpoint_xy),
            "normal_xy": list(self.normal_xy),
            "axis_aligned": self.axis_aligned,
            "first_orientation_by_operation": dict(self.first_reverse),
            "evidence": "FLAT_OUTLINE_START_HEURISTIC_NOT_MACHINE_POSE",
        }


def _distance_to_segment(point, a, b):
    ux, uy = b[0] - a[0], b[1] - a[1]
    v = ux * ux + uy * uy
    if v <= 1e-16:
        return hypot(point[0] - a[0], point[1] - a[1])
    t = max(0.0, min(1.0,
                     ((point[0] - a[0]) * ux + (point[1] - a[1]) * uy) / v))
    return hypot(point[0] - a[0] - t * ux, point[1] - a[1] - t * uy)


def _inside_polygon(point, polygon):
    """Even/odd polygon test for choosing outward CAD-edge normals."""
    x, y = point
    inside = False
    for i, (ax, ay) in enumerate(polygon):
        bx, by = polygon[(i + 1) % len(polygon)]
        if (ay > y) != (by > y):
            cut = ax + (y - ay) * (bx - ax) / (by - ay)
            if x < cut:
                inside = not inside
    return inside


def _cad_edge_sides(outline_xy, edge_polylines, diag):
    """Keep EVERY real outer CAD edge, splitting curved ones into 12° spans.

    A circular edge has infinitely many tangent directions, so it is sampled
    by curvature. A genuine short straight edge is never swallowed by a
    neighboring edge. The output retains source CAD edge order and avoids a
    hard 4/6/24-side assumption.
    """
    tol = max(1e-7, diag * 1e-9)
    test_dist = max(0.005, min(0.2, diag * 0.00005))
    edges = []
    signed_area = sum(
        outline_xy[i][0]*outline_xy[(i+1)%len(outline_xy)][1]
        - outline_xy[(i+1)%len(outline_xy)][0]*outline_xy[i][1]
        for i in range(len(outline_xy))
    )
    if abs(signed_area) < tol:
        return ()
    winding = 1.0 if signed_area > 0.0 else -1.0
    for source in edge_polylines:
        try:
            raw = [(float(p[0]), float(p[1])) for p in source]
        except (ValueError, TypeError, IndexError):
            return ()
        if not all(isfinite(v) for pt in raw for v in pt):
            return ()
        pts = []
        for p in raw:
            if not pts or hypot(p[0]-pts[-1][0], p[1]-pts[-1][1]) > tol:
                pts.append(p)
        if len(pts) < 2:
            continue
        # A true CAD Edge can itself be curved. One section is enough for
        # a straight edge; otherwise use direction changes to sample the
        # entire curved boundary, including its extremities.
        straight = (
            hypot(pts[-1][0]-pts[0][0], pts[-1][1]-pts[0][1]) > tol
            and all(_distance_to_segment(p, pts[0], pts[-1])
                    <= max(tol * 10, diag * 1e-6) for p in pts)
        )
        sections = []
        if straight:
            sections.append(pts)
        else:
            start = 0
            for i in range(2, len(pts)):
                dx0,dy0 = pts[start+1][0]-pts[start][0], pts[start+1][1]-pts[start][1]
                dx1,dy1 = pts[i][0]-pts[i-1][0], pts[i][1]-pts[i-1][1]
                len0,len1 = hypot(dx0,dy0),hypot(dx1,dy1)
                if len0 <= tol or len1 <= tol:
                    continue
                if (dx0*dx1+dy0*dy1)/(len0*len1) < cos(radians(12)):
                    sections.append(pts[start:i])
                    start = i-1
            if start < len(pts)-1:
                sections.append(pts[start:])
        for section in sections:
            a,b = section[0], section[-1]
            dx,dy = b[0]-a[0], b[1]-a[1]
            length = hypot(dx,dy)
            if length <= tol:
                continue
            # Use a real sampled boundary point for the outside test; the
            # chord midpoint of an arc may lie off its physical contour.
            mid_pt = section[len(section)//2]
            tangent = (dx/length,dy/length)
            nx,ny = -tangent[1],tangent[0]
            test_plus = _inside_polygon(
                (mid_pt[0]+test_dist*nx,mid_pt[1]+test_dist*ny),
                outline_xy)
            test_minus = _inside_polygon(
                (mid_pt[0]-test_dist*nx,mid_pt[1]-test_dist*ny),
                outline_xy)
            if test_plus != test_minus:
                if test_plus:
                    nx,ny = -nx,-ny
            elif winding > 0:
                nx,ny = tangent[1],-tangent[0]
            edges.append((a,b,mid_pt,(nx,ny)))
    return tuple(edges)


def simplified_outer_sides(outline_xy: Sequence, *, max_sides: int | None = None,
                           outer_edge_polylines_xy=None):
    """Enumerate the actual unfolded perimeter, never a bounding rectangle.

    Prefer the original exterior CAD edges when supplied; only straight
    collinear sampling points may collapse. Curved CAD edges are sampled
    by local tangent rotation. There is no default numerical side limit.
    max_sides is an optional strict validation threshold, not truncation.
    """
    try:
        raw = [(float(p[0]), float(p[1])) for p in outline_xy]
    except (TypeError, IndexError, ValueError):
        return ()
    if len(raw) < 3 or not all(isfinite(v) for p in raw for v in p):
        return ()
    xspan = max(p[0] for p in raw) - min(p[0] for p in raw)
    yspan = max(p[1] for p in raw) - min(p[1] for p in raw)
    diag = hypot(xspan, yspan)
    if diag < 1e-6:
        return ()
    tiny = max(1e-6, diag * 1e-7)
    pts = []
    for p in raw:
        if not pts or hypot(p[0]-pts[-1][0], p[1]-pts[-1][1]) > tiny:
            pts.append(p)
    if len(pts) > 1 and hypot(
            pts[0][0]-pts[-1][0], pts[0][1]-pts[-1][1]) <= tiny:
        pts.pop()
    if len(pts) < 3:
        return ()
    if outer_edge_polylines_xy:
        real_edges = _cad_edge_sides(pts, outer_edge_polylines_xy, diag)
        if max_sides is not None and len(real_edges) > max_sides:
            raise ValueError("Exterior contour exceeds explicit max_sides")
        return real_edges
    # Fallback for non-CAD callers: remove only numerically collinear
    # tessellation points. Never swallow a real short chamfer or notch.
    tolerance = max(1e-7, diag * 1e-7)
    short_edge = 0.0
    # Iterative vertex removal is deterministic and never reorders vertices.
    for _ in range(len(pts) * 2):
        changed = False
        if len(pts) <= 3:
            break
        for i in range(len(pts)):
            a, p, b = pts[i-1], pts[i], pts[(i+1) % len(pts)]
            ap = hypot(p[0]-a[0], p[1]-a[1])
            pb = hypot(b[0]-p[0], b[1]-p[1])
            ab = hypot(b[0]-a[0], b[1]-a[1])
            if (ab > tiny and (
                (ap + pb - ab <= tolerance
                 and _distance_to_segment(p, a, b) <= tolerance)
                or (min(ap, pb) < short_edge
                    and _distance_to_segment(p, a, b) <= tolerance * 3)
            )):
                del pts[i]
                changed = True
                break
        if not changed:
            break
    if len(pts) < 3:
        return ()
    if max_sides is not None and len(pts) > max_sides:
        raise ValueError("Exterior contour exceeds explicit max_sides")
    signed_area = sum(
        pts[i][0]*pts[(i+1)%len(pts)][1] -
        pts[(i+1)%len(pts)][0]*pts[i][1]
        for i in range(len(pts))
    )
    if abs(signed_area) <= tiny:
        return ()
    ccw = signed_area > 0
    edges = []
    for i, a in enumerate(pts):
        b = pts[(i+1)%len(pts)]
        dx,dy = b[0]-a[0],b[1]-a[1]
        length = hypot(dx,dy)
        if length < tiny:
            return ()
        nx,ny = ((dy/length,-dx/length) if ccw
                 else (-dy/length,dx/length))
        edges.append((a,b,((a[0]+b[0])/2,(a[1]+b[1])/2),(nx,ny)))
    return tuple(edges)


def forced_side_seeds(outline_xy, operations, fold_state, *, per_side_starts=2,
                      outer_edge_polylines_xy=None):
    """Give every outer side its own explicitly forced first-bend search.

    If no bend is approximately parallel to a side, a proximity fallback
    is marked axis_aligned=False. This is *not* an assertion that loading
    from that side is physically possible.
    """
    edges = simplified_outer_sides(
        outline_xy, outer_edge_polylines_xy=outer_edge_polylines_xy,
    )
    if not edges:
        return ()
    entries = []
    for op in operations:
        if getattr(op, "kind", None) == "FINISH":
            continue
        bend = getattr(fold_state, "bends", {}).get(op.bend_id)
        if bend is None:
            continue
        try:
            a,b=bend.axis_xyz
            ux,uy=float(b[0])-float(a[0]),float(b[1])-float(a[1])
            length=hypot(ux,uy)
            if length <= 1e-8:
                continue
            midpoint=((float(a[0])+float(b[0]))/2,
                      (float(a[1])+float(b[1]))/2)
            bend_sign = 1.0 if float(bend.angle_deg) >= 0.0 else -1.0
            entries.append((op.operation_id, midpoint,
                            (ux/length,uy/length), bend_sign))
        except (IndexError, TypeError, ValueError):
            continue
    if not entries:
        return tuple(
            OuterSideSeed("SIDE_"+str(i+1), (), edge[2], edge[3], False)
            for i,edge in enumerate(edges)
        )
    side_names = {}
    if len(edges) == 4:
        compass = (("RIGHT",(1.,0.)),("TOP",(0.,1.)),
                   ("LEFT",(-1.,0.)),("BOTTOM",(0.,-1.)))
        best = max(
            permutations(range(4)),
            key=lambda indices: sum(
                sum(edges[i][3][k] * compass[indices[i]][1][k]
                    for k in range(2)) for i in range(4)
            ),
        )
        side_names = {i:compass[best[i]][0] for i in range(4)}
    else:
        # Preserve the actual source exterior CAD edge order. Unlike
        # sorting solely by normal direction, this distinguishes separate
        # edges of notches and stair-stepped outlines with equal normals.
        side_names = {i:"SIDE_"+str(i+1) for i in range(len(edges))}

    seeds=[]
    for i,(a,b,midpoint,normal) in enumerate(edges):
        edge_dir=(b[0]-a[0],b[1]-a[1])
        norm=hypot(*edge_dir)
        edge_dir=(edge_dir[0]/norm,edge_dir[1]/norm)
        scored=[]
        for op_id, axis_midpoint, axis_dir, bend_sign in entries:
            # A perpendicular bend line cannot be reached as a simple
            # side-parallel first bend. Retain a marked heuristic fallback.
            cross=abs(edge_dir[0]*axis_dir[1]-edge_dir[1]*axis_dir[0])
            distance=_distance_to_segment(axis_midpoint,a,b)
            # Consistent with flat _flat_short_rear_preferences:
            # machine rear +s = bend_sign*(u_y*dx-u_x*dy). The source side
            # must be towards operator (-s) for a side-first setup.
            side_s=bend_sign*(axis_dir[1] *
                             (midpoint[0]-axis_midpoint[0]) -
                             axis_dir[0] *
                             (midpoint[1]-axis_midpoint[1]))
            reverse=(side_s > 0.0) if abs(side_s)>0.5 and cross<=0.259 else None
            scored.append((cross, distance, op_id, reverse))
        aligned=[s for s in scored if s[0] <= 0.259]  # ~15 degrees
        selected=sorted(aligned or scored,
                        key=lambda v:(v[1],v[0],v[2]))
        seeds.append(OuterSideSeed(
            side_names[i],
            tuple(item[2] for item in selected[:max(1,per_side_starts)]),
            midpoint,normal,bool(aligned),
            tuple((item[2],item[3]) for item in selected[:max(1,per_side_starts)]
                  if item[3] is not None),
        ))
    # Always show the four named sides in natural scan order. Generic
    # polygons use numerical side order irrespective of source wire start.
    order={"LEFT":0,"RIGHT":1,"TOP":2,"BOTTOM":3}
    return tuple(sorted(
        seeds,
        key=lambda seed:(order.get(seed.name,4),
                         int(seed.name.split("_")[1])
                         if seed.name.startswith("SIDE_") else 0),
    ))
