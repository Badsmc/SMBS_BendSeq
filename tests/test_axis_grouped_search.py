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
