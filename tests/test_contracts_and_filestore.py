"""
Unit tests for FileStore, SHA-256 content hashing, and JSON Schemas.
Tests compliance with ТЗ v1.2 §4.2, §4.5, §7.2 and Principles 2, 8, 12, 13, 21, 23, 24.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from smbs_core.protocols.file_store import (
    FileStore,
    ContractError,
    canonicalize_json_dict,
    compute_json_content_hash,
    compute_file_sha256,
    RunStatus,
    VerificationStatus,
    ExportStatus,
)


class TestContractsAndFileStore(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.store = FileStore()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_canonical_json_determinism(self):
        """Keys order must not affect canonical representation or SHA-256 hash."""
        d1 = {"b": 2, "a": 1, "nested": {"z": 10, "y": 20}}
        d2 = {"nested": {"y": 20, "z": 10}, "a": 1, "b": 2}
        
        c1 = canonicalize_json_dict(d1)
        c2 = canonicalize_json_dict(d2)
        self.assertEqual(c1, c2)
        self.assertEqual(compute_json_content_hash(d1), compute_json_content_hash(d2))

    def test_hash_excludes_content_hash_field(self):
        """content_hash field inside dictionary must not change its own computed hash."""
        d = {"schema_name": "test", "val": 42}
        h1 = compute_json_content_hash(d)
        d_with_hash = dict(d)
        d_with_hash["content_hash"] = "dummy_value"
        h2 = compute_json_content_hash(d_with_hash)
        self.assertEqual(h1, h2)

    def test_atomic_write_and_verified_read(self):
        """Test atomic writing without leftover tmp files, and successful verified reading."""
        target_file = Path(self.test_dir) / "test_artifact.json"
        data = {
            "schema_name": "envelope",
            "schema_version": "1.1",
            "run_id": "run_001",
            "part_id": "part_123",
            "input_fingerprint": "a" * 64,
            "producer": {"name": "test_worker", "version": "1.0"},
            "units": "mm",
            "frame_convention": "STANDARD_CARTESIAN_MM",
            "status": "SUCCESS",
            "payload": {"hello": "world"},
            "diagnostics": [],
            "created_at": "2026-10-11T20:00:00Z"
        }

        content_hash = self.store.write_json_atomic(target_file, data, schema_name="envelope")
        self.assertTrue(target_file.exists())
        self.assertEqual(len(content_hash), 64)

        # Ensure no .tmp files remain
        tmp_files = list(Path(self.test_dir).glob("*.tmp"))
        self.assertEqual(len(tmp_files), 0)

        # Verified read
        read_back = self.store.read_json_verified(target_file, expected_schema_name="envelope")
        self.assertEqual(read_back["content_hash"], content_hash)
        self.assertEqual(read_back["run_id"], "run_001")

    def test_tampering_detection(self):
        """Tampering with file contents must trigger ContractError on read."""
        target_file = Path(self.test_dir) / "tampered.json"
        data = {
            "schema_name": "envelope",
            "schema_version": "1.1",
            "run_id": "run_001",
            "part_id": "part_123",
            "input_fingerprint": "a" * 64,
            "producer": {"name": "test_worker", "version": "1.0"},
            "units": "mm",
            "frame_convention": "STANDARD_CARTESIAN_MM",
            "status": "SUCCESS",
            "payload": {"val": 100},
            "diagnostics": [],
            "created_at": "2026-10-11T20:00:00Z"
        }
        self.store.write_json_atomic(target_file, data, schema_name="envelope")

        # Tamper with file
        with open(target_file, 'r', encoding='utf-8') as f:
            tampered_data = json.load(f)
        tampered_data["payload"]["val"] = 999  # Changed value without updating hash

        with open(target_file, 'w', encoding='utf-8') as f:
            json.dump(tampered_data, f)

        with self.assertRaises(ContractError) as ctx:
            self.store.read_json_verified(target_file)
        self.assertIn("Integrity check failed", str(ctx.exception))

    def test_consumer_never_repairs_producer_data(self):
        """Missing required fields in contract must raise ContractError, never repaired silently (Principle 23)."""
        target_file = Path(self.test_dir) / "invalid_envelope.json"
        invalid_data = {
            "schema_name": "envelope",
            "schema_version": "1.1",
            # missing run_id, part_id, etc.
            "status": "SUCCESS"
        }

        with self.assertRaises(ContractError):
            self.store.write_json_atomic(target_file, invalid_data, schema_name="envelope", validate=True)

    def test_canonical_part_schema(self):
        """Valid CanonicalPart document passes validation."""
        part_data = {
            "schema_name": "canonical_part",
            "schema_version": "1.1",
            "run_id": "run_42",
            "part_id": "bracket_L",
            "input_fingerprint": "b" * 64,
            "producer": {"name": "topology_worker", "version": "1.0"},
            "units": "mm",
            "frame_convention": "STANDARD_CARTESIAN_MM",
            "status": "SUCCESS",
            "diagnostics": [],
            "created_at": "2026-10-11T20:00:00Z",
            "payload": {
                "input_mode": "NEUTRAL_BREP",
                "thickness_mm": {
                    "value": 1.5,
                    "unit": "mm",
                    "source": "GEOMETRIC_INFERENCE",
                    "confidence": "CONFIRMED"
                },
                "reference_panel_id": "PANEL_01",
                "geometry_artifact": {
                    "file_path": "runs/run_42/part.brep",
                    "format": "BREP",
                    "content_hash": "c" * 64
                },
                "panels": {
                    "PANEL_01": {
                        "panel_id": "PANEL_01",
                        "area_mm2": 1500.0,
                        "centroid": [0.0, 25.0, 0.0],
                        "normal": [0.0, 0.0, 1.0],
                        "adjacent_bend_ids": ["BEND_01"]
                    },
                    "PANEL_02": {
                        "panel_id": "PANEL_02",
                        "area_mm2": 1000.0,
                        "centroid": [50.0, 25.0, 25.0],
                        "normal": [1.0, 0.0, 0.0],
                        "adjacent_bend_ids": ["BEND_01"]
                    }
                },
                "bends": {
                    "BEND_01": {
                        "bend_id": "BEND_01",
                        "parent_panel_id": "PANEL_01",
                        "child_panel_id": "PANEL_02",
                        "angle_deg": {"value": 90.0, "source": "GEOMETRIC_INFERENCE", "confidence": "CONFIRMED"},
                        "radius_mm": {"value": 1.5, "source": "GEOMETRIC_INFERENCE", "confidence": "CONFIRMED"},
                        "length_mm": {"value": 50.0, "source": "GEOMETRIC_INFERENCE", "confidence": "CONFIRMED"},
                        "direction": {"value": "UP", "source": "GEOMETRIC_INFERENCE", "confidence": "CONFIRMED"},
                        "k_factor": {"value": 0.44, "source": "DEFAULT_POLICY", "confidence": "ESTIMATED"},
                        "axis_point": [0.0, 0.0, 0.0],
                        "axis_direction": [0.0, 1.0, 0.0]
                    }
                }
            }
        }
        target_file = Path(self.test_dir) / "canonical_part.json"
        hash_val = self.store.write_json_atomic(target_file, part_data, schema_name="canonical_part")
        self.assertTrue(target_file.exists())
        self.assertEqual(len(hash_val), 64)

    def test_part_bendseq_v1_1_schema(self):
        """Valid part_bendseq v1.1 production contract passes validation."""
        bendseq_data = {
            "schema_name": "part_bendseq",
            "schema_version": "1.1",
            "run_id": "run_99",
            "part_id": "part_u_channel",
            "units": "mm",
            "input_mode": "NEUTRAL_BREP",
            "planning_status": "SUCCESS",
            "verification_status": "VERIFIED",
            "export_status": "EXPORTED",
            "production_eligible": True,
            "created_at": "2026-10-11T20:00:00Z",
            "sequence": [
                {
                    "step": 1,
                    "bend_id": "BEND_01",
                    "direction": {"value": "UP", "source": "SHEETMETAL_NATIVE", "confidence": "CONFIRMED"},
                    "angle_deg": {"value": 90.0, "source": "SHEETMETAL_NATIVE", "confidence": "CONFIRMED"},
                    "radius_mm": {"value": 2.0, "source": "SHEETMETAL_NATIVE", "confidence": "CONFIRMED"},
                    "k_factor": {"value": 0.44, "source": "SHEETMETAL_NATIVE", "confidence": "CONFIRMED"},
                    "backgauge": {
                        "x_mm": 47.5,
                        "r_mm": 0.0,
                        "z1_mm": -25.0,
                        "z2_mm": 25.0,
                        "confidence": "ESTIMATED"
                    },
                    "tool_id": "PUNCH_STD_R1",
                    "planner_status": "VALID",
                    "collision_status": "VALID",
                    "tooling_status": "REAL",
                    "alignment_status": "OK",
                    "verification_status": "VERIFIED"
                }
            ],
            "verification": {"hausdorff_mm": 0.002},
            "metadata": {
                "solver": "AStar",
                "termination_reason": "GOAL_REACHED",
                "tolerance_profile": "standard_fine",
                "commit": "7a0d87b",
                "backend": "pythonocc-core"
            }
        }
        target_file = Path(self.test_dir) / "part_bendseq.json"
        hash_val = self.store.write_json_atomic(target_file, bendseq_data, schema_name="part_bendseq")
        self.assertTrue(target_file.exists())
        self.assertEqual(len(hash_val), 64)


if __name__ == '__main__':
    unittest.main()
