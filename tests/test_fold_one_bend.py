"""
test_fold_one_bend.py - Unit test suite for 1-bend folding assembly (ТЗ v1.2 §5.1 Step 2).

Verifies:
1. fold_one_bend reconstructs a 3D folded L-bracket from its flat pattern components.
2. The reconstructed shape is a valid monolithic Solid with exactly 1 shell.
3. Contains exactly 2 cylindrical faces (inner R=1.0 and outer R=2.5).
4. Delta volume < 1e-3 mm^3 against reference B-Rep shape.
5. Delta bounding box < 1e-3 mm against reference B-Rep shape.
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

from tests.test_technological_unfold_single_bend import create_l_bracket_shape

sys.path.append("/Applications/FreeCAD.app/Contents/Resources/lib")
sys.path.append(os.path.expanduser("~/Library/Application Support/FreeCAD/v1-1/Mod/sheetmetal"))

import FreeCAD
import Part
import SheetMetalBendSolid
from OCC.Core.BRepTools import breptools


def fold_one_bend(
    l1: float = 95.5,
    l2: float = 47.5,
    bend_r: float = 1.0,
    thickness: float = 1.5,
    kfactor: float = 0.44,
    width: float = 50.0,
    angle_deg: float = 90.0
) -> Part.Shape:
    """
    Executes 1-bend physical fold from flat pattern components:
    1. Flange 1: [0, l1] x [0, width] x [0, thickness]
    2. Bend sector: generated via SheetMetalBendSolid.bend_solid
    3. Flange 2: rotated and positioned at the bend exit
    4. Fuse pieces into a monolithic Part.Solid
    """
    neutral_r = bend_r + kfactor * thickness
    unfold_len = neutral_r * math.radians(angle_deg)

    # 1. Flange 1
    f1 = Part.makeBox(l1, width, thickness, FreeCAD.Vector(0, 0, 0))

    # 2. Bend sector
    bend_face = Part.makePolygon([
        FreeCAD.Vector(l1, 0, thickness),
        FreeCAD.Vector(l1 + unfold_len, 0, thickness),
        FreeCAD.Vector(l1 + unfold_len, width, thickness),
        FreeCAD.Vector(l1, width, thickness),
        FreeCAD.Vector(l1, 0, thickness)
    ])
    bend_face = Part.Face(bend_face)
    bend_edge = Part.makeLine(FreeCAD.Vector(l1, 0, thickness), FreeCAD.Vector(l1, width, thickness))
    axis = FreeCAD.Vector(0, 1.0, 0)

    b_sector = SheetMetalBendSolid.bend_solid(
        sel_face=bend_face,
        sel_edge=bend_edge,
        bend_r=bend_r,
        thickness=thickness,
        neutral_radius=neutral_r,
        axis=axis,
        flipped=False
    )

    # 3. Flange 2
    # Origin at (l1 + bend_r, 0, thickness + bend_r) = (96.5, 0, 2.5)
    f2 = Part.makeBox(thickness, width, l2, FreeCAD.Vector(l1 + bend_r, 0, thickness + bend_r))

    # 4. Fuse
    fused = f1.fuse(b_sector).fuse(f2)
    return fused.Solids[0] if fused.Solids else fused


class TestFoldOneBend(unittest.TestCase):
    def setUp(self):
        self.l1 = 95.5
        self.l2 = 47.5
        self.bend_r = 1.0
        self.thickness = 1.5
        self.kfactor = 0.44
        self.width = 50.0

        # Create golden reference OCC shape
        ref_shape_occ = create_l_bracket_shape(
            angle_deg=90.0,
            radius=self.bend_r,
            thickness=self.thickness,
            length=self.width,
            l1=self.l1,
            l2=self.l2
        )
        with tempfile.NamedTemporaryFile(suffix=".brep", delete=False) as tf:
            breptools.Write(ref_shape_occ, tf.name)
            self.ref_fc = Part.Shape()
            self.ref_fc.read(tf.name)

    def test_fold_one_bend_matches_reference(self):
        """Validates that fold_one_bend matches reference B-Rep shape in all invariants."""
        folded_solid = fold_one_bend(
            l1=self.l1,
            l2=self.l2,
            bend_r=self.bend_r,
            thickness=self.thickness,
            kfactor=self.kfactor,
            width=self.width,
            angle_deg=90.0
        )

        # 1. Topological invariants
        self.assertEqual(folded_solid.ShapeType, "Solid")
        self.assertTrue(folded_solid.isValid())
        self.assertEqual(len(folded_solid.Shells), 1)

        # 2. Cylindrical faces count & radii
        cyl_radii = []
        for f in folded_solid.Faces:
            if getattr(f.Surface, "TypeId", "") == "Part::GeomCylinder":
                cyl_radii.append(round(f.Surface.Radius, 4))
        cyl_radii.sort()

        self.assertEqual(len(cyl_radii), 2, f"Expected 2 cylindrical faces, found: {cyl_radii}")
        self.assertAlmostEqual(cyl_radii[0], self.bend_r, places=3)
        self.assertAlmostEqual(cyl_radii[1], self.bend_r + self.thickness, places=3)

        # 3. Volume invariant
        delta_vol = abs(folded_solid.Volume - self.ref_fc.Volume)
        self.assertLess(delta_vol, 1e-3, f"Delta volume {delta_vol} exceeds 1e-3 mm^3")

        # 4. Bounding box invariant
        bb = folded_solid.BoundBox
        ref_bb = self.ref_fc.BoundBox
        self.assertAlmostEqual(bb.XMin, ref_bb.XMin, places=3)
        self.assertAlmostEqual(bb.XMax, ref_bb.XMax, places=3)
        self.assertAlmostEqual(bb.YMin, ref_bb.YMin, places=3)
        self.assertAlmostEqual(bb.YMax, ref_bb.YMax, places=3)
        self.assertAlmostEqual(bb.ZMin, ref_bb.ZMin, places=3)
        self.assertAlmostEqual(bb.ZMax, ref_bb.ZMax, places=3)


if __name__ == "__main__":
    unittest.main()
