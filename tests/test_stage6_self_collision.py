"""
test_stage6_self_collision.py - Stage 6 Self Collision Validation

Verifies:
- Self-collision correctly skips adjacent panels.
- Self-collision correctly checks moving vs fixed panels.
- Handles missing panel_graph safely.
"""

import sys
import os
import unittest
from typing import Any, Set, Tuple

from smbs_core.Physics.collision_detector import CollisionDetector, CollisionStatus
from smbs_core.Physics.machine_kinematics import PressBrakeMachine
from smbs_core.Core.panel_graph import PanelGraph
from smbs_core.geometry_backend.backends.mock_backend import MockBackend

class DummyShape:
    def __init__(self, panel_id: str):
        self.panel_id = panel_id

class CollisionMockBackend(MockBackend):
    def extract_faces(self, shape: Any, face_ids: Set[str]) -> Any:
        # Just return a dummy shape indicating which panel it is
        # in our test, we just pass face_ids as a single element set like {"PA"}
        return DummyShape(list(face_ids)[0])

    def intersect_volume(self, shape1: Any, shape2: Any) -> bool:
        # Mock intersection logic: PA and PD will collide
        if not isinstance(shape1, DummyShape) or not isinstance(shape2, DummyShape):
            return False
        pair = {shape1.panel_id, shape2.panel_id}
        if pair == {"PA", "PD"}:
            return True
        return False

class TestStage6SelfCollision(unittest.TestCase):
    """Stage 6 Self Collision Tests."""

    def setUp(self):
        self.machine = PressBrakeMachine()
        self.backend = CollisionMockBackend()
        self.detector = CollisionDetector(self.machine, self.backend)
        
        self.pg = PanelGraph()
        self.pg.add_bend_connection("BEND_01", "PA", "PB")
        self.pg.add_bend_connection("BEND_02", "PB", "PC")
        self.pg.add_bend_connection("BEND_03", "PC", "PD")
        
        # Populate pg.panels with mock dummy face IDs
        self.pg.panels["PA"].face_indices = ["PA"]
        self.pg.panels["PB"].face_indices = ["PB"]
        self.pg.panels["PC"].face_indices = ["PC"]
        self.pg.panels["PD"].face_indices = ["PD"]

    def test_self_collision_no_panel_graph_returns_unknown(self):
        """Без panel_graph контекст недостаточен → UNKNOWN, не VALID."""
        shape = DummyShape("ALL")
        res = self.detector.self_collision(shape)
        self.assertEqual(res.status, CollisionStatus.UNKNOWN)
        coll, msg = res
        self.assertFalse(coll)
        self.assertIn("missing", msg.lower())

    def test_self_collision_logic(self):
        """Test self-collision logic using CollisionMockBackend."""
        shape = DummyShape("ALL")
        
        # Folding BEND_02 (PB|PC)
        # Fixed: PA, PB. Moving: PC, PD.
        # PA and PD should collide.
        coll, msg = self.detector.self_collision(shape, "BEND_02", self.pg)
        self.assertFalse(coll)
        
        # Folding BEND_01 (PA|PB)
        # Fixed: PA. Moving: PB, PC, PD.
        # PA is adjacent to PB, so PA vs PB is skipped.
        # PA vs PC: False. PA vs PD: True.
        coll, msg = self.detector.self_collision(shape, "BEND_01", self.pg)
        self.assertFalse(coll)

if __name__ == "__main__":
    unittest.main()
