"""
test_coordinator_tooling_selection.py - Integration tests for tooling_selection in Coordinator (Req 4 Phase E, S.30c).
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from smbs_core.coordinator.run_pipeline import Coordinator
from smbs_core.geometry_backend.io.clamp_validator import ClampConfig
from smbs_core.geometry_backend.io.tooling_catalog import ToolDescriptor
from smbs_core.protocols.file_store import FileStore
from smbs_core.tooling.selection import ToolingSelectionError


class TestCoordinatorToolingSelection(unittest.TestCase):
    def setUp(self):
        self.coordinator = Coordinator()
        self.run_dir = Path("/tmp/test_coordinator_tooling_run")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = "run_test_tooling_sel_001"
        self.part_id = "part_box_01"
        self.input_fingerprint = "sha256:dummysha"

        self.search_result = {
            "payload": {
                "candidate_sequence": [
                    {"step": 1, "bend_id": "bend_1"},
                    {"step": 2, "bend_id": "bend_2"}
                ]
            }
        }
        self.canonical_part = {
            "payload": {
                "bends": {
                    "bend_1": {"length_mm": 262.5, "radius_mm": 1.0, "angle_deg": 88.0, "thickness_mm": 2.0},
                    "bend_2": {"length_mm": 312.5, "radius_mm": 1.0, "angle_deg": 88.0, "thickness_mm": 2.0}
                }
            }
        }
        self.punch_desc = ToolDescriptor(
            tool_id="p1", kind="punch", fcstd_path=Path("/tmp/p1.FCStd"),
            sidecar_path=None, has_sidecar=True,
            metadata={"radius_mm": 1.0, "angle_deg": 88.0, "height_mm": 100.0}
        )
        self.die_desc = ToolDescriptor(
            tool_id="d1", kind="die", fcstd_path=Path("/tmp/d1.FCStd"),
            sidecar_path=None, has_sidecar=True,
            metadata={"v_width_mm": 12.0, "angle_deg": 88.0, "height_mm": 60.0}
        )
        self.clamps_config = ClampConfig.from_defaults(positions_mm=(75.0, 275.0, 475.0))

    def test_singleton_path_success(self):
        """Case 1: Library tooling provided + valid clamps -> 12_tooling_selection.json with status=SUCCESS."""
        machine_config = {
            "clamps_config": self.clamps_config,
            "segment_catalog": [100.0, 50.0, 20.0, 15.0, 10.0]
        }
        out_file = self.coordinator._write_tooling_selection(
            run_dir=self.run_dir,
            run_id=self.run_id,
            part_id=self.part_id,
            input_fingerprint=self.input_fingerprint,
            search_result=self.search_result,
            canonical_part=self.canonical_part,
            machine_config=machine_config,
            punch_desc=self.punch_desc,
            die_desc=self.die_desc
        )
        self.assertTrue(out_file.exists())
        self.assertEqual(out_file.name, "12_tooling_selection.json")

        data = FileStore().read_json_verified(out_file, expected_schema_name="tooling_selection")
        self.assertEqual(data["status"], "SUCCESS")
        self.assertEqual(len(data["payload"]["assignments"]), 2)
        self.assertEqual(data["payload"]["total_tool_changes"], 0)

    def test_path_based_tooling_produces_unknown(self):
        """Case 2: Path-based tooling without resolved descriptors -> status=UNKNOWN, empty assignments."""
        out_file = self.coordinator._write_tooling_selection(
            run_dir=self.run_dir,
            run_id=self.run_id,
            part_id=self.part_id,
            input_fingerprint=self.input_fingerprint,
            search_result=self.search_result,
            canonical_part=self.canonical_part,
            machine_config={"punch_path": "/tmp/punch.FCStd", "die_path": "/tmp/die.FCStd"},
            punch_desc=None,
            die_desc=None
        )
        self.assertTrue(out_file.exists())
        data = FileStore().read_json_verified(out_file, expected_schema_name="tooling_selection")
        self.assertEqual(data["status"], "UNKNOWN")
        self.assertEqual(len(data["payload"]["assignments"]), 0)
        self.assertTrue(any("path-based input not supported" in d for d in data["diagnostics"]))

    def test_clamps_config_missing_records_error_and_unknown(self):
        """Case 3: Library tooling but clamps_config=None -> ToolingSelectionError caught, status=UNKNOWN."""
        machine_config = {
            "clamps_config": None
        }
        out_file = self.coordinator._write_tooling_selection(
            run_dir=self.run_dir,
            run_id=self.run_id,
            part_id=self.part_id,
            input_fingerprint=self.input_fingerprint,
            search_result=self.search_result,
            canonical_part=self.canonical_part,
            machine_config=machine_config,
            punch_desc=self.punch_desc,
            die_desc=self.die_desc
        )
        self.assertTrue(out_file.exists())
        data = FileStore().read_json_verified(out_file, expected_schema_name="tooling_selection")
        self.assertEqual(data["status"], "UNKNOWN")
        self.assertEqual(len(data["payload"]["assignments"]), 0)
        self.assertTrue(any("enforcement requires clamps_config" in d.lower() for d in data["diagnostics"]))


if __name__ == "__main__":
    unittest.main()
