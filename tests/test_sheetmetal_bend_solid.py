"""
test_sheetmetal_bend_solid.py - Dedicated contract test for SheetMetalBendSolid.bend_solid.
Compliant with technical specification v1.2 §3.1, §3.3 R1.

Verifies:
1. bend_solid produces a genuine FreeCAD Part.Solid.
2. Contains exactly 2 cylindrical faces with R_inner and R_outer = R_inner + thickness.
3. Bounding box matches expected cylindrical sector bounds.
4. Analytic volume matches (pi/4) * (R_out^2 - R_in^2) * width.
"""

from __future__ import annotations
import math
import os
import sys
import unittest
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))
sys.path.append("/Applications/FreeCAD.app/Contents/Resources/lib")
sys.path.append(os.path.expanduser("~/Library/Application Support/FreeCAD/v1-1/Mod/sheetmetal"))

import FreeCAD
import Part
import SheetMetalBendSolid


class TestSheetMetalBendSolidContract(unittest.TestCase):
    def test_bend_solid_sector_geometry(self):
        """Tests bend_solid primitive on 90-degree bend strip for R=1.0, t=1.5, k=0.44."""
        bend_r = 1.0
        thickness = 1.5
        kfactor = 0.44
        width = 50.0
        neutral_r = bend_r + kfactor * thickness
        unfold_len = neutral_r * (math.pi / 2.0)  # 90 deg = pi/2 rad

        l1 = 95.5
        # Define flat bend strip face: X in [l1, l1 + unfold_len], Y in [0, width], Z = 0
        poly = [
            FreeCAD.Vector(l1, 0, 0),
            FreeCAD.Vector(l1 + unfold_len, 0, 0),
            FreeCAD.Vector(l1 + unfold_len, width, 0),
            FreeCAD.Vector(l1, width, 0),
            FreeCAD.Vector(l1, 0, 0)
        ]
        wire = Part.makePolygon(poly)
        face = Part.Face(wire)
        edge = Part.makeLine(FreeCAD.Vector(l1, 0, 0), FreeCAD.Vector(l1, width, 0))
        axis = FreeCAD.Vector(0, 1.0, 0)

        # Call native SheetMetal bend_solid
        b_solid = SheetMetalBendSolid.bend_solid(
            sel_face=face,
            sel_edge=edge,
            bend_r=bend_r,
            thickness=thickness,
            neutral_radius=neutral_r,
            axis=axis,
            flipped=False
        )

        # 1. Solid type verification
        self.assertEqual(b_solid.ShapeType, "Solid")
        self.assertTrue(b_solid.isValid())
        self.assertEqual(len(b_solid.Solids), 1)

        # 2. Cylindrical face detection & radii verification
        cyl_radii = []
        for f in b_solid.Faces:
            if getattr(f.Surface, "TypeId", "") == "Part::GeomCylinder":
                cyl_radii.append(round(f.Surface.Radius, 4))

        cyl_radii.sort()
        self.assertEqual(len(cyl_radii), 2, f"Expected exactly 2 cylindrical faces, found: {cyl_radii}")
        self.assertAlmostEqual(cyl_radii[0], bend_r, places=3, msg="Inner cylinder radius must equal bend_r")
        self.assertAlmostEqual(cyl_radii[1], bend_r + thickness, places=3, msg="Outer cylinder radius must equal bend_r + thickness")

        # 3. Bounding box check
        # X should span [l1, l1 + bend_r + thickness] = [95.5, 98.0]
        # Y should span [0, width] = [0.0, 50.0]
        # Z should span [-thickness, bend_r] = [-1.5, 1.0]
        bb = b_solid.BoundBox
        self.assertAlmostEqual(bb.XMin, l1, places=2)
        self.assertAlmostEqual(bb.XMax, l1 + bend_r + thickness, places=2)
        self.assertAlmostEqual(bb.YMin, 0.0, places=2)
        self.assertAlmostEqual(bb.YMax, width, places=2)
        self.assertAlmostEqual(bb.ZMin, -thickness, places=2)
        self.assertAlmostEqual(bb.ZMax, bend_r, places=2)

        # 4. Analytic volume verification
        # Sector of 90 deg between R_out and R_in:
        # Cross-sectional area = (pi/4) * (R_out^2 - R_in^2) = (pi/4) * (2.5^2 - 1.0^2) = (pi/4) * 5.25
        # Volume = Area * width = (pi/4) * 5.25 * 50 = 206.1670 mm^3
        expected_volume = (math.pi / 4.0) * ((bend_r + thickness)**2 - bend_r**2) * width
        actual_volume = b_solid.Volume
        self.assertAlmostEqual(actual_volume, expected_volume, delta=0.5, msg=f"Actual volume {actual_volume} vs expected {expected_volume}")


if __name__ == "__main__":
    unittest.main()
