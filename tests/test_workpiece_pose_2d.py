"""Pure-Python regression tests for the independent machine-space workpiece pose."""
from __future__ import annotations

import math
import unittest

from smbs_core.kinematics2d.workpiece_pose_2d import (
    WorkpiecePose2D,
    WorkpiecePoseError,
    symmetric_v_pose,
)


def _section(angle_deg: float):
    a = math.radians(angle_deg)
    return {
        "fixed_polygon": ((-50.0, 0.0), (-1.0, 0.0), (-1.0, -2.0), (-50.0, -2.0)),
        "moving_polygon": (
            (1.0, 0.0),
            (1.0 + 49.0 * math.cos(a), 49.0 * math.sin(a)),
            (1.0 + 49.0 * math.cos(a), 49.0 * math.sin(a) - 2.0),
            (1.0, -2.0),
        ),
        "neutral_curve": ((-1.0, -1.0), (0.0, -1.0), (1.0, -1.0)),
        "target_angle_deg": angle_deg,
    }


class WorkpiecePoseTests(unittest.TestCase):
    def test_inverse_round_trip(self):
        pose = WorkpiecePose2D(pivot=(2, -1), rotation_deg=-31, mirror_z=True, shift=(7, 5))
        for point in ((-2, 12), (8, -9), (2, -1)):
            reconstructed = pose.inverse(pose.apply(point))
            self.assertAlmostEqual(point[0], reconstructed[0], places=9)
            self.assertAlmostEqual(point[1], reconstructed[1], places=9)

    def test_symmetric_flange_rise_and_distance(self):
        for angle in (0, 20, 45, 90, -20, -45, -90):
            with self.subTest(angle=angle):
                section = _section(angle)
                pose = symmetric_v_pose(section)
                fixed, moving = section["fixed_polygon"], section["moving_polygon"]
                far_left, near_left = pose.apply(fixed[0]), pose.apply(fixed[1])
                near_right, far_right = pose.apply(moving[0]), pose.apply(moving[1])
                left_elevation = math.atan2(
                    far_left[1] - near_left[1], near_left[0] - far_left[0]
                )
                right_elevation = math.atan2(
                    far_right[1] - near_right[1], far_right[0] - near_right[0]
                )
                self.assertAlmostEqual(left_elevation, right_elevation, places=8)
                self.assertAlmostEqual(left_elevation, math.radians(abs(angle)) / 2, places=8)
                self.assertAlmostEqual(
                    math.dist(fixed[0], fixed[1]),
                    math.dist(far_left, near_left),
                    places=8,
                )

    def test_invalid_flange_rejected(self):
        sec = _section(20)
        sec["fixed_polygon"] = ((0, 0), (0, 0), (0, -1), (0, -1))
        with self.assertRaises(WorkpiecePoseError):
            symmetric_v_pose(sec)


if __name__ == "__main__":
    unittest.main()
