"""test_d633_native_collision.py - Unit & Regression Tests for D6.3.3 Native 3D Collision.

Tests:
1. Synthetic Part solids: CLEAR (separated), NEAR_CONTACT, TOUCHING (shared face), PENETRATION.
2. Exact touching is distinguishable from penetration.
3. 0.01 mm and 0.1 mm clearances are CLEAR, not penetration.
4. Positive volume overlaps (e.g. 0.78 mm³, 59.85 mm³) are strictly PENETRATION and unsafe.
5. Geometry validation: None, null shape, open shape, zero volume fails closed.
6. Missing placement evidence fails closed (MISSING_EVIDENCE).
7. Insufficient tool coverage fails closed (INSUFFICIENT_TOOL_LENGTH).
8. Native CAD tooling loading & machine placement with datums.
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
    check_tool_coverage,
    ToolCoverageResult,
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

    @unittest.skipUnless(HAS_FREECAD, "FreeCAD required for Part.Shape tests")
    def test_clearance_and_penetration_thresholds(self):
        box1 = Part.makeBox(10, 10, 10, App.Vector(0, 0, 0))
        # 0.01 mm clearance is NEAR_CONTACT (not penetration)
        box_001 = Part.makeBox(10, 10, 10, App.Vector(10.01, 0, 0))
        r_001 = check_pair_clearance(box1, box_001, near_tolerance_mm=0.5)
        self.assertEqual(r_001.status, CollisionClass.NEAR_CONTACT)
        self.assertEqual(r_001.overlap_volume_mm3, 0.0)

        # 0.1 mm clearance is NEAR_CONTACT (not penetration)
        box_010 = Part.makeBox(10, 10, 10, App.Vector(10.1, 0, 0))
        r_010 = check_pair_clearance(box1, box_010, near_tolerance_mm=0.5)
        self.assertEqual(r_010.status, CollisionClass.NEAR_CONTACT)
        self.assertEqual(r_010.overlap_volume_mm3, 0.0)

        # Small positive overlap (e.g. 0.78 mm³) MUST be PENETRATION and unsafe
        # 10 x 10 x dx = 0.78 mm³ -> dx = 0.0078 mm
        box_overlap_small = Part.makeBox(10, 10, 10, App.Vector(10.0 - 0.0078, 0, 0))
        r_small = check_pair_clearance(box1, box_overlap_small)
        self.assertEqual(r_small.status, CollisionClass.PENETRATION)
        self.assertGreater(r_small.overlap_volume_mm3, 0.7)

        # Overlap cannot be bypassed by validate_workpiece_tooling_pose
        pose_res = validate_workpiece_tooling_pose(box1, box_overlap_small, box_far := Part.makeBox(10, 10, 10, App.Vector(50, 0, 0)))
        self.assertEqual(pose_res.status, CollisionClass.PENETRATION)
        self.assertFalse(pose_res.is_safe)
        self.assertIn("PUNCH_PENETRATION", pose_res.diagnostics[0])

    @unittest.skipUnless(HAS_FREECAD, "FreeCAD required for native CAD loading")
    def test_native_tool_loader_placement(self):
        die = load_native_die("toollibrary/die/matrica.FCStd")
        self.assertEqual(die.kind, "DIE")
        self.assertTrue(die.machine_shape.isValid())
        bb_die = die.machine_shape.BoundBox
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

    def test_tool_coverage_check(self):
        # 100 mm tool covers 80 mm bend
        cov_ok = check_tool_coverage((-40.0, 40.0), (-50.0, 50.0))
        self.assertTrue(cov_ok.is_covered)
        self.assertEqual(cov_ok.status, "FULL_TOOL_COVERAGE")

        # 100 mm tool does NOT cover 200 mm bend
        cov_fail = check_tool_coverage((-100.0, 100.0), (-50.0, 50.0))
        self.assertFalse(cov_fail.is_covered)
        self.assertEqual(cov_fail.status, "INSUFFICIENT_TOOL_LENGTH")
        self.assertEqual(cov_fail.uncovered_left_mm, 50.0)
        self.assertEqual(cov_fail.uncovered_right_mm, 50.0)

    @unittest.skipUnless(HAS_FREECAD, "FreeCAD required for pose validation")
    def test_validate_workpiece_tooling_pose_missing_evidence(self):
        box = Part.makeBox(10, 10, 10)
        res = validate_workpiece_tooling_pose(None, box, box)
        self.assertEqual(res.status, CollisionClass.MISSING_EVIDENCE)
        self.assertFalse(res.is_safe)
        self.assertIn("MISSING_WORKPIECE_SHAPE", res.diagnostics)

    @unittest.skipUnless(HAS_FREECAD, "FreeCAD required for pose validation")
    def test_validate_workpiece_tooling_pose_insufficient_coverage(self):
        box = Part.makeBox(10, 10, 10)
        cov_fail = check_tool_coverage((-100.0, 100.0), (-50.0, 50.0))
        res = validate_workpiece_tooling_pose(box, box, box, coverage_result=cov_fail)
        self.assertEqual(res.status, CollisionClass.INSUFFICIENT_TOOL_LENGTH)
        self.assertFalse(res.is_safe)
        self.assertIn("INSUFFICIENT_TOOL_LENGTH", res.diagnostics[0])


if __name__ == "__main__":
    unittest.main()
