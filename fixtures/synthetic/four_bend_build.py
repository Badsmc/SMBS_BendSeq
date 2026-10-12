"""
Independent OCC construction of 4-bend stepped busbar synthetic fixture.
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

def build_four_bend():
    l1, l2, l3, l4, l5 = 40.0, 30.0, 50.0, 30.0, 40.0
    w = 50.0
    t = 1.5
    r = 1.0
    R = r + t  # 2.5 mm

    # 1. Flange 1: Box at origin along +X
    # X in [0.0, 40.0], Y in [0.0, 50.0], Z in [0.0, 1.5]
    f1 = BRepPrimAPI_MakeBox(gp_Pnt(0.0, 0.0, 0.0), gp_Pnt(l1, w, t)).Shape()

    # 2. Bend 1 (+90 deg up): Center at (40.0, 0, 2.5)
    # Bounded in quadrant X in [40.0, 42.5], Z in [0.0, 2.5]
    b1 = _make_quadrant_sector(gp_Pnt(l1, 0.0, R), r, R, w, gp_Pnt(l1, 0.0, 0.0), gp_Pnt(l1 + R, w, R))

    # 3. Flange 2: Box along +Z
    # X in [41.0, 42.5], Y in [0.0, 50.0], Z in [2.5, 32.5]
    f2 = BRepPrimAPI_MakeBox(gp_Pnt(l1 + r, 0.0, R), gp_Pnt(l1 + R, w, R + l2)).Shape()

    # 4. Bend 2 (-90 deg to +X): Center at (43.5, 0, 32.5)
    # Outer circle flush with X=41.0, inner circle flush with X=42.5
    # Bounded in quadrant X in [41.0, 43.5], Z in [32.5, 35.0]
    b2_center_x = l1 + r + R  # 40.0 + 1.0 + 2.5 = 43.5
    b2_center_z = R + l2      # 2.5 + 30.0 = 32.5
    b2 = _make_quadrant_sector(gp_Pnt(b2_center_x, 0.0, b2_center_z), r, R, w,
                              gp_Pnt(l1 + r, 0.0, b2_center_z), gp_Pnt(b2_center_x, w, b2_center_z + R))

    # 5. Flange 3: Box along +X
    # X in [43.5, 93.5], Y in [0.0, 50.0], Z in [33.5, 35.0]
    f3_start_x = b2_center_x
    f3_end_x = f3_start_x + l3
    f3_bottom_z = b2_center_z + r
    f3_top_z = b2_center_z + R
    f3 = BRepPrimAPI_MakeBox(gp_Pnt(f3_start_x, 0.0, f3_bottom_z), gp_Pnt(f3_end_x, w, f3_top_z)).Shape()

    # 6. Bend 3 (+90 deg up): Center at (93.5, 0, 36.0)
    # Outer bottom flush with Z=33.5, inner bottom at Z=35.0
    # Bounded in quadrant X in [93.5, 96.0], Z in [33.5, 36.0]
    b3_center_x = f3_end_x
    b3_center_z = f3_top_z + r  # 35.0 + 1.0 = 36.0
    b3 = _make_quadrant_sector(gp_Pnt(b3_center_x, 0.0, b3_center_z), r, R, w,
                              gp_Pnt(b3_center_x, 0.0, f3_bottom_z), gp_Pnt(b3_center_x + R, w, b3_center_z))

    # 7. Flange 4: Box along +Z
    # X in [94.5, 96.0], Y in [0.0, 50.0], Z in [36.0, 66.0]
    f4_left_x = b3_center_x + r
    f4_right_x = b3_center_x + R
    f4_start_z = b3_center_z
    f4_end_z = f4_start_z + l4
    f4 = BRepPrimAPI_MakeBox(gp_Pnt(f4_left_x, 0.0, f4_start_z), gp_Pnt(f4_right_x, w, f4_end_z)).Shape()

    # 8. Bend 4 (-90 deg to +X): Center at (97.0, 0, 66.0)
    # Outer circle flush with X=94.5, inner circle flush with X=96.0
    # Bounded in quadrant X in [94.5, 97.0], Z in [66.0, 68.5]
    b4_center_x = f4_left_x + R  # 94.5 + 2.5 = 97.0
    b4_center_z = f4_end_z       # 66.0
    b4 = _make_quadrant_sector(gp_Pnt(b4_center_x, 0.0, b4_center_z), r, R, w,
                              gp_Pnt(f4_left_x, 0.0, b4_center_z), gp_Pnt(b4_center_x, w, b4_center_z + R))

    # 9. Flange 5: Box along +X
    # X in [97.0, 137.0], Y in [0.0, 50.0], Z in [67.0, 68.5]
    f5_start_x = b4_center_x
    f5_end_x = f5_start_x + l5
    f5_bottom_z = b4_center_z + r
    f5_top_z = b4_center_z + R
    f5 = BRepPrimAPI_MakeBox(gp_Pnt(f5_start_x, 0.0, f5_bottom_z), gp_Pnt(f5_end_x, w, f5_top_z)).Shape()

    # 10. Fuse all primitives sequentially
    parts = [f1, b1, f2, b2, f3, b3, f4, b4, f5]
    solid = parts[0]
    for p in parts[1:]:
        solid = BRepAlgoAPI_Fuse(solid, p).Shape()

    # 11. Unify coplanar tangent faces
    unifier = ShapeUpgrade_UnifySameDomain(solid, True, True, True)
    unifier.Build()
    unified_busbar = unifier.Shape()

    out_brep = Path(__file__).parent / "four_bend.brep"
    breptools.Write(unified_busbar, str(out_brep))
    print(f"Saved 4-bend busbar BREP to {out_brep}")
    return out_brep

if __name__ == "__main__":
    build_four_bend()
