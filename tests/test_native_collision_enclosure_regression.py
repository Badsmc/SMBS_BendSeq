"""Regression tests for native 3D collision detection with focus on volume enclosure,
separated solids, touching, clashing, and cavity geometries under candidate face pruning.
"""

import math
import unittest
from unittest.mock import patch
import FreeCAD
import Part
from smbs_core.kinematics2d.native_collision_3d import (
    check_pair_clearance,
    _compute_exact_distance_with_pruning,
    CollisionClass,
)
import smbs_core.kinematics2d.native_collision_3d as nc


def create_regular_prism(n_sides: int, radius: float, height: float) -> Part.Shape:
    """Create a regular polygonal prism with n_sides (faces = n_sides + 2)."""
    pts = []
    for i in range(n_sides):
        ang = 2.0 * math.pi * i / n_sides
        pts.append(FreeCAD.Vector(radius * math.cos(ang), radius * math.sin(ang), 0.0))
    pts.append(pts[0])
    wire = Part.makePolygon(pts)
    face = Part.Face(wire)
    prism = face.extrude(FreeCAD.Vector(0.0, 0.0, height))
    return prism


class TestNativeCollisionEnclosureRegression(unittest.TestCase):
    """Verify that candidate face pruning never falsely returns CLEAR for enclosed solids,
    and accurately validates separated, touching, clashing, and cavity configurations.
    """

    def setUp(self):
        # Create an 18-face solid: 16-gon prism, radius 40, height 20
        # Centered around origin in XY, Z from -10 to +10
        base_prism = create_regular_prism(16, radius=40.0, height=20.0)
        base_prism.translate(FreeCAD.Vector(0.0, 0.0, -10.0))
        self.outer_prism = base_prism
        self.assertGreater(len(self.outer_prism.Faces), 12)
        self.assertEqual(len(self.outer_prism.Faces), 18)

        # Small box solid: 2 x 2 x 2 at origin (Volume = 8 mm3)
        self.inner_box = Part.makeBox(2.0, 2.0, 2.0, FreeCAD.Vector(-1.0, -1.0, -1.0))
        self.assertAlmostEqual(self.inner_box.Volume, 8.0, delta=1e-3)

    def test_enclosed_solid_both_orders(self):
        """1. A small solid is floating completely inside a closed solid with >12 faces.
        Pruning candidate faces yields positive boundary distance (e.g. 9.0mm > near_tolerance 0.5mm).
        Must return PENETRATION with non-zero volume matching direct OCC common() in both (A, B) and (B, A).
        """
        # Ensure pruning branch actually triggers and returns positive boundary distance
        box_prism = self.outer_prism.BoundBox
        box_inner = self.inner_box.BoundBox
        boundary_dist = _compute_exact_distance_with_pruning(
            self.outer_prism, self.inner_box, box_prism, box_inner
        )
        self.assertGreater(boundary_dist, 0.5)

        # Expected direct OCC common volume:
        expected_common = self.outer_prism.common(self.inner_box)
        self.assertAlmostEqual(expected_common.Volume, 8.0, delta=1e-2)

        # Order (A, B) = (outer_prism, inner_box)
        res_ab = check_pair_clearance(self.outer_prism, self.inner_box, near_tolerance_mm=0.5, volume_tolerance_mm3=1e-4)
        self.assertEqual(res_ab.status, CollisionClass.PENETRATION)
        self.assertAlmostEqual(res_ab.overlap_volume_mm3, expected_common.Volume, delta=1e-2)
        self.assertEqual(res_ab.distance_mm, 0.0)
        self.assertIn("enclosed", res_ab.reason)

        # Order (B, A) = (inner_box, outer_prism)
        res_ba = check_pair_clearance(self.inner_box, self.outer_prism, near_tolerance_mm=0.5, volume_tolerance_mm3=1e-4)
        self.assertEqual(res_ba.status, CollisionClass.PENETRATION)
        self.assertAlmostEqual(res_ba.overlap_volume_mm3, expected_common.Volume, delta=1e-2)
        self.assertEqual(res_ba.distance_mm, 0.0)
        self.assertIn("enclosed", res_ba.reason)

    def test_separated_solids(self):
        """2. Two solids with >12 faces clearly separated by >5mm.
        Must return CLEAR, distance matching direct OCC distToShape.
        """
        other_prism = create_regular_prism(16, radius=40.0, height=20.0)
        # Shift along X so there is a 10mm gap between the two prisms
        # outer_prism X is [-40, 40]. Shift other_prism by +90 -> X is [50, 130].
        other_prism.translate(FreeCAD.Vector(90.0, 0.0, -10.0))

        direct_dist = self.outer_prism.distToShape(other_prism)[0]
        self.assertGreater(direct_dist, 5.0)

        res = check_pair_clearance(self.outer_prism, other_prism, near_tolerance_mm=0.5, volume_tolerance_mm3=1e-4)
        self.assertEqual(res.status, CollisionClass.CLEAR)
        self.assertAlmostEqual(res.distance_mm, direct_dist, places=3)
        self.assertEqual(res.overlap_volume_mm3, 0.0)

    def test_touching_solids(self):
        """3. Two solids in exact boundary surface contact (gap = 0, volume = 0).
        Must return TOUCHING.
        """
        touching_box = Part.makeBox(20.0, 20.0, 20.0, FreeCAD.Vector(40.0, -10.0, -10.0))
        # Distance should be 0.0 or <= 1e-4
        direct_dist = self.outer_prism.distToShape(touching_box)[0]
        self.assertAlmostEqual(direct_dist, 0.0, delta=1e-4)

        common_vol = self.outer_prism.common(touching_box).Volume
        self.assertAlmostEqual(common_vol, 0.0, delta=1e-4)

        res = check_pair_clearance(self.outer_prism, touching_box, near_tolerance_mm=0.5, volume_tolerance_mm3=1e-4)
        self.assertEqual(res.status, CollisionClass.TOUCHING)
        self.assertAlmostEqual(res.distance_mm, 0.0, delta=1e-4)
        self.assertLessEqual(res.overlap_volume_mm3, 1e-4)

    def test_clashing_partial_penetration(self):
        """4. Partial intersection between complex solid (>12 faces) and another solid.
        Must return PENETRATION with volume matching OCC common().
        """
        clash_box = Part.makeBox(20.0, 20.0, 20.0, FreeCAD.Vector(30.0, -10.0, -10.0))
        expected_vol = self.outer_prism.common(clash_box).Volume
        self.assertGreater(expected_vol, 1.0)

        res = check_pair_clearance(self.outer_prism, clash_box, near_tolerance_mm=0.5, volume_tolerance_mm3=1e-4)
        self.assertEqual(res.status, CollisionClass.PENETRATION)
        self.assertAlmostEqual(res.overlap_volume_mm3, expected_vol, delta=1e-2)
        self.assertEqual(res.distance_mm, 0.0)

    def test_cavity_internal_void(self):
        """5. Solid with an internal cavity (hollow shell / box with interior void, >12 faces).
        A smaller solid placed strictly inside the cavity void without intersecting the walls.
        Must return CLEAR, volume = 0, distance to cavity inner wall > 0.
        """
        outer_cube = Part.makeBox(100.0, 100.0, 100.0, FreeCAD.Vector(-50.0, -50.0, -50.0))
        hex_void = create_regular_prism(12, radius=25.0, height=60.0)
        hex_void.translate(FreeCAD.Vector(0.0, 0.0, -30.0))
        hollow_solid = outer_cube.cut(hex_void)
        self.assertGreater(len(hollow_solid.Faces), 12)

        # Place a small 4x4x4 box in the center of the cavity
        cavity_object = Part.makeBox(4.0, 4.0, 4.0, FreeCAD.Vector(-2.0, -2.0, -2.0))

        # Check direct OCC: common volume should be exactly 0
        common_vol = hollow_solid.common(cavity_object).Volume
        self.assertAlmostEqual(common_vol, 0.0, delta=1e-4)

        direct_dist = hollow_solid.distToShape(cavity_object)[0]
        self.assertGreater(direct_dist, 5.0)

        # Clearance check must recognize enclosure check, find vol == 0, and return CLEAR
        res = check_pair_clearance(hollow_solid, cavity_object, near_tolerance_mm=0.5, volume_tolerance_mm3=1e-4)
        self.assertEqual(res.status, CollisionClass.CLEAR)
        self.assertEqual(res.reason, "distance_separated_enclosure_checked")
        self.assertAlmostEqual(res.distance_mm, direct_dist, places=3)
        self.assertEqual(res.overlap_volume_mm3, 0.0)

    def test_invalid_distance_handling(self):
        """6. Missing, invalid or non-finite distance results must yield explicit INVALID_GEOMETRY
        and never invent exact 0.0.
        """
        # Test pruning function directly when distToShape returns empty or non-finite:
        class MockEmptySolid:
            def __init__(self, ref):
                self.BoundBox = ref.BoundBox
                self.Faces = ()
            def distToShape(self, other):
                return []

        class MockNaNSolid:
            def __init__(self, ref):
                self.BoundBox = ref.BoundBox
                self.Faces = ()
            def distToShape(self, other):
                return [float("nan")]

        mock_empty = MockEmptySolid(self.outer_prism)
        with self.assertRaises(ValueError) as ctx_empty:
            _compute_exact_distance_with_pruning(mock_empty, self.inner_box, mock_empty.BoundBox, self.inner_box.BoundBox)
        self.assertIn("KERNEL_EMPTY_DISTANCE_RESULT", str(ctx_empty.exception))

        mock_nan = MockNaNSolid(self.outer_prism)
        with self.assertRaises(ValueError) as ctx_nan:
            _compute_exact_distance_with_pruning(mock_nan, self.inner_box, mock_nan.BoundBox, self.inner_box.BoundBox)
        self.assertIn("KERNEL_NON_FINITE_DISTANCE", str(ctx_nan.exception))

        # Test that check_pair_clearance handles exceptions gracefully and fails closed with INVALID_GEOMETRY:
        with patch.object(nc, "_compute_exact_distance_with_pruning", side_effect=ValueError("KERNEL_EMPTY_DISTANCE_RESULT")):
            res = check_pair_clearance(self.outer_prism, self.inner_box, near_tolerance_mm=0.5, volume_tolerance_mm3=1e-4)
            self.assertEqual(res.status, CollisionClass.INVALID_GEOMETRY)
            self.assertIn("KERNEL_EMPTY_DISTANCE_RESULT", res.reason)

        with patch.object(nc, "_compute_exact_distance_with_pruning", side_effect=ValueError("KERNEL_NON_FINITE_DISTANCE:nan")):
            res_nan = check_pair_clearance(self.outer_prism, self.inner_box, near_tolerance_mm=0.5, volume_tolerance_mm3=1e-4)
            self.assertEqual(res_nan.status, CollisionClass.INVALID_GEOMETRY)
            self.assertIn("KERNEL_NON_FINITE_DISTANCE", res_nan.reason)


if __name__ == "__main__":
    unittest.main()
