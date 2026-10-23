"""
test_tooling_selection_schema.py - Contract tests for tooling_selection.schema.json (Req 4 Phase E).
"""

import unittest
from copy import deepcopy

from smbs_core.protocols.file_store import ContractError, FileStore


class TestToolingSelectionSchema(unittest.TestCase):
    def setUp(self):
        self.file_store = FileStore()
        self.valid_payload = {
            "schema_name": "tooling_selection",
            "schema_version": "1.0",
            "run_id": "run-test-001",
            "part_id": "part-test-box",
            "input_fingerprint": "sha256:dummyfingerprint",
            "producer": {"name": "tooling_selection_solver", "version": "1.0"},
            "units": "mm",
            "frame_convention": "STANDARD_CARTESIAN_MM",
            "status": "SUCCESS",
            "diagnostics": [],
            "created_at": "2026-10-21T22:00:00Z",
            "content_hash": "dummy_content_hash_123",
            "payload": {
                "objective": "MIN_TOOL_CHANGES",
                "assignments": [
                    {
                        "bend_id": "bend_1",
                        "punch_id": "punch_p1",
                        "die_id": "die_d1",
                        "segment_composition": [
                            {"length_mm": 100.0, "start_mm": 0.0, "end_mm": 100.0, "segment_id": "seg_100"},
                            {"length_mm": 50.0, "start_mm": 100.0, "end_mm": 150.0, "segment_id": None}
                        ],
                        "clamp_validation": {
                            "valid": True,
                            "errors": [],
                            "warnings": []
                        },
                        "changes_from_previous": {
                            "punch_changed": False,
                            "die_changed": False
                        },
                        "tool_change_count": 0
                    },
                    {
                        "bend_id": "bend_2",
                        "punch_id": "punch_p1",
                        "die_id": "die_d2",
                        "segment_composition": [
                            {"length_mm": 150.0, "start_mm": 0.0, "end_mm": 150.0}
                        ],
                        "clamp_validation": {
                            "valid": True,
                            "errors": []
                        },
                        "changes_from_previous": {
                            "punch_changed": False,
                            "die_changed": True
                        },
                        "tool_change_count": 1
                    }
                ],
                "total_tool_changes": 1,
                "warnings": []
            }
        }

    def test_happy_path_valid(self):
        """Case 1: Valid payload with assignments passes validation."""
        self.file_store.validate(self.valid_payload, "tooling_selection")

    def test_missing_required_top_level(self):
        """Case 2: Missing mandatory top-level field (run_id) raises ContractError."""
        invalid = deepcopy(self.valid_payload)
        del invalid["run_id"]
        with self.assertRaises(ContractError):
            self.file_store.validate(invalid, "tooling_selection")

    def test_empty_assignments_allowed_for_unknown_or_failed(self):
        """Case 3: Empty assignments array is valid (e.g., when no solution or status=UNKNOWN)."""
        valid_empty = deepcopy(self.valid_payload)
        valid_empty["status"] = "UNKNOWN"
        valid_empty["payload"]["assignments"] = []
        valid_empty["payload"]["total_tool_changes"] = 0
        self.file_store.validate(valid_empty, "tooling_selection")

    def test_empty_segment_composition_fails(self):
        """Case 4: Empty segment_composition violates minItems: 1."""
        invalid = deepcopy(self.valid_payload)
        invalid["payload"]["assignments"][0]["segment_composition"] = []
        with self.assertRaises(ContractError):
            self.file_store.validate(invalid, "tooling_selection")

    def test_clamp_validation_type_mismatch(self):
        """Case 5: clamp_validation.valid being string instead of boolean raises ContractError."""
        invalid = deepcopy(self.valid_payload)
        invalid["payload"]["assignments"][0]["clamp_validation"]["valid"] = "yes"
        with self.assertRaises(ContractError):
            self.file_store.validate(invalid, "tooling_selection")

    def test_invalid_objective_enum(self):
        """Case 6: Unsupported objective enum raises ContractError."""
        invalid = deepcopy(self.valid_payload)
        invalid["payload"]["objective"] = "FASTEST_SPEED"
        with self.assertRaises(ContractError):
            self.file_store.validate(invalid, "tooling_selection")

    def test_segment_composition_length_zero_or_negative_fails(self):
        """Case 7: segment length_mm <= 0 violates exclusiveMinimum: 0."""
        invalid = deepcopy(self.valid_payload)
        invalid["payload"]["assignments"][0]["segment_composition"][0]["length_mm"] = 0.0
        with self.assertRaises(ContractError):
            self.file_store.validate(invalid, "tooling_selection")


if __name__ == "__main__":
    unittest.main()
