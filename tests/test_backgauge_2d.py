"""Backgauge 2D geometry, collision oracle and scoring regressions.

Only sampled machine-section checks are claimed; no 3D or physical stop
travel certification.
"""
import unittest

from smbs_core.kinematics2d.backgauge_2d import (
    BackgaugeModel2D, BackgaugePosition2D,
)
from smbs_core.kinematics2d.operation_cost import (
    OperationWeights, WorkpieceMachineSetup, score_sequence,
)
from smbs_core.kinematics2d.contact_oracle_2d import (
    ContactStatus, classify_polygon_contact,
)


class Backgauge2DTests(unittest.TestCase):
    def model(self):
        return BackgaugeModel2D(10, 6, {
            "B01": BackgaugePosition2D(20, 10),
            "B02": BackgaugePosition2D(120, 10),
        })

    def test_polygon_uses_explicit_machine_s_z(self):
        self.assertEqual(
            self.model().polygon_for("B01"),
            ((15, 7), (25, 7), (25, 13), (15, 13)),
        )

    def test_missing_target_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            self.model().polygon_for("B03")

    def test_json_round_trip(self):
        restored = BackgaugeModel2D.from_dict(self.model().to_dict())
        self.assertEqual(restored.polygon_for("B02"),
                         self.model().polygon_for("B02"))

    def test_automatic_target_uses_fixed_panel_edge_not_moving_panel(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        model = BackgaugeModel2D(
            10, 6, {}, automatic=True, edge_clearance_mm=0.0)
        state = SimpleNamespace(bends={
            "B01": SimpleNamespace(
                bend_zone=SimpleNamespace(outer_radius_mm=2))
        })
        # Fixed-panel edge is at s=120, moving panel extends s=-500:
        # machine gauge must reference fixed support, never global BBox.
        section = {
            "fixed_polygon": ((0, 1), (120, 1), (120, -1), (0, -1)),
            "moving_polygon": ((0, 1), (-500, 1), (-500, -1), (0, -1)),
        }
        with patch(
            "smbs_core.kinematics2d.workpiece_placement_3d."
            "WorkpiecePlacement3D.compute_placement",
            return_value=object(),
        ), patch(
            "smbs_core.kinematics2d.machine_section_3d."
            "compute_machine_cutting_plane_section",
            return_value=section,
        ):
            pos, depth, source = model.resolve_for_state(
                state, {"P01": [(0, 0), (1, 0), (1, 1)]}, "B01")
        self.assertEqual(depth, 120)
        self.assertEqual(pos.s_mm, 125)
        self.assertEqual(pos.z_mm, 0)
        self.assertIn("AUTO", source)

    def test_automatic_rejects_operator_side_stop_even_if_edge_exists(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        model = BackgaugeModel2D(10, 6, {}, automatic=True)
        state = SimpleNamespace(bends={
            "B01": SimpleNamespace(
                bend_zone=SimpleNamespace(outer_radius_mm=2))
        })
        section = {
            "fixed_polygon": ((0, 1), (-120, 1), (-120, -1), (0, -1)),
        }
        with patch(
            "smbs_core.kinematics2d.workpiece_placement_3d."
            "WorkpiecePlacement3D.compute_placement", return_value=object(),
        ), patch(
            "smbs_core.kinematics2d.machine_section_3d."
            "compute_machine_cutting_plane_section", return_value=section,
        ):
            with self.assertRaisesRegex(ValueError, "positive rear edge"):
                model.resolve_for_state(state, {}, "B01")

    def test_automatic_rejects_missing_fixed_edge(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        model = BackgaugeModel2D(10, 6, {}, automatic=True)
        state = SimpleNamespace(bends={
            "B01": SimpleNamespace(
                bend_zone=SimpleNamespace(outer_radius_mm=2))
        })
        with patch(
            "smbs_core.kinematics2d.workpiece_placement_3d."
            "WorkpiecePlacement3D.compute_placement",
            return_value=object(),
        ), patch(
            "smbs_core.kinematics2d.machine_section_3d."
            "compute_machine_cutting_plane_section",
            return_value={"fixed_polygon": ()},
        ):
            with self.assertRaisesRegex(ValueError, "no usable"):
                model.resolve_for_state(state, {}, "B01")

    def test_initial_feed_preview_is_opt_in_and_round_trips(self):
        disabled = BackgaugeModel2D(10, 6, {}, automatic=True)
        self.assertEqual(disabled.initial_feed_preview_mm, 0.0)
        configured = BackgaugeModel2D(
            10, 6, {}, automatic=True, initial_feed_preview_mm=40.0)
        restored = BackgaugeModel2D.from_dict(configured.to_dict())
        self.assertEqual(restored.initial_feed_preview_mm, 40.0)
        self.assertEqual(restored.to_dict()["initial_feed_preview_mm"], 40.0)
        with self.assertRaisesRegex(ValueError, "initial_feed_preview_mm"):
            BackgaugeModel2D(10, 6, {}, initial_feed_preview_mm=-10)

    def test_manual_mode_still_uses_explicit_coordinates(self):
        p, depth, source = self.model().resolve_for_state(None, {}, "B01")
        self.assertEqual(p.s_mm, 20)
        self.assertTrue(depth != depth)
        self.assertEqual(source, "MANUAL_TARGET")

    def test_bad_dimensions_rejected(self):
        with self.assertRaises(ValueError):
            BackgaugeModel2D(0, 5, {})

    def test_overlap_and_clear_classified_by_existing_oracle(self):
        stop = self.model().polygon_for("B01")
        overlap = ((18, 8), (30, 8), (30, 11), (18, 11))
        clear = ((60, 60), (70, 60), (70, 70), (60, 70))
        self.assertEqual(
            classify_polygon_contact(stop, overlap)[0],
            ContactStatus.INTERFERENCE,
        )
        self.assertEqual(
            classify_polygon_contact(stop, clear)[0],
            ContactStatus.CLEAR,
        )

    def test_position_change_adds_independent_weight(self):
        weights = OperationWeights(
            backgauge_move_fixed=3,
            backgauge_move_per_100_mm=2,
        )
        first = WorkpieceMachineSetup(
            "B01", 0, False, 0, 100,
            backgauge_s_mm=20, backgauge_z_mm=10,
        )
        second = WorkpieceMachineSetup(
            "B02", 0, False, 0, 100,
            backgauge_s_mm=120, backgauge_z_mm=10,
        )
        penalty = score_sequence(("B01", "B02"), (first, second), weights)
        self.assertEqual(penalty.steps[1].backgauge_move_mm, 100)
        self.assertEqual(penalty.steps[1].backgauge_move_cost, 5)
        self.assertEqual(penalty.steps[1].total, 5)
        self.assertEqual(penalty.steps[0].total, 0)

    def test_missing_position_does_not_invent_motion_cost(self):
        first = WorkpieceMachineSetup("B01", 0, False, 0, 100)
        second = WorkpieceMachineSetup("B02", 0, False, 0, 100)
        step = score_sequence(("B01", "B02"), (first, second)).steps[1]
        self.assertIsNone(step.backgauge_move_mm)
        self.assertEqual(step.backgauge_move_cost, 0)
        self.assertFalse(step.backgauge_position_known)


if __name__ == "__main__":
    unittest.main()
