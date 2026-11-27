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

    def test_continuous_tools_leave_the_opposite_middle_section_separate(self):
        from freecad.bendseq.core.kinematics2d.shared_strokes import continuous_strokes, stroke_axis

        steps = [self.steps[key] for key in "ACB"]
        self.assertEqual(continuous_strokes(steps, self.bends, self.orientations),
                         (("A",), ("C",), ("B",)))
        bends = {k: self.bends[k] for k in "AC"}
        self.assertEqual(continuous_strokes(steps[:2], bends, self.orientations), (("A", "C"),))
        self.assertEqual(stroke_axis(("A", "C"), bends), ((0., 0., 0.), (50., 0., 0.)))

    def test_full_cycle_and_bend_only_move_both_sections_and_skip_second_stroke(self):
        from freecad.bendseq.core.kinematics2d.panel_graph import GraphEdge, PanelGraph
        from freecad.bendseq.core.kinematics2d.panel_kinematics import FoldState
        from freecad.bendseq.core.kinematics2d.press_cycle import CycleExecutionMode, PressCycleController
        from freecad.bendseq.core.kinematics2d.staged_bends import StagedBendSequenceController
        from freecad.bendseq.core.kinematics2d.tooling_25d import ToolingSetup2D

        graph = PanelGraph(("base", "A", "C", "B"), [
            GraphEdge(b.bend_id, b.moving_panel if b.moving_left else "base",
                      "base" if b.moving_left else b.moving_panel,
                      b.angle_deg, b.axis_xyz, b.bend_zone)
            for b in self.bends.values()
        ])
        for mode in CycleExecutionMode:
            with self.subTest(mode=mode):
                state = FoldState(graph, "base")
                sequence = StagedBendSequenceController(state, order=("A", "C", "B"))
                cycle = PressCycleController(sequence, ToolingSetup2D())
                cycle.shared_stroke_for_step = lambda i: (("A", "C"), ("C",), ("B",))[i]
                cycle.set_execution_mode(mode)
                cycle.play_all()
                paused = False
                seen = set()
                for _ in range(400):
                    seen.add(sequence.active_index)
                    cycle.advance_time(0.1)
                    self.assertEqual(state.angles["A"], state.angles["C"])
                    if 0 < state.angles["A"] < 90 and not paused:
                        cycle.pause()
                        cycle.resume()
                        paused = True
                    if state.angles["B"] == -90:
                        break
                self.assertTrue(paused)
                self.assertEqual(seen, {0, 2})
                self.assertEqual(state.angles, {"A": 90, "B": -90, "C": 90})

    def test_shared_layout_rejects_short_punch_and_accepts_full_span(self):
        from freecad.bendseq.core.geometry_backend.io.clamp_validator import ClampConfig
        from freecad.bendseq.core.kinematics2d.segment_assembly_planner import plan_pair_for_bend
        from freecad.bendseq.core.kinematics2d.shared_strokes import spans_cover_interval

        clamps = ClampConfig(12, 0, (0,), 1, 0.1, 1, repositionable=False)
        die = [{"length_mm": 50, "quantity": 1}]
        short = [{"length_mm": 40, "quantity": 1}]
        self.assertEqual(plan_pair_for_bend(short, die, 50, clamps)["status"],
                         "ASSEMBLY_CANDIDATE_UNVERIFIED")
        self.assertNotEqual(plan_pair_for_bend(short, die, 50, clamps, full_coverage=True)["status"],
                            "ASSEMBLY_CANDIDATE_UNVERIFIED")
        plan = plan_pair_for_bend(die, die, 50, clamps, full_coverage=True)
        for kind in ("punch", "die"):
            spans = [(item["start_x_mm"], item["end_x_mm"]) for item in plan[kind + "_segments"]]
            self.assertTrue(spans_cover_interval(spans, -25, 25))
