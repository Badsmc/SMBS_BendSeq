import math
import unittest
from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph, PanelGraphError
from smbs_core.kinematics2d.panel_kinematics import FoldState, hinge_transform


def graph():
    return PanelGraph(("P1", "P2", "P3"), (
        GraphEdge("B1", "P1", "P2", 90, ((0, 0, 0), (1, 0, 0))),
        GraphEdge("B2", "P2", "P3", -45, ((0, 1, 0), (1, 1, 0))),
    ))


class PanelGraphTests(unittest.TestCase):
    def test_rooted_subtrees(self):
        bends = graph().rooted("P1")
        self.assertEqual([(b.bend_id, b.moving_subtree) for b in bends],
                         [("B1", ("P2", "P3")), ("B2", ("P3",))])

    def test_reverse_root(self):
        bends = graph().rooted("P3")
        self.assertEqual([(b.bend_id, b.moving_subtree) for b in bends],
                         [("B2", ("P2", "P1")), ("B1", ("P1",))])

    def test_cycle_rejected(self):
        with self.assertRaises(PanelGraphError):
            PanelGraph(("A", "B", "C"), (
                GraphEdge("1", "A", "B", 90, ((0,0,0),(1,0,0))),
                GraphEdge("2", "A", "B", 90, ((0,0,0),(1,0,0)))))

    def test_disconnected_rejected(self):
        with self.assertRaises(PanelGraphError):
            PanelGraph(("A", "B", "C", "D"), (
                GraphEdge("1", "A", "B", 90, ((0,0,0),(1,0,0))),
                GraphEdge("2", "C", "D", 90, ((0,0,0),(1,0,0))),
                GraphEdge("3", "A", "B", 90, ((0,0,0),(1,0,0)))))


class PanelKinematicsTests(unittest.TestCase):
    def test_right_hand_rule(self):
        p = hinge_transform((0,0,0), (1,0,0), 90).point((0,1,0))
        for actual, expected in zip(p, (0,0,1)):
            self.assertAlmostEqual(actual, expected, places=8)

    def test_root_remains_fixed_and_signed_bends(self):
        s = FoldState(graph(), "P1")
        s.set_fraction("B1", 1)
        s.set_fraction("B2", 1)
        self.assertEqual(s.transforms["P1"].point((0,1,0)), (0,1,0))
        self.assertAlmostEqual(s.transforms["P2"].point((0,1,0))[2], 1)
        self.assertAlmostEqual(s.angles["B2"], -45)

    def test_parent_after_child_keeps_relative_hinge(self):
        s = FoldState(graph(), "P1")
        s.set_angle("B2", -45)
        p_before = s.transforms["P3"].point((0,2,0))
        s.set_angle("B1", 90)
        p_after = s.transforms["P3"].point((0,2,0))
        expected = hinge_transform((0,0,0), (1,0,0), 90).point(p_before)
        for actual, target in zip(p_after, expected):
            self.assertAlmostEqual(actual, target, places=8)

    def test_absolute_angle_no_double_application(self):
        s = FoldState(graph(), "P1")
        s.set_angle("B1", 90)
        s.set_angle("B1", 90)
        self.assertAlmostEqual(s.transforms["P2"].point((0,1,0))[2], 1)
        s.set_angle("B1", 0)
        for actual, target in zip(s.transforms["P2"].point((0,1,0)), (0,1,0)):
            self.assertAlmostEqual(actual, target, places=8)


if __name__ == "__main__":
    unittest.main()
