import unittest
from smbs_core.Physics.collision_detector import CollisionDetector, CollisionStatus
from smbs_core.geometry_backend.backends.mock_backend import MockBackend

class ExceptionRaisingBackend(MockBackend):
    def intersect_volume(self, shape1, shape2):
        raise RuntimeError("Simulated OCC Error")

class TestPhase1Validator(unittest.TestCase):
    def test_missing_geometry_returns_unknown(self):
        detector = CollisionDetector(assembly=None, backend=MockBackend())
        res = detector._check_intersection(None, None)
        self.assertEqual(res.status, CollisionStatus.UNKNOWN)
        self.assertFalse(res.is_valid)

    def test_empty_panels_returns_unknown(self):
        detector = CollisionDetector(assembly=None, backend=MockBackend())
        res = detector.self_collision(shape="dummy_shape", bend_id=None, panel_graph=None)
        self.assertEqual(res.status, CollisionStatus.UNKNOWN)
        self.assertFalse(res.is_valid)

    def test_no_assembly_returns_unknown(self):
        detector = CollisionDetector(assembly=None, backend=MockBackend())
        bend_info = {"angle": 90.0, "origin": (0, 0, 0), "axis": (1, 0, 0)}
        self.assertEqual(detector.approach_collision("shape", bend_info).status, CollisionStatus.UNKNOWN)
        self.assertEqual(detector.tool_collision("shape", bend_info).status, CollisionStatus.UNKNOWN)
        self.assertEqual(detector.machine_collision("shape", bend_info).status, CollisionStatus.UNKNOWN)
        self.assertEqual(detector.trajectory_collision("shape", bend_info).status, CollisionStatus.UNKNOWN)

    def test_trajectory_collision_not_always_valid(self):
        detector = CollisionDetector(assembly=None, backend=MockBackend())
        bend_info = {"angle": 90.0, "origin": (0, 0, 0), "axis": (1, 0, 0)}
        res = detector.trajectory_collision("shape", bend_info)
        self.assertEqual(res.status, CollisionStatus.UNKNOWN)
        self.assertFalse(res.is_valid)

    def test_occ_exception_returns_unknown(self):
        detector = CollisionDetector(assembly=None, backend=ExceptionRaisingBackend())
        res = detector._check_intersection("shape1", "shape2")
        self.assertEqual(res.status, CollisionStatus.UNKNOWN)
        self.assertFalse(res.is_valid)

    def test_validator_propagates_unknown(self):
        from smbs_core.Physics.validator import PhysicalValidator
        backend = MockBackend()
        validator = PhysicalValidator(machine=None, backend=backend)
        self.assertIn(validator.tooling_status.value, ["missing", "simplified"])

if __name__ == "__main__":
    unittest.main()
