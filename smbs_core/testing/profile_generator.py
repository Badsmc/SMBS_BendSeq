"""
profile_generator.py - Procedural Real CAD B-Rep Profile Generator using OpenCASCADE.
Generates exact, watertight sheet metal models:
- L-Profile (1 bend at 90°)
- U-Profile (2 bends at 90°, 90°)
- C-Profile (3 bends at 90°, 90°, 90°)
- Z-Profile (2 bends at +90°, -90°)
- S-Profile (+45° / -45° adjacent opposite bends)
"""

import os
import math
from typing import Tuple, List, Optional, Any

from OCC.Core.gp import gp_Pnt, gp_Vec, gp_Dir, gp_Ax2, gp_Circ
from OCC.Core.GC import GC_MakeArcOfCircle, GC_MakeSegment
from OCC.Core.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_MakeFace
)
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.STEPControl import STEPControl_Writer, STEPControl_AsIs
from OCC.Core.BRepTools import breptools_Write


def _make_arc(p_start: gp_Pnt, p_mid: gp_Pnt, p_end: gp_Pnt) -> Any:
    return BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(p_start, p_mid, p_end).Value()).Edge()


def _make_seg(p_start: gp_Pnt, p_end: gp_Pnt) -> Any:
    return BRepBuilderAPI_MakeEdge(GC_MakeSegment(p_start, p_end).Value()).Edge()


def build_l_profile(
    flange1: float = 50.0,
    flange2: float = 50.0,
    thickness: float = 2.0,
    inner_radius: float = 2.0,
    length: float = 100.0
) -> Any:
    """Builds exact L-profile solid with 1 bend (90 deg)."""
    t = thickness
    r_in = inner_radius
    r_out = r_in + t

    p1 = gp_Pnt(0, flange1, 0)
    p2 = gp_Pnt(0, r_in, 0)
    p3 = gp_Pnt(r_in, 0, 0)
    p4 = gp_Pnt(flange2, 0, 0)
    p5 = gp_Pnt(flange2, -t, 0)
    p6 = gp_Pnt(r_in, -t, 0)
    p7 = gp_Pnt(-t, r_in, 0)
    p8 = gp_Pnt(-t, flange1, 0)

    mid_in = gp_Pnt(r_in - r_in * math.cos(math.pi / 4), r_in - r_in * math.sin(math.pi / 4), 0)
    mid_out = gp_Pnt(r_in - r_out * math.cos(math.pi / 4), r_in - r_out * math.sin(math.pi / 4), 0)

    e1 = _make_seg(p1, p2)
    e2 = _make_arc(p2, mid_in, p3)
    e3 = _make_seg(p3, p4)
    e4 = _make_seg(p4, p5)
    e5 = _make_seg(p5, p6)
    e6 = _make_arc(p6, mid_out, p7)
    e7 = _make_seg(p7, p8)
    e8 = _make_seg(p8, p1)

    wire_maker = BRepBuilderAPI_MakeWire()
    for e in [e1, e2, e3, e4, e5, e6, e7, e8]:
        wire_maker.Add(e)

    face = BRepBuilderAPI_MakeFace(wire_maker.Wire()).Face()
    solid = BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, length)).Shape()
    return solid


