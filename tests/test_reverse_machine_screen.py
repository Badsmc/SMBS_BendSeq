"""Pure-Python machine-space AABB heuristic regression tests.

The tool envelopes are supplied as data so this test does not need FreeCAD.
"""
import unittest

from smbs_core.kinematics2d.panel_graph import PanelGraph, GraphEdge
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.reverse_machine_screen import (
    ToolAABB, _box_overlap, _shift_z, score_candidate_machine,
)


class MachineScreenTests(unittest.TestCase):
    def test_axis_aligned_boxes_and_punch_z_motion(self):
        a = (0, 0, 0, 2, 2, 2)
        b = (1, 1, 1, 3, 3, 3)
        self.assertEqual(_box_overlap(a, b), 1.0)
        self.assertEqual(_box_overlap(a, (2, 2, 2, 3, 3, 3)), 0.0)
        self.assertEqual(_shift_z(a, -1), (0, 0, -1, 2, 2, 1))

    def test_two_loading_orientations_scored_without_mutation(self):
        graph = PanelGraph(
            ("P1", "P2"),
            (GraphEdge("B1", "P1", "P2", 90.0,
                       ((10., 0., 0.), (10., 10., 0.))),),
        )
        source = FoldState(graph, "P1")
        polygons = {
            "P1": ((0, 0), (10, 0), (10, 10), (0, 10)),
            "P2": ((10, 0), (20, 0), (20, 10), (10, 10)),
        }
        tool = ToolAABB(
            punch=(-5, -5, 40, 5, 5, 60),
            die=(-5, -5, -10, 5, 5, 0),
        )
        angles_before = dict(source.angles)
        score, orientation, pairs, hits = score_candidate_machine(
            ("B1",), source, polygons, 1.5, {"B1": tool}
        )
        self.assertGreaterEqual(score, 0)
        self.assertEqual(dict(orientation).keys(), {"B1"})
        self.assertIsInstance(dict(orientation)["B1"], bool)
        self.assertGreater(pairs, 0)
        self.assertGreaterEqual(hits, 0)
        self.assertEqual(angles_before, source.angles)

    def test_unknown_tool_is_not_called_safe(self):
        graph = PanelGraph(
            ("P1", "P2"),
            (GraphEdge("B1", "P1", "P2", 90.0,
                       ((10., 0., 0.), (10., 10., 0.))),),
        )
        source = FoldState(graph, "P1")
        score, orientation, pairs, hits = score_candidate_machine(
            ("B1",), source, {}, 1.5, {}
        )
        self.assertEqual((score, pairs, hits), (0., 0, 0))
        self.assertEqual(dict(orientation), {"B1": False})


if __name__ == "__main__":
    unittest.main()
