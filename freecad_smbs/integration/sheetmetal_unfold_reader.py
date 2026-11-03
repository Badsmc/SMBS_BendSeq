"""Read canonical bend information from FreeCAD SheetMetal's new unfolder.

Read-only adapter. Does not infer panel ownership or physical feasibility.
Run inside FreeCAD with the SheetMetal workbench installed.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Tuple


class UnfoldReadError(RuntimeError):
    """Source or geometry cannot be read without ambiguity."""


@dataclass(frozen=True)
class BendRecord2D:
    bend_id: str
    source_angle_deg: float
    fold_angle_deg: float
    radius_mm: float
    length_mm: float
    start_xyz: Tuple[float, float, float]
    end_xyz: Tuple[float, float, float]


@dataclass(frozen=True)
class UnfoldData2D:
    source_object: str
    source_face: str
    unfold_object: str
    bends: Tuple[BendRecord2D, ...]


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

    material = getattr(unfold, "MaterialSheet", "_manual")
    standard = str(unfold.KFactorStandard)
    if material in ("_manual", "_none", None):
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


def read_unfold(unfold_name="Body_Unfold", document=None):
    """Return immutable canonical bend records from the referenced source shape.

    The reference 'Bend001.Face4' is resolved against Bend001.Shape, NEVER
    against Body.Shape.Face4. No recompute or document modification occurs.
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
    try:
        result = SM.getUnfold(_calculator(unfold), source, face_name)
    except Exception as exc:
        raise UnfoldReadError(
            "SheetMetal getUnfold failed for {}.{}".format(source.Name, face_name)
        ) from exc
    if not isinstance(result, (tuple, list)) or len(result) < 5:
        raise UnfoldReadError("Unexpected SheetMetal getUnfold result")
    info = result[4]
    if not info:
        raise UnfoldReadError("No bends found for {}.{}".format(source.Name, face_name))
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
        bends.append(BendRecord2D(
            bend_id="B{:02d}".format(i),
            source_angle_deg=angle,
            fold_angle_deg=fold_angle_from_sheetmetal(angle),
            radius_mm=radius,
            length_mm=length,
            start_xyz=_point(edge.Vertexes[0]),
            end_xyz=_point(edge.Vertexes[1]),
        ))
    return UnfoldData2D(
        source_object=source.Name,
        source_face=face_name,
        unfold_object=unfold.Name,
        bends=tuple(bends),
    )
