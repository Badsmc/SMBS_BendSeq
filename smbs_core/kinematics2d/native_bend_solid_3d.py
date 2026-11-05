"""D6.3: real FreeCAD Part solid for ONE SheetMetal bend zone.

Consumes authoritative FoldState + BendZone; does not introduce new bend
angles, K-factors, or transformations. The solid is a polygonal approximation
of the existing finite-radius section, extruded along the actual bend line.
Only the active bend zone is modeled: neighboring panels, ends/corners,
station contact and machine clearances are NOT certified.

No FreeCAD import until build is invoked. No document objects are created.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, hypot

from .panel_kinematics import FoldState


class NativeBendSolidError(ValueError):
    pass


def zone_outline(zone, angle_deg, segments=64):
    """Ordered (s,z) perimeter with adjacent duplicate points removed.

    Retains the exact existing BendZone section contract. This step is pure
    Python; Part.Face/solid extrusion happens only in FreeCAD.
    """
    if not isinstance(segments, int) or segments < 8:
        raise NativeBendSolidError("segments must be integer >= 8")
    profile = zone.section_profile(angle_deg, segments=segments)
    # Inner and outer are equally sampled along the same source bend, and
    # their endpoints may coincide along the arc when angle=0 or fully bent.
    source = tuple(profile["inner"]) + tuple(reversed(profile["outer"]))
    clean = []
    for raw in source:
        s, z = float(raw[0]), float(raw[1])
        if not isfinite(s) or not isfinite(z):
            raise NativeBendSolidError("nonfinite_section")
        if not clean or hypot(s-clean[-1][0], z-clean[-1][1]) > 1e-8:
            clean.append((s,z))
    if len(clean) > 1 and hypot(clean[0][0]-clean[-1][0],clean[0][1]-clean[-1][1]) <= 1e-8:
        clean.pop()
    if len(clean) < 4:
        raise NativeBendSolidError("degenerate_section_outline")
    signed_area = 0.5 * sum(
        clean[i][0]*clean[(i+1)%len(clean)][1] -
        clean[(i+1)%len(clean)][0]*clean[i][1]
        for i in range(len(clean))
    )
    if not isfinite(signed_area) or abs(signed_area) < 1e-7:
        raise NativeBendSolidError("invalid_section_area")
    return tuple(clean)


@dataclass(frozen=True)
class NativeBendSolid:
    bend_id: str
    angle_deg: float
    shape: object
    volume_mm3: float
    bend_line_length_mm: float
    section_area_mm2: float
    facets_approximation: int
    is_entire_sheet: bool = False
    physical_certified: bool = False


def build_bend_zone_solid(state: FoldState, bend_id: str, *,
                          segments=64) -> NativeBendSolid:
    """Build one true closed FreeCAD Part.Shape for a FoldState bend zone.

    Positive and negative angle signs retain BendZone convention. Uses the
    transform of the fixed panel, preserving preceding completed folds.
    This geometry is independent of and never modifies a FreeCAD Document.
    """
    if not isinstance(state, FoldState) or bend_id not in state.bends:
        raise NativeBendSolidError("missing_fold_state_or_bend")
    bend = state.bends[bend_id]
    zone = getattr(bend, "bend_zone", None)
    if zone is None or zone.bend_id != bend_id:
        raise NativeBendSolidError("missing_or_mismatched_bend_zone")
    if abs(float(state.angles[bend_id])) > abs(float(bend.angle_deg))+1e-8:
        raise NativeBendSolidError("angle_outside_target")
    outline = zone_outline(zone, state.angles[bend_id], segments=segments)
    try:
        import FreeCAD as App
        import Part
    except ImportError as exc:
        raise NativeBendSolidError("FreeCAD_Part_not_available") from exc

    # Local (s,z) lies in a plane perpendicular to the actual bend axis.
    side = 1.0 if bend.moving_left else -1.0
    lx,ly = zone.left_xy
    ox,oy = zone.origin_xy
    tf = state.transforms[bend.fixed_panel]
    (ax,ay),(bx,by) = zone.axis_xy
    v0 = tf.point((ax,ay,0.0))
    v1 = tf.point((bx,by,0.0))
    length = ((v1[0]-v0[0])**2+(v1[1]-v0[1])**2+(v1[2]-v0[2])**2)**0.5
    if not isfinite(length) or length < 1e-6:
        raise NativeBendSolidError("invalid_bend_axis")

    points=[]
    for s,z in outline:
        x = ox + lx*side*s
        y = oy + ly*side*s
        p = tf.point((x,y,z))
        points.append(App.Vector(*p))
    try:
        wire=Part.makePolygon(points+[points[0]])
        face=Part.Face(wire)
        extrusion=App.Vector(v1[0]-v0[0],v1[1]-v0[1],v1[2]-v0[2])
        solid=face.extrude(extrusion)
        if (solid.isNull() or not solid.isValid() or not solid.isClosed()
                or len(solid.Solids)!=1 or not isfinite(float(solid.Volume))
                or float(solid.Volume)<=0):
            raise NativeBendSolidError("invalid_or_open_part_solid")
    except NativeBendSolidError:
        raise
    except Exception as exc:
        raise NativeBendSolidError("occ_zone_build_failed: "+str(exc)) from exc

    area=float(face.Area)
    if abs(float(solid.Volume)-area*length)>max(1e-4,area*length*1e-6):
        raise NativeBendSolidError("inconsistent_extrusion_volume")
    return NativeBendSolid(
        bend_id, float(state.angles[bend_id]), solid,
        float(solid.Volume), length, area, len(outline),
    )


@dataclass(frozen=True)
class NativeSingleBendSheet:
    """Validated joined OCC solid for a two-panel, one-bend sheet only."""
    bend_id: str
    angle_deg: float
    shape: object
    volume_mm3: float
    components: int
    physical_certified: bool = False


def _apply_rigid_transform(shape, transform, App):
    """Apply existing FoldState panel transform to an isolated Part.Shape."""
    mat = App.Matrix()
    for i in range(3):
        for j in range(3):
            setattr(mat, "A{}{}".format(i+1,j+1), float(transform.rotation[i][j]))
        setattr(mat, "A{}4".format(i+1), float(transform.translation[i]))
    independent = shape.copy()
    transformed = independent.transformShape(mat, True)
    # FreeCAD's transformShape API returns a Shape in supported versions.
    if transformed is None:
        raise NativeBendSolidError("FreeCAD_transformShape_did_not_return_shape")
    if not transformed.isValid() or transformed.isNull():
        raise NativeBendSolidError("invalid_transformed_panel")
    return transformed


def build_single_bend_sheet_solid(state: FoldState, panel_build, bend_id: str, *,
                                  segments=64) -> NativeSingleBendSheet:
    """Attempt full-sheet solid from authoritative two-panel SheetMetal faces.

    Exactly ONE bend and TWO rigid faces are required. Other geometries fail
    closed; there is NO implicit dropping of neighboring bends or panel faces.
    Rigid panel faces are those returned by the existing PanelBuilder, cut
    at the finite-width BendZone boundaries, not a second 2D decomposition.

    Geometric union must be one valid connected closed OCC solid. A non-fused
    compound, a gap or a self-intersection fails rather than being accepted.
    Physical tool contact and intermediate full-cycle motion are not tested.
    """
    if not isinstance(state, FoldState) or bend_id not in state.bends:
        raise NativeBendSolidError("missing_state")
    if len(state.ordered_bends) != 1 or len(state.graph.panels) != 2:
        raise NativeBendSolidError("only_single_bend_two_panel_parts_supported")
    panels = getattr(panel_build, "panels", None)
    faces = getattr(panel_build, "panel_faces", None)
    if (panels is None or faces is None or len(panels) != 2 or len(faces) != 2
            or {p.panel_id for p in panels} != set(state.graph.panels)):
        raise NativeBendSolidError("panel_build_mismatch")
    zone_result = build_bend_zone_solid(state, bend_id, segments=segments)
    t = float(state.bends[bend_id].bend_zone.thickness_mm)
    try:
        import FreeCAD as App
        import Part
        solids = []
        for panel, face in zip(panels, faces):
            if face.isNull() or not face.isValid() or face.Area <= 0:
                raise NativeBendSolidError("invalid_rigid_panel_face")
            panel_solid = face.extrude(App.Vector(0,0,-t))
            if panel_solid.isNull() or not panel_solid.isValid() or len(panel_solid.Solids)!=1:
                raise NativeBendSolidError("invalid_rigid_panel_solid")
            solids.append(_apply_rigid_transform(
                panel_solid, state.transforms[panel.panel_id], App
            ))
        joined=solids[0].fuse(zone_result.shape).fuse(solids[1])
        if (joined.isNull() or not joined.isValid() or not joined.isClosed()
                or len(joined.Solids)!=1 or not isfinite(float(joined.Volume))
                or float(joined.Volume)<=0):
            raise NativeBendSolidError("zone_and_panels_not_one_valid_closed_solid")
        expected=sum(x.Volume for x in solids)+zone_result.volume_mm3
        if abs(float(joined.Volume)-expected)>max(1e-3,expected*1e-5):
            raise NativeBendSolidError("panel_zone_overlap_or_missing_material")
    except NativeBendSolidError:
        raise
    except Exception as exc:
        raise NativeBendSolidError("occ_sheet_assembly_failed:"+str(exc)) from exc
    return NativeSingleBendSheet(
        bend_id,float(state.angles[bend_id]),joined,float(joined.Volume),
        len(solids)+1,
    )
