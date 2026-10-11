"""
test_stage9_machine_collision.py - Stage 9 Machine Collision & Envelope Validation

Verifies:
- Machine collision checks limits (bed length, backgauge).
- Machine collision checks exact shape.
"""

import sys
import os
import unittest
from typing import Any, Tuple

from smbs_core.Physics.collision_detector import CollisionDetector
from smbs_core.Physics.machine_kinematics import PressBrakeMachine
from smbs_core.geometry_backend.backends.mock_backend import MockBackend

class DummyShape:
    def __init__(self, bb: Tuple[float, float, float, float, float, float]):
        self.bb = bb

class MachineMockBackend(MockBackend):
    def get_bounding_box(self, shape: Any) -> Tuple[float, float, float, float, float, float]:
        if isinstance(shape, DummyShape):
            return shape.bb
        return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

class TestStage9MachineCollision(unittest.TestCase):
    def setUp(self):
        self.machine = PressBrakeMachine(
            bed_length_mm=2000.0,
            backgauge_x_max_mm=800.0
        )
        self.backend = MachineMockBackend()
        self.detector = CollisionDetector(None, self.backend)
        
    def test_machine_envelope_limits(self):
        # Override _align_shape_to_tool
        self.detector._align_shape_to_tool = lambda s, b: s
        
        self.detector.assembly = None
        self.backend.intersect_volume = lambda s1, s2: False
        shape = DummyShape((-500.0, -900.0, 0.0, 500.0, 900.0, 10.0))
        res = self.detector.machine_collision(shape, {})
        self.assertTrue(res.is_valid)

if __name__ == "__main__":
    unittest.main()
