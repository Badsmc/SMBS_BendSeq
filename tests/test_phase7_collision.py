import unittest
from smbs_core.Physics.collision_detector import CollisionDetector, CollisionStatus, CollisionResult
from smbs_core.models.tooling import GeometryStatus, ToolingKind
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
        self.assertIn("no machine_frame or machine_bed shape", res.message.lower())

    def test_machine_collision_missing_frame_but_valid_bed_returns_valid(self):
        detector = CollisionDetector(self.machine, self.backend)
        detector.assembly.get_world_shape = lambda kind: "mock_bed" if kind == ToolingKind.MACHINE_BED else None
        bend_info = {"angle": 90.0, "origin": (0, 0, 0), "axis": (1, 0, 0)}
        res = detector.machine_collision("shape", bend_info)
        self.assertEqual(res.status, CollisionStatus.VALID)
        self.assertTrue(res.is_valid)

    def test_machine_collision_missing_both_returns_unknown(self):
        detector = CollisionDetector(self.machine, self.backend)
        detector.assembly.get_world_shape = lambda kind: None
        bend_info = {"angle": 90.0, "origin": (0, 0, 0), "axis": (1, 0, 0)}
        res = detector.machine_collision("shape", bend_info)
        self.assertEqual(res.status, CollisionStatus.UNKNOWN)
        self.assertFalse(res.is_valid)

    def test_machine_collision_invalid_bed_with_missing_frame_returns_invalid(self):
        detector = CollisionDetector(self.machine, CollidingBackend())
        detector.assembly.get_world_shape = lambda kind: "mock_bed" if kind == ToolingKind.MACHINE_BED else None
        bend_info = {"angle": 90.0, "origin": (0, 0, 0), "axis": (1, 0, 0)}
        res = detector.machine_collision("shape", bend_info)
        self.assertEqual(res.status, CollisionStatus.INVALID)
        self.assertFalse(res.is_valid)

    def test_approach_collision_simplified_returns_unknown(self):
        detector = CollisionDetector(self.machine, self.backend)
        bend_info = {"angle": 90.0, "origin": (0, 0, 0), "axis": (1, 0, 0)}
        res = detector.approach_collision("shape", bend_info)
        self.assertEqual(res.status, CollisionStatus.UNKNOWN)
        self.assertFalse(res.is_valid)
        self.assertIn("simplified", res.message.lower())

    def test_self_collision_all_panels_adjacent_returns_valid(self):
        from smbs_core.Core.panel_graph import PanelGraph
        detector = CollisionDetector(self.machine, self.backend)
        pg = PanelGraph()
        pg.add_bend_connection("B1", "P1", "P2")  # adjacent only
        pg.panels["P1"].face_indices = ["F1"]
        pg.panels["P2"].face_indices = ["F2"]
        # extract_panel_shapes returns shapes for P1 and P2
        detector._extract_panel_shapes = lambda shape, graph: {"P1": "mock_p1", "P2": "mock_p2"}
        res = detector.self_collision("shape", bend_id="B1", panel_graph=pg)
        self.assertEqual(res.status, CollisionStatus.VALID)
        self.assertTrue(res.is_valid)
        self.assertIn("all non-adjacent pair candidates are adjacent", res.message.lower())

    def test_self_collision_panels_missing_geometry_returns_unknown(self):
        from smbs_core.Core.panel_graph import PanelGraph
        detector = CollisionDetector(self.machine, self.backend)
        pg = PanelGraph()
        pg.add_bend_connection("B1", "P1", "P2")
        pg.panels["P1"].face_indices = ["F1"]
        pg.panels["P2"].face_indices = ["F2"]
        detector._extract_panel_shapes = lambda shape, graph: {}
        res = detector.self_collision("shape", bend_id="B1", panel_graph=pg)
        self.assertEqual(res.status, CollisionStatus.UNKNOWN)
        self.assertFalse(res.is_valid)

    def test_self_collision_mixed_missing_and_adjacent_returns_unknown(self):
        from smbs_core.Core.panel_graph import PanelGraph
        detector = CollisionDetector(self.machine, self.backend)
        pg = PanelGraph()
        # P1 connected to P2, P2 connected to P3
        pg.add_bend_connection("B1", "P1", "P2")
        pg.add_bend_connection("B2", "P2", "P3")
        pg.panels["P1"].face_indices = ["F1"]
        pg.panels["P2"].face_indices = ["F2"]
        pg.panels["P3"].face_indices = ["F3"]
        # In bend B1: fixed={P1}, moving={P2, P3}. Pair (P1, P2) adjacent, Pair (P1, P3) non-adjacent!
        # If P3 geometry is None -> missing geometry on non-adjacent pair candidate
        detector._extract_panel_shapes = lambda shape, graph: {"P1": "mock_p1", "P2": "mock_p2", "P3": None}
        res = detector.self_collision("shape", bend_id="B1", panel_graph=pg)
        self.assertEqual(res.status, CollisionStatus.UNKNOWN)
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
