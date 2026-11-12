"""Headless regression: manual angle scrubbing drives the *real* coupled ram.

These tests do not need FreeCAD, a GUI, or an OCC boolean. They confirm the
new explicit preview API without changing PressCycleController.set_manual_angle
(parking semantics used by other callers).
"""
from __future__ import annotations

import unittest

from smbs_core.kinematics2d.coupled_kinematics_2d import CoupledAirBendingKinematics
from smbs_core.kinematics2d.fold_sequence import PlaybackStatus
from smbs_core.kinematics2d.press_cycle import (
    CyclePhase, PressCycleController, PressCycleError,
)
from smbs_core.kinematics2d.tooling_25d import ToolingSetup2D


class _Sequence:
    status = PlaybackStatus.IDLE

    def __init__(self, target=90.0):
        self.target_angle_deg = target
        self.current_angle_deg = 0.0
        self.order = ("B01", "B02")

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
