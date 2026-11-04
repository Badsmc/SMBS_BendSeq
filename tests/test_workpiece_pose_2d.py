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
    upper_zone_at_machine_axis,
    animated_machine_pose_for_cycle,
    remap_section_to_reference,
    interpolate_rigid_placement,
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
                fixed_near, fixed_far = pose.apply(fixed[1]), pose.apply(fixed[0])
                moving_near, moving_far = pose.apply(moving[0]), pose.apply(moving[1])
                # A negative bend is presented by a rigid 180° workpiece
                # turn: fixed/moving swap LEFT/RIGHT sides in machine space.
                rays = ((fixed_near, fixed_far), (moving_near, moving_far))
                rises = []
                for near, far in rays:
                    self.assertGreater(far[1]-near[1], -1e-7)
                    rises.append(math.atan2(far[1]-near[1], abs(far[0]-near[0])))
                self.assertAlmostEqual(rises[0], rises[1], places=8)
                self.assertAlmostEqual(rises[0], math.radians(abs(angle)) / 2, places=8)
                self.assertAlmostEqual(
                    math.dist(fixed[0], fixed[1]),
                    math.dist(fixed_far, fixed_near),
                    places=8,
                )
                self.assertFalse(pose.mirror_z)
                if angle < 0:
                    self.assertIn("RIGID_180_DEG", pose.mode)
                    self.assertGreater(fixed_far[0], fixed_near[0])
                    self.assertLess(moving_far[0], moving_near[0])

    def test_folded_neighboring_panels_do_not_require_horizontal_start(self):
        # Reproduce the screenshot's second-operation condition: existing
        # FoldState transforms have already rotated the two active flange rays.
        for angle in (45.0, 90.0, -45.0, -90.0):
            with self.subTest(angle=angle):
                section = _section(angle)
                initial_pose = symmetric_v_pose(section)
                rotated = transform_sheet_section(
                    section, WorkpiecePose2D(rotation_deg=73.0)
                )
                actual_pose = symmetric_v_pose(rotated)
                for key in ("fixed_polygon", "moving_polygon"):
                    expected = initial_pose.points(section[key])
                    observed = actual_pose.points(rotated[key])
                    for a_pt, b_pt in zip(expected, observed):
                        self.assertAlmostEqual(a_pt[0], b_pt[0], places=7)
                        self.assertAlmostEqual(a_pt[1], b_pt[1], places=7)

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

    def test_die_shoulder_support_prevents_floating(self):
        class Identity:
            def apply(self, p):
                return tuple(p)

        class Die:
            die_datums = type("Datums", (), {
                "left_shoulder": (-16.0, -2.0),
                "right_shoulder": (16.0, -2.0),
            })()
            def source_to_local_transform(self):
                return Identity()

        class Tooling:
            aligned = True
            die = Die()
            die_machine_transform = Identity()
            alignment_anchor_sz = (0.0, -2.0)
            def die_v_center_machine(self):
                return (0.0, -2.0)

        class Cycle:
            contact_z_mm = 0.0
            bottom_z_mm = -4.0
            ram_z_mm = 5.0
            phase = "APPROACH"

        from smbs_core.kinematics2d.workpiece_pose_2d import _lower_sheet_at_s
        tooling = Tooling()
        cycle = Cycle()
        for angle in (0.0, 30.0, 60.0, 90.0):
            with self.subTest(angle=angle):
                sec = _section(angle)
                sec["zone_polygon"] = (
                    (-1., 0.), (1., 0.), (1., -2.), (-1., -2.),
                )
                pose = machine_pose_for_cycle(sec, cycle, tooling)
                self.assertIn("SHOULDER_SUPPORTED_PREVIEW", pose.mode)
                left = _lower_sheet_at_s(sec, pose, -16.0)
                right = _lower_sheet_at_s(sec, pose, 16.0)
                self.assertIsNotNone(left)
                self.assertIsNotNone(right)
                self.assertGreaterEqual(left + 1e-7, -2.0)
                self.assertGreaterEqual(right + 1e-7, -2.0)
                self.assertAlmostEqual(min(left, right), -2.0, places=6)

    def test_one_missing_shoulder_reports_error(self):
        class Identity:
            def apply(self, p):
                return tuple(p)
        class Die:
            die_datums = type("Datums", (), {
                "left_shoulder": (-250.0, -2.0),
                "right_shoulder": (16.0, -2.0),
            })()
            def source_to_local_transform(self):
                return Identity()
        class Tooling:
            aligned = True
            die = Die()
            die_machine_transform = Identity()
            alignment_anchor_sz = (0.0, -2.0)
            def die_v_center_machine(self):
                return (0.0, -2.0)
        class Cycle:
            contact_z_mm = 0.0
            bottom_z_mm = -4.0
            ram_z_mm = 5.0
            phase = "APPROACH"
        section = _section(45.0)
        section["zone_polygon"] = ((-1., 0.), (1., 0.), (1., -2.), (-1., -2.))
        pose = machine_pose_for_cycle(section, Cycle(), Tooling())
        self.assertIn("PARTIAL_SHOULDER_SUPPORTED_PREVIEW", pose.mode)

    def test_two_supports_retain_relative_flange_angle(self):
        class Identity:
            def apply(self, p):
                return tuple(p)
        class Die:
            die_datums = type("Datums", (), {
                "left_shoulder": (-16.0, -2.0),
                "right_shoulder": (16.0, -2.0),
            })()
            def source_to_local_transform(self):
                return Identity()
        class Tooling:
            aligned = True
            die = Die()
            die_machine_transform = Identity()
            alignment_anchor_sz = (0.0, -2.0)
            def die_v_center_machine(self):
                return (0.0, -2.0)
        class Cycle:
            contact_z_mm = 0.0
            bottom_z_mm = -4.0
            ram_z_mm = -2.0
            phase = "BENDING"

        sec = _section(45.0)
        sec["zone_polygon"] = ((-1., 0.), (1., 0.), (1., -2.), (-1., -2.))
        source = repr(sec)
        pose = machine_pose_for_cycle(sec, Cycle(), Tooling())
        self.assertIn("SHOULDER_SUPPORTED_PREVIEW", pose.mode)
        left = pose.points((sec["fixed_polygon"][1], sec["fixed_polygon"][0]))
        right = pose.points((sec["moving_polygon"][0], sec["moving_polygon"][1]))
        left_ray = (left[1][0]-left[0][0], left[1][1]-left[0][1])
        right_ray = (right[1][0]-right[0][0], right[1][1]-right[0][1])
        dot = left_ray[0]*right_ray[0]+left_ray[1]*right_ray[1]
        angle = math.degrees(math.acos(dot/(math.hypot(*left_ray)*math.hypot(*right_ray))))
        self.assertAlmostEqual(angle, 180.0 - 45.0, places=6)
        self.assertEqual(repr(sec), source)

    def test_contact_support_does_not_spoil_symmetric_flange_rise(self):
        class Identity:
            def apply(self, p):
                return tuple(p)
        class Die:
            die_datums = type("Datums", (), {
                "left_shoulder": (-16.0, -2.0),
                "right_shoulder": (16.0, -2.0),
            })()
            def source_to_local_transform(self):
                return Identity()
        class Tooling:
            aligned = True
            die = Die()
            die_machine_transform = Identity()
            alignment_anchor_sz = (0.0, -2.0)
            def die_v_center_machine(self):
                return (0.0, -2.0)
        class Cycle:
            contact_z_mm = 0.0
            bottom_z_mm = -4.0
            ram_z_mm = 5.0
            phase = "APPROACH"
        for angle in (0, 15, 30, 45, 60, 90, -45, -90):
            with self.subTest(angle=angle):
                sec = _section(angle)
                sec["zone_polygon"] = ((-1., 0.), (1., 0.), (1., -2.), (-1., -2.))
                pose = machine_pose_for_cycle(sec, Cycle(), Tooling())
                expected_rotation = (
                    -180.0 + abs(angle) / 2.0 if angle < 0 else -angle / 2.0
                )
                self.assertAlmostEqual(pose.rotation_deg, expected_rotation, places=7)
                fixed = pose.points((sec["fixed_polygon"][1], sec["fixed_polygon"][0]))
                moving = pose.points((sec["moving_polygon"][0], sec["moving_polygon"][1]))
                rises = [
                    math.atan2(far[1]-near[1], abs(far[0]-near[0]))
                    for near, far in (fixed, moving)
                ]
                self.assertAlmostEqual(rises[0], rises[1], places=7)
                self.assertGreaterEqual(rises[0], -1e-7)


    def test_punch_contact_samples_bend_axis_not_maximum_zone(self):
        zone = (
            (-12.0, 4.0), (0.0, 0.0), (12.0, 4.0),
            (12.0, -2.0), (0.0, -2.0), (-12.0, -2.0),
        )
        sec = {"zone_polygon": zone}
        pose = WorkpiecePose2D(shift=(25.0, -3.0))
        z = upper_zone_at_machine_axis(sec, pose, 25.0)
        self.assertAlmostEqual(z, -3.0)
        # Earlier max(zone.z) = 4 would have parked the punch 4 mm
        # above the actual material directly beneath the punch tip.
        self.assertAlmostEqual(max(p[1] for p in pose.points(zone)), 1.0)
        with self.assertRaisesRegex(WorkpiecePoseError, "does not cross"):
            upper_zone_at_machine_axis(sec, pose, 1000.0)

    def test_animated_transfer_moves_all_flange_geometry_rigidly(self):
        class Cycle:
            contact_z_mm = 0.0
            bottom_z_mm = -4.0
            ram_z_mm = 5.0
            phase = "TRANSFER"
            transfer_progress = 0.5
            transfer_lift_mm = 12.0
        class Tooling:
            aligned = False
        src = _section(90.0)
        dst = _section(-90.0)
        for section in (src, dst):
            section["zone_polygon"] = ((-1.,0.),(1.,0.),(1.,-2.),(-1.,-2.))
        start = machine_pose_for_cycle(src, Cycle(), Tooling())
        end = machine_pose_for_cycle(dst, Cycle(), Tooling())
        mid = animated_machine_pose_for_cycle(src, Cycle(), Tooling(), dst)
        self.assertIn("RIGID_CONTINUOUS", mid.mode)
        self.assertGreater(mid.apply((0.,0.))[1], min(
            start.apply((0.,0.))[1],end.apply((0.,0.))[1]
        ))
        self.assertAlmostEqual(
            math.dist(*mid.points(src["fixed_polygon"][:2])),
            math.dist(*start.points(src["fixed_polygon"][:2])),
            places=8,
        )
        self.assertEqual(
            interpolate_rigid_placement(start,end,0.0),start
        )
        self.assertEqual(
            interpolate_rigid_placement(start,end,1.0),end
        )
        with self.assertRaisesRegex(WorkpiecePoseError, "TRANSFER_TARGET_MISSING"):
            animated_machine_pose_for_cycle(src, Cycle(), Tooling())

    def test_reference_section_mapping_reproduces_shared_world_axis(self):
        class Zone:
            def __init__(self, x):
                self.origin_xy=(x, 0.0)
                self.left_xy=(1.0, 0.0)

        ref=Zone(10.0)
        active=Zone(70.0)
        section=_section(0.0)
        # s_active=5 mm => x_world=75 => s_ref=65 mm.
        projected=remap_section_to_reference(
            section, (active,1.0),(ref,1.0)
        )
        self.assertAlmostEqual(
            projected["fixed_polygon"][0][0],
            section["fixed_polygon"][0][0]+60.0,
        )
        # Reversed projection axis corresponds to the SAME physical point.
        reverse=remap_section_to_reference(
            section,(active,-1.0),(ref,1.0)
        )
        self.assertAlmostEqual(reverse["fixed_polygon"][0][0],
                               -section["fixed_polygon"][0][0]+60.0)
        self.assertEqual(projected["target_angle_deg"],0.0)
        self.assertEqual(section["fixed_polygon"][0],(-50.0,0.0))

    def test_invalid_flange_rejected(self):
        sec = _section(20)
        sec["fixed_polygon"] = ((0, 0), (0, 0), (0, -1), (0, -1))
        with self.assertRaises(WorkpiecePoseError):
            symmetric_v_pose(sec)


if __name__ == "__main__":
    unittest.main()
