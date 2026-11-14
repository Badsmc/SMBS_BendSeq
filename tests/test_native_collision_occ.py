"""Real OCC acceptance. Skips explicitly if genuine FreeCAD is unavailable.

Run in FreeCAD Python: python -m unittest tests.test_native_collision_occ -v
No mocked kernel, shape validity, distance or Common is used in these tests.
"""
import unittest

try:
    import FreeCAD as App
    import Part
except ImportError:
    App = Part = None

from smbs_core.kinematics2d.native_collision_3d import (
    CollisionClass, EndZoneCollisionPolicy, PunchCollisionZoneClassification,
    check_pair_clearance, classify_punch_overlap_zones, validate_workpiece_tooling_pose,
)


@unittest.skipIf(Part is None, 'requires genuine FreeCAD/OCC')
class NativeCollisionOCCTests(unittest.TestCase):
    def setUp(self):
        self.punch = Part.makeBox(100, 10, 10)
        self.die = Part.makeBox(100, 10, 10, App.Vector(0, 0, -30))
        self.policy = EndZoneCollisionPolicy(True, 10.)

    def report(self, workpiece, policy=None):
        return validate_workpiece_tooling_pose(
            workpiece, self.punch, self.die,
            end_zone_policy=self.policy if policy is None else policy)

    def test_end_zone_remains_conditional_with_one_common(self):
        sheet = Part.makeBox(2, 2, 2, App.Vector(1, 1, 1))
        result = self.report(sheet)
        self.assertTrue(result.is_conditional_safe)
        self.assertFalse(result.is_safe)
        self.assertIs(result.status, CollisionClass.PENETRATION)
        self.assertEqual(result.punch_result.common_queries_count, 1)
        self.assertAlmostEqual(result.punch_result.overlap_volume_mm3, 8.)
        self.assertIs(result.punch_end_zone_analysis.classification,
                      PunchCollisionZoneClassification.END_ZONE_ONLY)
        # Independent public API runs its own input validation and Common.
        standalone = classify_punch_overlap_zones(sheet, self.punch, 10.)
        self.assertEqual(standalone.to_dict(), result.punch_end_zone_analysis.to_dict())

    def test_central_containment_is_forbidden_even_with_positive_surface_distance(self):
        sheet = Part.makeBox(2, 2, 2, App.Vector(50, 1, 1))
        self.assertGreater(sheet.Shells[0].distToShape(self.punch.Shells[0])[0], 0.)
        result = self.report(sheet)
        self.assertIs(result.status, CollisionClass.PENETRATION)
        self.assertFalse(result.is_conditional_safe)
        self.assertAlmostEqual(result.punch_result.overlap_volume_mm3, 8.)

    def test_same_shape_object_mutation_is_revalidated(self):
        sheet = Part.makeBox(2, 2, 2, App.Vector(1, 1, 1))
        self.assertTrue(self.report(sheet).is_conditional_safe)
        sheet.translate(App.Vector(49, 0, 0))
        result = self.report(sheet)
        self.assertFalse(result.is_conditional_safe)
        self.assertIs(result.punch_end_zone_analysis.classification,
                      PunchCollisionZoneClassification.CENTRAL_ZONE)

    def test_disabled_policy_does_not_permit_end_zone_collision(self):
        sheet = Part.makeBox(2, 2, 2, App.Vector(1, 1, 1))
        result = self.report(sheet, EndZoneCollisionPolicy(False, 10.))
        self.assertFalse(result.is_conditional_safe)
        self.assertIs(result.status, CollisionClass.PENETRATION)

    def test_invalid_policy_and_null_geometry_never_clear(self):
        sheet = Part.makeBox(2, 2, 2, App.Vector(1, 1, 1))
        invalid_policy = self.report(sheet, EndZoneCollisionPolicy(True, 60.))
        self.assertIs(invalid_policy.status, CollisionClass.INVALID_GEOMETRY)
        self.assertFalse(invalid_policy.is_conditional_safe)
        result = self.report(Part.Shape())
        self.assertIs(result.status, CollisionClass.INVALID_GEOMETRY)
        self.assertFalse(result.is_safe)

    def test_die_penetration_is_not_allowed_by_punch_policy(self):
        result = self.report(Part.makeBox(2, 2, 2, App.Vector(1, 1, -29)))
        self.assertIs(result.die_result.status, CollisionClass.PENETRATION)
        self.assertFalse(result.is_conditional_safe)

    def test_aabb_clearance_and_contact_are_distinct(self):
        distant = Part.makeBox(2, 2, 2, App.Vector(101, 0, 0))
        result = check_pair_clearance(distant, self.punch)
        self.assertIs(result.status, CollisionClass.CLEAR)
        self.assertEqual(result.common_queries_count, 0)
        touching = Part.makeBox(2, 2, 2, App.Vector(100, 0, 0))
        self.assertIs(check_pair_clearance(touching, self.punch).status,
                      CollisionClass.TOUCHING)
