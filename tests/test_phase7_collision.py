import unittest
from smbs_core.Physics.collision_detector import CollisionDetector, CollisionStatus, CollisionResult
from smbs_core.Physics.machine_kinematics import PressBrakeMachine
from smbs_core.geometry_backend.backends.mock_backend import MockBackend

class CollidingBackend(MockBackend):
    def intersect_volume(self, shape1, shape2):
        from smbs_core.geometry_backend.base import CollisionCheckResult
        if shape1 is None or shape2 is None:
            return CollisionCheckResult(collides=False, status="unknown", volume=0.0, message="Missing geometry")
        return CollisionCheckResult(collides=True, status="invalid", volume=15.0, message="Intersection detected")

class ExceptionBackend(MockBackend):
    def intersect_volume(self, shape1, shape2):
        raise RuntimeError("OCC B-Rep error")

class TestPhase7Collision(unittest.TestCase):
    def setUp(self):
        self.backend = MockBackend()
        self.machine = PressBrakeMachine()

    def test_none_shape_unknown(self):
        detector = CollisionDetector(self.machine, self.backend)
        res = detector._check_intersection(None, "shape2")
        self.assertEqual(res.status, CollisionStatus.UNKNOWN)
        self.assertFalse(res.is_valid)

    def test_occ_failure_unknown(self):
        detector = CollisionDetector(self.machine, ExceptionBackend())
        res = detector._check_intersection("shape1", "shape2")
        self.assertEqual(res.status, CollisionStatus.UNKNOWN)
        self.assertFalse(res.is_valid)

    def test_part_vs_die_wall_collision_invalid(self):
        detector = CollisionDetector(self.machine, CollidingBackend())
        bend_info = {"angle": 90.0, "origin": (0, 0, 0), "axis": (1, 0, 0)}
        res = detector.tool_collision("shape", bend_info)
        self.assertEqual(res.status, CollisionStatus.INVALID)
        self.assertFalse(res.is_valid)

    def test_missing_tooling_geometry_unknown(self):
        detector = CollisionDetector(assembly=None, backend=self.backend)
        bend_info = {"angle": 90.0, "origin": (0, 0, 0), "axis": (1, 0, 0)}
        res = detector.approach_collision("shape", bend_info)
        self.assertEqual(res.status, CollisionStatus.UNKNOWN)
        self.assertFalse(res.is_valid)

    def test_backgauge_collision_invalid(self):
        detector = CollisionDetector(self.machine, CollidingBackend())
        res = detector.backgauge_collision("part_shape", "backgauge_finger_shape")
        self.assertEqual(res.status, CollisionStatus.INVALID)
        self.assertFalse(res.is_valid)

if __name__ == "__main__":
    unittest.main()