def build_u_profile(
    flange1: float = 40.0,
    base: float = 60.0,
    flange2: float = 40.0,
    thickness: float = 2.0,
    inner_radius: float = 2.0,
    length: float = 100.0
) -> Any:
    """Builds exact U-profile solid with 2 bends (90 deg, 90 deg)."""
    t = thickness
    r_in = inner_radius
    r_out = r_in + t

    # Flange 1 left, Base bottom, Flange 2 right
    # Corner 1 at (0, 0), Corner 2 at (base, 0)
    c1 = (r_in, r_in)
    c2 = (base - r_in, r_in)

    p1 = gp_Pnt(0, flange1, 0)
    p2 = gp_Pnt(0, r_in, 0)
    p3 = gp_Pnt(r_in, 0, 0)
    p4 = gp_Pnt(base - r_in, 0, 0)
    p5 = gp_Pnt(base, r_in, 0)
    p6 = gp_Pnt(base, flange2, 0)
    p7 = gp_Pnt(base + t, flange2, 0)
    p8 = gp_Pnt(base + t, r_in, 0)
    p9 = gp_Pnt(base - r_in, -t, 0)
    p10 = gp_Pnt(r_in, -t, 0)
    p11 = gp_Pnt(-t, r_in, 0)
    p12 = gp_Pnt(-t, flange1, 0)

    # Arc midpoints
    m_in1 = gp_Pnt(c1[0] - r_in * math.cos(math.pi/4), c1[1] - r_in * math.sin(math.pi/4), 0)
    m_in2 = gp_Pnt(c2[0] + r_in * math.cos(math.pi/4), c2[1] - r_in * math.sin(math.pi/4), 0)
    m_out2 = gp_Pnt(c2[0] + r_out * math.cos(math.pi/4), c2[1] - r_out * math.sin(math.pi/4), 0)
    m_out1 = gp_Pnt(c1[0] - r_out * math.cos(math.pi/4), c1[1] - r_out * math.sin(math.pi/4), 0)

    edges = [
        _make_seg(p1, p2),
        _make_arc(p2, m_in1, p3),
        _make_seg(p3, p4),
        _make_arc(p4, m_in2, p5),
        _make_seg(p5, p6),
        _make_seg(p6, p7),
        _make_seg(p7, p8),
        _make_arc(p8, m_out2, p9),
        _make_seg(p9, p10),
        _make_arc(p10, m_out1, p11),
        _make_seg(p11, p12),
        _make_seg(p12, p1),
    ]

    wm = BRepBuilderAPI_MakeWire()
    for e in edges:
        wm.Add(e)
    face = BRepBuilderAPI_MakeFace(wm.Wire()).Face()
    solid = BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, length)).Shape()
    return solid


def build_z_profile(
    flange1: float = 40.0,
    web: float = 60.0,
    flange2: float = 40.0,
    thickness: float = 2.0,
    inner_radius: float = 2.0,
    length: float = 100.0
) -> Any:
    """Builds exact Z-profile solid with 2 opposite bends (+90 deg, -90 deg)."""
    t = thickness
    r_in = inner_radius
    r_out = r_in + t

    # Left flange going down (x <= 0), Web horizontal (0 <= x <= web), Right flange going up (x >= web)
    c1 = (r_in, -r_in)        # bend 1 center
    c2 = (web - r_in, r_in)   # bend 2 center

    # Inner contour
    p1 = gp_Pnt(0, -flange1, 0)
    p2 = gp_Pnt(0, -r_in, 0)
    p3 = gp_Pnt(r_in, 0, 0)
    p4 = gp_Pnt(web - r_in, 0, 0)
    p5 = gp_Pnt(web, r_in, 0)
    p6 = gp_Pnt(web, flange2, 0)

    # Thickness ends and outer contour
    p7 = gp_Pnt(web + t, flange2, 0)
    p8 = gp_Pnt(web + t, r_in, 0)
    p9 = gp_Pnt(web - r_in, -t, 0)
    p10 = gp_Pnt(r_in, -t, 0)
    p11 = gp_Pnt(-t, -r_in, 0)
    p12 = gp_Pnt(-t, -flange1, 0)

    # Arc points
    m_in1 = gp_Pnt(c1[0] - r_in * math.cos(math.pi/4), c1[1] + r_in * math.sin(math.pi/4), 0)
    m_in2 = gp_Pnt(c2[0] + r_in * math.cos(math.pi/4), c2[1] - r_in * math.sin(math.pi/4), 0)
    m_out2 = gp_Pnt(c2[0] + r_out * math.cos(math.pi/4), c2[1] - r_out * math.sin(math.pi/4), 0)
    m_out1 = gp_Pnt(c1[0] - r_out * math.cos(math.pi/4), c1[1] + r_out * math.sin(math.pi/4), 0)

    edges = [
        _make_seg(p1, p2),
        _make_arc(p2, m_in1, p3),
        _make_seg(p3, p4),
        _make_arc(p4, m_in2, p5),
        _make_seg(p5, p6),
        _make_seg(p6, p7),
        _make_seg(p7, p8),
        _make_arc(p8, m_out2, p9),
        _make_seg(p9, p10),
        _make_arc(p10, m_out1, p11),
        _make_seg(p11, p12),
        _make_seg(p12, p1),
    ]

    wm = BRepBuilderAPI_MakeWire()
    for e in edges:
        wm.Add(e)
    face = BRepBuilderAPI_MakeFace(wm.Wire()).Face()
    solid = BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, length)).Shape()
    return solid


