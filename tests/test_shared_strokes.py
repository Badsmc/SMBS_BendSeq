from dataclasses import replace
import unittest

from freecad.bendseq.core.kinematics2d.bend_zone import bend_parameters, make_bend_zone
from freecad.bendseq.core.kinematics2d.panel_graph import RootedBend
from freecad.bendseq.core.kinematics2d.shared_strokes import propose_shared_strokes
from freecad.bendseq.core.kinematics2d.staged_bends import BendOperation


class SharedStrokeTests(unittest.TestCase):
    def setUp(self):
        self.steps = {
            key: BendOperation(key, key, angle)
            for key, angle in zip(("A", "B", "C"), (90.0, -90.0, 90.0))
        }
        self.bends = {}
        for index, step in enumerate(self.steps.values()):
            start, end = index * 20.0, index * 20.0 + 10.0
            if step.bend_id == "C":
                start, end = end, start
            axis = ((start, 0.0, 0.0), (end, 0.0, 0.0))
            zone = make_bend_zone(
                step.bend_id, tuple(point[:2] for point in axis),
                bend_parameters(step.target_angle_deg, 2.0, 1.0, 0.4),
            )
            self.bends[step.bend_id] = RootedBend(
                step.bend_id, "base", step.bend_id, (step.bend_id,),
                step.target_angle_deg, axis, zone, start < end,
            )
        self.orientations = dict.fromkeys(self.steps, False)

    def propose(self, order):
        return propose_shared_strokes(
            [self.steps[key] for key in order], self.bends, self.orientations,
        )

    def test_same_direction_outer_sections_can_share_a_stroke(self):
        self.assertEqual(self.propose("ACB"), (("A", "C"), ("B",)))

    def test_preserves_order_and_opposite_direction(self):
        self.assertEqual(self.propose("ABC"), (("A",), ("B",), ("C",)))

    def test_keeps_different_orientations_and_staged_operations_separate(self):
        self.orientations["C"] = True
        self.assertEqual(self.propose("ACB"), (("A",), ("C",), ("B",)))
        self.orientations["C"] = False
        self.steps["C"] = replace(self.steps["C"], kind="FINISH")
        self.assertEqual(self.propose("ACB"), (("A",), ("C",), ("B",)))

    def test_nearby_parallel_axis_is_not_collinear(self):
        self.bends["C"] = replace(
            self.bends["C"], axis_xyz=((50.0, 0.2, 0.0), (40.0, 0.2, 0.0)),
        )
        self.assertEqual(self.propose("ACB"), (("A",), ("C",), ("B",)))
