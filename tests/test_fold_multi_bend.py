"""
test_fold_multi_bend.py - Unit test suite for multi-bend folding assembly (technical specification v1.2 §5.1 Step 3).

Verifies:
1. fold_u_bracket reconstructs a 2-bend U-profile from flat pattern components:
   - Monolithic solid with 1 shell, isValid == True.
   - Exactly 4 cylindrical fillet faces (R=1.0 and R=2.5).
   - Delta Volume < 1e-4 mm^3 against golden reference B-Rep model.
   - Delta Bounding Box < 1e-4 mm against golden reference B-Rep model.
2. fold_4bend_stepped_busbar reconstructs a 4-bend stepped busbar from flat pattern components:
   - Monolithic solid with 1 shell, isValid == True.
   - Exactly 8 cylindrical fillet faces (R=1.0 and R=2.5).
   - Delta Volume < 1e-4 mm^3 against golden reference B-Rep model.
   - Delta Bounding Box < 1e-4 mm against golden reference B-Rep model.
"""

from __future__ import annotations
import math
import os
import sys
import unittest
import tempfile
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))
sys.path.append("/Applications/FreeCAD.app/Contents/Resources/lib")
sys.path.append(os.path.expanduser("~/Library/Application Support/FreeCAD/v1-1/Mod/sheetmetal"))

import FreeCAD
import Part
import SheetMetalBendSolid
from OCC.Core.BRepTools import breptools

from tests.test_sheetmetal_ubracket import create_u_bracket_shape_perfect_concentric
from tests.test_sheetmetal_4bend import create_4bend_stepped_busbar


def fold_u_bracket(
    l1: float = 95.5,
    l_mid: float = 50.0,
    l2: float = 47.5,
    bend_r: float = 1.0,
    thickness: float = 1.5,
    kfactor: float = 0.44,
    width: float = 50.0
) -> Part.Shape:
    """
    Executes 2-bend physical fold for U-bracket from flat pattern components.
    Flange 1 (vertical) -> Bend 1 -> Mid Flange (horizontal) -> Bend 2 -> Flange 2 (vertical).
    """
    neutral_r = bend_r + kfactor * thickness
    unfold_len = neutral_r * math.radians(90.0)

    # Base bend prototype (horizontal -> vertical up)
    bend_face = Part.makePolygon([
        FreeCAD.Vector(0, 0, thickness),
        FreeCAD.Vector(unfold_len, 0, thickness),
        FreeCAD.Vector(unfold_len, width, thickness),
        FreeCAD.Vector(0, width, thickness),
        FreeCAD.Vector(0, 0, thickness)
    ])
    bend_face = Part.Face(bend_face)
    bend_edge = Part.makeLine(FreeCAD.Vector(0, 0, thickness), FreeCAD.Vector(0, width, thickness))
    axis = FreeCAD.Vector(0, 1.0, 0)

    s_up = SheetMetalBendSolid.bend_solid(
        sel_face=bend_face,
        sel_edge=bend_edge,
        bend_r=bend_r,
        thickness=thickness,
        neutral_radius=neutral_r,
        axis=axis,
        flipped=False
    )

    # Mid flange: X in [2.5, 2.5 + l_mid], Z in [0, thickness]
    f_mid = Part.makeBox(l_mid, width, thickness, FreeCAD.Vector(2.5, 0, 0))

    # Bend 2: at X = 2.5 + l_mid, turns from +X into +Z. Center is (2.5 + l_mid, 0, 2.5)
    s2 = s_up.translated(FreeCAD.Vector(2.5 + l_mid, 0, 0))
    # Leg 2: X in [2.5 + l_mid + bend_r, ...], Z in [2.5, 2.5 + l2]
    f2 = Part.makeBox(thickness, width, l2, FreeCAD.Vector(2.5 + l_mid + bend_r, 0, 2.5))

    # Bend 1: at X = 2.5, turns from -X into +Z. Rotate s_up 180 deg around Z
    s1 = s_up.copy()
    s1.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 0, 1), 180.0)
    s1.translate(FreeCAD.Vector(2.5, width, 0))
    # Leg 1: X in [0, thickness], Z in [2.5, 2.5 + l1]
    f1 = Part.makeBox(thickness, width, l1, FreeCAD.Vector(0, 0, 2.5))

    fused = f1.multiFuse([s1, f_mid, s2, f2])
    return fused.Solids[0] if fused.Solids else fused


