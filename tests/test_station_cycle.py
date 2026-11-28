from dataclasses import dataclass, replace
from types import SimpleNamespace
import unittest

from freecad.bendseq.core.kinematics2d.bend_zone import bend_parameters, make_bend_zone
from freecad.bendseq.core.kinematics2d.bending_stations import BendingStation, StationSetup
from freecad.bendseq.core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from freecad.bendseq.core.kinematics2d.panel_kinematics import FoldState
from freecad.bendseq.core.kinematics2d.press_cycle import CycleConfig, CyclePhase, PressCycleError
from freecad.bendseq.core.kinematics2d.staged_bends import StagedBendSequenceController
from freecad.bendseq.core.kinematics2d.station_cycle import build_station_cycle


@dataclass(frozen=True)
class Profile:
    tool_id: str
    punch_datums: object = None
    die_datums: object = None
    punch_reversed: bool = False
    validation_status: object = None


class StationCycleTests(unittest.TestCase):
    def setUp(self):
        zone = make_bend_zone("A", ((0, 0), (20, 0)), bend_parameters(90, 2, 1, .4))
        graph = PanelGraph(("base", "flange"), (
            GraphEdge("A", "flange", "base", 90, ((0, 0, 0), (20, 0, 0)), zone),
        ))
        self.source = StagedBendSequenceController(FoldState(graph, "base"))
        self.source.add_prebend("A", 30)
        self.setup = StationSetup((-200, 200), tuple(
            BendingStation(sid, sid, "P", die, x, ((-25, 25),), ((-25, 25),))
            for sid, die, x in (("S1", "D1", -100), ("S2", "D2", 100))
        ), assignments=(("A.1", "S1"), ("A.2", "S2")))
        self.profiles = {
            ("punch", "P"): Profile("P", punch_datums=SimpleNamespace(
                tip_radius_mm=1, included_angle_deg=85)),
            **{("die", sid): Profile(sid, die_datums=SimpleNamespace(
                v_opening_mm=opening, shoulder_radius_mm=1, die_angle_deg=85,
                v_depth_mm=15)) for sid, opening in (("D1", 10), ("D2", 14))},
        }
        self.outlines = {"base": ((0, -20), (20, -20), (20, -3), (0, -3))}

    def build(self, setup):
        return build_station_cycle(setup, self.source, self.profiles,
                                   {"A.1": False, "A.2": True}, self.outlines, CycleConfig())

    def test_prebend_then_finish_at_different_stations(self):
        cycle, stations = self.build(self.setup)
        self.assertEqual(stations, ("S1", "S2"))
        self.assertEqual(cycle.coupled_kinematics.die_opening_v_mm, 10)
        cycle.play_all()
        transfer_seen = False
        arrival_seen = False
        for _ in range(1000):
            cycle.advance_time(0.05)
            if cycle.phase == CyclePhase.TRANSFER:
                transfer_seen = True
                self.assertAlmostEqual(cycle.current_angle_deg, 30)
                axis_center = (10, 0, 0)
                self.assertAlmostEqual(cycle.transfer_3d.start.apply(axis_center)[0], -100)
                self.assertAlmostEqual(cycle.transfer_3d.end.apply(axis_center)[0], 100)
            if cycle.active_index == 1 and cycle.phase == CyclePhase.APPROACH:
                arrival_seen = True
                self.assertAlmostEqual(cycle.current_angle_deg, 30)
                self.assertEqual(cycle.tooling.die.tool_id, "D2")
                self.assertEqual(cycle.coupled_kinematics.die_opening_v_mm, 14)
            if cycle.phase == CyclePhase.COMPLETED:
                break
        self.assertTrue(transfer_seen and arrival_seen)
        self.assertEqual(cycle.phase, CyclePhase.COMPLETED)
        self.assertAlmostEqual(cycle.current_angle_deg, 90)
        self.assertEqual(self.source.fold_state.angles, {"A": 0.0})

    def test_missing_finish_assignment_blocks_before_playback(self):
        with self.assertRaisesRegex(PressCycleError, r"A\.2: no assigned station"):
            self.build(replace(self.setup, assignments=(("A.1", "S1"),)))

    def test_inadequate_finish_coverage_blocks_before_playback(self):
        first, second = self.setup.stations
        second = replace(second, die_spans=((-5, 5),))
        with self.assertRaisesRegex(PressCycleError, r"A\.2: station does not cover"):
            self.build(replace(self.setup, stations=(first, second)))
