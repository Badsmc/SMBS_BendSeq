import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smbs_core.coordinator.run_pipeline import Coordinator
from smbs_core.cli import main


class TestCoordinatorToolingIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parent.parent
        cls.punch_src = cls.repo_root / "toollibrary" / "punch" / "puancon.FCStd"
        cls.punch_sidecar = cls.repo_root / "toollibrary" / "punch" / "puancon.tooling.json"
        cls.die_src = cls.repo_root / "toollibrary" / "die" / "matrica.FCStd"
        cls.die_sidecar = cls.repo_root / "toollibrary" / "die" / "matrica.tooling.json"

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.lib_dir = Path(self.temp_dir) / "test_lib"
        (self.lib_dir / "punch").mkdir(parents=True, exist_ok=True)
        (self.lib_dir / "die").mkdir(parents=True, exist_ok=True)

        # Copy real test tools into temporary library
        shutil.copy2(self.punch_src, self.lib_dir / "punch" / "puancon.FCStd")
        shutil.copy2(self.punch_sidecar, self.lib_dir / "punch" / "puancon.tooling.json")
        shutil.copy2(self.die_src, self.lib_dir / "die" / "matrica.FCStd")
        shutil.copy2(self.die_sidecar, self.lib_dir / "die" / "matrica.tooling.json")

        self.coordinator = Coordinator(workspace_root=self.temp_dir)
        self.dummy_part = Path(self.temp_dir) / "dummy.step"
        self.dummy_part.write_text("dummy step content")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_machine_config_with_ids_resolves_paths(self):
        """machine_config with punch_id & die_id resolves paths via ToolingLibrary."""
        run_id = "run_resolve_ok"
        run_dir = Path(self.temp_dir) / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "geometry.brep").write_text("brep")

        machine_cfg = {
            "punch_id": "puancon",
            "die_id": "matrica",
            "library_path": str(self.lib_dir),
        }
        original_machine_cfg = dict(machine_cfg)

        with patch.object(self.coordinator.geometry_worker, "execute") as mock_geom, \
             patch.object(self.coordinator.topology_worker, "execute") as mock_topo, \
             patch.object(self.coordinator.baseline_worker, "execute") as mock_base, \
             patch.object(self.coordinator.file_store, "read_json_verified") as mock_read, \
             patch("smbs_core.coordinator.run_pipeline.compute_file_sha256", return_value="0" * 64), \
             patch.object(self.coordinator.solver_worker, "execute", side_effect=Exception("stop_after_tooling")):

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
                                "content_hash": "0" * 64,
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
                    source_file_path=self.dummy_part,
                    part_id="part_test",
                    run_id=run_id,
                    machine_config=machine_cfg,
                )
            except Exception as e:
                if str(e) != "stop_after_tooling":
                    raise

        # Check caller dict is NOT mutated
        self.assertEqual(machine_cfg, original_machine_cfg)

        # Check tooling_manifest.json created with REAL status and correct model paths
        manifest_path = run_dir / "tooling_manifest.json"
        self.assertTrue(manifest_path.exists())
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        punch = data["payload"]["punch"]
        die = data["payload"]["die"]

        self.assertEqual(punch["tooling_status"], "REAL")
        self.assertEqual(die["tooling_status"], "REAL")
        self.assertEqual(punch["tool_id"], "puancon")
        self.assertEqual(die["tool_id"], "matrica")
        self.assertEqual(Path(punch["model_path"]).resolve(), (self.lib_dir / "punch" / "puancon.FCStd").resolve())
        self.assertEqual(Path(die["model_path"]).resolve(), (self.lib_dir / "die" / "matrica.FCStd").resolve())

    def test_machine_config_with_ids_missing_die_raises(self):
        """Providing only punch_id without die_id raises ValueError."""
        with self.assertRaises(ValueError) as cm:
            self.coordinator.start_run(
                source_file_path=self.dummy_part,
                part_id="part_test",
                machine_config={
                    "punch_id": "puancon",
                    "library_path": str(self.lib_dir),
                },
            )
        self.assertIn("must be provided together", str(cm.exception))

    def test_machine_config_with_ids_missing_library_path_raises(self):
        """Providing punch_id and die_id without library_path raises ValueError."""
        with self.assertRaises(ValueError) as cm:
            self.coordinator.start_run(
                source_file_path=self.dummy_part,
                part_id="part_test",
                machine_config={
                    "punch_id": "puancon",
                    "die_id": "matrica",
                },
            )
        self.assertIn("library_path required", str(cm.exception))

    def test_machine_config_with_ids_and_paths_raises(self):
        """Mixing punch_id/die_id with punch_path/die_path raises ValueError."""
        with self.assertRaises(ValueError) as cm:
            self.coordinator.start_run(
                source_file_path=self.dummy_part,
                part_id="part_test",
                machine_config={
                    "punch_id": "puancon",
                    "die_id": "matrica",
                    "library_path": str(self.lib_dir),
                    "punch_path": "/some/path",
                },
            )
        self.assertIn("cannot mix", str(cm.exception))

    def test_machine_config_punch_id_resolves_to_wrong_kind_raises(self):
        """punch_id resolving to 'die' descriptor raises ValueError."""
        with self.assertRaises(ValueError) as cm:
            self.coordinator.start_run(
                source_file_path=self.dummy_part,
                part_id="part_test",
                machine_config={
                    "punch_id": "matrica",  # die passed as punch
                    "die_id": "matrica",
                    "library_path": str(self.lib_dir),
                },
            )
        self.assertIn("expected 'punch'", str(cm.exception))

    def test_machine_config_tool_not_found_raises(self):
        """Nonexistent punch_id raises ValueError wrapping ToolingLibraryError."""
        with self.assertRaises(ValueError) as cm:
            self.coordinator.start_run(
                source_file_path=self.dummy_part,
                part_id="part_test",
                machine_config={
                    "punch_id": "nonexistent_punch",
                    "die_id": "matrica",
                    "library_path": str(self.lib_dir),
                },
            )
        self.assertIn("Failed to resolve tooling from library", str(cm.exception))

    def test_cli_punch_id_without_die_id_exit_2(self):
        """CLI: passing --punch-id without --die-id exits with code 2."""
        with patch.object(sys, "argv", ["smbs-cli", str(self.dummy_part), "--punch-id", "puancon"]):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 2)

    def test_cli_ids_with_tooling_config_exit_2(self):
        """CLI: passing --punch-id/--die-id with --tooling-config exits with code 2."""
        with patch.object(sys, "argv", [
            "smbs-cli", str(self.dummy_part),
            "--punch-id", "puancon",
            "--die-id", "matrica",
            "--tooling-config", "/path/to/cfg.json"
        ]):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