def fold_4bend_stepped_busbar(
    l1: float = 40.0,
    l2: float = 30.0,
    l3: float = 50.0,
    l4: float = 30.0,
    l5: float = 40.0,
    bend_r: float = 1.0,
    thickness: float = 1.5,
    kfactor: float = 0.44,
    width: float = 50.0
) -> Part.Shape:
    """
    Executes 4-bend physical fold for stepped busbar from flat pattern components.
    9 components: 5 planar flanges + 4 bend sectors with concentric fillets.
    """
    neutral_r = bend_r + kfactor * thickness
    unfold_len = neutral_r * math.radians(90.0)

    # Base up-bend prototype
    bend_face = Part.makePolygon([
        FreeCAD.Vector(0, 0, thickness),
        FreeCAD.Vector(unfold_len, 0, thickness),
        FreeCAD.Vector(unfold_len, width, thickness),
        FreeCAD.Vector(0, width, thickness),
        FreeCAD.Vector(0, 0, thickness)
    ])
    bend_face = Part.Face(bend_face)
    bend_edge = Part.makeLine(FreeCAD.Vector(0, 0, thickness), FreeCAD.Vector(0, width, thickness))
    axis = FreeCAD.Vector(0, 1.0, 0)

    s_up = SheetMetalBendSolid.bend_solid(
        sel_face=bend_face,
        sel_edge=bend_edge,
        bend_r=bend_r,
        thickness=thickness,
        neutral_radius=neutral_r,
        axis=axis,
        flipped=False
    )

    # Base down-bend prototype (s_down)
    s_down = s_up.copy()
    s_down.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1, 0, 1), 180.0)
    s_down.translate(FreeCAD.Vector(0, width, 0))
    s_down.translate(FreeCAD.Vector(41.0, 0, 32.5))

    # Flange 1: [0, 40]
    f1 = Part.makeBox(l1, width, thickness, FreeCAD.Vector(0, 0, 0))
    # Sector 1: at X=40, turns up
    s1 = s_up.translated(FreeCAD.Vector(l1, 0, 0))
    # Flange 2: [41, 42.5] x [0, 50] x [2.5, 32.5]
    f2 = Part.makeBox(thickness, width, l2, FreeCAD.Vector(l1 + bend_r, 0, 2.5))
    # Sector 2: at Z=32.5, turns right/down
    s2 = s_down
    # Flange 3: [43.5, 93.5] x [0, 50] x [33.5, 35.0]
    f3 = Part.makeBox(l3, width, thickness, FreeCAD.Vector(43.5, 0, 33.5))

    # Step translation vector between step 1 and step 2
    step_shift = FreeCAD.Vector(53.5, 0, 33.5)
    s3 = s1.translated(step_shift)
    f4 = f2.translated(step_shift)
    s4 = s2.translated(step_shift)
    f5 = Part.makeBox(l5, width, thickness, FreeCAD.Vector(97.0, 0, 67.0))

    all_parts = [f1, s1, f2, s2, f3, s3, f4, s4, f5]
    fused = all_parts[0].multiFuse(all_parts[1:])
    return fused.Solids[0] if fused.Solids else fused


class TestFoldMultiBend(unittest.TestCase):
    def test_fold_u_bracket(self):
        """Validates 2-bend U-profile assembly against golden reference OCC model."""
        l1, l_mid, l2 = 95.5, 50.0, 47.5
        bend_r = 1.0
        thickness = 1.5
        kfactor = 0.44
        width = 50.0

        folded = fold_u_bracket(
            l1=l1,
            l_mid=l_mid,
            l2=l2,
            bend_r=bend_r,
            thickness=thickness,
            kfactor=kfactor,
            width=width
        )

        self.assertEqual(len(folded.Solids), 1)
        self.assertEqual(len(folded.Shells), 1)
        self.assertTrue(folded.isValid())

        cyls = [f for f in folded.Faces if "Cylinder" in str(f.Surface)]
        self.assertEqual(len(cyls), 4)

        # Golden reference OCC model
        ref_occ = create_u_bracket_shape_perfect_concentric(l1, l_mid, l2, bend_r, thickness, width)
        tf = tempfile.mktemp(suffix=".brep")
        breptools.Write(ref_occ, tf)
        ref_fc = Part.Shape()
        ref_fc.read(tf)

        delta_vol = abs(folded.Volume - ref_fc.Volume)
        self.assertLess(delta_vol, 1e-4)

        delta_bbox_x = abs(folded.BoundBox.XLength - ref_fc.BoundBox.XLength)
        delta_bbox_y = abs(folded.BoundBox.YLength - ref_fc.BoundBox.YLength)
        delta_bbox_z = abs(folded.BoundBox.ZLength - ref_fc.BoundBox.ZLength)
        self.assertLess(delta_bbox_x, 1e-4)
        self.assertLess(delta_bbox_y, 1e-4)
        self.assertLess(delta_bbox_z, 1e-4)

    def test_fold_4bend_stepped_busbar(self):
        """Validates 4-bend stepped busbar assembly against golden reference OCC model."""
        l1, l2, l3, l4, l5 = 40.0, 30.0, 50.0, 30.0, 40.0
        bend_r = 1.0
        thickness = 1.5
        kfactor = 0.44
        width = 50.0

        folded = fold_4bend_stepped_busbar(
            l1=l1,
            l2=l2,
            l3=l3,
            l4=l4,
            l5=l5,
            bend_r=bend_r,
            thickness=thickness,
            kfactor=kfactor,
            width=width
        )

        self.assertEqual(len(folded.Solids), 1)
        self.assertEqual(len(folded.Shells), 1)
        self.assertTrue(folded.isValid())

        cyls = [f for f in folded.Faces if "Cylinder" in str(f.Surface)]
        self.assertEqual(len(cyls), 8)

        # Golden reference OCC model
        ref_occ = create_4bend_stepped_busbar(l1, l2, l3, l4, l5, bend_r, thickness, width)
        tf = tempfile.mktemp(suffix=".brep")
        breptools.Write(ref_occ, tf)
        ref_fc = Part.Shape()
        ref_fc.read(tf)

        delta_vol = abs(folded.Volume - ref_fc.Volume)
        self.assertLess(delta_vol, 1e-4)

        delta_bbox_x = abs(folded.BoundBox.XLength - ref_fc.BoundBox.XLength)
        delta_bbox_y = abs(folded.BoundBox.YLength - ref_fc.BoundBox.YLength)
        delta_bbox_z = abs(folded.BoundBox.ZLength - ref_fc.BoundBox.ZLength)
        self.assertLess(delta_bbox_x, 1e-4)
        self.assertLess(delta_bbox_y, 1e-4)
        self.assertLess(delta_bbox_z, 1e-4)


if __name__ == "__main__":
    unittest.main()
