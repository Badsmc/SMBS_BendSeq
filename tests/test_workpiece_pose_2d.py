"""Pure-Python regression tests for the independent machine-space workpiece pose."""
from __future__ import annotations

import math
import unittest

from smbs_core.kinematics2d.workpiece_pose_2d import (
    WorkpiecePose2D,
    WorkpiecePoseError,
    symmetric_v_pose,
    machine_pose_for_cycle,
    transform_sheet_section,
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

    def test_machine_pose_follows_ram_only_during_bending(self):
        class Cycle:
            contact_z_mm = 0.0
            bottom_z_mm = -4.0
            ram_z_mm = 5.0
            phase = "APPROACH"

        class Tooling:
            aligned = True
            alignment_anchor_sz = (25.0, -2.0)

            def die_v_center_machine(self):
                return (25.0, -2.0)

        sec = _section(90.0)
        # Actual finite-radius zone envelope is a separate geometry,
        # unlike the flange endpoints, and supplies the active contact datum.
        sec["zone_polygon"] = (
            (-1.0, -0.5), (0.0, 0.0), (1.0, -0.5),
            (1.0, -1.5), (0.0, -2.0), (-1.0, -1.5),
        )
        cycle, tooling = Cycle(), Tooling()
        initial = machine_pose_for_cycle(sec, cycle, tooling)
        self.assertAlmostEqual(initial.apply((0.0, -1.0))[0], 25.0)
        self.assertAlmostEqual(max(z for _s, z in initial.points(sec["zone_polygon"])), 0.0)

        cycle.phase, cycle.ram_z_mm = "CONTACT", 0.0
        contacted = machine_pose_for_cycle(sec, cycle, tooling)
        self.assertEqual(initial.shift, contacted.shift)

        cycle.phase, cycle.ram_z_mm = "BENDING", -2.0
        during = machine_pose_for_cycle(sec, cycle, tooling)
        self.assertAlmostEqual(max(z for _s, z in during.points(sec["zone_polygon"])), -2.0)
        self.assertAlmostEqual(during.shift[1], contacted.shift[1] - 2.0)

        cycle.phase, cycle.ram_z_mm = "BOTTOM", -4.0
        bottom = machine_pose_for_cycle(sec, cycle, tooling)
        self.assertAlmostEqual(max(z for _s, z in bottom.points(sec["zone_polygon"])), -4.0)

        cycle.phase, cycle.ram_z_mm = "RETRACT", 5.0
        retracted = machine_pose_for_cycle(sec, cycle, tooling)
        self.assertEqual(bottom.shift, retracted.shift)

    def test_transforms_entire_sheet_without_mutation(self):
        sec = _section(45.0)
        sec.update({
            "zone_polygon": ((-1.0, 0.0), (1.0, 0.0), (1.0, -2.0), (-1.0, -2.0)),
            "inner_surface": ((-50.0, -2.0), (0.0, -2.0), (50.0, -2.0)),
            "outer_surface": ((-50.0, 0.0), (0.0, 0.0), (50.0, 0.0)),
            "other_panel_polygons": (("B03", ((51.0, 0.0), (55.0, 0.0))),),
            "other_zone_polygons": (("B02", ((50.0, 0.0), (51.0, 0.0))),),
            "other_inner_curves": (((2.0, -2.0), (3.0, -2.0)),),
            "other_outer_curves": (((2.0, 0.0), (3.0, 0.0)),),
            "other_neutral_curves": (((2.0, -1.0), (3.0, -1.0)),),
            "other_tangent_lines": ((((1.0, 0.0), (1.0, -2.0))),),
            "tangent_points_top": ((-1.0, 0.0), (1.0, 0.0)),
            "tangent_points_bottom": ((-1.0, -2.0), (1.0, -2.0)),
        })
        original = repr(sec)
        pose = symmetric_v_pose(sec)
        moved = transform_sheet_section(sec, pose)
        self.assertEqual(repr(sec), original)
        self.assertEqual(moved["angle_deg"] if "angle_deg" in moved else None, None)
        self.assertEqual(moved["fixed_polygon"], pose.points(sec["fixed_polygon"]))
        self.assertEqual(moved["other_panel_polygons"][0][1],
                         pose.points(sec["other_panel_polygons"][0][1]))
        self.assertEqual(moved["other_tangent_lines"][0][0],
                         pose.apply(sec["other_tangent_lines"][0][0]))

    def test_bend_only_identity_pose(self):
        sec = _section(90.0)
        sec["zone_polygon"] = ((-1, 0), (1, 0), (1, -2), (-1, -2))
        sec["inner_surface"] = ((-1, -2), (1, -2))
        sec["outer_surface"] = ((-1, 0), (1, 0))
        result = transform_sheet_section(sec, WorkpiecePose2D())
        for key in ("fixed_polygon", "moving_polygon", "zone_polygon",
                    "inner_surface", "outer_surface", "neutral_curve"):
            self.assertEqual(result[key], sec[key])

    def test_invalid_flange_rejected(self):
        sec = _section(20)
        sec["fixed_polygon"] = ((0, 0), (0, 0), (0, -1), (0, -1))
        with self.assertRaises(WorkpiecePoseError):
            symmetric_v_pose(sec)


if __name__ == "__main__":
    unittest.main()
