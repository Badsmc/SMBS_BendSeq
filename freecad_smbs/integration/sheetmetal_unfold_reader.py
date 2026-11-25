"""Read canonical bend information from FreeCAD SheetMetal's new unfolder.

Read-only adapter. Does not infer panel ownership or physical feasibility.
Run inside FreeCAD with the SheetMetal workbench installed.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, radians
from typing import Tuple

from smbs_core.kinematics2d.bend_zone import BendZoneError, bend_parameters


class UnfoldReadError(RuntimeError):
    """Source or geometry cannot be read without ambiguity."""


@dataclass(frozen=True)
class BendRecord2D:
    """Canonical bend data in the SheetMetal sketch frame (flat pattern z=0).

    radius_mm is SheetMetal's BendInfo.radius: the radius of the cylinder on
    the unfold reference-face side (inner for UP/positive bends, outer for
    DOWN/negative bends). inner_radius_mm is derived explicitly from it.
    bend_allowance_mm is the value SheetMetal itself used for the flat pattern.
    """
    bend_id: str
    source_angle_deg: float
    fold_angle_deg: float
    radius_mm: float
    length_mm: float
    start_xyz: Tuple[float, float, float]
    end_xyz: Tuple[float, float, float]
    thickness_mm: float
    k_factor_ansi: float
    inner_radius_mm: float
    bend_allowance_mm: float


@dataclass(frozen=True)
class UnfoldData2D:
    source_object: str
    source_face: str
    unfold_object: str
    bends: Tuple[BendRecord2D, ...]
    thickness_mm: float
    k_factor_standard: str
    root_centroid_xy: Tuple[float, float] = (0.0, 0.0)
    sketch_outline: object = None
    sketch_bend_lines_xy: Tuple[Tuple[Tuple[float, float], Tuple[float, float]], ...] = ()


def fold_angle_from_sheetmetal(angle_deg: float) -> float:
    """Convert signed SheetMetal included angle to signed bend magnitude.

    Confirmed for the tested +/-30, +45 and +60 degree examples.
    Sign alone does NOT specify moving-panel side or viewer rotation axis.
    """
    if not isfinite(angle_deg) or not (0 < abs(angle_deg) < 180):
        raise UnfoldReadError("Unsupported SheetMetal bend angle: {!r}".format(angle_deg))
    return (1.0 if angle_deg > 0 else -1.0) * (180.0 - abs(angle_deg))


def _point(vertex):
    p = vertex.Point
    xyz = (float(p.x), float(p.y), float(p.z))
    if not all(isfinite(v) for v in xyz):
        raise UnfoldReadError("Non-finite bend endpoint")
    return xyz


def _calculator(unfold):
    import SheetMetalNewUnfolder as SM

    for prop in ("KFactor", "KFactorStandard"):
        if not hasattr(unfold, prop):
            raise UnfoldReadError("Unfold object has no {} property; refusing to "
                                  "assume a K-factor".format(prop))
    material = getattr(unfold, "MaterialSheet", "_manual")
    standard = str(unfold.KFactorStandard)
    if standard not in ("ansi", "din"):
        raise UnfoldReadError("Unknown KFactorStandard: {!r}".format(standard))
    if material in ("_manual", "_none", None):
        k = float(unfold.KFactor)
        if not isfinite(k) or not 0 <= k <= (2.0 if standard == "din" else 1.0):
            raise UnfoldReadError("Invalid K-factor: {!r}".format(k))
        return SM.BendAllowanceCalculator.from_single_value(
            float(unfold.KFactor), standard
        )
    if isinstance(material, str) and material.startswith("csv_"):
        raise UnfoldReadError(
            "CSV material table requires thickness-aware K-factor lookup; "
            "refusing to silently substitute manual K-factor"
        )
    raise UnfoldReadError(
        "Spreadsheet material table requires explicit calculator integration: {!r}".format(material)
    )


def _resolve_tip_source(source, face_name, linked):
    """Promote stale Body_Unfold feature references to Body.Tip when unambiguous."""
    tip = getattr(linked, "Tip", None)
    if (
        tip is None
        or tip.Name == source.Name
        or not hasattr(tip, "Shape")
        or tip.Shape.isNull()
        or len(tip.Shape.Faces) <= len(source.Shape.Faces)
    ):
        return source, face_name
    try:
        src_face = source.Shape.getElement(face_name)
        n_src = src_face.normalAt(0, 0)
        c_src = src_face.CenterOfMass
    except Exception:
        return source, face_name
    candidates = []
    for idx, f in enumerate(tip.Shape.Faces, 1):
        if getattr(f.Surface, "TypeId", "") != "Part::GeomPlane":
            continue
        try:
            n_f = f.normalAt(0, 0)
            if n_f.dot(n_src) < 0.999999:
                continue
            if abs((f.CenterOfMass - c_src).dot(n_src)) > 1e-4:
                continue
            common_area = f.common(src_face).Area
        except Exception:
            continue
        if common_area > 0.5 * min(f.Area, src_face.Area):
            candidates.append((common_area, "Face{}".format(idx)))
    if len(candidates) == 1:
        return tip, candidates[0][1]
    return source, face_name


def read_unfold(unfold_name="Body_Unfold", document=None):
    """Return immutable canonical bend records from the referenced source shape.

    The reference 'Bend001.Face4' is resolved against Bend001.Shape, NEVER
    against Body.Shape.Face4. If Body_Unfold references an earlier feature inside
    a PartDesign::Body whose Tip has additional bends, promotes cleanly to the
    corresponding planar reference face on Body.Tip. No recompute or document
    modification occurs.
    """
    import FreeCAD as App
    import SheetMetalNewUnfolder as SM

    doc = document if document is not None else App.ActiveDocument
    if doc is None:
        raise UnfoldReadError("No active FreeCAD document")
    unfold = doc.getObject(unfold_name)
    if unfold is None:
        raise UnfoldReadError("Unfold object not found: " + unfold_name)
    try:
        linked, subelements = unfold.baseObject
    except (AttributeError, TypeError, ValueError) as exc:
        raise UnfoldReadError("Invalid unfold baseObject") from exc
    if not subelements or len(subelements) != 1:
        raise UnfoldReadError("Expected exactly one reference face")
    reference = str(subelements[0])
    if "." in reference:
        source_name, face_name = reference.rsplit(".", 1)
        source = doc.getObject(source_name)
        if source is None:
            raise UnfoldReadError("Referenced source object missing: " + source_name)
    else:
        source = linked
        face_name = reference
    if not face_name.startswith("Face") or not face_name[4:].isdigit():
        raise UnfoldReadError("Invalid face reference: " + reference)
    if source is None or not hasattr(source, "Shape") or source.Shape.isNull():
        raise UnfoldReadError("Referenced source has no valid shape")
    face_index = int(face_name[4:])
    if not 1 <= face_index <= len(source.Shape.Faces):
        raise UnfoldReadError("Face index outside referenced source shape")
    source, face_name = _resolve_tip_source(source, face_name, linked)
    try:
        bac = _calculator(unfold)
        result = SM.getUnfold(bac, source, face_name)
    except UnfoldReadError:
        raise
    except Exception as exc:
        raise UnfoldReadError(
            "SheetMetal getUnfold failed for {}.{}".format(source.Name, face_name)
        ) from exc
    if not isinstance(result, (tuple, list)) or len(result) < 5:
        raise UnfoldReadError("Unexpected SheetMetal getUnfold result")
    rf, unbent, _trimmed, norm, info = result[:5]
    if not info:
        raise UnfoldReadError("No bends found for {}.{}".format(source.Name, face_name))
    root_centroid_xy = (0.0, 0.0)
    sketch_outline = None
    sketch_bend_lines_xy = ()
    try:
        sketch_profile, fold_lines, _ = SM.SketchExtraction.extract_manually(unbent, norm)
        tr_sketch = SM.SketchExtraction.move_to_origin(sketch_profile, rf)
        rf_sf = rf.transformed(tr_sketch)
        root_centroid_xy = (float(rf_sf.CenterOfMass.x), float(rf_sf.CenterOfMass.y))
        sketch_outline = sketch_profile.transformed(tr_sketch)
        lines_xy = []
        for fl in fold_lines:
            fl_sf = fl.transformed(tr_sketch)
            for edge in fl_sf.Edges:
                if len(edge.Vertexes) == 2:
                    p0, p1 = edge.Vertexes[0].Point, edge.Vertexes[1].Point
                    lines_xy.append(((float(p0.x), float(p0.y)), (float(p1.x), float(p1.y))))
        sketch_bend_lines_xy = tuple(lines_xy)
    except Exception:
        pass
    thickness = _thickness(SM, source, face_name)
    standard = str(unfold.KFactorStandard)
    bends = []
    for i, item in enumerate(info, 1):
        angle = float(item.angle)
        radius = float(item.radius)
        edge = item.line
        length = float(edge.Length)
        if not all(isfinite(v) for v in (radius, length)) or radius <= 0 or length <= 0:
            raise UnfoldReadError("Invalid bend radius or line length at index {}".format(i))
        if len(edge.Vertexes) != 2:
            raise UnfoldReadError("Bend line is not a two-endpoint edge at index {}".format(i))
        k_ansi = float(bac.get_k_factor(radius, thickness))
        try:
            params = bend_parameters(angle, radius, thickness, k_ansi)
        except BendZoneError as exc:
            raise UnfoldReadError("B{:02d}: {}".format(i, exc)) from exc
        # Cross-check against SheetMetal's own allowance formula.
        direction = SM.BendDirection.UP if angle > 0 else SM.BendDirection.DOWN
        sm_ba = float(bac.get_bend_allowance(direction, radius, thickness,
                                             radians(abs(params.fold_angle_deg))))
        if abs(sm_ba - params.bend_allowance_mm) > 1e-9 * max(1.0, sm_ba):
            raise UnfoldReadError("B{:02d}: bend allowance disagrees with SheetMetal "
                                  "({} vs {})".format(i, params.bend_allowance_mm, sm_ba))
        bends.append(BendRecord2D(
            bend_id="B{:02d}".format(i),
            source_angle_deg=angle,
            fold_angle_deg=fold_angle_from_sheetmetal(angle),
            radius_mm=radius,
            length_mm=length,
            start_xyz=_point(edge.Vertexes[0]),
            end_xyz=_point(edge.Vertexes[1]),
            thickness_mm=thickness,
            k_factor_ansi=k_ansi,
            inner_radius_mm=params.inner_radius_mm,
            bend_allowance_mm=params.bend_allowance_mm,
        ))
    return UnfoldData2D(
        source_object=source.Name,
        source_face=face_name,
        unfold_object=unfold.Name,
        bends=tuple(bends),
        thickness_mm=thickness,
        k_factor_standard=standard,
        root_centroid_xy=root_centroid_xy,
        sketch_outline=sketch_outline,
        sketch_bend_lines_xy=sketch_bend_lines_xy,
    )


def _thickness(SM, source, face_name):
    """Sheet thickness exactly as SheetMetal's getUnfold estimates it."""
    shp = source.Shape.transformed(source.Placement.toMatrix().inverse())
    try:
        index = shp.findSubShape(shp.getElement(face_name))[1] - 1
        value = float(SM.EstimateThickness.using_best_method(shp, index))
    except Exception as exc:
        raise UnfoldReadError("Cannot determine sheet thickness via SheetMetal") from exc
    if not isfinite(value) or value <= 0:
        raise UnfoldReadError("Invalid sheet thickness: {!r}".format(value))
    return value
