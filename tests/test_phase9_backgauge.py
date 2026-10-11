import unittest
from smbs_core.Physics.backgauge_calculator import BackgaugeCalculator
from smbs_core.Physics.machine_kinematics import PressBrakeMachine
from smbs_core.Solvers.tool_optimizer import ToolOptimizer
from smbs_core.IO.tool_database import ToolDatabase

class TestPhase9Backgauge(unittest.TestCase):
    def setUp(self):
        self.machine = PressBrakeMachine()
        self.calculator = BackgaugeCalculator(self.machine)
        self.optimizer = ToolOptimizer(ToolDatabase())

    def test_backgauge_computed_from_geometry(self):
        bend_info = {"length": 100.0, "angle": 90.0, "geometry_confirmed": True}
        bg = self.calculator.compute_step_backgauge(1, bend_info, part_thickness=2.0)
        self.assertTrue(bg["geometry_confirmed"])
        self.assertIsNone(bg["warning"])
        self.assertGreater(bg["X_mm"], 0.0)

    def test_backgauge_marks_partial_when_uncertain(self):
        bend_info = {"length": 100.0, "angle": 90.0}
        bg = self.calculator.compute_step_backgauge(1, bend_info, part_thickness=2.0)
        self.assertFalse(bg["geometry_confirmed"])
        self.assertIsNotNone(bg["warning"])

    def test_tool_optimizer_minimizes_stations(self):
        sequence = [
            {"step_number": 1, "axis": (1, 0, 0), "tooling": {"die_id": "DIE_1"}, "backgauge": {"X_mm": 50, "R_mm": 5}},
            {"step_number": 2, "axis": (1, 0, 0), "tooling": {"die_id": "DIE_1"}, "backgauge": {"X_mm": 50, "R_mm": 5}},
            {"step_number": 3, "axis": (0, 1, 0), "tooling": {"die_id": "DIE_2"}, "backgauge": {"X_mm": 60, "R_mm": 5}},
        ]
        optimized = self.optimizer.optimize_sequence_tooling(sequence)
        self.assertEqual(len(optimized), 3)
        self.assertFalse(optimized[0]["operator_instructions"]["requires_part_flip_180"])
        self.assertTrue(optimized[2]["operator_instructions"]["requires_part_flip_180"])

if __name__ == "__main__":
    unittest.main()
