"""Regression: automatic inter-bend handling uses the existing deterministic controller.

No Qt, FreeCAD, a second timer or synthetic bend-angle storage.
"""
import unittest

from smbs_core.kinematics2d.panel_graph import PanelGraph, GraphEdge
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.bend_zone import bend_parameters, make_bend_zone
from smbs_core.kinematics2d.fold_sequence import BendSequenceController, PlaybackStatus
from smbs_core.kinematics2d.press_cycle import (
    CycleConfig, CyclePhase, PressCycleController,
)
from smbs_core.kinematics2d.tooling_25d import ToolingSetup2D


class WorkpieceTransferTests(unittest.TestCase):
    def make_cycle(self, enabled=True):
        up = bend_parameters(90.0, 1.0, 1.5, 0.4)
        down = bend_parameters(-90.0, 2.5, 1.5, 0.4)
        one = make_bend_zone("B01", ((0.0, 10.0), (100.0, 10.0)), up)
        two = make_bend_zone("B02", ((0.0, 65.0), (100.0, 65.0)), down)
        edges = (
            GraphEdge("B01", "A", "B", 90.0,
                      ((0.0, 10.0, 0.0), (100.0, 10.0, 0.0)), one),
            GraphEdge("B02", "B", "C", -90.0,
                      ((0.0, 65.0, 0.0), (100.0, 65.0, 0.0)), two),
        )
        state = FoldState(PanelGraph(("A", "B", "C"), edges), "A")
        controller = BendSequenceController(state)
        cycle = PressCycleController(
            controller,
            ToolingSetup2D(),
            config=CycleConfig(bend_rate_deg_s=180.0,
                               initial_clearance_mm=5.0,
                               preview_stroke_mm=4.0,
                               bottom_dwell_s=0.0),
            animate_workpiece_transfer=enabled,
        )
        return cycle, state

    def reach_transfer(self, cycle):
        cycle.play_all()
        for _ in range(100):
            if cycle.phase == CyclePhase.TRANSFER:
                return
            cycle.advance_time(0.1)
        self.fail("Transfer stage not entered")

    def test_transfer_keeps_completed_angles_and_stationary_punch(self):
        cycle, state = self.make_cycle()
        self.reach_transfer(cycle)
        self.assertAlmostEqual(state.angles["B01"], 90.0)
        self.assertAlmostEqual(state.angles["B02"], 0.0)
        self.assertEqual(cycle.active_bend_id, "B01")
        self.assertEqual(cycle.sequence.status, PlaybackStatus.PLAYING)
        self.assertAlmostEqual(cycle.ram_z_mm, cycle.upper_z_mm)
        self.assertAlmostEqual(cycle.transfer_feed_mm, 55.0)
        self.assertAlmostEqual(abs(cycle.transfer_target_feed_mm), 55.0)
        initial_z = cycle.ram_z_mm
        progress_samples = []
        for _ in range(8):
            cycle.advance_time(0.1)
            progress_samples.append(cycle.transfer_progress)
            self.assertAlmostEqual(cycle.ram_z_mm, initial_z)
            self.assertAlmostEqual(state.angles["B01"], 90.0)
            self.assertAlmostEqual(state.angles["B02"], 0.0)
        self.assertTrue(all(
            nxt > prev for prev,nxt in zip(progress_samples,progress_samples[1:])
        ))
        self.assertGreater(cycle.transfer_lift_mm, 0.0)
        self.assertAlmostEqual(abs(cycle.transfer_rotation_deg), 180.0)

    def test_signed_transfer_feed_comes_from_actual_bend_axes(self):
        cycle, _state = self.make_cycle()
        self.reach_transfer(cycle)
        self.assertAlmostEqual(cycle.transfer_feed_mm, 55.0)
        self.assertAlmostEqual(abs(cycle.transfer_target_feed_mm), 55.0)
        self.assertEqual(cycle.active_bend_id, "B01")


    def test_transfer_pauses_and_resumes_without_reset(self):
        cycle, state = self.make_cycle()
        self.reach_transfer(cycle)
        for _ in range(6):
            cycle.advance_time(0.1)
        previous = cycle.transfer_progress
        cycle.pause()
        for _ in range(8):
            self.assertFalse(cycle.advance_time(0.1))
        self.assertEqual(cycle.transfer_progress, previous)
        cycle.resume()
        self.assertEqual(cycle.phase, CyclePhase.TRANSFER)
        self.assertEqual(cycle.sequence.status, PlaybackStatus.PLAYING)
        cycle.advance_time(0.1)
        self.assertGreater(cycle.transfer_progress, previous)
        for _ in range(30):
            if cycle.phase != CyclePhase.TRANSFER:
                break
            cycle.advance_time(0.1)
        self.assertEqual(cycle.phase, CyclePhase.APPROACH)
        self.assertEqual(cycle.active_bend_id, "B02")
        self.assertAlmostEqual(state.angles["B01"], 90.0)
        self.assertAlmostEqual(state.angles["B02"], 0.0)

    def test_transfer_is_opt_in_for_legacy_headless_cycle(self):
        cycle, state = self.make_cycle(enabled=False)
        cycle.play_all()
        phases = []
        for _ in range(100):
            phases.append(cycle.phase)
            if cycle.active_bend_id == "B02":
                break
            cycle.advance_time(0.1)
        self.assertNotIn(CyclePhase.TRANSFER, phases)
        self.assertEqual(cycle.active_bend_id, "B02")

    def test_reset_clears_intermediate_transfer(self):
        cycle, state = self.make_cycle()
        self.reach_transfer(cycle)
        cycle.advance_time(0.1)
        self.assertGreater(cycle.transfer_progress, 0.0)
        cycle.reset()
        self.assertEqual(cycle.phase, CyclePhase.IDLE)
        self.assertEqual(cycle.transfer_progress, 0.0)
        self.assertAlmostEqual(state.angles["B01"], 0.0)
        self.assertAlmostEqual(state.angles["B02"], 0.0)


if __name__ == "__main__":
    unittest.main()