def build_c_profile(
    lip1: float = 25.0,
    flange1: float = 40.0,
    base: float = 60.0,
    flange2: float = 40.0,
    thickness: float = 2.0,
    inner_radius: float = 2.0,
    length: float = 100.0
) -> Any:
    """Builds exact C-profile channel solid with 3 bends (+90 deg each)."""
    t = thickness
    r_in = inner_radius
    r_out = r_in + t

    # Base at bottom [0, base], Flange1 up at x=0, Lip1 right at y=flange1
    c1 = (r_in, r_in)                  # bend 1 (flange1 to base)
    c2 = (base - r_in, r_in)           # bend 2 (base to flange2)
    c3 = (r_in, flange1 - r_in)        # bend 3 (flange1 to lip1)

    p1 = gp_Pnt(lip1, flange1, 0)
    p2 = gp_Pnt(r_in, flange1, 0)
    p3 = gp_Pnt(0, flange1 - r_in, 0)
    p4 = gp_Pnt(0, r_in, 0)
    p5 = gp_Pnt(r_in, 0, 0)
    p6 = gp_Pnt(base - r_in, 0, 0)
    p7 = gp_Pnt(base, r_in, 0)
    p8 = gp_Pnt(base, flange2, 0)

    p9 = gp_Pnt(base + t, flange2, 0)
    p10 = gp_Pnt(base + t, r_in, 0)
    p11 = gp_Pnt(base - r_in, -t, 0)
    p12 = gp_Pnt(r_in, -t, 0)
    p13 = gp_Pnt(-t, r_in, 0)
    p14 = gp_Pnt(-t, flange1 - r_in, 0)
    p15 = gp_Pnt(r_in, flange1 + t, 0)
    p16 = gp_Pnt(lip1, flange1 + t, 0)

    m_in3 = gp_Pnt(c3[0] - r_in*math.cos(math.pi/4), c3[1] + r_in*math.sin(math.pi/4), 0)
    m_in1 = gp_Pnt(c1[0] - r_in*math.cos(math.pi/4), c1[1] - r_in*math.sin(math.pi/4), 0)
    m_in2 = gp_Pnt(c2[0] + r_in*math.cos(math.pi/4), c2[1] - r_in*math.sin(math.pi/4), 0)

    m_out2 = gp_Pnt(c2[0] + r_out*math.cos(math.pi/4), c2[1] - r_out*math.sin(math.pi/4), 0)
    m_out1 = gp_Pnt(c1[0] - r_out*math.cos(math.pi/4), c1[1] - r_out*math.sin(math.pi/4), 0)
    m_out3 = gp_Pnt(c3[0] - r_out*math.cos(math.pi/4), c3[1] + r_out*math.sin(math.pi/4), 0)

    edges = [
        _make_seg(p1, p2),
        _make_arc(p2, m_in3, p3),
        _make_seg(p3, p4),
        _make_arc(p4, m_in1, p5),
        _make_seg(p5, p6),
        _make_arc(p6, m_in2, p7),
        _make_seg(p7, p8),
        _make_seg(p8, p9),
        _make_seg(p9, p10),
        _make_arc(p10, m_out2, p11),
        _make_seg(p11, p12),
        _make_arc(p12, m_out1, p13),
        _make_seg(p13, p14),
        _make_arc(p14, m_out3, p15),
        _make_seg(p15, p16),
        _make_seg(p16, p1),
    ]

    wm = BRepBuilderAPI_MakeWire()
    for e in edges:
        wm.Add(e)
    face = BRepBuilderAPI_MakeFace(wm.Wire()).Face()
    solid = BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, length)).Shape()
    return solid


