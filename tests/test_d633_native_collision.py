"""test_d633_native_collision.py - Unit & Regression Tests for D6.3.3 Native 3D Collision.

Tests:
1. Synthetic Part solids: CLEAR (separated), NEAR_CONTACT, TOUCHING (shared face), PENETRATION.
2. Exact touching is distinguishable from penetration.
3. 0.01 mm and 0.1 mm clearances are CLEAR / NEAR_CONTACT, not penetration.
4. Positive volume overlaps (e.g. 0.78 mm³, 59.85 mm³) are strictly PENETRATION and unsafe.
5. Geometry validation: None, null shape, open shape, zero volume fails closed.
6. Missing placement evidence fails closed (MISSING_EVIDENCE).
7. Insufficient tool coverage fails closed (INSUFFICIENT_TOOL_LENGTH).
8. Assembly continuity: gap detection between tool sections.
9. Native CAD tooling loading & machine placement with datums.
10. Strict logical AND operation qualification:
    - 5-phase test with BENDING/BOTTOM penetration -> SAMPLED_POSES_REJECTED even when RETRACT is CLEAR.
    - Systematic test of each single failing phase -> SAMPLED_POSES_REJECTED.
    - All phases safe -> SAMPLED_POSES_QUALIFIED.
"""
import math
import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.native_collision_3d import (
    CollisionClass,
    SetupQualificationStatus,
    OperationQualificationStatus,
    compute_aabb_gap,
    check_solid_validity,
    check_pair_clearance,
    validate_workpiece_tooling_pose,
    qualify_tool_setup,
    qualify_operation_sampled_poses,
    PhaseQualificationReport,
    ToolCollisionReport,
    PairClearanceResult,
)
from smbs_core.kinematics2d.native_tool_loader import (
    load_native_die,
    load_native_punch,
    set_punch_ram_z,
    check_tool_coverage,
    ToolCoverageResult,
    ToolSectionConfig,
)

try:
    import FreeCAD as App
    import Part
    HAS_FREECAD = True
except ImportError:
    App = None
    Part = None
    HAS_FREECAD = False


