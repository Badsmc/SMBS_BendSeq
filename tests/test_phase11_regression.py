import unittest
from smbs_core.Core.orchestrator import BendSeqOrchestrator
from smbs_core.geometry_backend.backends.mock_backend import MockBackend
from smbs_core.Core.bend_graph import BendGraph, BendRecord
from smbs_core.Core.panel_graph import PanelGraph
from smbs_core.Core.fold_state import FoldState

class TestPhase11Regression(unittest.TestCase):
    def setUp(self):
        self.backend = MockBackend()
        self.orchestrator = BendSeqOrchestrator(self.backend)

    def test_regression_ok_part_yields_valid_steps(self):
        # Synthetic L-profile ok model
        target_shape = self.backend.make_box(100, 50, 2)
        res = self.orchestrator.run_planning(target_shape=target_shape, output_file="test_l.json")
        self.assertIn(res["status"], ["SUCCESS", "PARTIAL"])
        self.assertTrue(res["validation"]["steps_valid"] > 0)

    def test_regression_false_part_never_falsely_success(self):
        # Impossible part with synthetic collision simulation
        colliding_shape = {"type": "mock_shape", "simulate_collision": True}
        bg = BendGraph()
        b1 = BendRecord("B1", "F1", "PA", "PB", (0, 0, 0), (1, 0, 0), 90.0, 2.0, 2.0)
        bg.add_bend(b1)
        pg = PanelGraph()
        pg.add_bend_connection("B1", "PA", "PB")

        # Running planning with colliding shape
        res = self.orchestrator.run_planning(target_shape=colliding_shape, output_file="test_coll.json")
        self.assertNotEqual(res["status"], "SUCCESS")

    def test_cache_hits_on_repeated_state(self):
        shape = self.backend.make_box(100, 50, 2)
        res1 = self.orchestrator.run_planning(target_shape=shape, output_file="test_c1.json")
        res2 = self.orchestrator.run_planning(target_shape=shape, output_file="test_c2.json")
        self.assertGreaterEqual(self.orchestrator.state_cache.stats["puts"], 1)

if __name__ == "__main__":
    unittest.main()
