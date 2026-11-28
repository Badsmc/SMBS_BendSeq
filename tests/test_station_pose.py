from dataclasses import replace
import unittest

from freecad.bendseq.core.kinematics2d.bend_zone import bend_parameters, make_bend_zone
from freecad.bendseq.core.kinematics2d.bending_stations import BendingStation, StationSetup
from freecad.bendseq.core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from freecad.bendseq.core.kinematics2d.panel_kinematics import FoldState
from freecad.bendseq.core.kinematics2d.staged_bends import BendOperation
from freecad.bendseq.core.kinematics2d.station_pose import resolve_station_poses


class StationPoseTests(unittest.TestCase):
    def setUp(self):
        edges = []
        for bid, low in (("A", 0), ("B", 30)):
            a, b = (low, 0, 0), (low + 20, 0, 0)
            zone = make_bend_zone(bid, (a[:2], b[:2]), bend_parameters(90, 2, 1, .4))
            edges.append(GraphEdge(bid, bid, "base", 90, (a, b), zone))
        self.state = FoldState(PanelGraph(("base", "A", "B"), edges), "base")
        self.steps = tuple(BendOperation(bid, bid, 90) for bid in ("A", "B"))
        self.stations = tuple(
            BendingStation(key, key, "P", "D", x, ((-30, 30),), ((-30, 30),))
            for key, x in (("S1", -100), ("S2", 100))
        )
        self.setup = StationSetup((-300, 300), self.stations, 10,
                                  (("A", "S1"), ("B", "S2")))

    def resolve(self, setup, steps, reverse=False):
        return resolve_station_poses(
            setup, steps, dict.fromkeys(("A", "B"), reverse), self.state,
            phase="APPROACH", support_plane_zm=2,
        )

    def test_assignments_move_workpiece_between_fixed_machine_stations(self):
        for step, x in zip(self.steps, (-100, 100)):
            pose = self.resolve(self.setup, (step,))[0]
            self.assertEqual(pose.midpoint_xm, x)
            self.assertEqual(pose.bend_span_xm, (x - 10, x + 10))
        with self.assertRaisesRegex(ValueError, "same assigned station"):
            self.resolve(self.setup, self.steps)

    def test_shared_bend_centers_full_span_with_either_loading_direction(self):
        setup = replace(self.setup, assignments=(("A", "S2"), ("B", "S2")))
        for reverse in (False, True):
            pose = self.resolve(setup, self.steps, reverse)[0]
            self.assertEqual(pose.bend_span_xm, (75, 125))
        first, second = setup.stations
        gapped = replace(second, punch_spans=((-30, -5), (5, 30)))
        self.assertEqual(self.resolve(replace(setup, stations=(first, gapped)), self.steps), ())

    def test_two_stage_ids_resolve_at_different_stations(self):
        stages = (BendOperation("A.1", "A", 30, "PREBEND"),
                  BendOperation("A.2", "A", 90, "FINISH"))
        setup = replace(self.setup, assignments=(("A.1", "S1"), ("A.2", "S2")))
        locations = [
            resolve_station_poses(setup, (op,), {op.operation_id: False}, self.state,
                                  phase="APPROACH", support_plane_zm=2)[0].midpoint_xm
            for op in stages
        ]
        self.assertEqual(locations, [-100, 100])
