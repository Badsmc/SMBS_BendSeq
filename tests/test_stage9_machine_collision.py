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

from smbs_core.Physics.collision_detector import CollisionDetector, CollisionStatus
from smbs_core.Physics.machine_kinematics import PressBrakeMachine
from smbs_core.models.tooling import GeometryStatus
from smbs_core.geometry_backend.backends.mock_backend import MockBackend
from smbs_core.geometry_backend.base import CollisionCheckResult

class DummyShape:
    def __init__(self, bb: Tuple[float, float, float, float, float, float]):
        self.bb = bb

class MachineMockBackend(MockBackend):
    def get_bounding_box(self, shape: Any) -> Tuple[float, float, float, float, float, float]:
        if isinstance(shape, DummyShape):
            return shape.bb
        return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    def intersect_volume(self, shape1: Any, shape2: Any) -> CollisionCheckResult:
        for s in (shape1, shape2):
            if isinstance(s, DummyShape):
                xmin, ymin, zmin, xmax, ymax, zmax = s.bb
                if max(abs(xmax - xmin), abs(ymax - ymin)) > 2000.0:
                    return CollisionCheckResult(collides=True, status="invalid", volume=100.0, message="Part exceeds machine envelope")
        return CollisionCheckResult(collides=False, status="valid", volume=0.0, message="Clear")

class TestStage9MachineCollision(unittest.TestCase):
    def setUp(self):
        self.machine = PressBrakeMachine(
            bed_length_mm=2000.0,
            backgauge_x_max_mm=800.0
        )
        self.backend = MachineMockBackend()
        self.detector = CollisionDetector(None, self.backend)

    def test_machine_collision_no_assembly_returns_unknown(self):
        """Без assembly → UNKNOWN, никогда не VALID."""
        self.detector.assembly = None
        shape = DummyShape((-500.0, -900.0, 0.0, 500.0, 900.0, 10.0))
        res = self.detector.machine_collision(shape, {})
        self.assertFalse(res.is_valid)
        self.assertEqual(res.status, CollisionStatus.UNKNOWN)

    def test_machine_envelope_limits(self):
        """С non-SIMPLIFIED mock shapes: valid_box → VALID, oversized_box → INVALID."""
        assembly = self.machine.setup_assembly(self.backend)
        assembly.punch.geometry_status = GeometryStatus.REAL
        assembly.die.geometry_status = GeometryStatus.REAL
        assembly.get_world_shape = lambda kind: DummyShape((-1000.0, -1000.0, -1000.0, 1000.0, 1000.0, 1000.0))
        self.detector.assembly = assembly

        # Valid box within bed length
        valid_shape = DummyShape((-500.0, -900.0, 0.0, 500.0, 900.0, 10.0))
        res_valid = self.detector.machine_collision(valid_shape, {})
        self.assertTrue(res_valid.is_valid)
        self.assertEqual(res_valid.status, CollisionStatus.VALID)

        # Oversized box exceeding bed length (4000x100x100)
        oversized_shape = DummyShape((-2000.0, -50.0, 0.0, 2000.0, 50.0, 100.0))
        res_over = self.detector.machine_collision(oversized_shape, {})
        self.assertFalse(res_over.is_valid)
        self.assertEqual(res_over.status, CollisionStatus.INVALID)

if __name__ == "__main__":
    unittest.main()
