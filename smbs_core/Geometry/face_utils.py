"""
Geometry Face Utilities for SMBS / BendSeq.
Provides shared OCC face queries across workers and pipeline stages.
"""

from typing import Optional

from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.GProp import GProp_GProps
from OCC.Core.GeomAbs import GeomAbs_Plane
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopoDS import TopoDS_Face, TopoDS_Shape, topods


def find_largest_planar_face(shape: TopoDS_Shape) -> Optional[TopoDS_Face]:
    """
    Finds the planar face with the largest surface area in an OCC shape.
    Returns None if no planar face exists.
    """
    max_area = -1.0
    best_face = None
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = topods.Face(exp.Current())
        surf = BRepAdaptor_Surface(face, True)
        if surf.GetType() == GeomAbs_Plane:
            props = GProp_GProps()
            brepgprop.SurfaceProperties(face, props)
            area = props.Mass()
            if area > max_area:
                max_area = area
                best_face = face
        exp.Next()
    return best_face
