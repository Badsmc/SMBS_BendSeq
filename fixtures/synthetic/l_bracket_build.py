"""
Independent OCC construction of L-bracket synthetic fixture.
DOES NOT USE any SMBS modules or folding functions.
Uses standard OCC primitives:
- BRepPrimAPI_MakeBox
- BRepPrimAPI_MakeCylinder
- BRepAlgoAPI_Common
- BRepAlgoAPI_Cut
- BRepAlgoAPI_Fuse
- ShapeUpgrade_UnifySameDomain
- BRepTools.Write
"""

import sys
from pathlib import Path
from OCC.Core.gp import gp_Ax2, gp_Pnt, gp_Dir
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Fuse, BRepAlgoAPI_Cut, BRepAlgoAPI_Common
from OCC.Core.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCC.Core.BRepTools import breptools

def build_l_bracket():
    l1 = 95.5
    l2 = 47.5
    w = 50.0
    t = 1.5
    r = 1.0
    R = r + t  # 2.5 mm

    # 1. Flange 1: Box at origin along +X
    # X in [0.0, l1], Y in [0.0, w], Z in [0.0, t]
    f1 = BRepPrimAPI_MakeBox(l1, w, t).Shape()

    # 2. Bend Sector: Cylinder along +Y cut to 90 deg quadrant
    # Bend axis origin at X = l1, Y = 0.0, Z = R = 2.5, direction (0, 1, 0)
    axis = gp_Ax2(gp_Pnt(l1, 0.0, R), gp_Dir(0, 1, 0), gp_Dir(1, 0, 0))
    cyl_outer = BRepPrimAPI_MakeCylinder(axis, R, w).Shape()
    cyl_inner = BRepPrimAPI_MakeCylinder(axis, r, w).Shape()

    # Quadrant box bounding the bend sector: X in [l1, l1 + R], Y in [0, w], Z in [0, R]
    quad_box = BRepPrimAPI_MakeBox(gp_Pnt(l1, 0.0, 0.0), gp_Pnt(l1 + R, w, R)).Shape()
    sector_full = BRepAlgoAPI_Common(cyl_outer, quad_box).Shape()
    bend_sector = BRepAlgoAPI_Cut(sector_full, cyl_inner).Shape()

    # 3. Flange 2: Box along +Z
    # X in [l1 + r, l1 + R] = [96.5, 98.0]
    # Y in [0.0, w] = [0.0, 50.0]
    # Z in [R, R + l2] = [2.5, 50.0]
    f2 = BRepPrimAPI_MakeBox(gp_Pnt(l1 + r, 0.0, R), gp_Pnt(l1 + R, w, R + l2)).Shape()

    # 4. Fuse primitives
    fuse1 = BRepAlgoAPI_Fuse(f1, bend_sector).Shape()
    bracket = BRepAlgoAPI_Fuse(fuse1, f2).Shape()

    # 5. Unify coplanar tangent faces to form canonical B-Rep solid
    unifier = ShapeUpgrade_UnifySameDomain(bracket, True, True, True)
    unifier.Build()
    unified_bracket = unifier.Shape()

    out_brep = Path(__file__).parent / "l_bracket.brep"
    breptools.Write(unified_bracket, str(out_brep))
    print(f"Saved L-bracket BREP to {out_brep}")
    return out_brep

if __name__ == "__main__":
    build_l_bracket()
