import unittest
from smbs_core.Core.bend_graph import BendGraph, BendRecord
from smbs_core.Core.panel_graph import PanelGraph
from smbs_core.Core.fold_state import FoldState
from smbs_core.Physics.machine_kinematics import PressBrakeMachine
from smbs_core.Physics.validator import PhysicalValidator
from smbs_core.Solvers.base_planner import PlannerStatus, PlanResult
from smbs_core.Solvers.astar_backward import AStarBackwardPlanner
from smbs_core.Solvers.dfs_backward import DFSBackwardPlanner
from smbs_core.Solvers.greedy_unfold import GreedyUnfoldPlanner
from smbs_core.geometry_backend.backends.mock_backend import MockBackend

class TestPhase8Solvers(unittest.TestCase):
    def setUp(self):
        self.backend = MockBackend()
        self.machine = PressBrakeMachine()
        self.validator = PhysicalValidator(self.machine, self.backend)

        self.bg = BendGraph()
        self.b1 = BendRecord("B1", "F1", "PA", "PB", (0, 0, 0), (1, 0, 0), 90.0, 2.0, 2.0)
        self.bg.add_bend(self.b1)

        self.pg = PanelGraph()
        self.pg.add_bend_connection("B1", "PA", "PB")

        self.root_state = FoldState.initial(self.backend.make_box(10, 10, 2), self.bg, self.pg)

    def test_planner_status_enum_values(self):
        self.assertEqual(PlannerStatus.FOUND, "FOUND")
        self.assertEqual(PlannerStatus.OPTIMAL, "OPTIMAL")
        self.assertEqual(PlannerStatus.NOT_FOUND_BUDGET, "NOT_FOUND_BUDGET")
        self.assertEqual(PlannerStatus.CANNOT_COMPUTE, "CANNOT_COMPUTE")

    def test_astar_finds_valid_sequence_l_profile(self):
        planner = AStarBackwardPlanner(self.bg, self.validator, self.pg)
        res = planner.solve(self.root_state, max_iterations=100)
        self.assertTrue(res["success"])
        self.assertEqual(len(res["sequence"]), 1)

    def test_dfs_finds_valid_sequence(self):
        planner = DFSBackwardPlanner(self.bg, self.validator, self.pg)
        res = planner.solve(self.root_state, max_iterations=100)
        self.assertTrue(res["success"])
        self.assertEqual(len(res["sequence"]), 1)

    def test_greedy_unfold_planner(self):
        planner = GreedyUnfoldPlanner(self.bg, self.validator, self.pg)
        res = planner.solve(self.root_state)
        self.assertTrue(res["success"])
        self.assertEqual(len(res["sequence"]), 1)

if __name__ == "__main__":
    unittest.main()
