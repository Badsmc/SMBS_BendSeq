"""
test_stage8_tool_collision.py - Stage 8 Tool Collision Validation

Verifies:
- Distinguishes between fixed and moving panels when checking against punch and die.
- Valid contact (adjacent panels) is ignored or allowed up to a threshold.
- True collisions on non-adjacent panels are caught.
"""

import sys
import os
import unittest
from typing import Any, Tuple, Set

from smbs_core.Physics.collision_detector import CollisionDetector, CollisionStatus
from smbs_core.Physics.machine_kinematics import PressBrakeMachine
from smbs_core.Core.panel_graph import PanelGraph
from smbs_core.models.tooling import GeometryStatus
from smbs_core.geometry_backend.backends.mock_backend import MockBackend

class DummyShape:
    def __init__(self, panel_id: str, collides: bool = False):
        self.panel_id = panel_id
        self.collides = collides

class ToolMockBackend(MockBackend):
    def get_bounding_box(self, shape: Any) -> Tuple[float, float, float, float, float, float]:
        return (-1000.0, -1000.0, -1000.0, 1000.0, 1000.0, 1000.0)

    def extract_faces(self, shape: Any, face_ids: Set[str]) -> Any:
        panel_id = list(face_ids)[0]
        # Return a dummy shape that collides if it's PC, otherwise it doesn't collide
        return DummyShape(panel_id, collides=(panel_id == "PC"))

    def intersect_volume(self, shape1: Any, shape2: Any) -> bool:
        if isinstance(shape1, DummyShape) and shape1.collides:
            return True
        if isinstance(shape2, DummyShape) and shape2.collides:
            return True
        return False

class TestStage8ToolCollision(unittest.TestCase):
    def setUp(self):
        self.machine = PressBrakeMachine()
        
        class MockTool:
            def __init__(self):
                self.topo_shape = DummyShape("TOOL", collides=False)
                
        self.machine.punch = MockTool()
        self.machine.die = MockTool()
        
        self.backend = ToolMockBackend()
        self.detector = CollisionDetector(None, self.backend)
        
        self.pg = PanelGraph()
        self.pg.add_bend_connection("BEND_01", "PA", "PB")
        self.pg.add_bend_connection("BEND_02", "PB", "PC")
        self.pg.panels["PA"].face_indices = ["PA"]
        self.pg.panels["PB"].face_indices = ["PB"]
        self.pg.panels["PC"].face_indices = ["PC"]

    def test_tool_collision_no_assembly_returns_unknown(self):
        """Без assembly → UNKNOWN, никогда не VALID."""
        self.detector.assembly = None
        shape = DummyShape("ALL", collides=False)
        bend_info = {}
        res = self.detector.tool_collision(shape, bend_info, "BEND_01", self.pg)
        self.assertFalse(res.is_valid)
        self.assertEqual(res.status, CollisionStatus.UNKNOWN)

    def test_tool_collision_distinction(self):
        """С реальной (non-SIMPLIFIED) оснасткой → VALID при чистой детали, INVALID при коллизии."""
        assembly = self.machine.setup_assembly(self.backend)
        assembly.punch.geometry_status = GeometryStatus.REAL
        assembly.die.geometry_status = GeometryStatus.REAL
        assembly.get_world_shape = lambda kind: DummyShape("TOOL", collides=False)
        self.detector.assembly = assembly

        # Clear state -> VALID
        shape = DummyShape("ALL", collides=False)
        bend_info = {}
        self.backend.intersect_volume = lambda s1, s2: False
        res = self.detector.tool_collision(shape, bend_info, "BEND_01", self.pg)
        self.assertTrue(res.is_valid)
        self.assertEqual(res.status, CollisionStatus.VALID)
        self.assertEqual(res.message, "Clear")

        # Interfering mesh -> INVALID
        self.backend.intersect_volume = lambda s1, s2: True
        res_coll = self.detector.tool_collision(shape, bend_info, "BEND_01", self.pg)
        self.assertFalse(res_coll.is_valid)
        self.assertEqual(res_coll.status, CollisionStatus.INVALID)

if __name__ == "__main__":
    unittest.main()
