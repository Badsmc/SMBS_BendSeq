"""
Independent OCC construction of U-bracket synthetic fixture.
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

def _make_quadrant_sector(center_pnt: gp_Pnt, r_in: float, r_out: float, length: float, box_p1: gp_Pnt, box_p2: gp_Pnt):
    axis = gp_Ax2(center_pnt, gp_Dir(0, 1, 0), gp_Dir(1, 0, 0))
    cyl_out = BRepPrimAPI_MakeCylinder(axis, r_out, length).Shape()
    cyl_in = BRepPrimAPI_MakeCylinder(axis, r_in, length).Shape()
    box = BRepPrimAPI_MakeBox(box_p1, box_p2).Shape()
    sec_full = BRepAlgoAPI_Common(cyl_out, box).Shape()
    sector = BRepAlgoAPI_Cut(sec_full, cyl_in).Shape()
    return sector

def build_u_bracket():
    l1 = 60.0
    l2 = 40.0
    l3 = 60.0
    w = 50.0
    t = 1.5
    r = 1.0
    R = r + t  # 2.5 mm

    # 1. Base Flange (middle web): Box along X
    # X in [R, R + l2] = [2.5, 42.5]
    # Y in [0.0, w] = [0.0, 50.0]
    # Z in [0.0, t] = [0.0, 1.5]
    f_base = BRepPrimAPI_MakeBox(gp_Pnt(R, 0.0, 0.0), gp_Pnt(R + l2, w, t)).Shape()

    # 2. Bend 1 (left): Center at (R, 0, R) = (2.5, 0, 2.5)
    # Bounded in quadrant X in [0.0, R], Z in [0.0, R]
    b1 = _make_quadrant_sector(gp_Pnt(R, 0.0, R), r, R, w, gp_Pnt(0.0, 0.0, 0.0), gp_Pnt(R, w, R))

    # 3. Leg 1 (left upright): Box along +Z
    # X in [0.0, t] = [0.0, 1.5]
    # Y in [0.0, w] = [0.0, 50.0]
    # Z in [R, R + l1] = [2.5, 62.5]
    f_leg1 = BRepPrimAPI_MakeBox(gp_Pnt(0.0, 0.0, R), gp_Pnt(t, w, R + l1)).Shape()

    # 4. Bend 2 (right): Center at (R + l2, 0, R) = (42.5, 0, 2.5)
    # Bounded in quadrant X in [R + l2, 2*R + l2] = [42.5, 45.0], Z in [0.0, R] = [0.0, 2.5]
    b2 = _make_quadrant_sector(gp_Pnt(R + l2, 0.0, R), r, R, w, gp_Pnt(R + l2, 0.0, 0.0), gp_Pnt(2*R + l2, w, R))

    # 5. Leg 2 (right upright): Box along +Z
    # X in [R + l2 + r, 2*R + l2] = [43.5, 45.0]
    # Y in [0.0, w] = [0.0, 50.0]
    # Z in [R, R + l3] = [2.5, 62.5]
    f_leg2 = BRepPrimAPI_MakeBox(gp_Pnt(R + l2 + r, 0.0, R), gp_Pnt(2*R + l2, w, R + l3)).Shape()

    # 6. Fuse primitives
    parts = [f_base, b1, f_leg1, b2, f_leg2]
    solid = parts[0]
    for p in parts[1:]:
        solid = BRepAlgoAPI_Fuse(solid, p).Shape()

    # 7. Unify coplanar tangent faces
    unifier = ShapeUpgrade_UnifySameDomain(solid, True, True, True)
    unifier.Build()
    unified_u = unifier.Shape()

    out_brep = Path(__file__).parent / "u_bracket.brep"
    breptools.Write(unified_u, str(out_brep))
    print(f"Saved U-bracket BREP to {out_brep}")
    return out_brep

if __name__ == "__main__":
    build_u_bracket()
