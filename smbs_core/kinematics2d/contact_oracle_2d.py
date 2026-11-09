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
    if len(result) < 3 or any(not isfinite(x) or not isfinite(y) for x, y in result):
        raise ContactGeometryError("Polygon requires >=3 finite vertices")
    # BendZone.section_profile(angle=0) intentionally produces repeated
    # points: a zero-angle arc samples the same coordinate many times.
    # Remove only numerically identical consecutive vertices, NOT short
    # finite edges at the user's physical tolerance. A real narrow/short
    # feature must still be classified as UNVERIFIED, not silently erased.
    duplicate_eps_mm = 1e-9
    clean = []
    for point in result:
        if not clean or hypot(point[0] - clean[-1][0],
                              point[1] - clean[-1][1]) > duplicate_eps_mm:
            clean.append(point)
    if len(clean) > 1 and hypot(clean[0][0] - clean[-1][0],
                                clean[0][1] - clean[-1][1]) <= duplicate_eps_mm:
        clean.pop()
    result = tuple(clean)
    if len(result) < 3:
        raise ContactGeometryError("Polygon collapses after duplicate vertex removal")
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
        for pa,pb in _segments(a):
            for qa,qb in _segments(ring):
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
    for p in _interior_edge_samples(b,tol):
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



def sample_bend_stroke_2d(fold_state, panel_vertices_xy, tooling, bend_id,
                          *, samples: int = 12, tolerance_mm: float = 0.02):
    """Stage 2 noncertifying diagnostic of the 2D bend stroke.

    Samples the currently active bend, retaining all previously folded bends.
    Checks approximate ram/die and sheet pose at each intermediate bend angle
    using the SAME contact oracle and coupled kinematics already in the app.

    A contact report here is evidence for an evaluated 2D *frame*, NOT a
    continuous collision proof. Never returns a qualified manufacturing CLEAR.
    Does not alter caller's FoldState, machine tooling, or FreeCAD document.
    """
    from copy import deepcopy
    from .machine_section_3d import compute_machine_cutting_plane_section
    from .workpiece_placement_3d import WorkpiecePlacement3D
    from .coupled_kinematics_2d import CoupledAirBendingKinematics
    from .tooling_25d import ValidationStatus

    if not isinstance(samples, int) or isinstance(samples, bool) or not 2 <= samples <= 180:
        raise ValueError("samples must be integer between 2 and 180")
    if bend_id not in fold_state.bends:
        raise ValueError("Unknown bend ID")
    records = []
    previous = dict(fold_state.angles)
    candidate = deepcopy(tooling)
    try:
        if (candidate.punch is None or candidate.die is None
                or candidate.punch.validation_status != ValidationStatus.GEOMETRY_VERIFIED
                or candidate.die.validation_status != ValidationStatus.GEOMETRY_VERIFIED):
            return {"status": "UNKNOWN", "reason": "missing_verified_tooling", "frames": ()}
        # Sheet is positioned for the selected active bend, with prior folds
        # preserved. Tooling remains on a single fixed machine datum.
        fold_state.set_angle(bend_id, 0.0)
        start_pose = WorkpiecePlacement3D.compute_placement(
            fold_state, bend_id, support_plane_zm=0.0, align_bisector=False
        )
        start_sec = compute_machine_cutting_plane_section(
            fold_state, panel_vertices_xy, start_pose, active_bend_id=bend_id,
            segments=32
        )
        if not (start_sec.get("zone_polygon")
                and start_sec.get("fixed_polygon")
                and start_sec.get("moving_polygon")):
            return {"status": "UNKNOWN", "reason": "incomplete_machine_section", "frames": ()}
        candidate.align_to_cross_section(start_sec, clearance_mm=20.0, bend_id=bend_id)
        kin = CoupledAirBendingKinematics.from_tooling_and_fold_state(
            candidate, fold_state, bend_id
        )
        if kin.tooling_status != "VERIFIED":
            return {"status": "UNKNOWN",
                    "reason": "coupled_tooling_parameters_not_verified",
                    "frames": ()}
        target = float(fold_state.bends[bend_id].angle_deg)
        # Endpoints for APPROACH / CONTACT / RETRACT, plus sampled BENDING
        # and BOTTOM. These are discrete inspection frames, not a full
        # time-continuous ram or transfer trajectory.
        parked_z = kin.contact_z_mm + 20.0
        inspection = [("APPROACH", 0.0, parked_z),
                      ("CONTACT", 0.0, kin.contact_z_mm)]
        inspection += [
            ("BOTTOM" if index == samples else "BENDING",
             target * index / samples,
             kin.ram_z_for_angle(target * index / samples))
            for index in range(1, samples + 1)
        ]
        inspection.append(("RETRACT", target, parked_z))
        for phase, angle, ram_z in inspection:
            fold_state.set_angle(bend_id, angle)
            # Reuse physical stroke reference, do not realign the die per frame.
            candidate.set_punch_ram_z(
                ram_z, stroke_mm=max(0.0, kin.contact_z_mm - ram_z)
            )
            # WorkpiecePlacement3D.support_plane_zm is an ABSOLUTE machine
            # Z for the active axis, whereas kin.support_plane_zm is referenced
            # to die_top_z_mm. The initially aligned sheet is already placed
            # in machine coordinates at support_plane_zm=0. Passing the
            # absolute die top here would translate the sheet by die_top_z_mm
            # a SECOND time and create false die intersections even during
            # APPROACH, before the punch has touched the sheet.
            # Use only the incremental displacement from the flat setup.
            support_delta_z = (
                kin.support_plane_zm(angle) - kin.support_plane_zm(0.0)
            )
            if phase in ("APPROACH", "CONTACT"):
                pose = start_pose  # Exact pose used for initial tool alignment.
            else:
                pose = WorkpiecePlacement3D.compute_placement(
                    fold_state, bend_id,
                    support_plane_zm=support_delta_z,
                    align_bisector=True,
                )
            sec = compute_machine_cutting_plane_section(
                fold_state, panel_vertices_xy, pose,
                active_bend_id=bend_id, segments=32
            )
            if not (sec.get("zone_polygon") and sec.get("fixed_polygon")
                    and sec.get("moving_polygon")):
                records.append({"phase": phase, "angle_deg": angle,
                                "status": "UNVERIFIED",
                                "reason": "incomplete_machine_section"})
                continue
            valid, condition, _ = kin.validate_geometric_constraints(
                angle, ram_z, flange_min_len_mm=0.0
            )
            report = check_sheet_tooling_preview(
                sec, candidate, tolerance_mm=tolerance_mm
            )
            # Kinematic failures identify model constraints; contact predicates
            # identify overlaps *at sampled frames* only.
            frame = {
                "phase": phase,
                "angle_deg": angle,
                "ram_z_mm": ram_z,
                "support_delta_z_mm": support_delta_z,
                "contact": report.status.value,
                "kinematics": condition,
                "status": ("INTERFERENCE" if report.status == ContactStatus.INTERFERENCE
                           else "UNVERIFIED" if report.status == ContactStatus.UNVERIFIED
                           else report.status.value),
                "findings": tuple(
                    (finding.tool, finding.sheet_component, finding.status.value, finding.reason)
                    for finding in report.findings
                    if finding.status != ContactStatus.CLEAR
                ),
            }
            if not valid:
                frame["status"] = "UNVERIFIED"
                frame["reason"] = "coupled_geometric_constraint: " + condition
            records.append(frame)

        return {
            "status": "SAMPLED_2D_NOT_CONTINUOUSLY_VERIFIED",
            "reason": "finite_frames_do_not_prove_full_machine_stroke",
            "frames": tuple(records),
        }
    except (ValueError, ArithmeticError, TypeError, KeyError, AttributeError) as exc:
        return {"status": "UNKNOWN", "reason": "unsupported_stroke: " + str(exc),
                "frames": tuple(records)}
    finally:
        # Preserve the exact caller state even if an intermediate bend raises.
        fold_state.reset_all()
        for bid, angle in previous.items():
            if abs(angle) > 1e-12:
                fold_state.set_angle(bid, angle)


