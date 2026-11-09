"""Tests for CoupledAirBendingKinematics (Stage D4-P1)."""
from __future__ import annotations

import math
import unittest

from smbs_core.kinematics2d.coupled_kinematics_2d import (
    CoupledAirBendingKinematics,
)


class TestCoupledAirBendingKinematics(unittest.TestCase):
    def setUp(self):
        # Default typical press brake tooling: V=32mm, Rd=2mm, Rp=0.8mm, t=1.5mm, die angle 85 deg, punch angle 30 deg
        self.kin = CoupledAirBendingKinematics(
            die_opening_v_mm=32.0,
            die_shoulder_radius_mm=2.0,
            die_angle_deg=85.0,
            die_v_depth_mm=16.5,
            punch_tip_radius_mm=0.8,
            punch_included_angle_deg=30.0,
            sheet_thickness_mm=1.5,
            sheet_inner_radius_mm=0.8,
            target_bend_angle_deg=90.0,
            die_top_z_mm=0.0,
        )

    def test_stage2_coupling_requires_complete_real_datums(self):
        from types import SimpleNamespace as NS

        zone = NS(thickness_mm=1.5, inner_radius_mm=1.0)
        state = NS(bends={"B01": NS(angle_deg=90.0, bend_zone=zone)})
        die_datums = NS(v_opening_mm=12.0, shoulder_radius_mm=1.0,
                        die_angle_deg=88.0, v_depth_mm=8.0)
        punch_datums = NS(tip_radius_mm=0.8, included_angle_deg=88.0)
        tools = NS(
            die=NS(die_datums=die_datums),
            punch=NS(punch_datums=punch_datums),
            sheet_bottom_z_mm=0.0,
        )
        result = CoupledAirBendingKinematics.from_tooling_and_fold_state(
            tools, state, "B01"
        )
        self.assertEqual(result.tooling_status, "VERIFIED")
        self.assertEqual(result.die_opening_v_mm, 12.0)
        self.assertEqual(result.sheet_thickness_mm, 1.5)

    def test_stage2_missing_die_field_fails_closed(self):
        from types import SimpleNamespace as NS
        state = NS(bends={"B01": NS(
            angle_deg=-45.0, bend_zone=NS(thickness_mm=1.5, inner_radius_mm=1.0)
        )})
        tools = NS(
            die=NS(die_datums=NS(v_opening_mm=12.0,
                                 shoulder_radius_mm=1.0,
                                 die_angle_deg=88.0,
                                 v_depth_mm=None)),
            punch=NS(punch_datums=NS(tip_radius_mm=0.8,
                                      included_angle_deg=88.0)),
            sheet_bottom_z_mm=0.0,
        )
        result = CoupledAirBendingKinematics.from_tooling_and_fold_state(
            tools, state, "B01"
        )
        self.assertNotEqual(result.tooling_status, "VERIFIED")

    def test_stage2_invalid_punch_and_missing_bend_fail_closed(self):
        from types import SimpleNamespace as NS
        tools = NS(
            die=NS(die_datums=NS(v_opening_mm=12.0,
                                 shoulder_radius_mm=1.0,
                                 die_angle_deg=88.0,
                                 v_depth_mm=8.0)),
            punch=NS(punch_datums=NS(tip_radius_mm=float("nan"),
                                      included_angle_deg=88.0)),
            sheet_bottom_z_mm=0.0,
        )
        state = NS(bends={"B01": NS(
            angle_deg=90.0, bend_zone=NS(thickness_mm=1.5, inner_radius_mm=1.0)
        )})
        invalid = CoupledAirBendingKinematics.from_tooling_and_fold_state(
            tools, state, "B01"
        )
        missing = CoupledAirBendingKinematics.from_tooling_and_fold_state(
            tools, state, "DOES_NOT_EXIST"
        )
        self.assertNotEqual(invalid.tooling_status, "VERIFIED")
        self.assertNotEqual(missing.tooling_status, "VERIFIED")

    def test_stroke_at_zero_angle(self):
        h0 = self.kin.stroke_for_angle(0.0)
        self.assertAlmostEqual(h0, 0.0, places=6)
        # At zero angle, support plane is at die shoulder reference plane (Z=0.0)
        self.assertAlmostEqual(self.kin.support_plane_zm(0.0), 0.0, places=6)

    def test_nonlinear_angle_stroke_curve(self):
        h45 = self.kin.stroke_for_angle(45.0)
        h90 = self.kin.stroke_for_angle(90.0)

        self.assertGreater(h45, 0.0)
        self.assertGreater(h90, h45)
        # Non-linear test: tan(beta) grows non-linearly with angle
        # h(90) should NOT equal 2 * h(45)
        self.assertNotAlmostEqual(h90, 2.0 * h45, places=1)
        self.assertTrue(13.5 < h90 < 15.0, f"Expected h90 in (13.5, 15.0), got {h90}")

    def test_angle_inversion_from_stroke(self):
        test_angles = [5.0, 15.0, 30.0, 45.0, 60.0, 75.0, 90.0]
        for ang in test_angles:
            h = self.kin.stroke_for_angle(ang)
            inv_ang = self.kin.angle_for_stroke(h, target_angle_deg=90.0)
            self.assertAlmostEqual(inv_ang, ang, places=4,
                                   msg=f"Inversion failed for angle {ang}: got {inv_ang}")

    def test_die_shoulder_contacts(self):
        # At 0 degrees: contacts are at +/- V/2 = +/- 16.0 mm at Z = die_top_z = 0.0
        c_left, c_right = self.kin.die_shoulder_contacts(0.0)
        self.assertAlmostEqual(c_left[0], -16.0, places=4)
        self.assertAlmostEqual(c_left[1], 0.0, places=4)
        self.assertAlmostEqual(c_right[0], 16.0, places=4)
        self.assertAlmostEqual(c_right[1], 0.0, places=4)

        # At 90 degrees (beta = 45 deg):
        # sc = 16.0 - 2.0 * sin(45) = 16.0 - 1.4142 = 14.5858 mm
        # z = 0.0 - 2.0 * (1 - cos(45)) = -0.5858 mm
        c90_left, c90_right = self.kin.die_shoulder_contacts(90.0)
        self.assertAlmostEqual(c90_left[0], -14.5858, places=3)
        self.assertAlmostEqual(c90_left[1], -0.5858, places=3)
        self.assertAlmostEqual(c90_right[0], 14.5858, places=3)
        self.assertAlmostEqual(c90_right[1], -0.5858, places=3)

    def test_bend_zone_position(self):
        h90 = self.kin.stroke_for_angle(90.0)
        pos90 = self.kin.bend_zone_position(90.0)
        inner = pos90["inner_apex"]
        outer = pos90["outer_apex"]

        self.assertAlmostEqual(inner[0], 0.0, places=6)
        self.assertAlmostEqual(outer[0], 0.0, places=6)
        # Inner apex is at contact_z_mm - h = sheet_thickness - h
        self.assertAlmostEqual(inner[1], 1.5 - h90, places=4)
        # Outer apex is at die_top_z_mm - h = 0.0 - h
        self.assertAlmostEqual(outer[1], -h90, places=4)

    def test_negative_bend_direction(self):
        # Negative bend angles should have symmetrical stroke and apex lowering
        h_pos = self.kin.stroke_for_angle(90.0)
        h_neg = self.kin.stroke_for_angle(-90.0)
        self.assertAlmostEqual(h_pos, h_neg, places=6)

        z_pos = self.kin.support_plane_zm(90.0)
        z_neg = self.kin.support_plane_zm(-90.0)
        self.assertAlmostEqual(z_pos, z_neg, places=6)

        inv_neg = self.kin.angle_for_stroke(h_neg, target_angle_deg=-90.0)
        self.assertAlmostEqual(inv_neg, -90.0, places=4)

    def test_geometric_validation_valid_bend(self):
        h90 = self.kin.stroke_for_angle(90.0)
        ram_z = self.kin.contact_z_mm - h90
        is_valid, status, msg = self.kin.validate_geometric_constraints(90.0, ram_z)
        self.assertTrue(is_valid)
        self.assertEqual(status, "GEOMETRIC_COUPLING_VERIFIED")

    def test_geometric_validation_punch_flank_collision(self):
        # Punch included angle is 30 deg. Max bend angle is 180 - 30 = 150 deg.
        # At 155 deg, punch flank collision occurs
        h155 = self.kin.stroke_for_angle(155.0)
        ram_z = self.kin.contact_z_mm - h155
        is_valid, status, msg = self.kin.validate_geometric_constraints(155.0, ram_z)
        self.assertFalse(is_valid)
        self.assertTrue(status.startswith("PUNCH_FLANK_COLLISION"))

    def test_geometric_validation_die_bottoming(self):
        # Shallow die depth test: 10 mm
        shallow_kin = CoupledAirBendingKinematics(
            die_opening_v_mm=32.0,
            die_shoulder_radius_mm=2.0,
            die_angle_deg=85.0,
            die_v_depth_mm=10.0,
            punch_tip_radius_mm=0.8,
            punch_included_angle_deg=30.0,
            sheet_thickness_mm=1.5,
            sheet_inner_radius_mm=0.8,
            target_bend_angle_deg=90.0,
            die_top_z_mm=0.0,
        )
        h90 = shallow_kin.stroke_for_angle(90.0)
        ram_z = shallow_kin.contact_z_mm - h90
        is_valid, status, msg = shallow_kin.validate_geometric_constraints(90.0, ram_z)
        self.assertFalse(is_valid)
        self.assertTrue(status.startswith("DIE_BOTTOMING_OUT"))

    def test_geometric_validation_die_wall_collision(self):
        # Die depth deep enough (25 mm) so bottoming does not occur before die wall collision
        deep_kin = CoupledAirBendingKinematics(
            die_opening_v_mm=32.0,
            die_shoulder_radius_mm=2.0,
            die_angle_deg=85.0,
            die_v_depth_mm=25.0,
            punch_tip_radius_mm=0.8,
            punch_included_angle_deg=30.0,
            sheet_thickness_mm=1.5,
            sheet_inner_radius_mm=0.8,
            target_bend_angle_deg=100.0,
            die_top_z_mm=0.0,
        )
        # Die included angle is 85 deg, so sheet included angle at 100 deg is 80 deg < 85 deg
        h100 = deep_kin.stroke_for_angle(100.0)
        ram_z = deep_kin.contact_z_mm - h100
        is_valid, status, msg = deep_kin.validate_geometric_constraints(100.0, ram_z)
        self.assertFalse(is_valid)
        self.assertTrue(status.startswith("DIE_WALL_COLLISION"))

    def test_flange_slip_detection(self):
        h90 = self.kin.stroke_for_angle(90.0)
        ram_z = self.kin.contact_z_mm - h90
        # Flange length of 5 mm is less than required span (V/2 = 16 mm)
        is_valid, status, msg = self.kin.validate_geometric_constraints(
            90.0, ram_z, flange_min_len_mm=5.0
        )
        self.assertFalse(is_valid)
        self.assertTrue(status.startswith("UNSUPPORTED_FLANGE"))


if __name__ == "__main__":
    unittest.main()
