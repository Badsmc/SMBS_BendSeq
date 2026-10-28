"""
fcstd_reader.py - Headless reader for .FCStd documents using zipfile + pythonocc-core.
Extracts B-Rep shapes (.brp) and their Placements (Pos, Axis, Angle) without FreeCAD API.
"""

import os
import zipfile
import tempfile
import xml.etree.ElementTree as ET
from typing import List, Tuple, Any

try:
    from OCC.Core.BRepTools import breptools_Read
    from OCC.Core.BRep import BRep_Builder
    from OCC.Core.TopoDS import TopoDS_Shape
    from OCC.Core.gp import gp_Trsf, gp_Vec, gp_Ax1, gp_Pnt, gp_Dir
    HAS_OCC = True
except ImportError:
    HAS_OCC = False

from smbs_core.models.tooling import GeometryStatus


def _parse_placement(prop_element: ET.Element) -> Tuple[Tuple[float, float, float], Tuple[float, float, float], float]:
    """Extracts (pos, axis, angle_rad) from PropertyPlacement XML."""
    placement = prop_element.find("Placement")
    if placement is None:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 0.0

    pos_elem = placement.find("Position")
    axis_elem = placement.find("Axis")
    angle_elem = placement.find("Angle")

    pos = (0.0, 0.0, 0.0)
    if pos_elem is not None:
        pos = (
            float(pos_elem.get("x", 0.0)),
            float(pos_elem.get("y", 0.0)),
            float(pos_elem.get("z", 0.0))
        )

    axis = (0.0, 0.0, 1.0)
    if axis_elem is not None:
        axis = (
            float(axis_elem.get("x", 0.0)),
            float(axis_elem.get("y", 0.0)),
            float(axis_elem.get("z", 1.0))
        )

    angle_rad = 0.0
    if angle_elem is not None and angle_elem.text:
        angle_rad = float(angle_elem.text)

    return pos, axis, angle_rad


def _build_trsf(pos: Tuple[float, float, float], axis: Tuple[float, float, float], angle_rad: float) -> Any:
    """Build gp_Trsf transformation."""
    trsf = gp_Trsf()
    if angle_rad != 0.0:
        ax1 = gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(*axis))
        trsf.SetRotation(ax1, angle_rad)
    if pos != (0.0, 0.0, 0.0):
        trsf.SetTranslationPart(gp_Vec(*pos))
    return trsf


def read_fcstd(path: str) -> Tuple[List[Tuple[str, Any, Any]], GeometryStatus]:
    """
    Reads an .FCStd document and extracts valid B-Rep shapes.
    Returns: (list_of_tuples[(obj_name, shape, trsf)], GeometryStatus)
    Rule: If no .brp shapes exist or OCC fails -> returns ([], GeometryStatus.MISSING). Never invents geometry.
    """
    if not HAS_OCC:
        return [], GeometryStatus.MISSING

    if not os.path.exists(path):
        return [], GeometryStatus.MISSING

    results = []

    try:
        with zipfile.ZipFile(path, 'r') as zf:
            if "Document.xml" not in zf.namelist():
                return [], GeometryStatus.MISSING

            xml_data = zf.read("Document.xml")
            root = ET.fromstring(xml_data)

            for obj in root.iter("Object"):
                obj_name = obj.get("name", "Unknown")
                properties = obj.find("Properties")
                if properties is None:
                    continue

                brp_file = None
                pos = (0.0, 0.0, 0.0)
                axis = (0.0, 0.0, 1.0)
                angle_rad = 0.0

                for prop in properties.findall("Property"):
                    p_name = prop.get("name")
                    if p_name == "Shape":
                        part = prop.find("Part")
                        if part is not None:
                            brp_file = part.get("file")
                    elif p_name == "Placement":
                        pos, axis, angle_rad = _parse_placement(prop)

                if brp_file and brp_file in zf.namelist():
                    brp_data = zf.read(brp_file)
                    fd, tmp_path = tempfile.mkstemp(suffix=".brp")
                    try:
                        with os.fdopen(fd, 'wb') as f:
                            f.write(brp_data)

                        shape = TopoDS_Shape()
                        builder = BRep_Builder()
                        try:
                            from OCC.Core.BRepTools import breptools
                            success = breptools.Read(shape, tmp_path, builder)
                        except AttributeError:
                            from OCC.Core.BRepTools import breptools_Read
                            success = breptools_Read(shape, tmp_path, builder)

                        if success and not shape.IsNull():
                            trsf = _build_trsf(pos, axis, angle_rad)
                            # FreeCAD .brp files already include the Location, applying Placement again causes double transformation
                            results.append((obj_name, shape, trsf))
                    finally:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
    except Exception:
        return [], GeometryStatus.MISSING

    if not results:
        return [], GeometryStatus.MISSING

    return results, GeometryStatus.REAL
