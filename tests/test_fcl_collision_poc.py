"""D6 FCL PoC unit contract: works with no FreeCAD/python-fcl installed."""
import unittest
from unittest.mock import patch

from smbs_core.kinematics2d.fcl_collision_poc import (
    FCLStatus, FCLCheck, check_static_pair, benchmark_fcl_pair, build_fcl_box,
)


class FCLContracts(unittest.TestCase):
    def test_no_fcl_backend_fails_closed(self):
        with patch(
            "smbs_core.kinematics2d.fcl_collision_poc._require_runtime",
            side_effect=RuntimeError("python-fcl missing"),
        ):
            got = check_static_pair(object(), object())
        self.assertEqual(got.status, FCLStatus.UNVERIFIED)
        self.assertIn("python-fcl missing", got.reason)

    def test_negative_clearance_fails_closed(self):
        result = check_static_pair(object(), object(), minimum_clearance_mm=-1)
        self.assertEqual(result.status, FCLStatus.UNVERIFIED)

    def test_repeatability_detects_status_flap(self):
        seq = [
            FCLCheck(FCLStatus.COLLISION, "hit", None, 0.1),
            FCLCheck(FCLStatus.UNVERIFIED, "unknown", None, 0.2),
            FCLCheck(FCLStatus.COLLISION, "hit", None, 0.1),
        ]
        with patch(
            "smbs_core.kinematics2d.fcl_collision_poc.check_static_pair",
            side_effect=seq,
        ):
            report = benchmark_fcl_pair(
                object(), object(), expected=FCLStatus.COLLISION, iterations=3,
            )
        self.assertFalse(report["pass"])
        self.assertEqual(report["failed_iterations"], 1)
        self.assertEqual(report["statuses"]["UNVERIFIED"], 1)

    def test_repeatability_passes_reliable_clear(self):
        with patch(
            "smbs_core.kinematics2d.fcl_collision_poc.check_static_pair",
            return_value=FCLCheck(FCLStatus.CLEAR, "distance", 20.0, 0.1),
        ):
            result = benchmark_fcl_pair(
                object(), object(), expected=FCLStatus.CLEAR, iterations=12,
            )
        self.assertTrue(result["pass"])
        self.assertEqual(result["min_distance_mm"], 20.0)
        self.assertEqual(result["max_distance_mm"], 20.0)

    def test_reject_invalid_iterations(self):
        with self.assertRaises(ValueError):
            benchmark_fcl_pair(object(), object(),
                               expected=FCLStatus.COLLISION, iterations=0)

    def test_statuses_are_explicit(self):
        self.assertEqual({x.value for x in FCLStatus},
                         {"CLEAR", "COLLISION", "UNVERIFIED"})

    def test_optional_real_fcl_if_available(self):
        try:
            import fcl
            import numpy
        except (ImportError, OSError):
            self.skipTest("python-fcl not in host environment")
        a = build_fcl_box((10,10,10), (5,5,5))
        disjoint = build_fcl_box((10,10,10), (45,5,5))
        overlap = build_fcl_box((10,10,10), (13,5,5))
        self.assertEqual(check_static_pair(a, disjoint).status, FCLStatus.CLEAR)
        self.assertEqual(check_static_pair(a, overlap).status, FCLStatus.COLLISION)


if __name__ == "__main__":
    unittest.main()
