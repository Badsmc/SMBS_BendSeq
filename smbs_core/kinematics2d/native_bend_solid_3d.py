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
