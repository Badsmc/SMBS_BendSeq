"""Headless Phase 5C pilot: deterministic 2D section contact oracle.

Checks *cross-section polygon interiors*, not bounding boxes alone. Tool holes
are empty space. Boundary contact is allowed, positive-area overlap is not.

This oracle is a conservative KINEMATIC_PREVIEW classifier, not a verified
press-brake stroke, a complete 3D collision detector, or contact mechanics.
For ambiguous boundary/degenerate cases it returns UNVERIFIED, never CLEAR.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import hypot, isfinite
from typing import Iterable, Sequence

Point = tuple[float, float]
Ring = tuple[Point, ...]


class ContactStatus(str, Enum):
    CLEAR = "CLEAR"
    TOUCH = "TOUCH"
    INTERFERENCE = "INTERFERENCE"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class ContactFinding:
    tool: str
    sheet_component: str
    status: ContactStatus
    reason: str


@dataclass(frozen=True)
class ContactReport:
    status: ContactStatus
    findings: tuple[ContactFinding, ...]
    tolerance_mm: float
    mode: str = "CROSS_SECTION_POLYGON_PREVIEW_NOT_3D_VALIDATED"


class ContactGeometryError(ValueError):
    pass


def _validate_ring(points: Sequence[Point], tol: float) -> Ring:
    try:
        result = tuple((float(p[0]), float(p[1])) for p in points)
    except (IndexError, TypeError, ValueError) as exc:
        raise ContactGeometryError("Invalid polygon coordinates") from exc
    if len(result) > 1 and result[0] == result[-1]:
        result = result[:-1]
    if len(result) < 3 or any(not isfinite(x) or not isfinite(y) for x, y in result):
        raise ContactGeometryError("Polygon requires >=3 finite vertices")
    twice_area = sum(
        result[i][0] * result[(i + 1) % len(result)][1]
        - result[(i + 1) % len(result)][0] * result[i][1]
        for i in range(len(result))
    )
    if abs(twice_area) <= tol * tol:
        raise ContactGeometryError("Degenerate polygon with zero area")
    return result


def _segments(ring: Ring):
    for index, point in enumerate(ring):
        yield point, ring[(index + 1) % len(ring)]


def _cross(a, b, p) -> float:
    return (b[0]-a[0]) * (p[1]-a[1]) - (b[1]-a[1]) * (p[0]-a[0])


def _distance_segment(point, a, b) -> float:
    dx, dy = b[0]-a[0], b[1]-a[1]
    length_sq = dx*dx+dy*dy
    if length_sq <= 1e-18:
        return hypot(point[0]-a[0], point[1]-a[1])
    t = max(0.0, min(1.0,
        ((point[0]-a[0])*dx+(point[1]-a[1])*dy)/length_sq))
    return hypot(point[0]-a[0]-t*dx, point[1]-a[1]-t*dy)


def _point_ring(point, ring, tol):
    """Return inside=1, boundary=0, outside=-1 (winding-independent)."""
    if any(_distance_segment(point, a, b) <= tol for a, b in _segments(ring)):
        return 0
    x, y = point
    inside = False
    for a, b in _segments(ring):
        if (a[1] > y) != (b[1] > y):
            xi = a[0]+(y-a[1])*(b[0]-a[0])/(b[1]-a[1])
            if xi > x:
                inside = not inside
    return 1 if inside else -1


def _in_material(point, outer, holes, tol):
    side = _point_ring(point, outer, tol)
    if side != 1:
        return side
    for hole in holes:
        h = _point_ring(point, hole, tol)
        if h == 0:
            return 0
        if h == 1:
            return -1
    return 1


def _proper_cross(a, b, c, d, tol):
    """Strictly crossing edges, excluding tangents/endpoints/coincidences."""
    len_ab = hypot(b[0]-a[0], b[1]-a[1])
    len_cd = hypot(d[0]-c[0], d[1]-c[1])
    if len_ab <= tol or len_cd <= tol:
        raise ContactGeometryError("Degenerate polygon edge")
    o1, o2 = _cross(a,b,c)/len_ab, _cross(a,b,d)/len_ab
    o3, o4 = _cross(c,d,a)/len_cd, _cross(c,d,b)/len_cd
    return ((o1 > tol and o2 < -tol or o1 < -tol and o2 > tol)
            and (o3 > tol and o4 < -tol or o3 < -tol and o4 > tol))


def _segments_near(a, b, c, d, tol):
    return min(
        _distance_segment(a,c,d), _distance_segment(b,c,d),
        _distance_segment(c,a,b), _distance_segment(d,a,b)
    ) <= tol


def _bbox(ring):
    return (
        min(x for x,y in ring), min(y for x,y in ring),
        max(x for x,y in ring), max(y for x,y in ring)
    )


def _bbox_disjoint(a,b,tol):
    return (a[2] < b[0]-tol or b[2] < a[0]-tol
            or a[3] < b[1]-tol or b[3] < a[1]-tol)


def _edge_samples(ring):
    for a,b in _segments(ring):
        yield a
        yield ((a[0]+b[0])/2.0,(a[1]+b[1])/2.0)


def _interior_edge_samples(ring, tol):
    """Interior witness near each simple-polygon edge (in either winding)."""
    twice_area = sum(
        a[0]*b[1]-b[0]*a[1] for a,b in _segments(ring)
    )
    direction = 1.0 if twice_area > 0 else -1.0
    for a,b in _segments(ring):
        dx,dy=b[0]-a[0],b[1]-a[1]
        length=hypot(dx,dy)
        if length <= tol:
            continue
        midpoint=((a[0]+b[0])/2.0,(a[1]+b[1])/2.0)
        for fraction in (0.1,0.35):
            step=max(4.0*tol,length*fraction)
            candidate=(midpoint[0]-direction*dy/length*step,
                       midpoint[1]+direction*dx/length*step)
            if _point_ring(candidate,ring,tol)==1:
                yield candidate
                break


def classify_polygon_contact(
    sheet: Sequence[Point],
    tool_outer: Sequence[Point],
    tool_holes: Iterable[Sequence[Point]] = (),
    tolerance_mm: float = 0.02,
) -> tuple[ContactStatus, str]:
    """Return a conservative sheet/tool polygon contact classification.

    True penetration is distinguished from boundary-only contact. Near-
    tangencies/near-boundary samples are UNVERIFIED, not a false CLEAR/PASS.
    Complex self-intersections and three-dimensional variations are outside
    this oracle's certified scope.
    """
    tol = float(tolerance_mm)
    if not isfinite(tol) or not 1e-5 <= tol <= 1.0:
        raise ContactGeometryError("tolerance_mm must be in [1e-5, 1] mm")
    a = _validate_ring(sheet,tol)
    b = _validate_ring(tool_outer,tol)
    holes = tuple(_validate_ring(h,tol) for h in tool_holes)
    if _bbox_disjoint(_bbox(a),_bbox(b),tol):
        return ContactStatus.CLEAR,"separated_bounding_boxes"
    touching = False
    near = False
    for ring in (b, *holes):
        if _bbox_disjoint(_bbox(a),_bbox(ring),tol):
            continue
        # Broad-phase per edge. AABB separation is a necessary condition
        # for either a proper crossing or a distance <= tolerance. Skip
        # expensive exact geometry only when these cannot occur.
        tool_edges = tuple(
            (qa, qb, (min(qa[0], qb[0]), min(qa[1], qb[1]),
                      max(qa[0], qb[0]), max(qa[1], qb[1])))
            for qa, qb in _segments(ring)
        )
        for pa, pb in _segments(a):
            pbox = (min(pa[0], pb[0]), min(pa[1], pb[1]),
                    max(pa[0], pb[0]), max(pa[1], pb[1]))
            for qa, qb, qbox in tool_edges:
                if _bbox_disjoint(pbox, qbox, tol):
                    continue
                if _proper_cross(pa,pb,qa,qb,tol):
                    return ContactStatus.INTERFERENCE,"proper_material_boundary_crossing"
                if _segments_near(pa,pb,qa,qb,tol):
                    touching = True

    # Interior witness samples also catch coincident polygons/overlapping
    # collinear boundaries: those have positive overlap even when no edges
    # cross strictly and all boundary vertices lie exactly on each other.
    for p in _interior_edge_samples(a,tol):
        if _in_material(p,b,holes,tol)==1:
            return ContactStatus.INTERFERENCE,"sheet_in_tool_material"
    sheet_bounds = _bbox(a)
    for p in _interior_edge_samples(b,tol):
        # A witness outside the sheet bounding box cannot lie in its
        # interior. Preserve the same exact containment predicates inside.
        if (p[0] < sheet_bounds[0] or p[0] > sheet_bounds[2]
                or p[1] < sheet_bounds[1] or p[1] > sheet_bounds[3]):
            continue
        if _point_ring(p,a,tol)==1 and _in_material(p,b,holes,tol)==1:
            return ContactStatus.INTERFERENCE,"tool_in_sheet_material"

    # A polygon can be completely enclosed without any crossed boundaries.
    # Sample actual outlines and edge interiors; all rings are tested in
    # material space, respecting empty holes.
    for p in _edge_samples(a):
        result = _in_material(p,b,holes,tol)
        if result == 1:
            return ContactStatus.INTERFERENCE,"sheet_in_tool_material"
        if result == 0:
            near = True
    for p in _edge_samples(b):
        result = _point_ring(p,a,tol)
        if result == 1 and _in_material(p,b,holes,tol) >= 0:
            return ContactStatus.INTERFERENCE,"tool_in_sheet_material"
        if result == 0:
            near = True

    # If a nonconvex geometry or a tool hole leaves ambiguous narrow contact,
    # avoid claiming robust clearance: report boundary contact / uncertainty.
    if touching:
        return ContactStatus.TOUCH,"boundaries_within_tolerance"
    if near:
        return ContactStatus.UNVERIFIED,"boundary_tolerance_ambiguity"
    return ContactStatus.CLEAR,"no_polygon_interior_overlap"


def check_machine_section(
    sheet_polygons: Iterable[tuple[str, Sequence[Point]]],
    punch: tuple[Sequence[Point], Iterable[Sequence[Point]]] | None,
    die: tuple[Sequence[Point], Iterable[Sequence[Point]]] | None,
    tolerance_mm: float = 0.02,
) -> ContactReport:
    """Report all sheet components, not just active bend, against both tools."""
    findings = []
    tools = (("PUNCH", punch), ("DIE",die))
    components = tuple(sheet_polygons)
    for tool_name, shape in tools:
        if shape is None:
            findings.append(ContactFinding(tool_name,"*",ContactStatus.UNVERIFIED,
                                           "missing_tool_geometry"))
            continue
        outer, holes = shape
        for identifier, polygon in components:
            try:
                status, reason = classify_polygon_contact(
                    polygon,outer,holes,tolerance_mm
                )
            except ContactGeometryError as exc:
                status, reason = ContactStatus.UNVERIFIED,str(exc)
            findings.append(ContactFinding(tool_name,str(identifier),status,reason))
    if not components:
        findings.append(ContactFinding("SHEET","*",ContactStatus.UNVERIFIED,
                                       "missing_sheet_geometry"))
    priority = (
        ContactStatus.INTERFERENCE,ContactStatus.UNVERIFIED,
        ContactStatus.TOUCH,ContactStatus.CLEAR,
    )
    worst = next(
        (p for p in priority if any(x.status == p for x in findings)),
        ContactStatus.UNVERIFIED,
    )
    return ContactReport(worst,tuple(findings),float(tolerance_mm))


def check_sheet_tooling_preview(section, tooling, tolerance_mm: float = 0.02) -> ContactReport:
    """Read-only adapter of current machine-space FoldState section and tooling.

    The section MUST already have a valid machine placement from Phase 5B.
    Calling this with an unplaced sheet would produce meaningless results.
    The oracle never changes FreeCAD documents, FoldState angles or ToolProfiles.
    """
    if section is None or not getattr(tooling,"aligned",False):
        return ContactReport(
            ContactStatus.UNVERIFIED,
            (ContactFinding("MACHINE","*",ContactStatus.UNVERIFIED,
                            "sheet_machine_pose_or_tool_alignment_missing"),),
            float(tolerance_mm),
        )
    polygons = []
    for key,label in (
        ("fixed_polygon","active_fixed"),
        ("moving_polygon","active_moving"),
        ("zone_polygon","active_bend_zone"),
    ):
        if section.get(key):
            polygons.append((label,section[key]))
    for key in ("other_panel_polygons","other_zone_polygons"):
        for name,poly in section.get(key,()):
            polygons.append((str(name),poly))
    return check_machine_section(
        polygons,
        tooling.punch_polygons_machine(arc_segments=24),
        tooling.die_polygons_machine(arc_segments=24),
        tolerance_mm,
    )