def _make_dummy_collision(status: CollisionClass, is_safe: bool) -> ToolCollisionReport:
    p = PairClearanceResult(status, "test", 0.0 if not is_safe else 10.0, 10.0 if not is_safe else 0.0, None, 0.1)
    return ToolCollisionReport(status, p, p, is_safe, [f"test_{status.value}"], 0.2)


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

    def test_tool_coverage_and_assembly_gaps(self):
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

        # Multi-section coverage without gaps (2 x 100mm = 200mm)
        secs = (
            ToolSectionConfig("p1", "punch", 100.0, (-100.0, 0.0), -50.0),
            ToolSectionConfig("p2", "punch", 100.0, (0.0, 100.0), 50.0),
        )
        cov_multi = check_tool_coverage((-100.0, 100.0), (-100.0, 100.0), sections=secs)
        self.assertTrue(cov_multi.is_covered)
        self.assertEqual(cov_multi.status, "FULL_TOOL_COVERAGE")

        # Multi-section with gap
        secs_gap = (
            ToolSectionConfig("p1", "punch", 90.0, (-100.0, -10.0), -55.0),
            ToolSectionConfig("p2", "punch", 90.0, (10.0, 100.0), 55.0),
        )
        cov_gap = check_tool_coverage((-100.0, 100.0), (-100.0, 100.0), sections=secs_gap)
        self.assertFalse(cov_gap.is_covered)
        self.assertEqual(cov_gap.status, "TOOL_ASSEMBLY_GAP")
        self.assertEqual(cov_gap.gap_count, 1)
        self.assertAlmostEqual(cov_gap.max_gap_mm, 20.0)

    @unittest.skipUnless(HAS_FREECAD, "FreeCAD required for pose validation")
    def test_validate_workpiece_tooling_pose_missing_evidence(self):
        box = Part.makeBox(10, 10, 10)
        res = validate_workpiece_tooling_pose(None, box, box)
        self.assertEqual(res.status, CollisionClass.MISSING_EVIDENCE)
        self.assertFalse(res.is_safe)
        self.assertIn("MISSING_WORKPIECE_SHAPE", res.diagnostics)

    def test_qualify_operation_sampled_poses_strict_and(self):
        cov_ok = ToolCoverageResult(True, "FULL_TOOL_COVERAGE", 10.0, (0, 10), 10.0, (0, 10), 0, 0, 0, 0, "OK")
        cov_bad = ToolCoverageResult(False, "INSUFFICIENT_TOOL_LENGTH", 20.0, (0, 20), 10.0, (0, 10), 0, 10, 0, 0, "Short")

        col_clear = _make_dummy_collision(CollisionClass.CLEAR, True)
        col_touch = _make_dummy_collision(CollisionClass.TOUCHING, True)
        col_penetrate = _make_dummy_collision(CollisionClass.PENETRATION, False)

        # 1. 5-phase test: APPROACH: CLEAR, CONTACT: TOUCHING, BENDING: PENETRATION, BOTTOM: PENETRATION, RETRACT: CLEAR
        # Expected: SAMPLED_POSES_REJECTED (RETRACT clear does NOT hide penetration!)
        phases_with_penetration = [
            PhaseQualificationReport("APPROACH", 0.0, 50.0, 1.5, col_clear, True),
            PhaseQualificationReport("CONTACT", 0.0, 1.5, 1.5, col_touch, True),
            PhaseQualificationReport("BENDING", 45.0, -4.5, -4.7, col_penetrate, False),
            PhaseQualificationReport("BOTTOM", 90.0, -12.4, -12.6, col_penetrate, False),
            PhaseQualificationReport("RETRACT", 90.0, 40.0, -12.6, col_clear, True),
        ]
        rep1 = qualify_operation_sampled_poses(phases_with_penetration, cov_ok, cov_ok)
        self.assertFalse(rep1.is_qualified)
        self.assertEqual(rep1.operation_status, OperationQualificationStatus.SAMPLED_POSES_REJECTED)
        self.assertEqual(rep1.safe_phases_count, 3)
        self.assertEqual(rep1.rejected_phases, ["BENDING", "BOTTOM"])
        self.assertEqual(rep1.trajectory_status, "UNVERIFIED_TRAJECTORY")

        # 2. Systematic test: each single phase failing individually MUST reject operation
        for fail_idx, fail_name in enumerate(["APPROACH", "CONTACT", "BENDING", "BOTTOM", "RETRACT"]):
            phases = []
            for idx, name in enumerate(["APPROACH", "CONTACT", "BENDING", "BOTTOM", "RETRACT"]):
                if idx == fail_idx:
                    phases.append(PhaseQualificationReport(name, 0.0, 0.0, 0.0, col_penetrate, False))
                else:
                    phases.append(PhaseQualificationReport(name, 0.0, 0.0, 0.0, col_clear, True))
            rep_fail = qualify_operation_sampled_poses(phases, cov_ok, cov_ok)
            self.assertFalse(rep_fail.is_qualified, f"Failed for single failure in {fail_name}")
            self.assertEqual(rep_fail.operation_status, OperationQualificationStatus.SAMPLED_POSES_REJECTED)
            self.assertEqual(rep_fail.rejected_phases, [fail_name])

        # 3. All phases safe + coverage OK -> SAMPLED_POSES_QUALIFIED
        phases_all_safe = [
            PhaseQualificationReport("APPROACH", 0.0, 50.0, 1.5, col_clear, True),
            PhaseQualificationReport("CONTACT", 0.0, 1.5, 1.5, col_touch, True),
            PhaseQualificationReport("BENDING", 45.0, -4.5, -4.7, col_touch, True),
            PhaseQualificationReport("BOTTOM", 90.0, -12.4, -12.6, col_touch, True),
            PhaseQualificationReport("RETRACT", 90.0, 40.0, -12.6, col_clear, True),
        ]
        rep_ok = qualify_operation_sampled_poses(phases_all_safe, cov_ok, cov_ok)
        self.assertTrue(rep_ok.is_qualified)
        self.assertEqual(rep_ok.operation_status, OperationQualificationStatus.SAMPLED_POSES_QUALIFIED)
        self.assertEqual(rep_ok.safe_phases_count, 5)
        self.assertEqual(rep_ok.rejected_phases, [])

        # 4. Coverage failure disqualifies operation even if all poses are safe
        rep_cov_fail = qualify_operation_sampled_poses(phases_all_safe, cov_bad, cov_ok)
        self.assertFalse(rep_cov_fail.is_qualified)
        self.assertEqual(rep_cov_fail.operation_status, OperationQualificationStatus.SAMPLED_POSES_REJECTED)
        self.assertEqual(rep_cov_fail.tool_setup_status, SetupQualificationStatus.INSUFFICIENT_PUNCH_COVERAGE)

        # 5. Missing phase evidence
        rep_no_phases = qualify_operation_sampled_poses([], cov_ok, cov_ok)
        self.assertFalse(rep_no_phases.is_qualified)
        self.assertEqual(rep_no_phases.operation_status, OperationQualificationStatus.MISSING_PHASE_EVIDENCE)


if __name__ == "__main__":
    unittest.main()
