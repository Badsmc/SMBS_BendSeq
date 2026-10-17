import unittest
from smbs_core.Physics.collision_detector import CollisionDetector, CollisionStatus, CollisionResult
from smbs_core.models.tooling import GeometryStatus
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
        # Explicitly configure tooling as REAL with mock shapes so collision check is performed
        detector.assembly.punch.geometry_status = GeometryStatus.REAL
        detector.assembly.die.geometry_status = GeometryStatus.REAL
        detector.assembly.get_world_shape = lambda kind: f"mock_{kind.value}_shape"
        bend_info = {"angle": 90.0, "origin": (0, 0, 0), "axis": (1, 0, 0)}
        res = detector.tool_collision("shape", bend_info)
        self.assertEqual(res.status, CollisionStatus.INVALID)
        self.assertFalse(res.is_valid)

    def test_tool_collision_simplified_returns_unknown(self):
        detector = CollisionDetector(self.machine, self.backend)
        bend_info = {"angle": 90.0, "origin": (0, 0, 0), "axis": (1, 0, 0)}
        res = detector.tool_collision("shape", bend_info)
        self.assertEqual(res.status, CollisionStatus.UNKNOWN)
        self.assertFalse(res.is_valid)
        self.assertIn("simplified", res.message.lower())

    def test_machine_collision_no_frame_returns_unknown(self):
        detector = CollisionDetector(self.machine, self.backend)
        # Ensure machine frame and bed are None
        detector.assembly.get_world_shape = lambda kind: None
        bend_info = {"angle": 90.0, "origin": (0, 0, 0), "axis": (1, 0, 0)}
        res = detector.machine_collision("shape", bend_info)
        self.assertEqual(res.status, CollisionStatus.UNKNOWN)
        self.assertFalse(res.is_valid)
        self.assertIn("no machine_frame shape", res.message.lower())

    def test_approach_collision_simplified_returns_unknown(self):
        detector = CollisionDetector(self.machine, self.backend)
        bend_info = {"angle": 90.0, "origin": (0, 0, 0), "axis": (1, 0, 0)}
        res = detector.approach_collision("shape", bend_info)
        self.assertEqual(res.status, CollisionStatus.UNKNOWN)
        self.assertFalse(res.is_valid)
        self.assertIn("simplified", res.message.lower())

    def test_self_collision_all_panels_skipped_returns_unknown(self):
        from smbs_core.Core.panel_graph import PanelGraph
        detector = CollisionDetector(self.machine, self.backend)
        pg = PanelGraph()
        pg.add_bend_connection("B1", "P1", "P2")  # adjacent only
        pg.panels["P1"].face_indices = ["F1"]
        pg.panels["P2"].face_indices = ["F2"]
        # In a single-bend graph P1 and P2 are adjacent, so 0 non-adjacent pairs exist
        res = detector.self_collision("shape", bend_id="B1", panel_graph=pg)
        self.assertEqual(res.status, CollisionStatus.UNKNOWN)
        self.assertFalse(res.is_valid)
        self.assertIn("no non-adjacent panel pairs", res.message.lower())

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
