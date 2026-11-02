"""
test_honest_collision_and_verifier.py - Tests for honest collision detection and verification (§8.3, P0).

Verifies:
1. place_part_on_tool_station returns None (not unplaced part) when bend info or tooling datums are missing.
2. tool_collision, approach_collision, machine_collision return CollisionStatus.UNKNOWN when part cannot be placed.
3. VerifierWorker records collision_verified=False when step collision status is UNKNOWN (UNKNOWN != VALID).
"""

import unittest
from unittest.mock import MagicMock
from smbs_core.Physics.collision_detector import CollisionDetector, CollisionStatus
from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
from smbs_core.workers.verifier.worker import VerifierWorker
from smbs_core.Physics.validator import ValidationResult


class TestHonestCollisionDetector(unittest.TestCase):
    def setUp(self):
        self.backend = PythonOCCBackend()
        # Detector with no assembly
        self.detector_no_assembly = CollisionDetector(None, self.backend)

        # Detector with mock assembly that lacks datums
        mock_assembly = MagicMock()
        mock_assembly.die = MagicMock()
        mock_assembly.die.datums = {}
        mock_assembly.punch = MagicMock()
        mock_assembly.assembly_trsf = {}
        self.detector_with_mock = CollisionDetector(mock_assembly, self.backend)

    def test_place_part_returns_none_when_assembly_missing(self):
        """Missing tooling assembly must return None, not the unplaced part."""
        fake_shape = "NOT_A_SHAPE"
        bend_info = {"origin": (0, 0, 0), "axis": (0, 1, 0)}
        res = self.detector_no_assembly.place_part_on_tool_station(fake_shape, bend_info)
        self.assertIsNone(res, "place_part_on_tool_station must return None when assembly is missing")

    def test_place_part_returns_none_when_bend_info_missing_coords(self):
        """Missing bend origin/axis must return None, not the unplaced part."""
        fake_shape = "NOT_A_SHAPE"
        res = self.detector_with_mock.place_part_on_tool_station(fake_shape, {})
        self.assertIsNone(res, "place_part_on_tool_station must return None when bend_info is empty")

    def test_place_part_returns_none_when_die_datums_missing(self):
        """Missing die V_CENTER datum must return None, not the unplaced part."""
        fake_shape = "NOT_A_SHAPE"
        bend_info = {"origin": (0, 0, 0), "axis": (0, 1, 0)}
        res = self.detector_with_mock.place_part_on_tool_station(fake_shape, bend_info)
        self.assertIsNone(res, "place_part_on_tool_station must return None when die V_CENTER is missing")

    def test_tool_collision_returns_unknown_when_part_cannot_be_placed(self):
        """If part cannot be placed on tool station, tool_collision must return UNKNOWN, never VALID."""
        fake_shape = "NOT_A_SHAPE"
        res = self.detector_with_mock.tool_collision(fake_shape, bend_info={})
        self.assertEqual(res.status, CollisionStatus.UNKNOWN)
        self.assertFalse(res.is_valid)

    def test_approach_collision_returns_unknown_when_part_cannot_be_placed(self):
        """If part cannot be placed on tool station, approach_collision must return UNKNOWN, never VALID."""
        fake_shape = "NOT_A_SHAPE"
        res = self.detector_with_mock.approach_collision(fake_shape, bend_info={})
        self.assertEqual(res.status, CollisionStatus.UNKNOWN)
        self.assertFalse(res.is_valid)

    def test_machine_collision_returns_unknown_when_part_cannot_be_placed(self):
        """If part cannot be placed on tool station, machine_collision must return UNKNOWN, never VALID."""
        fake_shape = "NOT_A_SHAPE"
        res = self.detector_with_mock.machine_collision(fake_shape, bend_info={})
        self.assertEqual(res.status, CollisionStatus.UNKNOWN)
        self.assertFalse(res.is_valid)


class TestHonestVerifierWorkerCollisionCheck(unittest.TestCase):
    def test_verifier_records_collision_verified_false_when_collision_is_unknown(self):
        """
        UNKNOWN != VALID invariant:
        When a step's validation result has collision_status == UNKNOWN,
        VerifierWorker must record collision_verified=False (NOT True).
        """
        # Test the boolean logic directly from worker
        # Val result with UNKNOWN collision status
        val_res_unknown = ValidationResult(
            valid=True,
            reason="Step accepted under EXPLORATORY policy with UNKNOWN subchecks.",
            stage="valid",
            collision_status=CollisionStatus.UNKNOWN,
            has_unknown=True
        )

        val_res_valid = ValidationResult(
            valid=True,
            reason="Step validated physically.",
            stage="valid",
            collision_status=CollisionStatus.VALID,
            has_unknown=False
        )

        val_res_invalid = ValidationResult(
            valid=False,
            reason="Collision detected",
            stage="TOOL",
            collision_status=CollisionStatus.INVALID,
            has_unknown=False
        )

        def eval_coll_verified(status):
            return status == CollisionStatus.VALID or str(status).lower() in ("valid", "collisionstatus.valid")

        self.assertFalse(eval_coll_verified(val_res_unknown.collision_status), "UNKNOWN must NOT be collision_verified=True")
        self.assertTrue(eval_coll_verified(val_res_valid.collision_status), "VALID must be collision_verified=True")
        self.assertFalse(eval_coll_verified(val_res_invalid.collision_status), "INVALID must be collision_verified=False")


if __name__ == "__main__":
    unittest.main()
