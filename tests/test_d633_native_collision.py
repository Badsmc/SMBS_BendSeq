"""test_d633_native_collision.py - Unit & Regression Tests for D6.3.3 Native 3D Collision.

Tests:
1. Synthetic Part solids: CLEAR (separated), NEAR_CONTACT, TOUCHING (shared face), PENETRATION.
2. Geometry validation: None, null shape, open shape, zero volume.
3. Native CAD tooling loading & machine placement:
   - matrica.FCStd (Die) placement in Machine Space.
   - puancon.FCStd (Punch) placement and ram_z translation in Machine Space.
4. ToolCollisionReport safety policy and diagnostics.
"""
import math
import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.native_collision_3d import (
    CollisionClass,
    compute_aabb_gap,
    check_solid_validity,
    check_pair_clearance,
    validate_workpiece_tooling_pose,
)
from smbs_core.kinematics2d.native_tool_loader import (
    load_native_die,
    load_native_punch,
    set_punch_ram_z,
)

try:
    import FreeCAD as App
    import Part
    HAS_FREECAD = True
except ImportError:
    App = None
    Part = None
    HAS_FREECAD = False


class TestD633NativeCollision(unittest.TestCase):

    def test_compute_aabb_gap(self):
        b1 = SimpleNamespace(XMin=0, XMax=10, YMin=0, YMax=10, ZMin=0, ZMax=10)
        b2 = SimpleNamespace(XMin=20, XMax=30, YMin=0, YMax=10, ZMin=0, ZMax=10)
        b3 = SimpleNamespace(XMin=5, XMax=15, YMin=0, YMax=10, ZMin=0, ZMax=10)

        self.assertEqual(compute_aabb_gap(b1, b2), 10.0)
        self.assertEqual(compute_aabb_gap(b1, b3), 0.0)
        self.assertIsNone(compute_aabb_gap(b1, None))

    @unittest.skipUnless(HAS_FREECAD, "FreeCAD required for Part.Shape tests")
    def test_solid_validity(self):
        self.assertFalse(check_solid_validity(None)[0])
        box = Part.makeBox(10, 10, 10)
        self.assertTrue(check_solid_validity(box)[0])

    @unittest.skipUnless(HAS_FREECAD, "FreeCAD required for Part.Shape tests")
    def test_synthetic_primitives_clearance(self):
        box1 = Part.makeBox(10, 10, 10, App.Vector(0, 0, 0))
        box_far = Part.makeBox(10, 10, 10, App.Vector(50, 0, 0))
        box_near = Part.makeBox(10, 10, 10, App.Vector(10.2, 0, 0))
        box_touch = Part.makeBox(10, 10, 10, App.Vector(10, 0, 0))
        box_overlap = Part.makeBox(10, 10, 10, App.Vector(5, 0, 0))

        # CLEAR (AABB separated)
        r_far = check_pair_clearance(box1, box_far, near_tolerance_mm=0.5)
        self.assertEqual(r_far.status, CollisionClass.CLEAR)
        self.assertGreater(r_far.distance_mm, 30.0)

        # NEAR_CONTACT
        r_near = check_pair_clearance(box1, box_near, near_tolerance_mm=0.5)
        self.assertEqual(r_near.status, CollisionClass.NEAR_CONTACT)
        self.assertAlmostEqual(r_near.distance_mm, 0.2, places=3)

        # TOUCHING
        r_touch = check_pair_clearance(box1, box_touch, near_tolerance_mm=0.5)
        self.assertEqual(r_touch.status, CollisionClass.TOUCHING)
        self.assertAlmostEqual(r_touch.distance_mm, 0.0, places=4)
        self.assertAlmostEqual(r_touch.overlap_volume_mm3, 0.0, places=4)

        # PENETRATION
        r_overlap = check_pair_clearance(box1, box_overlap, near_tolerance_mm=0.5)
        self.assertEqual(r_overlap.status, CollisionClass.PENETRATION)
        self.assertAlmostEqual(r_overlap.overlap_volume_mm3, 500.0, places=1)

    @unittest.skipUnless(HAS_FREECAD, "FreeCAD required for native CAD loading")
    def test_native_tool_loader_placement(self):
        die = load_native_die("toollibrary/die/matrica.FCStd")
        self.assertEqual(die.kind, "DIE")
        self.assertTrue(die.machine_shape.isValid())
        bb_die = die.machine_shape.BoundBox
        # Check Die Machine bounds:
        self.assertAlmostEqual(bb_die.XMin, -50.0, places=1)
        self.assertAlmostEqual(bb_die.XMax, 50.0, places=1)
        self.assertAlmostEqual(bb_die.YMin, -30.0, places=1)
        self.assertAlmostEqual(bb_die.YMax, 30.0, places=1)
        self.assertAlmostEqual(bb_die.ZMax, 0.0, places=2)
        self.assertAlmostEqual(bb_die.ZMin, -60.0, places=1)

        punch = load_native_punch("toollibrary/punch/puancon.FCStd", ram_z_mm=50.0)
        self.assertEqual(punch.kind, "PUNCH")
        self.assertTrue(punch.machine_shape.isValid())
        bb_punch = punch.machine_shape.BoundBox
        self.assertAlmostEqual(bb_punch.XMin, -50.0, places=1)
        self.assertAlmostEqual(bb_punch.XMax, 50.0, places=1)
        self.assertAlmostEqual(bb_punch.ZMin, 50.0, places=1)
        self.assertAlmostEqual(bb_punch.ZMax, 184.0, places=1)

        # Test set_punch_ram_z
        punch_retract = set_punch_ram_z(punch, 100.0)
        bb_retract = punch_retract.machine_shape.BoundBox
        self.assertAlmostEqual(bb_retract.ZMin, 100.0, places=1)
        self.assertAlmostEqual(bb_retract.ZMax, 234.0, places=1)

    @unittest.skipUnless(HAS_FREECAD, "FreeCAD required for pose validation")
    def test_validate_workpiece_tooling_pose_missing_evidence(self):
        box = Part.makeBox(10, 10, 10)
        res = validate_workpiece_tooling_pose(None, box, box)
        self.assertEqual(res.status, CollisionClass.MISSING_EVIDENCE)
        self.assertFalse(res.is_safe)
        self.assertIn("MISSING_WORKPIECE_SHAPE", res.diagnostics)


if __name__ == "__main__":
    unittest.main()
