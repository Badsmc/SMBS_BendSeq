"""Stage X0: prove extra native wall-time timing has no physics side effect.

The logged regions are adjacent, never nested inside full-sheet union or
native kernel Common/distance measurement. This test runs without FreeCAD.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.native_geometry_build_metrics import (
    count_native_geometry_builds, measure_native_geometry_region,
)
from smbs_core.kinematics2d.native_collision_3d import (
    CollisionClass, check_pair_clearance,
)


def bounds(xmin, xmax):
    return SimpleNamespace(
        XMin=xmin, XMax=xmax, YMin=0.0, YMax=1.0,
        ZMin=0.0, ZMax=1.0,
    )


class DummySolid:
    def __init__(self, xmin, xmax, valid=True):
        self.BoundBox = bounds(xmin, xmax)
        self.Volume = max(xmax - xmin, .01)
        self.valid = valid
        self.calls = 0

    def isNull(self):
        return False

    def isValid(self):
        self.calls += 1
        return self.valid

    def isClosed(self):
        return True


class ExtendedTimingContracts(unittest.TestCase):
    def test_pair_geometry_sanity_does_not_change_safe_status(self):
        sheet, tool = DummySolid(0, 1), DummySolid(5, 6)
        plain = check_pair_clearance(sheet, tool)
        with count_native_geometry_builds() as metrics:
            measured = check_pair_clearance(sheet, tool)
        self.assertEqual(measured.status, plain.status)
        self.assertEqual(measured.reason, plain.reason)
        self.assertEqual(measured.status, CollisionClass.CLEAR)
        self.assertEqual(metrics.calls.get("pair_geometry_sanity"), 2)
        self.assertGreaterEqual(
            metrics.elapsed_s.get("pair_geometry_sanity", -1), 0
        )
        self.assertIn("machine_workpiece_transform",
                      metrics.to_dict()["calls"])
        self.assertEqual(metrics.calls.get("sheet_qualification", 0), 0)

    def test_invalid_native_solid_still_fails_closed(self):
        sheet, tool = DummySolid(0, 1, valid=False), DummySolid(5, 6)
        with count_native_geometry_builds() as metrics:
            result = check_pair_clearance(sheet, tool)
        self.assertEqual(result.status, CollisionClass.INVALID_GEOMETRY)
        self.assertEqual(metrics.calls.get("pair_geometry_sanity"), 1)

    def test_new_regions_count_without_ever_changing_result(self):
        with count_native_geometry_builds() as metrics:
            for kind in ("machine_workpiece_copy",
                         "machine_workpiece_transform",
                         "native_punch_pose"):
                with measure_native_geometry_region(kind):
                    pass
        self.assertEqual(
            sum(metrics.calls.get(label, 0) for label in (
                "machine_workpiece_copy",
                "machine_workpiece_transform", "native_punch_pose"
            )), 3,
        )
        self.assertGreaterEqual(metrics.to_dict()["measured_total_s"], 0)

    def test_unknown_region_is_rejected_instead_of_silently_disappearing(self):
        with self.assertRaisesRegex(ValueError, "unknown geometry"):
            with measure_native_geometry_region("invented_category"):
                pass


if __name__ == "__main__":
    unittest.main()
