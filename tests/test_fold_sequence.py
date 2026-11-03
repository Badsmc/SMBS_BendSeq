import unittest
from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from smbs_core.kinematics2d.fold_sequence import FoldSequenceController, SequenceError


class FoldSequenceTests(unittest.TestCase):
    def setUp(self):
        self.graph = PanelGraph(("P1", "P2", "P3"), (
            GraphEdge("B1", "P1", "P2", 90, ((0,0,0),(1,0,0))),
            GraphEdge("B2", "P2", "P3", -45, ((0,1,0),(1,1,0))),
        ))
        self.controller = FoldSequenceController(self.graph, "P1")

    def test_flat_and_complete(self):
        flat = self.controller.frame(0)
        self.assertEqual(dict(flat.angles), {"B1": 0, "B2": 0})
        complete = self.controller.frame(2)
        self.assertEqual(dict(complete.angles), {"B1": 90, "B2": -45})
        self.assertEqual(complete.step, 2)
        self.assertIsNone(complete.active_bend)

    def test_mid_step_accumulates(self):
        mid = self.controller.frame(1.5)
        self.assertEqual(dict(mid.angles), {"B1": 90, "B2": -22.5})
        self.assertEqual(mid.active_bend, "B2")

    def test_seek_is_deterministic(self):
        a = self.controller.frame(1.25)
        self.controller.frame(2)
        b = self.controller.frame(1.25)
        self.assertEqual(a.transforms, b.transforms)

    def test_invalid_order(self):
        with self.assertRaises(SequenceError):
            FoldSequenceController(self.graph, "P1", ("B1", "B1"))

    def test_out_of_bounds(self):
        with self.assertRaises(SequenceError):
            self.controller.frame(-0.1)
        with self.assertRaises(SequenceError):
            self.controller.frame(2.1)


if __name__ == "__main__":
    unittest.main()
