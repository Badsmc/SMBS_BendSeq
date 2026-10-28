"""
test_tooling_set_validator.py - Unit tests for ToolingSet validation and changeover detection (Requirement 3.1).
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smbs_core.coordinator.run_pipeline import Coordinator
from smbs_core.geometry_backend.io.tooling_catalog import ToolDescriptor
from smbs_core.geometry_backend.io.tooling_set import ToolingSet, save_set_file
from smbs_core.tooling.set_validator import (
    ToolingSetValidationReport,
    validate_tooling_set,
)


class TestToolingSetValidator(unittest.TestCase):
    def setUp(self):
        self.punch_desc = ToolDescriptor(
            tool_id="punch_standard",
            kind="punch",
            fcstd_path=Path("/dummy/punch.FCStd"),
            sidecar_path=Path("/dummy/punch.tooling.json"),
            has_sidecar=True,
            metadata={"radius_mm": 1.0, "angle_deg": 88.0, "height_mm": 133.4},
        )
        self.die_desc = ToolDescriptor(
            tool_id="die_standard",
            kind="die",
            fcstd_path=Path("/dummy/die.FCStd"),
            sidecar_path=Path("/dummy/die.tooling.json"),
            has_sidecar=True,
            metadata={"v_width_mm": 12.0, "angle_deg": 88.0, "height_mm": 60.0},
        )
        self.tooling_set = ToolingSet(
            set_id="set_90deg",
            punch_id="punch_standard",
            die_id="die_standard",
            clamp_positions_mm=(75.0, 275.0),
            description="Standard 90 deg air bending set",
        )

    def test_fully_compatible_set(self):
        """All bends within radius and angle limits are fully compatible."""
        bends_map = {
            "B1": {"bend_id": "B1", "angle_deg": 90.0, "radius_mm": 1.5, "length_mm": 100.0},
            "B2": {"bend_id": "B2", "angle_deg": 90.0, "radius_mm": 1.0, "length_mm": 150.0},
        }
        report = validate_tooling_set(
            tooling_set=self.tooling_set,
            bends_map=bends_map,
            thickness_mm=2.0,
            punch_desc=self.punch_desc,
            die_desc=self.die_desc,
        )

        self.assertTrue(report.is_valid)
        self.assertTrue(report.fully_compatible)
        self.assertFalse(report.requires_changeover)
        self.assertEqual(sorted(report.compatible_bends), ["B1", "B2"])
        self.assertEqual(report.incompatible_bends, {})
        self.assertEqual(report.proposed_changeover_steps, [])

        # Check serialization
        d = report.to_dict()
        self.assertEqual(d["set_id"], "set_90deg")
        self.assertTrue(d["fully_compatible"])

    def test_incompatible_punch_tip_radius(self):
        """Punch tip radius exceeding bend internal radius * 1.2 is marked incompatible."""
        bends_map = {
            "B1": {"bend_id": "B1", "angle_deg": 90.0, "radius_mm": 0.5, "length_mm": 100.0},
        }
        report = validate_tooling_set(
            tooling_set=self.tooling_set,
            bends_map=bends_map,
            thickness_mm=1.0,
            punch_desc=self.punch_desc,  # radius 1.0 > 0.5 * 1.2 = 0.6
            die_desc=self.die_desc,
        )

        self.assertFalse(report.fully_compatible)
        self.assertIn("B1", report.incompatible_bends)
        self.assertTrue(any("tip radius" in r for r in report.incompatible_bends["B1"]))

    def test_incompatible_punch_angle_sharper_bend(self):
        """Punch angle greater than bend angle cannot form sharper bend without collision."""
        bends_map = {
            "B_acute": {"bend_id": "B_acute", "angle_deg": 60.0, "radius_mm": 1.0, "length_mm": 80.0},
        }
        report = validate_tooling_set(
            tooling_set=self.tooling_set,
            bends_map=bends_map,
            thickness_mm=1.5,
            punch_desc=self.punch_desc,  # angle 88° > 60°
            die_desc=self.die_desc,
        )

        self.assertFalse(report.fully_compatible)
        self.assertIn("B_acute", report.incompatible_bends)
        self.assertTrue(any("punch angle" in r for r in report.incompatible_bends["B_acute"]))

    def test_incompatible_die_v_width_too_narrow(self):
        """Die V-width below 3.5 * thickness is marked incompatible (risk of fracture)."""
        bends_map = {
            "B1": {"bend_id": "B1", "angle_deg": 90.0, "radius_mm": 2.0, "length_mm": 100.0},
        }
        report = validate_tooling_set(
            tooling_set=self.tooling_set,
            bends_map=bends_map,
            thickness_mm=4.0,  # 12mm V-die is < 3.5 * 4 = 14mm
            punch_desc=self.punch_desc,
            die_desc=self.die_desc,
        )

        self.assertFalse(report.fully_compatible)
        self.assertIn("B1", report.incompatible_bends)
        self.assertTrue(any("too narrow" in r for r in report.incompatible_bends["B1"]))

    def test_partial_compatibility_and_changeover_detection(self):
        """When some bends match and others do not, requires_changeover is True and steps are flagged (Q10)."""
        bends_map = {
            "B1": {"bend_id": "B1", "angle_deg": 90.0, "radius_mm": 1.5, "length_mm": 100.0},
            "B2": {"bend_id": "B2", "angle_deg": 45.0, "radius_mm": 1.5, "length_mm": 100.0},
            "B3": {"bend_id": "B3", "angle_deg": 90.0, "radius_mm": 1.5, "length_mm": 100.0},
        }
        seq = [
            {"bend_id": "B1"},
            {"bend_id": "B2"},
            {"bend_id": "B3"},
        ]
        report = validate_tooling_set(
            tooling_set=self.tooling_set,
            bends_map=bends_map,
            thickness_mm=1.5,
            punch_desc=self.punch_desc,
            die_desc=self.die_desc,
            candidate_sequence=seq,
        )

        self.assertTrue(report.is_valid)  # Valid because B1 and B3 can be bent
        self.assertFalse(report.fully_compatible)
        self.assertTrue(report.requires_changeover)
        self.assertEqual(report.compatible_bends, ["B1", "B3"])
        self.assertIn("B2", report.incompatible_bends)
        # Step 2 is the changeover step
        self.assertEqual(report.proposed_changeover_steps, [2])

    def test_clamp_validation_warnings(self):
        """Set with only 1 clamp triggers warning that minimum 2 clamps are recommended."""
        single_clamp_set = ToolingSet(
            set_id="single_clamp",
            punch_id="punch_standard",
            die_id="die_standard",
            clamp_positions_mm=(100.0,),
        )
        report = validate_tooling_set(
            tooling_set=single_clamp_set,
            bends_map={},
            thickness_mm=1.0,
        )
        self.assertIsNotNone(report.clamp_validation)
        self.assertFalse(report.clamp_validation["valid"])
        self.assertTrue(any("minimum 2 recommended" in w for w in report.warnings))

    def test_empty_bends_is_valid(self):
        """Part with zero bends is treated as valid without changeover."""
        report = validate_tooling_set(
            tooling_set=self.tooling_set,
            bends_map={},
            thickness_mm=1.0,
        )
        self.assertTrue(report.is_valid)
        self.assertTrue(report.fully_compatible)
        self.assertFalse(report.requires_changeover)


class TestCoordinatorToolingSetManifestIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.lib_dir = Path(self.temp_dir) / "test_lib"
        (self.lib_dir / "punch").mkdir(parents=True, exist_ok=True)
        (self.lib_dir / "die").mkdir(parents=True, exist_ok=True)
        (self.lib_dir / "sets").mkdir(parents=True, exist_ok=True)

        # Create dummy punch tool
        (self.lib_dir / "punch" / "p1.FCStd").write_text("punch fcstd")
        (self.lib_dir / "punch" / "p1.tooling.json").write_text(
            json.dumps({
                "schema_version": "1.0",
                "tool_id": "p1",
                "kind": "punch",
                "metadata": {"radius_mm": 1.0, "angle_deg": 88.0, "height_mm": 100.0},
            })
        )

        # Create dummy die tool
        (self.lib_dir / "die" / "d1.FCStd").write_text("die fcstd")
        (self.lib_dir / "die" / "d1.tooling.json").write_text(
            json.dumps({
                "schema_version": "1.0",
                "tool_id": "d1",
                "kind": "die",
                "metadata": {"v_width_mm": 12.0, "angle_deg": 88.0, "height_mm": 50.0},
            })
        )

        # Create tooling set
        self.tooling_set = ToolingSet(
            set_id="test_production_set",
            punch_id="p1",
            die_id="d1",
            clamp_positions_mm=(75.0, 275.0),
            description="Production setup",
        )
        save_set_file(self.lib_dir / "sets" / "test_production_set.json", self.tooling_set)

        self.model_file = Path(self.temp_dir) / "part.FCStd"
        self.model_file.write_text("dummy part")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_coordinator_writes_applied_tooling_set_section(self):
        """Coordinator validates set against canonical part and writes applied_tooling_set into tooling_manifest.json."""
        coordinator = Coordinator(workspace_root=self.temp_dir)
        run_id = "run_tset_manifest"
        run_dir = Path(self.temp_dir) / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "geometry.brep").write_text("brep")

        machine_cfg = {
            "tooling_set": "test_production_set",
            "library_path": str(self.lib_dir),
        }

        with patch.object(coordinator.geometry_worker, "execute") as mock_geom, \
             patch.object(coordinator.topology_worker, "execute") as mock_topo, \
             patch.object(coordinator.baseline_worker, "execute") as mock_base, \
             patch.object(coordinator.file_store, "read_json_verified") as mock_read, \
             patch("smbs_core.coordinator.run_pipeline.compute_file_sha256", return_value="0" * 64), \
             patch.object(coordinator.solver_worker, "execute", side_effect=Exception("stop_after_manifest")):

            mock_geom.return_value = (run_dir / "02_geometry_result.json", run_dir / "geometry.brep")
            mock_topo.return_value = run_dir / "04_canonical_part.json"
            mock_base.return_value = run_dir / "04_baseline_result.json"

            def mock_read_side_effect(path, expected_schema_name=None):
                p_str = str(path)
                if "02_geometry_result.json" in p_str:
                    return {
                        "payload": {
                            "geometry_artifact": {
                                "file_path": str((run_dir / "geometry.brep").relative_to(coordinator.workspace_root)),
                                "content_hash": "0" * 64,
                            }
                        }
                    }
                if "04_baseline_result.json" in p_str:
                    return {"payload": {"baseline_status": "VALID", "pass": True}, "diagnostics": []}
                if "04_canonical_part.json" in p_str:
                    return {
                        "content_hash": "a" * 64,
                        "payload": {
                            "thickness_mm": {"value": 2.0},
                            "bends": {
                                "B1": {"bend_id": "B1", "angle_deg": 90.0, "radius_mm": 1.0, "length_mm": 100.0},
                            },
                        },
                    }
                return {}

            mock_read.side_effect = mock_read_side_effect

            try:
                coordinator.start_run(
                    source_file_path=self.model_file,
                    part_id="part_manifest_test",
                    run_id=run_id,
                    machine_config=machine_cfg,
                )
            except Exception as e:
                if str(e) != "stop_after_manifest":
                    raise

        manifest_path = run_dir / "tooling_manifest.json"
        self.assertTrue(manifest_path.exists())
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)

        self.assertIn("applied_tooling_set", manifest_data["payload"])
        applied = manifest_data["payload"]["applied_tooling_set"]
        self.assertEqual(applied["set_id"], "test_production_set")
        self.assertEqual(applied["punch_id"], "p1")
        self.assertEqual(applied["die_id"], "d1")
        self.assertEqual(applied["clamp_positions_mm"], [75.0, 275.0])

        validation = applied["validation"]
        self.assertTrue(validation["is_valid"])
        self.assertTrue(validation["fully_compatible"])
        self.assertEqual(validation["compatible_bends"], ["B1"])
        self.assertEqual(validation["incompatible_bends"], {})
        self.assertFalse(validation["requires_changeover"])


if __name__ == "__main__":
    unittest.main()