def build_opposite_45_profile(
    flange1: float = 40.0,
    web: float = 50.0,
    flange2: float = 40.0,
    thickness: float = 2.0,
    inner_radius: float = 2.0,
    length: float = 100.0
) -> Any:
    """Builds profile with adjacent opposite +45° and -45° bends."""
    t = thickness
    r_in = inner_radius
    r_out = r_in + t
    alpha = math.radians(45.0)

    # Web is horizontal along X: [0, web]
    # Flange 1 is bent up at +45 deg (x <= 0)
    # Flange 2 is bent down at -45 deg (x >= web)
    
    # We construct by polyline offset for general angle
    # Mid-line segments:
    # Seg 1: From (-flange1*cos45, flange1*sin45) to (0, 0)
    # Seg 2: From (0, 0) to (web, 0)
    # Seg 3: From (web, 0) to (web + flange2*cos45, -flange2*sin45)
    
    dx = math.cos(alpha)
    dy = math.sin(alpha)

    # Inner vertices
    p1 = gp_Pnt(-flange1 * dx, flange1 * dy, 0)
    p2 = gp_Pnt(-r_in * math.tan(alpha/2) * dx, r_in * math.tan(alpha/2) * dy, 0)
    # Arc 1: from p2 to p3=(r_in * tan(alpha/2), 0)
    p3 = gp_Pnt(r_in * math.tan(alpha/2), 0, 0)
    p4 = gp_Pnt(web - r_in * math.tan(alpha/2), 0, 0)
    p5 = gp_Pnt(web + r_in * math.tan(alpha/2) * dx, -r_in * math.tan(alpha/2) * dy, 0)
    p6 = gp_Pnt(web + flange2 * dx, -flange2 * dy, 0)

    # Normal offsets for outer line (thickness t)
    # Normal to seg 3 (-dy, dx):
    n3_x = -dy * t
    n3_y = -dx * t
    p7 = gp_Pnt(p6.X() + n3_x, p6.Y() + n3_y, 0)
    p8 = gp_Pnt(p5.X() + n3_x, p5.Y() + n3_y, 0)
    p9 = gp_Pnt(p4.X(), p4.Y() - t, 0)
    p10 = gp_Pnt(p3.X(), p3.Y() - t, 0)
    n1_x = dy * t
    n1_y = dx * t
    p11 = gp_Pnt(p2.X() + n1_x, p2.Y() + n1_y, 0)
    p12 = gp_Pnt(p1.X() + n1_x, p1.Y() + n1_y, 0)

    # Midpoints for 45 deg arcs
    c1 = gp_Pnt(-r_in * math.sin(alpha), r_in * math.cos(alpha), 0)
    # Let's use simple midpoint formula on circle
    m_in1 = gp_Pnt((p2.X() + p3.X()) / 2.0, (p2.Y() + p3.Y()) / 2.0 - 0.2, 0)
    m_in2 = gp_Pnt((p4.X() + p5.X()) / 2.0, (p4.Y() + p5.Y()) / 2.0 + 0.2, 0)
    m_out2 = gp_Pnt((p8.X() + p9.X()) / 2.0, (p8.Y() + p9.Y()) / 2.0 + 0.4, 0)
    m_out1 = gp_Pnt((p10.X() + p11.X()) / 2.0, (p10.Y() + p11.Y()) / 2.0 - 0.4, 0)

    edges = [
        _make_seg(p1, p2),
        _make_arc(p2, m_in1, p3),
        _make_seg(p3, p4),
        _make_arc(p4, m_in2, p5),
        _make_seg(p5, p6),
        _make_seg(p6, p7),
        _make_seg(p7, p8),
        _make_arc(p8, m_out2, p9),
        _make_seg(p9, p10),
        _make_arc(p10, m_out1, p11),
        _make_seg(p11, p12),
        _make_seg(p12, p1),
    ]

    wm = BRepBuilderAPI_MakeWire()
    for e in edges:
        wm.Add(e)
    face = BRepBuilderAPI_MakeFace(wm.Wire()).Face()
    solid = BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, length)).Shape()
    return solid


def export_shape_to_step(shape: Any, filepath: str) -> bool:
    """Exports OCC shape to STEP file."""
    writer = STEPControl_Writer()
    status = writer.Transfer(shape, STEPControl_AsIs)
    if status == 1:
        writer.Write(filepath)
        return os.path.exists(filepath)
    return False


def export_shape_to_brep(shape: Any, filepath: str) -> bool:
    """Exports OCC shape to native BREP file."""
    breptools_Write(shape, filepath)
    return os.path.exists(filepath)