def make_axis_step_oracle_2d(panel_vertices_xy, tooling, *, evidence=None,
                             tolerance_mm: float = 0.02):
    """Bridge the axis-grouped order search to the EXISTING 2D contact oracle.

    A candidate is checked at its pre-bend machine setup with all prior bends
    already folded. Confirmed material interference -> BLOCKED. A sampled
    CLEAR is NOT an entire continuous bend stroke, 3D tool-length check, or
    qualified support / regrip: it remains UNKNOWN.

    This callback never edits the FreeCAD source document, owns no controller,
    and restores the candidate FoldState and tooling across every exception.
    Attach an optional list as evidence to see each candidate's actual reason.
    """
    from copy import deepcopy
    from .tooling_25d import ValidationStatus, ToolingError

    if evidence is None:
        evidence = []

    def evaluate_step(prefix, bend_id, fold_state):
        item = {"prefix": tuple(prefix), "bend_id": bend_id,
                "status": "UNKNOWN", "reason": "unverified_2d_setup"}
        try:
            if bend_id not in fold_state.bends:
                item["reason"] = "unknown_bend_id"
                return "UNKNOWN"
            if not panel_vertices_xy:
                item["reason"] = "missing_panel_geometry"
                return "UNKNOWN"
            if tooling is None or getattr(tooling, "punch", None) is None or getattr(tooling, "die", None) is None:
                item["reason"] = "missing_punch_or_die"
                return "UNKNOWN"
            if (tooling.punch.validation_status != ValidationStatus.GEOMETRY_VERIFIED
                    or tooling.die.validation_status != ValidationStatus.GEOMETRY_VERIFIED):
                item["reason"] = "tool_geometry_not_verified"
                return "UNKNOWN"
            if any(abs(fold_state.angles[b]) > 1e-8 for b in fold_state.bends
                   if b not in prefix):
                item["reason"] = "nonprefix_bend_is_not_flat"
                return "UNKNOWN"

            # Resolve the geometry adapter at evaluation time. This also lets
            # the regression tests replace the adapter without simulating Qt
            # or FreeCAD and avoids retaining stale function references.
            from .machine_section_3d import compute_machine_cutting_plane_section
            from .workpiece_placement_3d import WorkpiecePlacement3D

            # The search loop already reconstructed this prefix from flat.
            # Align a PRIVATE tooling copy against the selected machine pose;
            # never mutate the GUI's selected punch/die, machine pose or ram.
            placement = WorkpiecePlacement3D.compute_placement(
                fold_state, bend_id, support_plane_zm=0.0,
                align_bisector=False,
            )
            section = compute_machine_cutting_plane_section(
                fold_state, panel_vertices_xy, placement,
                active_bend_id=bend_id, segments=32,
            )
            if not (section.get("zone_polygon")
                    and section.get("fixed_polygon")
                    and section.get("moving_polygon")):
                item["reason"] = "incomplete_active_machine_section"
                return "UNKNOWN"
            candidate_tooling = deepcopy(tooling)
            candidate_tooling.align_to_cross_section(
                section, clearance_mm=20.0, bend_id=bend_id
            )
            report = check_sheet_tooling_preview(
                section, candidate_tooling, tolerance_mm=tolerance_mm
            )
            item["contact_status"] = report.status.value
            item["findings"] = tuple(
                (f.tool, f.sheet_component, f.status.value, f.reason)
                for f in report.findings if f.status != ContactStatus.CLEAR
            )
            if report.status == ContactStatus.INTERFERENCE:
                item["status"] = "BLOCKED"
                item["reason"] = "verified_2d_setup_material_interference"
                return "BLOCKED"
            if report.status == ContactStatus.TOUCH:
                item["reason"] = "setup_boundary_contact_unverified"
            elif report.status == ContactStatus.UNVERIFIED:
                item["reason"] = "setup_contact_geometry_unverified"
            else:
                item["reason"] = "setup_clear_but_stroke_not_checked"
            return "UNKNOWN"
        except (ValueError, ArithmeticError, TypeError, KeyError, AttributeError, ToolingError) as exc:
            item["reason"] = "unsupported_setup: " + str(exc)
            return "UNKNOWN"
        finally:
            evidence.append(item)

    return evaluate_step
