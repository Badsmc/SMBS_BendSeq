"""Repeatability and fail-closed validity memo for a single native tool pose."""
import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.native_collision_3d import (
    CollisionClass, check_pair_clearance, validate_workpiece_tooling_pose,
)


class FakeShape:
    def __init__(self, x0, x1, *, valid=True):
        self.BoundBox = SimpleNamespace(XMin=x0, XMax=x1, YMin=0, YMax=1, ZMin=0, ZMax=1)
        self.valid = valid
        self.valid_calls = 0
        self.Volume = 1.0

    def isNull(self):
        return False

    def isValid(self):
        self.valid_calls += 1
        return self.valid

    def isClosed(self):
        return True


class SharedWorkpieceSanityTests(unittest.TestCase):
    def test_valid_workpiece_checked_once_for_two_tool_pairs(self):
        sheet, punch, die = FakeShape(0, 1), FakeShape(3, 4), FakeShape(5, 6)
        report = validate_workpiece_tooling_pose(sheet, punch, die)
        self.assertEqual(report.status, CollisionClass.CLEAR)
        self.assertEqual(sheet.valid_calls, 1)
        self.assertEqual(punch.valid_calls, 1)
        self.assertEqual(die.valid_calls, 1)

    def test_invalid_workpiece_still_blocks_both_pairs(self):
        sheet, punch, die = FakeShape(0, 1, valid=False), FakeShape(3, 4), FakeShape(5, 6)
        report = validate_workpiece_tooling_pose(sheet, punch, die)
        self.assertEqual(report.status, CollisionClass.INVALID_GEOMETRY)
        self.assertFalse(report.is_safe)
        self.assertEqual(sheet.valid_calls, 1)
        self.assertEqual(report.punch_result.status, CollisionClass.INVALID_GEOMETRY)
        self.assertEqual(report.die_result.status, CollisionClass.INVALID_GEOMETRY)

    def test_direct_pair_always_validates_without_memo(self):
        sheet, punch = FakeShape(0, 1), FakeShape(3, 4)
        result = check_pair_clearance(sheet, punch)
        self.assertEqual(result.status, CollisionClass.CLEAR)
        self.assertEqual(sheet.valid_calls, 1)

    def test_wrong_object_memo_must_revalidate(self):
        sheet, punch, other = FakeShape(0, 1), FakeShape(3, 4), FakeShape(0, 1)
        result = check_pair_clearance(
            sheet, punch,
            _shared_shape_a_check=(other, (True, "valid")),
        )
        self.assertEqual(result.status, CollisionClass.CLEAR)
        self.assertEqual(sheet.valid_calls, 1)

    def test_single_uncached_pair_keeps_strict_checks(self):
        sheet, punch, die = FakeShape(0, 1), FakeShape(3, 4), FakeShape(5, 6)
        from smbs_core.kinematics2d.native_collision_3d import PairClearanceResult
        existing = PairClearanceResult(CollisionClass.CLEAR, "previous_check", 2.0, 0.0, 2.0, 0.0)
        report = validate_workpiece_tooling_pose(
            sheet, punch, die, cached_die_result=existing,
        )
        self.assertEqual(report.status, CollisionClass.CLEAR)
        self.assertEqual(sheet.valid_calls, 1)
        self.assertEqual(punch.valid_calls, 1)
        self.assertEqual(die.valid_calls, 0)


if __name__ == "__main__":
    unittest.main()
