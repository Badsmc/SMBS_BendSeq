"""
test_bendseq.py - Comprehensive Unit & Integration Tests for BendSeq Addon
"""

import sys
import os
import unittest

# Add workspace path
SYS_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENDSEQ_PATH = os.path.join(SYS_PATH, "BendSeq")
if BENDSEQ_PATH not in sys.path:
    sys.path.insert(0, BENDSEQ_PATH)
if SYS_PATH not in sys.path:
    sys.path.insert(0, SYS_PATH)

from smbs_core.Core.state_cache import StateCache
from smbs_core.Core.backward_graph import BackwardGraph, BendState
from smbs_core.Core.orchestrator import BendSeqOrchestrator
from smbs_core.Geometry.feature_extractor import FeatureExtractor
from smbs_core.Solvers.astar_backward import AStarBackwardPlanner
from smbs_core.Solvers.greedy_unfold import GreedyUnfoldPlanner
from smbs_core.Solvers.tool_optimizer import ToolOptimizer
from smbs_core.Physics.machine_kinematics import PressBrakeMachine
from smbs_core.Physics.collision_detector import CollisionDetector
from smbs_core.Physics.backgauge_calculator import BackgaugeCalculator
from smbs_core.IO.tool_database import ToolDatabase
from smbs_core.IO.bendseq_writer import BendSeqWriter


class TestStateCache(unittest.TestCase):
    def test_cache_put_get(self):
        cache = StateCache()
        key = StateCache.compute_hash(None, {"BEND_01", "BEND_02"})
        self.assertFalse(cache.contains(key))
        
        dummy_state = {"state": "test"}
        cache.put(key, dummy_state)
        self.assertTrue(cache.contains(key))
        self.assertEqual(cache.get(key), dummy_state)
        
        stats = cache.stats
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 0)


class TestFeatureExtractor(unittest.TestCase):
    def test_fallback_features(self):
        from smbs_core.geometry_backend.backends.mock_backend import MockBackend
        from smbs_core.Geometry.feature_extractor import FeatureExtractionError
        backend = MockBackend()
        extractor = FeatureExtractor(backend)
        with self.assertRaises(FeatureExtractionError):
            extractor.extract_features(None)


class TestBackgaugeCalculator(unittest.TestCase):
    def test_backgauge_computation(self):
        machine = PressBrakeMachine()
        calc = BackgaugeCalculator(machine)
        bend_info = {"id": "BEND_01", "length": 150.0, "angle": 90.0}
        
        feasible = calc.is_backgauge_feasible(None, bend_info)
        self.assertTrue(feasible)
        
        bg = calc.compute_step_backgauge(1, bend_info, part_thickness=2.0)
        self.assertIn("X_mm", bg)
        self.assertIn("R_mm", bg)
        self.assertIn("Z1_mm", bg)
        self.assertIn("Z2_mm", bg)
        self.assertGreater(bg["X_mm"], 0.0)
        self.assertGreater(bg["Z2_mm"], bg["Z1_mm"])


class TestSolvers(unittest.TestCase):
    def test_astar_planning(self):
        from smbs_core.geometry_backend.backends.mock_backend import MockBackend
        backend = MockBackend()
        orchestrator = BendSeqOrchestrator(backend)
        # Mock feature extractor because we pass None for target_shape
        orchestrator.extractor = type('MockExtractor', (), {
            'extract_features': lambda self, shape: {
                "thickness": 2.0, "k_factor": 0.40,
                "bends": {
                    "BEND_01": {"origin": (0,0,0), "axis": (1,0,0), "angle": 90.0},
                    "BEND_02": {"origin": (1,0,0), "axis": (1,0,0), "angle": 90.0},
                    "BEND_03": {"origin": (2,0,0), "axis": (1,0,0), "angle": 90.0},
                    "BEND_04": {"origin": (3,0,0), "axis": (1,0,0), "angle": 90.0}
                }
            }
        })()
        
        result = orchestrator.run_planning(
            target_shape=None,
            algorithm="astar",
            output_file="test_bendseq_output.json"
        )
        self.assertTrue(result["success"] or result["status"] == "PARTIAL")
        self.assertEqual(len(result["sequence"]), 4)
        self.assertTrue(os.path.exists("test_bendseq_output.json"))
        if os.path.exists("test_bendseq_output.json"):
            os.remove("test_bendseq_output.json")

    def test_greedy_planning(self):
        from smbs_core.geometry_backend.backends.mock_backend import MockBackend
        backend = MockBackend()
        orchestrator = BendSeqOrchestrator(backend)
        orchestrator.extractor = type('MockExtractor', (), {
            'extract_features': lambda self, shape: {
                "thickness": 2.0, "k_factor": 0.40,
                "bends": {
                    "BEND_01": {"origin": (0,0,0), "axis": (1,0,0), "angle": 90.0},
                    "BEND_02": {"origin": (1,0,0), "axis": (1,0,0), "angle": 90.0},
                    "BEND_03": {"origin": (2,0,0), "axis": (1,0,0), "angle": 90.0},
                    "BEND_04": {"origin": (3,0,0), "axis": (1,0,0), "angle": 90.0}
                }
            }
        })()
        result = orchestrator.run_planning(
            target_shape=None,
            algorithm="greedy",
            output_file="test_greedy_output.json"
        )
        self.assertTrue(result["success"] or result["status"] == "PARTIAL")
        self.assertEqual(len(result["sequence"]), 4)
        if os.path.exists("test_greedy_output.json"):
            os.remove("test_greedy_output.json")


if __name__ == "__main__":
    unittest.main()
