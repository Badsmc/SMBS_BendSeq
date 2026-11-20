"""Headless regression contracts for cumulative pre-bend / finish-bend operations."""
from __future__ import annotations

import unittest

from smbs_core.kinematics2d.fold_sequence import PlaybackStatus, SequenceError
from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.staged_bends import StagedBendSequenceController


def controller(*, negative=False):
    graph = PanelGraph(
        ("P0", "P1", "P2", "P3"),
        (
            GraphEdge("B01", "P0", "P1", 90.0, ((0, 0, 0), (10, 0, 0))),
            GraphEdge("B02", "P1", "P2", -90.0 if negative else 60.0,
                      ((0, 10, 0), (10, 10, 0))),
            GraphEdge("B03", "P2", "P3", 90.0, ((0, 20, 0), (10, 20, 0))),
        ),
    )
    return StagedBendSequenceController(FoldState(graph, "P0"))


class StagedOperationsTests(unittest.TestCase):
    def test_stage_is_not_new_physical_bend_and_tracks_absolute_angles(self):
        c = controller()
        c.add_prebend("B01", 35)
        self.assertEqual(c.order, ("B01", "B02", "B03"))
        self.assertEqual(len(c.fold_state.angles), 3)
        self.assertEqual(c.total_steps, 4)
        self.assertEqual(
            [(s.operation_id, s.bend_id, s.target_angle_deg)
             for s in c.operation_steps[:2]],
            [("B01.1", "B01", 35.0), ("B01.2", "B01", 90.0)],
        )
        c.reorder_operations(("B01.1", "B02", "B03", "B01.2"))
        self.assertEqual(c.operation_start_deg(3), 35.0)

        for expected_id, expected_b01 in (
            ("B01.1", 35.0), ("B02", 35.0),
            ("B03", 35.0), ("B01.2", 90.0),
        ):
            self.assertEqual(c.active_operation_id, expected_id)
            c.next_step()
            self.assertAlmostEqual(c.fold_state.angles["B01"], expected_b01)
        self.assertEqual(c.status, PlaybackStatus.COMPLETED)
        self.assertEqual(c.fold_state.angles["B02"], 60.0)
        self.assertEqual(c.fold_state.angles["B03"], 90.0)

    def test_previous_step_unwinds_finish_to_prebend_not_to_zero(self):
        c = controller()
        c.add_prebend("B01", 35)
        c.reorder_operations(("B01.1", "B02", "B01.2", "B03"))
        for _ in range(3):
            c.next_step()
        self.assertEqual(c.fold_state.angles["B01"], 90.0)
        c.previous_step()
        self.assertEqual(c.fold_state.angles["B01"], 35.0)
        self.assertEqual(c.fold_state.angles["B02"], 60.0)
        self.assertEqual(c.active_operation_id, "B01.2")
        c.previous_step()
        self.assertEqual(c.fold_state.angles["B01"], 35.0)
        self.assertEqual(c.fold_state.angles["B02"], 0.0)
        c.previous_step()
        self.assertEqual(c.fold_state.angles["B01"], 0.0)

    def test_automatic_playback_uses_remaining_angle_for_finish(self):
        c = controller()
        c.add_prebend("B01", 35)
        c.reorder_operations(("B01.1", "B02", "B01.2", "B03"))
        c.speed_deg_per_sec = 10000.0
        c.play_all()
        for expected in ("B02", "B01.2", "B03"):
            c.advance_time(0.1)
            self.assertEqual(c.active_operation_id, expected)
        self.assertEqual(c.fold_state.angles["B01"], 90.0)
        c.advance_time(0.1)
        self.assertEqual(c.status, PlaybackStatus.COMPLETED)

    def test_invalid_order_and_angles_fail_without_mutating_plan(self):
        c = controller(negative=True)
        with self.assertRaises(SequenceError):
            c.add_prebend("B02", 35)
        with self.assertRaises(SequenceError):
            c.add_prebend("B02", -90)
        c.add_prebend("B02", -25)
        original = c.operation_snapshot()
        with self.assertRaises(SequenceError):
            c.reorder_operations(("B02.2", "B02.1", "B01", "B03"))
        self.assertEqual(c.operation_snapshot(), original)
        self.assertEqual(c.operation_start_deg(2), -25.0)

    def test_serialize_and_validate_replay(self):
        c = controller()
        c.add_prebend("B03", 30)
        c.reorder_operations(("B03.1", "B01", "B02", "B03.2"))
        payload = c.operation_snapshot()
        restored = controller()
        restored.restore_operation_snapshot(payload)
        self.assertEqual(restored.operation_snapshot(), payload)
        modified = [dict(step) for step in payload]
        modified[-1]["target_angle_deg"] = 75.0
        with self.assertRaises(SequenceError):
            controller().restore_operation_snapshot(modified)
        modified[-1]["target_angle_deg"] = 90
        modified[0]["target_angle_deg"] = 0
        with self.assertRaises(SequenceError):
            controller().restore_operation_snapshot(modified)

    def test_remove_prebend_preserves_one_final_operation(self):
        c = controller()
        c.add_prebend("B01", 35)
        c.remove_prebend("B01")
        self.assertFalse(c.has_stages)
        self.assertEqual(c.total_steps, 3)
        self.assertEqual(c.operation_steps[0].operation_id, "B01")

    def test_removing_one_prebend_preserves_other_stages_and_order(self):
        c = controller(negative=True)
        c.add_prebend("B01", 35.0)
        c.reorder_operations(("B01.1", "B02", "B03", "B01.2"))
        c.add_prebend("B02", -25.0)
        c.reorder_operations(("B01.1", "B02.1", "B03", "B01.2", "B02.2"))
        before = c.operation_snapshot()

        c.remove_prebend("B01")
        self.assertEqual(
            [step.operation_id for step in c.operation_steps],
            ["B02.1", "B03", "B01", "B02.2"],
        )
        self.assertEqual(c.operation_steps[2].target_angle_deg, 90.0)
        self.assertTrue(c.has_stages)
        self.assertEqual(
            [(step.operation_id, step.target_angle_deg)
             for step in c.operation_steps if step.bend_id == "B02"],
            [("B02.1", -25.0), ("B02.2", -90.0)],
        )
        self.assertEqual(len(before) - len(c.operation_steps), 1)

        c.remove_prebend("B02")
        self.assertEqual(
            [step.operation_id for step in c.operation_steps],
            ["B03", "B01", "B02"],
        )
        self.assertEqual(c.operation_steps[-1].target_angle_deg, -90.0)
        self.assertFalse(c.has_stages)
        self.assertEqual(c.canonical_order, ("B01", "B02", "B03"))

    def test_removing_unknown_or_non_split_bend_does_not_change_plan(self):
        c = controller()
        c.add_prebend("B03", 25.0)
        before = c.operation_snapshot()
        c.remove_prebend("B01")
        self.assertEqual(c.operation_snapshot(), before)
        c.remove_prebend("B03")
        self.assertEqual(c.total_steps, 3)
        self.assertEqual(c.operation_steps[2].operation_id, "B03")
        c.add_prebend("B03", 45.0)
        self.assertEqual(c.operation_steps[2].operation_id, "B03.1")
        self.assertEqual(c.operation_steps[3].operation_id, "B03.2")

    def test_full_cycle_can_register_completion_after_retract(self):
        c = controller()
        c.add_prebend("B01", 35)
        c.play_all()
        c.fold_state.set_angle("B01", 35)
        c.mark_operation_completed()
        self.assertEqual(c.completed_step_count, 1)
        self.assertEqual(c.fold_state.angles["B01"], 35)


if __name__ == "__main__":
    unittest.main()
