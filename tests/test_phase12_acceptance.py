import unittest
from smbs_core.Core.orchestrator import BendSeqOrchestrator
from smbs_core.geometry_backend.backends.mock_backend import MockBackend

class TestPhase12Acceptance(unittest.TestCase):
    def setUp(self):
        self.backend = MockBackend()
        self.orchestrator = BendSeqOrchestrator(self.backend)

    def test_clean_environment_imports(self):
        import smbs_core.cli
        import smbs_core.Core.orchestrator
        import smbs_core.Physics.validator
        import smbs_core.Solvers.astar_backward
        self.assertIsNotNone(smbs_core.cli)

    def test_orchestrator_returns_honest_status(self):
        shape = self.backend.make_box(50, 50, 2)
        res = self.orchestrator.run_planning(target_shape=shape, output_file="test_acc.json")
        self.assertIn(res["status"], ["SUCCESS", "PARTIAL", "FAILED"])
        self.assertIn("validation", res)
        self.assertIn("diagnostics", res)

if __name__ == "__main__":
    unittest.main()
