"""Stage P3: axis-first bounded order search with an explicit 2D oracle."""
import unittest

from smbs_core.kinematics2d.panel_graph import PanelGraph, GraphEdge
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.fold_sequence import search_axis_grouped_order


def make_state():
    graph = PanelGraph(
        ("P0", "P1", "P2", "P3"),
        (
            GraphEdge("B01", "P0", "P1", 90.0,
                      ((0, 0, 0), (1, 0, 0))),
            GraphEdge("B02", "P1", "P2", -90.0,
                      ((0, 1, 0), (-1, 1, 0))),
            GraphEdge("B03", "P2", "P3", 90.0,
                      ((0, 2, 0), (0, 3, 0))),
        )
    )
    return FoldState(graph, "P0")


class AxisGroupedSearchTests(unittest.TestCase):
    def test_stage2_stroke_requires_verified_tooling(self):
        from types import SimpleNamespace as NS
        from smbs_core.kinematics2d.contact_oracle_2d import sample_bend_stroke_2d
        from smbs_core.kinematics2d.tooling_25d import ValidationStatus

        state = make_state()
        original_angles = dict(state.angles)
        tooling = NS(
            punch=NS(validation_status=ValidationStatus.IMPORTED),
            die=NS(validation_status=ValidationStatus.GEOMETRY_VERIFIED),
        )
        result = sample_bend_stroke_2d(
            state, {}, tooling, "B01", samples=8
        )
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "missing_verified_tooling")
        self.assertEqual(state.angles, original_angles)

    def test_stage2_stroke_sampling_limit_rejected(self):
        from smbs_core.kinematics2d.contact_oracle_2d import sample_bend_stroke_2d
        state = make_state()
        with self.assertRaisesRegex(ValueError, "samples"):
            sample_bend_stroke_2d(state, {}, None, "B01", samples=0)
        with self.assertRaisesRegex(ValueError, "samples"):
            sample_bend_stroke_2d(state, {}, None, "B01", samples=True)

    def test_real_contact_bridge_refuses_missing_tools(self):
        from smbs_core.kinematics2d.contact_oracle_2d import make_axis_step_oracle_2d
        state = make_state()
        details = []
        oracle = make_axis_step_oracle_2d({"P0": ((0, 0), (1, 0), (1, 1))},
                                           None, evidence=details)
        self.assertEqual(oracle((), "B01", state), "UNKNOWN")
        self.assertEqual(details[-1]["reason"], "missing_punch_or_die")
        result = search_axis_grouped_order(state, oracle)
        self.assertEqual(result.status, "UNKNOWN_UNVERIFIED")
        self.assertFalse(result.order)

    def test_real_contact_bridge_blocks_actual_2d_interference(self):
        from types import SimpleNamespace as NS
        from unittest.mock import patch
        from smbs_core.kinematics2d.contact_oracle_2d import (
            make_axis_step_oracle_2d, ContactReport, ContactStatus
        )
        from smbs_core.kinematics2d.tooling_25d import ValidationStatus
        state = make_state()

        class Tooling:
            punch = NS(validation_status=ValidationStatus.GEOMETRY_VERIFIED)
            die = NS(validation_status=ValidationStatus.GEOMETRY_VERIFIED)
            aligned = False
            def align_to_cross_section(self, *args, **kwargs):
                self.aligned = True

        section = {"zone_polygon": ((0, 0), (1, 0), (1, 1)),
                   "fixed_polygon": ((0, 0), (1, 0), (1, 1)),
                   "moving_polygon": ((0, 0), (1, 0), (1, 1))}
        evidence = []
        bridge = make_axis_step_oracle_2d(
            {"P0": ((0, 0), (1, 0), (1, 1))}, Tooling(),
            evidence=evidence,
        )
        with (
            patch("smbs_core.kinematics2d.workpiece_placement_3d.WorkpiecePlacement3D.compute_placement", return_value=object()),
            patch("smbs_core.kinematics2d.machine_section_3d.compute_machine_cutting_plane_section", return_value=section),
            patch("smbs_core.kinematics2d.contact_oracle_2d.check_sheet_tooling_preview",
                  return_value=ContactReport(ContactStatus.INTERFERENCE, (), 0.02)),
        ):
            self.assertEqual(bridge((), "B01", state), "BLOCKED")
        self.assertEqual(evidence[-1]["reason"],
                         "verified_2d_setup_material_interference")

    def test_real_contact_bridge_sampled_clear_is_not_manufacturing_pass(self):
        from types import SimpleNamespace as NS
        from unittest.mock import patch
        from smbs_core.kinematics2d.contact_oracle_2d import (
            make_axis_step_oracle_2d, ContactReport, ContactStatus
        )
        from smbs_core.kinematics2d.tooling_25d import ValidationStatus
        state = make_state()

        class Tooling:
            punch = NS(validation_status=ValidationStatus.GEOMETRY_VERIFIED)
            die = NS(validation_status=ValidationStatus.GEOMETRY_VERIFIED)
            aligned = False
            def align_to_cross_section(self, *args, **kwargs):
                self.aligned = True

        section = {"zone_polygon": ((0, 0), (1, 0), (1, 1)),
                   "fixed_polygon": ((0, 0), (1, 0), (1, 1)),
                   "moving_polygon": ((0, 0), (1, 0), (1, 1))}
        evidence = []
        bridge = make_axis_step_oracle_2d(
            {"P0": ((0, 0), (1, 0), (1, 1))},
            Tooling(), evidence=evidence,
        )
        with (
            patch("smbs_core.kinematics2d.workpiece_placement_3d.WorkpiecePlacement3D.compute_placement", return_value=object()),
            patch("smbs_core.kinematics2d.machine_section_3d.compute_machine_cutting_plane_section", return_value=section),
            patch("smbs_core.kinematics2d.contact_oracle_2d.check_sheet_tooling_preview",
                  return_value=ContactReport(ContactStatus.CLEAR, (), 0.02)),
        ):
            self.assertEqual(bridge((), "B01", state), "UNKNOWN")
        self.assertEqual(evidence[-1]["reason"],
                         "setup_clear_but_stroke_not_checked")

    def test_first_axis_then_second_and_replays_prefix(self):
        state = make_state()
        calls = []

        def oracle(prefix, bid, fold_state):
            calls.append((prefix, bid, dict(fold_state.angles)))
            self.assertEqual(
                {b for b, angle in fold_state.angles.items() if abs(angle) > 1e-6},
                set(prefix),
            )
            # The first axis must be done B02 before B01, or the second
            # axis has no legal machine setup for this test oracle.
            if bid == "B03" and prefix != ("B02", "B01"):
                return "BLOCKED"
            if bid == "B02" and "B01" in prefix:
                return "BLOCKED"
            return "CLEAR"

        original = dict(state.angles)
        result = search_axis_grouped_order(state, oracle)
        self.assertEqual(result.status, "FOUND_2D_ORACLE")
        self.assertEqual(result.order, ("B02", "B01", "B03"))
        self.assertEqual(state.angles, original)
        self.assertTrue(calls)

    def test_unknown_is_not_accepted(self):
        state = make_state()
        result = search_axis_grouped_order(
            state, lambda prefix, bid, fs: "UNKNOWN"
        )
        self.assertEqual(result.status, "UNKNOWN_UNVERIFIED")
        self.assertFalse(result.order)

    def test_limit_stops_search(self):
        state = make_state()
        result = search_axis_grouped_order(
            state, lambda prefix, bid, fs: "BLOCKED",
            max_evaluations=1,
        )
        self.assertEqual(result.status, "UNKNOWN_LIMIT_REACHED")
        self.assertEqual(result.evaluated_steps, 1)

    def test_all_blocked_is_not_a_general_impossibility_proof(self):
        state = make_state()
        result = search_axis_grouped_order(
            state, lambda prefix, bid, fs: "BLOCKED"
        )
        self.assertEqual(result.status, "NO_VALID_AXIS_GROUPED_ORDER")
        self.assertFalse(result.order)

    def test_opposite_axis_directions_group_together(self):
        state = make_state()
        result = search_axis_grouped_order(
            state, lambda prefix, bid, fs: "CLEAR"
        )
        self.assertEqual(result.status, "FOUND_2D_ORACLE")
        self.assertEqual(result.axis_groups, (("B01", "B02"), ("B03",)))
        self.assertEqual(result.order, ("B01", "B02", "B03"))

    def test_callback_mutation_restored_even_on_exception(self):
        state = make_state()
        original_angles = dict(state.angles)
        original_transforms = dict(state.transforms)

        def broken(prefix, bid, fs):
            fs.set_angle(bid, fs.bends[bid].angle_deg)
            raise RuntimeError("oracle failed")

        with self.assertRaisesRegex(RuntimeError, "oracle failed"):
            search_axis_grouped_order(state, broken)
        self.assertEqual(state.angles, original_angles)
        self.assertEqual(state.transforms, original_transforms)


if __name__ == "__main__":
    unittest.main()
