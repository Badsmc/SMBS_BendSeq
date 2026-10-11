"""
test_stage7_kinematics.py - Stage 7 Kinematics (Approach & Retract)

Verifies:
- Approach collision check correctly flags intersection during translation.
- Retract collision check correctly flags intersection during extraction.
"""

import sys
import os
import unittest
from typing import Any, Tuple

from smbs_core.Physics.collision_detector import CollisionDetector
from smbs_core.Physics.machine_kinematics import PressBrakeMachine
from smbs_core.geometry_backend.backends.mock_backend import MockBackend

class DummyShape:
    def __init__(self, collides: bool = False):
        self.collides = collides

class KinematicsMockBackend(MockBackend):
    def get_bounding_box(self, shape: Any) -> Tuple[float, float, float, float, float, float]:
        return (-1000.0, -1000.0, -1000.0, 1000.0, 1000.0, 1000.0)

    def transform_shape(self, shape: Any, translation: Tuple[float, float, float], 
                        axis_origin: Tuple[float, float, float] = (0.0, 0.0, 0.0),
                        axis_dir: Tuple[float, float, float] = (0.0, 0.0, 1.0),
                        angle_deg: float = 0.0) -> Any:
        return DummyShape(collides=True)

    def intersect_volume(self, shape1: Any, shape2: Any) -> bool:
        if isinstance(shape1, DummyShape) and shape1.collides:
            return True
        if isinstance(shape2, DummyShape) and shape2.collides:
            return True
        return False

class TestStage7Kinematics(unittest.TestCase):
    def setUp(self):
        self.machine = PressBrakeMachine()
        
        # Inject mock punch
        class MockPunch:
            def __init__(self):
                self.topo_shape = DummyShape(collides=False)
        self.machine.punch = MockPunch()
        self.machine.die = None
        
        self.backend = KinematicsMockBackend()
        self.detector = CollisionDetector(self.machine, self.backend)
        
    def test_approach_and_retract(self):
        # Override _align_shape_to_tool to return the original shape
        self.detector._align_shape_to_tool = lambda s, b: s
        
        shape = DummyShape(collides=False)
        bend_info = {}
        
        app_coll, msg = self.detector.approach_collision(shape, bend_info)
        self.assertTrue(app_coll)
        
        ret_coll, msg = self.detector.retract_collision(shape, bend_info)
        self.assertTrue(ret_coll)

if __name__ == "__main__":
    unittest.main()
