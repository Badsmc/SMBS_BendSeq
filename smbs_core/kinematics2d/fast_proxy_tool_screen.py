"""Very cheap selected-tool silhouette screen inside FAST Beam Search.

This is *not* Stage B / OCC. Both proxy curves are simplified from the
operator-selected, geometrically verified punch/die cross-section contours,
never fabricated rectangles/triangles. Cross-sections of the already folded
rigid sheet panels at actual machine X=0 are tested against those silhouettes.

Only positive interior overlap receives a SOFT penalty. Mere boundary
contacts and the active forming zone never receive a penalty. All findings
are SAMPLED_PREVIEW_UNVERIFIED_3D; the assumed ram pose is not a verified
continuous press cycle and must never become a safety certificate.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import hypot, isfinite, radians, cos, tan
from time import perf_counter
from types import SimpleNamespace
from collections.abc import Mapping

from .contact_oracle_2d import ContactStatus, classify_polygon_contact
from .machine_section_3d import slice_polygon_3d_at_x
from .workpiece_placement_3d import WorkpiecePlacement3D
from .fast_orientation_25d import four_loading_poses, roll_about_machine_bend_axis, upper_punch_face_score


@dataclass(frozen=True)
class ProxyTool:
    name: str
    tool_id: str
    outer: tuple
    holes: tuple


@dataclass(frozen=True)
class ProxyTools:
    punch: ProxyTool
    die: ProxyTool
    simplification_mm: float
    tolerance_mm: float
    die_opening_mm: float | None = None
    die_shoulder_radius_mm: float | None = None


@dataclass(frozen=True)
class ProxyFinding:
    status: str
    penalty: float = 0.0
    tool: str = ""
    component: str = ""
    phase: str = ""
    reason: str = ""
    samples: int = 0

    def to_dict(self):
        return {
            "status":self.status, "penalty":self.penalty,
            "tool":self.tool, "component":self.component,
            "phase":self.phase, "reason":self.reason,
            "samples":self.samples,
            "evidence":"SAMPLED_SIMPLIFIED_TOOL_SECTION_NOT_3D_VERIFIED",
        }


def _point_segment_distance(p, a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    denom = dx*dx+dy*dy
    if denom <= 1e-18:
        return hypot(p[0]-a[0],p[1]-a[1])
    t = max(0.,min(1.,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/denom))
    return hypot(p[0]-a[0]-t*dx,p[1]-a[1]-t*dy)


def _simplify_path(points, tolerance):
    if len(points)<=2:
        return tuple(points)
    first,last=points[0],points[-1]
    index=max(range(1,len(points)-1),
              key=lambda i:_point_segment_distance(points[i],first,last))
    distance=_point_segment_distance(points[index],first,last)
    if distance<=tolerance:
        return (first,last)
    return (_simplify_path(points[:index+1],tolerance)[:-1] +
            _simplify_path(points[index:],tolerance))


def simplify_tool_ring(points, tolerance_mm=0.35):
    """Approximate a closed contour by curved-line segments, retaining shape.

    Preserve both endpoints of the ring's farthest diameter split; if
    approximation would degenerate or lose too much area use original.
    """
    pts=tuple((float(p[0]),float(p[1])) for p in points)
    if len(pts)>1 and pts[0]==pts[-1]:
        pts=pts[:-1]
    if len(pts)<3 or any(not all(isfinite(v) for v in p) for p in pts):
        raise ValueError("invalid_selected_tool_contour")
    if not isfinite(tolerance_mm) or not 0<=tolerance_mm<=1:
        raise ValueError("invalid_proxy_curve_tolerance")
    farthest=max(range(1,len(pts)),
                 key=lambda i:hypot(pts[i][0]-pts[0][0],
                                    pts[i][1]-pts[0][1]))
    a=_simplify_path(pts[:farthest+1],tolerance_mm)
    b=_simplify_path(pts[farthest:]+pts[:1],tolerance_mm)
    reduced=a[:-1]+b[:-1]
    if len(reduced)<3:
        return pts
    def area(ring):
        return abs(sum(p[0]*q[1]-q[0]*p[1]
                       for p,q in zip(ring,ring[1:]+ring[:1])))*0.5
    before=area(pts)
    # Strongly curved or thin tools can have significant area changes after
    # even small simplification. Fall back rather than inventing clearance.
    if before<=1e-9 or abs(area(reduced)-before)>max(0.1,0.01*before):
        return pts
    return reduced


def prepare_proxy_tools(tooling, *, simplification_mm=0.35,
                        tolerance_mm=0.5):
    """Snapshot the two *selected* tool geometries once per FAST run.

    A missing or unverified datum makes the proxy unavailable. The search
    must then continue without tool-derived penalties, not invent shapes.
    """
    if tooling is None:
        return None
    punch=getattr(tooling,"punch",None)
    die=getattr(tooling,"die",None)
    if punch is None or die is None:
        return None
    if (str(getattr(getattr(punch,"validation_status",None),"value",""))
                != "GEOMETRY_VERIFIED"
            or str(getattr(getattr(die,"validation_status",None),"value",""))
                != "GEOMETRY_VERIFIED"):
        return None
    try:
        def prepare(profile,name):
            outer,holes=profile.local_polygons(arc_segments=12)
            return ProxyTool(
                name, str(profile.tool_id),
                simplify_tool_ring(outer,simplification_mm),
                tuple(simplify_tool_ring(h,simplification_mm)
                      for h in holes),
            )
        # A real mid-stroke coordinate can be derived only from *verified*
        # selected die shoulder geometry, not from a generic V=32 default.
        datums=getattr(die,"die_datums",None)
        v=getattr(datums,"v_opening_mm",None)
        shoulder=getattr(datums,"shoulder_radius_mm",None)
        real_kinematics = (
            type(v) in (int,float) and type(shoulder) in (int,float)
            and isfinite(v) and isfinite(shoulder)
            and v>0 and shoulder>0
        )
        return ProxyTools(
            prepare(punch,"PUNCH"),prepare(die,"DIE"),
            float(simplification_mm),float(tolerance_mm),
            float(v) if real_kinematics else None,
            float(shoulder) if real_kinematics else None,
        )
    except (ValueError,TypeError,IndexError,AttributeError):
        return None


def _sampled_ram_stroke(degrees, outer_radius, tools):
    """Same air-bend stroke relationship as Stage B, with no defaults."""
    if (tools.die_opening_mm is None
            or tools.die_shoulder_radius_mm is None):
        return None
    beta=radians(min(175.0,abs(float(degrees)))*0.5)
    cb=cos(beta)
    if cb<=1e-5:
        return None
    return max(0.0,
        (tools.die_opening_mm*0.5)*tan(beta)
        - (outer_radius+tools.die_shoulder_radius_mm)
          * ((1.0-cb)/cb)
    )


def _translated(points, z):
    return tuple((p[0],p[1]+z) for p in points)


def _section_panel(polygon_xy, transform, placement, thickness):
    # The 2D tools represent the real profile at X_m=0. A panel whose
    # 3D x-span misses that machine station cannot physically intersect
    # this particular section. Do not project all panels into Y/Z.
    top=[placement.apply(transform.point(
        (float(x),float(y),0.0))) for x,y in polygon_xy]
    bot=[placement.apply(transform.point(
        (float(x),float(y),-thickness))) for x,y in polygon_xy]
    if max(p[0] for p in top)<-1e-5 or min(p[0] for p in top)>1e-5:
        return ()
    t=slice_polygon_3d_at_x(top,0.0)
    b=slice_polygon_3d_at_x(bot,0.0)
    if len(t)<2 or len(b)<2:
        return ()
    return ((-t[0][0],t[0][1]),(-t[-1][0],t[-1][1]),
            (-b[-1][0],b[-1][1]),(-b[0][0],b[0][1]))


def screen_proxy_transition(fold_state, panel_vertices_xy, operations,
                            completed_mask, active_index, reverse_axis,
                            tools, *, clearance_mm=5.0,
                            deadline=None,clock=perf_counter,
                            full_angle_probe=False,roll_180=False):
    """Sample selected punch/die curves against rigid sheet-panel sections.

    A fresh read-only FoldState view is built for each sampled angle. Only
    already-existing panels other than the two directly participating in
    this operation are penalized. The parked punch is additionally checked
    against the active fixed/moving flanges, never the actual active zone.
    This is a search RANKING signal, not a hard rejection.
    """
    if tools is None:
        return ProxyFinding("UNKNOWN",reason="no_verified_tool_pair")
    if any(op.kind!="SINGLE" for op in operations):
        return ProxyFinding("UNKNOWN",reason="staged_operation_not_modeled")
    if (not isinstance(panel_vertices_xy,Mapping)
            or any(len(panel_vertices_xy.get(panel,()))<3
                   for panel in fold_state.graph.panels)):
        return ProxyFinding("UNKNOWN",reason="incomplete_panel_outlines")
    if not 0<=active_index<len(operations) or completed_mask<0:
        return ProxyFinding("UNKNOWN",reason="invalid_search_state")
    active=operations[active_index]
    bend=fold_state.bends.get(active.bend_id)
    zone=getattr(bend,"bend_zone",None)
    if zone is None:
        return ProxyFinding("UNKNOWN",reason="bend_zone_unavailable")
    try:
        thickness=float(zone.thickness_mm)
        outer_radius=float(zone.outer_radius_mm)
        if thickness<=0 or outer_radius<=0 or not all(
                isfinite(v) for v in (thickness,outer_radius)):
            return ProxyFinding("UNKNOWN",reason="invalid_sheet_dimensions")
        angles={bid:0.0 for bid in fold_state.bends}
        for i,op in enumerate(operations):
            if completed_mask&(1<<i):
                angles[op.bend_id]=float(op.target_angle_deg)
        start_angle=angles[active.bend_id]
        target=float(active.target_angle_deg)
        if start_angle*target<0 or abs(start_angle)>abs(target)+1e-8:
            return ProxyFinding("UNKNOWN",reason="staged_angle_mismatch")
        samples=0
        invalid_punch_face=False
        # Match Stage B's physical setup: an APPROACH pose is aligned to
        # the flat fixed flange (NOT the changing fold bisector), whereas
        # BENDING follows the bisector with both sheet and punch lowered
        # by the nominal contact stroke from the selected die datums.
        phases=[("APPROACH",0.0)]
        if (tools.die_opening_mm is not None
                and tools.die_shoulder_radius_mm is not None):
            phases.append(("BENDING_MID",0.5))
        if full_angle_probe and len(phases)>1:
            phases.extend((("BENDING_LATE",0.75),("BOTTOM",1.0)))
        for phase,fraction in phases:
            if deadline is not None and clock()>=deadline:
                return ProxyFinding("TIME_BUDGET",samples=samples)
            curr=dict(angles)
            curr[active.bend_id]=start_angle+(target-start_angle)*fraction
            view=SimpleNamespace(
                graph=fold_state.graph, bends=fold_state.bends,
                fixed_panel=fold_state.fixed_panel,angles=curr,
                transforms=fold_state._compute_transforms(curr),
            )
            current_angle=curr[active.bend_id]
            stroke=0.0 if phase=="APPROACH" else _sampled_ram_stroke(
                current_angle,outer_radius,tools,
            )
            if stroke is None or not isfinite(stroke):
                return ProxyFinding(
                    "UNKNOWN",reason="selected_die_stroke_unavailable",
                    samples=samples,
                )
            placement=WorkpiecePlacement3D.compute_placement(
                view,active.bend_id,
                support_plane_zm=outer_radius-stroke,
                align_bisector=(phase!="APPROACH"),
                align_bend_arc_center=True,
                reverse_axis=reverse_axis,
            )
            if roll_180:
                placement=roll_about_machine_bend_axis(
                    placement,axis_z_mm=outer_radius-stroke,
                )
                # A roll which makes the signed bend's inner face point
                # away from the upper punch is useful for diagnostics ONLY.
                # Do not accidentally report it as a feasible loading pose.
                if upper_punch_face_score(view,active.bend_id,placement)<=1e-6:
                    invalid_punch_face=True
            punch_z=thickness-stroke+(
                clearance_mm if phase=="APPROACH" else 0.0
            )
            for panel_id in fold_state.graph.panels:
                if deadline is not None and clock()>=deadline:
                    return ProxyFinding("TIME_BUDGET",samples=samples)
                poly=_section_panel(
                    panel_vertices_xy[panel_id],
                    view.transforms[panel_id],placement,thickness,
                )
                if len(poly)<3:
                    continue
                # No active flange collision penalty *during forming*:
                # real punch/die contact is required in this region.
                is_active=panel_id in (bend.fixed_panel,bend.moving_panel)
                tool_pairs=(("PUNCH",tools.punch,punch_z),
                            ("DIE",tools.die,0.0))
                for tool_name,tool,z in tool_pairs:
                    if is_active and not (phase=="APPROACH" and tool_name=="PUNCH"):
                        continue
                    status,reason=classify_polygon_contact(
                        poly,_translated(tool.outer,z),
                        (_translated(h,z) for h in tool.holes),
                        tolerance_mm=tools.tolerance_mm,
                    )
                    if status is ContactStatus.INTERFERENCE:
                        penalty=(700.0 if is_active else
                                 1900.0 if tool_name=="PUNCH" else 1700.0)
                        if invalid_punch_face:
                            return ProxyFinding(
                                "INVALID_PUNCH_FACE",0.0,
                                tool_name,panel_id,phase,
                                "inverted_signed_punch_face;raw=POTENTIAL_INTERFERENCE;"+reason,
                                samples+1,
                            )
                        return ProxyFinding(
                            "POTENTIAL_INTERFERENCE",penalty,
                            tool_name,panel_id,phase,reason,samples+1,
                        )
            samples+=1
        if invalid_punch_face:
            return ProxyFinding(
                "INVALID_PUNCH_FACE",0.0,
                reason="inverted_signed_punch_face;raw=CLEAR_SAMPLED",
                samples=samples,
            )
        return ProxyFinding(
            "CLEAR_SAMPLED",0.0,samples=samples,
        )
    except (ValueError,TypeError,KeyError,IndexError,AttributeError,
            ZeroDivisionError,OverflowError) as exc:
        return ProxyFinding(
            "UNKNOWN",reason=type(exc).__name__+":"+str(exc),
        )


def diagnose_four_loading_poses(
    fold_state, panel_vertices_xy, operations,
    completed_mask, active_index, tools, *,
    deadline=None, clock=perf_counter,
):
    """Contrast the four old 2.5D presentations for ONE operation.

    The output is diagnostic only.  In particular, a roll that moves a
    pre-bent lip away from the punch but exposes the *wrong active bend face*
    is INVALID_PUNCH_FACE, not CLEAR.  All views are read-only.
    """
    findings=[]
    for case in four_loading_poses():
        finding=screen_proxy_transition(
            fold_state,panel_vertices_xy,operations,
            completed_mask,active_index,case.reverse_axis,tools,
            deadline=deadline,clock=clock,full_angle_probe=True,
            roll_180=case.roll_180,
        )
        findings.append({
            "pose_id":case.pose_id,
            "reverse_axis":case.reverse_axis,
            "roll_180":case.roll_180,
            "eligible_for_fast_ranking":case.eligible_for_fast_ranking,
            "finding":finding.to_dict(),
        })
        if finding.status=="TIME_BUDGET":
            break
    return tuple(findings)
