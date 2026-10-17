import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smbs_core.coordinator.run_pipeline import Coordinator
from smbs_core.protocols.file_store import FileStore


class TestCoordinatorMachineConfig(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.coordinator = Coordinator(workspace_root=self.temp_dir)
        self.file_store = FileStore()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_coordinator_passes_real_machine_config_to_manifest(self):
        """machine_config -> tooling_manifest.payload.punch.tooling_status == 'REAL'"""
        dummy_file = Path(self.temp_dir) / "test_part.step"
        dummy_file.write_text("dummy")

        with patch.object(self.coordinator.geometry_worker, "execute") as mock_geom,              patch.object(self.coordinator.topology_worker, "execute") as mock_topo,              patch.object(self.coordinator.baseline_worker, "execute") as mock_base,              patch.object(self.coordinator.file_store, "read_json_verified") as mock_read,              patch("smbs_core.coordinator.run_pipeline.compute_file_sha256", return_value="0" * 64),              patch.object(self.coordinator.solver_worker, "execute", side_effect=Exception("stop_after_tooling")):

            run_id = "run_mc_test"
            run_dir = Path(self.temp_dir) / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "geometry.brep").write_text("brep")

            mock_geom.return_value = (run_dir / "02_geometry_result.json", run_dir / "geometry.brep")
            mock_topo.return_value = run_dir / "04_canonical_part.json"
            mock_base.return_value = run_dir / "04_baseline_result.json"

            def mock_read_side_effect(path, expected_schema_name=None):
                p_str = str(path)
                if "02_geometry_result.json" in p_str:
                    return {
                        "payload": {
                            "geometry_artifact": {
                                "file_path": str((run_dir / "geometry.brep").relative_to(self.coordinator.workspace_root)),
                                "content_hash": "0" * 64
                            }
                        }
                    }
                if "04_baseline_result.json" in p_str:
                    return {"payload": {"baseline_status": "VALID", "pass": True}, "diagnostics": []}
                if "04_canonical_part.json" in p_str:
                    return {"content_hash": "a" * 64}
                return {}

            mock_read.side_effect = mock_read_side_effect

            machine_cfg = {
                "punch_path": "/path/to/puancon.FCStd",
                "die_path": "/path/to/matrica.FCStd",
                "punch_target_object_name": "Body",
                "die_target_object_name": "Body",
                "press_id": "press_test",
                "bed_length_mm": 2500.0
            }

            try:
                self.coordinator.start_run(
                    source_file_path=dummy_file,
                    part_id="part_mc",
                    run_id=run_id,
                    machine_config=machine_cfg
                )
            except Exception as e:
                if str(e) != "stop_after_tooling":
                    raise

        tooling_path = run_dir / "tooling_manifest.json"
        self.assertTrue(tooling_path.exists())
        with open(tooling_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        punch = data["payload"]["punch"]
        die = data["payload"]["die"]
        press = data["payload"]["press"]

        self.assertEqual(punch["tooling_status"], "REAL")
        self.assertEqual(punch["tool_id"], "puancon")
        self.assertTrue(punch["model_path"].endswith("puancon.FCStd"))
        self.assertEqual(punch.get("target_object_name"), "Body")

        self.assertEqual(die["tooling_status"], "REAL")
        self.assertEqual(die["tool_id"], "matrica")
        self.assertTrue(die["model_path"].endswith("matrica.FCStd"))
        self.assertEqual(die.get("target_object_name"), "Body")

        self.assertEqual(press["press_id"], "press_test")
        self.assertEqual(press["bed_length_mm"], 2500.0)
        self.assertEqual(data["diagnostics"], [])

    def test_coordinator_defaults_to_simplified_without_machine_config(self):
        """Без machine_config -> tooling_status == 'SIMPLIFIED', в diagnostics запись о дефолте"""
        dummy_file = Path(self.temp_dir) / "test_part_def.step"
        dummy_file.write_text("dummy")

        with patch.object(self.coordinator.geometry_worker, "execute") as mock_geom,              patch.object(self.coordinator.topology_worker, "execute") as mock_topo,              patch.object(self.coordinator.baseline_worker, "execute") as mock_base,              patch.object(self.coordinator.file_store, "read_json_verified") as mock_read,              patch("smbs_core.coordinator.run_pipeline.compute_file_sha256", return_value="0" * 64),              patch.object(self.coordinator.solver_worker, "execute", side_effect=Exception("stop_after_tooling")):

            run_id = "run_def_test"
            run_dir = Path(self.temp_dir) / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "geometry.brep").write_text("brep")

            mock_geom.return_value = (run_dir / "02_geometry_result.json", run_dir / "geometry.brep")
            mock_topo.return_value = run_dir / "04_canonical_part.json"
            mock_base.return_value = run_dir / "04_baseline_result.json"

            def mock_read_side_effect(path, expected_schema_name=None):
                p_str = str(path)
                if "02_geometry_result.json" in p_str:
                    return {
                        "payload": {
                            "geometry_artifact": {
                                "file_path": str((run_dir / "geometry.brep").relative_to(self.coordinator.workspace_root)),
                                "content_hash": "0" * 64
                            }
                        }
                    }
                if "04_baseline_result.json" in p_str:
                    return {"payload": {"baseline_status": "VALID", "pass": True}, "diagnostics": []}
                if "04_canonical_part.json" in p_str:
                    return {"content_hash": "a" * 64}
                return {}

            mock_read.side_effect = mock_read_side_effect

            try:
                self.coordinator.start_run(
                    source_file_path=dummy_file,
                    part_id="part_def",
                    run_id=run_id
                )
            except Exception as e:
                if str(e) != "stop_after_tooling":
                    raise

        tooling_path = run_dir / "tooling_manifest.json"
        self.assertTrue(tooling_path.exists())
        with open(tooling_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        punch = data["payload"]["punch"]
        die = data["payload"]["die"]

        self.assertEqual(punch["tooling_status"], "SIMPLIFIED")
        self.assertEqual(die["tooling_status"], "SIMPLIFIED")
        self.assertIn("machine_config not provided; default SIMPLIFIED tooling", data["diagnostics"])


if __name__ == "__main__":
    unittest.main()
