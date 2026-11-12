"""Headless regression: manual angle scrubbing drives the *real* coupled ram.

These tests do not need FreeCAD, a GUI, or an OCC boolean. They confirm the
new explicit preview API without changing PressCycleController.set_manual_angle
(parking semantics used by other callers).
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from smbs_core.kinematics2d.coupled_kinematics_2d import CoupledAirBendingKinematics
from smbs_core.kinematics2d.fold_sequence import PlaybackStatus
from smbs_core.kinematics2d.press_cycle import (
    CycleExecutionMode, CyclePhase, PressCycleController, PressCycleError,
)
from smbs_core.kinematics2d.tooling_25d import ToolingSetup2D


class _Sequence:
    status = PlaybackStatus.IDLE

    def __init__(self, target=90.0):
        self.target_angle_deg = target
        self.current_angle_deg = 0.0
        self.order = ("B01", "B02")
        self.active_bend_id = "B01"
        self.fold_state = SimpleNamespace(
            bends={
                "B01": SimpleNamespace(
                    bend_zone=SimpleNamespace(outer_radius_mm=2.5)
                )
            }
        )

    def set_manual_angle(self, value):
        self.current_angle_deg = float(value)


def _cycle(target=90.0):
    # Isolate stroke rendering from machine planning and CAD creation.
    pc = object.__new__(PressCycleController)
    pc.sequence = _Sequence(target)
    pc.tooling = ToolingSetup2D()
    pc.tooling.aligned = True
    pc.tooling.alignment_anchor_sz = (0.0, 0.0)
    pc.coupled_kinematics = CoupledAirBendingKinematics(
        target_bend_angle_deg=target
    )
    pc.contact_z_mm = pc.coupled_kinematics.contact_z_mm
    pc.upper_z_mm = pc.contact_z_mm + 15.0
    pc.ram_z_mm = pc.upper_z_mm
    pc.ram_stroke_mm = 0.0
    pc.dwell_remaining_s = 0.0
    pc.phase = CyclePhase.IDLE
    pc.execution_mode = CycleExecutionMode.FULL_CYCLE
    pc._manual_preview_active = False
    pc.transfer_fault = None
    pc.loading_orientation_by_bend = {}
    pc.rear_panel_vertices_xy = None
    return pc


class PreviewPunchRegression(unittest.TestCase):
    def test_slider_moves_punch_by_coupled_stroke_and_never_moves_die(self):
        pc = _cycle()
        original_die = pc.tooling.die_machine_transform
        strokes = []
        for angle in (0.0, 30.0, 60.0, 90.0):
            pc.set_manual_preview_angle(angle)
            expected = pc.coupled_kinematics.stroke_for_angle(angle)
            self.assertAlmostEqual(pc.ram_stroke_mm, expected, places=6)
            self.assertAlmostEqual(
                pc.tooling.punch_machine_transform.ty,
                pc.contact_z_mm - expected, places=6
            )
            self.assertEqual(pc.tooling.die_machine_transform, original_die)
            self.assertEqual(pc.sequence.order, ("B01", "B02"))
            strokes.append(pc.ram_stroke_mm)
        self.assertEqual(strokes, sorted(strokes))
        self.assertGreater(strokes[-1], strokes[0])

    def test_sheet_support_and_punch_share_stroke_at_every_slider_angle(self):
        pc = _cycle()
        fixed_die = pc.tooling.die_machine_transform
        heights = []
        punch_heights = []
        for angle in (0.0, 22.5, 45.0, 70.0, 90.0):
            pc.set_manual_preview_angle(angle)
            self.assertTrue(pc._manual_preview_active)
            self.assertEqual(pc.phase, CyclePhase.IDLE)  # Manual, not auto
            stroke = pc.coupled_kinematics.stroke_for_angle(angle)
            support = pc.preview_workpiece_support_zm()
            self.assertAlmostEqual(support, 2.5 - stroke, places=6)
            self.assertAlmostEqual(
                pc.tooling.ram_z_mm - support,
                pc.contact_z_mm - 2.5, places=6
            )
            heights.append(support)
            punch_heights.append(pc.tooling.ram_z_mm)
            self.assertEqual(pc.tooling.die_machine_transform, fixed_die)
            with patch(
                "smbs_core.kinematics2d.press_cycle.WorkpiecePlacement3D.compute_placement",
                return_value="test-placement"
            ) as place:
                self.assertEqual(
                    pc.current_workpiece_placement_3d(), "test-placement"
                )
                self.assertAlmostEqual(
                    place.call_args.kwargs["support_plane_zm"], support,
                    places=6
                )
                self.assertTrue(place.call_args.kwargs["align_bisector"])
                self.assertTrue(place.call_args.kwargs["align_bend_arc_center"])
        self.assertEqual(heights, sorted(heights, reverse=True))
        self.assertEqual(punch_heights, sorted(punch_heights, reverse=True))
        self.assertLess(heights[-1], heights[0])

    def test_manual_preview_position_is_signed_angle_symmetric(self):
        pc = _cycle(-90.0)
        pc.set_manual_preview_angle(-45.0)
        negative_height = pc.preview_workpiece_support_zm()
        positive = _cycle(90.0)
        positive.set_manual_preview_angle(45.0)
        self.assertAlmostEqual(
            negative_height, positive.preview_workpiece_support_zm()
        )

    def test_legacy_manual_setting_clears_manual_preview_placement(self):
        pc = _cycle()
        pc.set_manual_preview_angle(45.0)
        self.assertTrue(pc._manual_preview_active)
        pc.set_manual_angle(40.0)
        self.assertFalse(pc._manual_preview_active)
        self.assertAlmostEqual(pc.tooling.ram_z_mm, pc.upper_z_mm)

    def test_switching_to_bend_only_does_not_separate_sheet_and_punch(self):
        pc = _cycle()
        pc.set_manual_preview_angle(35.0)
        original_z = pc.ram_z_mm
        pc.set_execution_mode(CycleExecutionMode.BEND_ONLY)
        self.assertAlmostEqual(pc.ram_z_mm, original_z)
        self.assertAlmostEqual(
            pc.preview_workpiece_support_zm(),
            2.5 - pc.coupled_kinematics.stroke_for_angle(35.0),
        )
        self.assertTrue(pc._manual_preview_active)

    def test_tooling_change_reestablishes_punch_preview(self):
        pc = _cycle()
        pc.set_manual_preview_angle(35.0)
        original_z = pc.ram_z_mm
        with patch.object(
            pc, "_sync_bounds_and_orientation_for_active_bend"
        ) as resync:
            pc.on_tooling_changed()
            resync.assert_called_once_with(reposition_tools=True)
        self.assertAlmostEqual(pc.ram_z_mm, original_z)

    def test_missing_bend_zone_never_fabricates_sheet_height(self):
        pc = _cycle()
        pc.sequence.fold_state.bends["B01"].bend_zone = None
        with self.assertRaises(PressCycleError):
            pc.preview_workpiece_support_zm()

    def test_negative_bend_uses_same_stroke_magnitude(self):
        pc = _cycle(target=-90.0)
        pc.set_manual_preview_angle(-45.0)
        self.assertAlmostEqual(
            pc.ram_stroke_mm,
            pc.coupled_kinematics.stroke_for_angle(45.0),
            places=6,
        )

    def test_legacy_manual_angle_still_parks_punch(self):
        pc = _cycle()
        pc.set_manual_angle(45.0)
        self.assertAlmostEqual(pc.tooling.ram_z_mm, pc.upper_z_mm)
        self.assertAlmostEqual(pc.tooling.ram_stroke_mm, 0.0)

    def test_out_of_target_manual_preview_fails_closed(self):
        pc = _cycle()
        pc.sequence.current_angle_deg = 100.0
        with self.assertRaises(PressCycleError):
            pc.preview_punch_at_current_angle()
        self.assertAlmostEqual(pc.ram_z_mm, pc.upper_z_mm)


if __name__ == "__main__":
    unittest.main()
