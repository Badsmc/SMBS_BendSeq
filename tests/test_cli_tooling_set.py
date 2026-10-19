"""
test_cli_tooling_set.py - Unit tests for CLI --tooling-set flag and Coordinator integration (S.25b).
"""

import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from smbs_core.cli import main
from smbs_core.coordinator.run_pipeline import Coordinator
from smbs_core.geometry_backend.io.tooling_set import ToolingSet, save_set_file


class TestCliAndCoordinatorToolingSet(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.lib_dir = Path(self.temp_dir) / "test_lib"
        (self.lib_dir / "punch").mkdir(parents=True, exist_ok=True)
        (self.lib_dir / "die").mkdir(parents=True, exist_ok=True)
        (self.lib_dir / "sets").mkdir(parents=True, exist_ok=True)

        # Create dummy punch tool
        (self.lib_dir / "punch" / "dummy.FCStd").write_text("dummy punch")
        (self.lib_dir / "punch" / "dummy.tooling.json").write_text(
            json.dumps({"schema_version": "1.0", "tool_id": "dummy", "kind": "punch"})
        )

        # Create dummy die tool
        (self.lib_dir / "die" / "dummy_die.FCStd").write_text("dummy die")
        (self.lib_dir / "die" / "dummy_die.tooling.json").write_text(
            json.dumps({"schema_version": "1.0", "tool_id": "dummy_die", "kind": "die"})
        )

        # Create valid tooling set
        self.valid_set = ToolingSet(
            set_id="my_set",
            punch_id="dummy",
            die_id="dummy_die",
            clamp_positions_mm=(75.0, 275.0),
            description="Test set",
        )
        save_set_file(self.lib_dir / "sets" / "my_set.json", self.valid_set)

        self.model_file = Path(self.temp_dir) / "test_model.FCStd"
        self.model_file.write_text("dummy model content")
        self.output_file = Path(self.temp_dir) / "sequence.json"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _mock_coordinator_success(self, *args, **kwargs):
        export_file = Path(self.temp_dir) / "export.json"
        export_file.write_text(json.dumps({"dummy": True}))
        return {
            "status": "SUCCESS",
            "planning_status": "SUCCESS",
            "verification_status": "VERIFIED",
            "production_eligible": True,
            "export_file_path": str(export_file),
        }

    # -------------------------------------------------------------------------
    # CLI Tests
    # -------------------------------------------------------------------------

    @patch("smbs_core.cli.Coordinator")
    def test_cli_tooling_set_valid(self, mock_coord_cls):
        """Valid --tooling-set flag invokes Coordinator and prints honest notice to stderr."""
        mock_instance = mock_coord_cls.return_value
        mock_instance.start_run.side_effect = self._mock_coordinator_success

        cmd = [
            str(self.model_file),
            "--tooling-set", "my_set",
            "--library-path", str(self.lib_dir),
            "--output", str(self.output_file),
        ]
        stderr_buf = io.StringIO()
        with patch.object(sys, "argv", ["smbs-cli"] + cmd), patch("sys.stderr", stderr_buf):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 0)

        err_output = stderr_buf.getvalue()
        self.assertIn("Tooling set 'my_set' will be resolved by Coordinator", err_output)
        self.assertIn("substitution/validation of punch chain not applied", err_output)

        call_kwargs = mock_instance.start_run.call_args[1]
        self.assertEqual(
            call_kwargs["machine_config"],
            {
                "tooling_set": "my_set",
                "library_path": str(self.lib_dir),
            },
        )

    @patch("smbs_core.cli.Coordinator")
    def test_cli_tooling_set_missing_set(self, mock_coord_cls):
        """Coordinator raising ValueError for missing set causes CLI to exit with code 2."""
        mock_instance = mock_coord_cls.return_value
        mock_instance.start_run.side_effect = ValueError("Failed to resolve tooling_set 'missing_set' from library")

        cmd = [
            str(self.model_file),
            "--tooling-set", "missing_set",
            "--library-path", str(self.lib_dir),
            "--output", str(self.output_file),
        ]
        stderr_buf = io.StringIO()
        with patch.object(sys, "argv", ["smbs-cli"] + cmd), patch("sys.stderr", stderr_buf):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 2)

        err_output = stderr_buf.getvalue()
        self.assertIn("Failed to resolve tooling_set 'missing_set'", err_output)

    def test_cli_tooling_set_conflicts_with_punch_id(self):
        """--tooling-set cannot be combined with --punch-id / --die-id."""
        cmd = [
            str(self.model_file),
            "--tooling-set", "my_set",
            "--punch-id", "dummy",
            "--die-id", "dummy_die",
            "--library-path", str(self.lib_dir),
        ]
        stderr_buf = io.StringIO()
        with patch.object(sys, "argv", ["smbs-cli"] + cmd), patch("sys.stderr", stderr_buf):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 2)

        self.assertIn("cannot be combined with --punch-id/--die-id", stderr_buf.getvalue())

    def test_cli_tooling_set_conflicts_with_clamps_config(self):
        """--tooling-set cannot be combined with --clamps-config."""
        dummy_clamp_file = Path(self.temp_dir) / "clamps.json"
        dummy_clamp_file.write_text(json.dumps({
            "schema_version": "1.0",
            "clamp_positions_mm": [75.0, 275.0],
            "clamp_width_mm": 50.0,
            "min_clamp_spacing_mm": 100.0,
            "min_clamps_per_punch": 2,
        }))

        cmd = [
            str(self.model_file),
            "--tooling-set", "my_set",
            "--clamps-config", str(dummy_clamp_file),
            "--library-path", str(self.lib_dir),
        ]
        stderr_buf = io.StringIO()
        with patch.object(sys, "argv", ["smbs-cli"] + cmd), patch("sys.stderr", stderr_buf):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 2)

        self.assertIn("cannot be combined with --clamps-config", stderr_buf.getvalue())

    def test_cli_tooling_set_conflicts_with_tooling_config(self):
        """--tooling-set cannot be combined with --tooling-config (set already contains punch/die)."""
        dummy_tooling_config = Path(self.temp_dir) / "tooling.json"
        dummy_tooling_config.write_text(json.dumps({"dummy": True}))

        cmd = [
            str(self.model_file),
            "--tooling-set", "my_set",
            "--tooling-config", str(dummy_tooling_config),
            "--library-path", str(self.lib_dir),
        ]
        stderr_buf = io.StringIO()
        with patch.object(sys, "argv", ["smbs-cli"] + cmd), patch("sys.stderr", stderr_buf):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 2)

        self.assertIn("cannot be combined with --tooling-config", stderr_buf.getvalue())

    def test_cli_tooling_set_missing_library_path(self):
        """--tooling-set with nonexistent library path exits 2."""
        cmd = [
            str(self.model_file),
            "--tooling-set", "my_set",
            "--library-path", str(Path(self.temp_dir) / "nonexistent_lib"),
        ]
        stderr_buf = io.StringIO()
        with patch.object(sys, "argv", ["smbs-cli"] + cmd), patch("sys.stderr", stderr_buf):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 2)

        self.assertIn("library path not found", stderr_buf.getvalue())

    # -------------------------------------------------------------------------
    # Coordinator Tests
    # -------------------------------------------------------------------------

    def test_coordinator_tooling_set_resolves_paths(self):
        """Coordinator resolves tooling_set to punch_path, die_path, and clamps_config."""
        coordinator = Coordinator(workspace_root=self.temp_dir)
        run_id = "run_tset_resolve"
        run_dir = Path(self.temp_dir) / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "geometry.brep").write_text("brep")

        machine_cfg = {
            "tooling_set": "my_set",
            "library_path": str(self.lib_dir),
        }
        original_machine_cfg = dict(machine_cfg)

        with patch.object(coordinator.geometry_worker, "execute") as mock_geom, \
             patch.object(coordinator.topology_worker, "execute") as mock_topo, \
             patch.object(coordinator.baseline_worker, "execute") as mock_base, \
             patch.object(coordinator.file_store, "read_json_verified") as mock_read, \
             patch("smbs_core.coordinator.run_pipeline.compute_file_sha256", return_value="0" * 64), \
             patch.object(coordinator.solver_worker, "execute", side_effect=Exception("stop_after_tooling")):

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
                    return {"content_hash": "a" * 64}
                return {}

            mock_read.side_effect = mock_read_side_effect

            try:
                coordinator.start_run(
                    source_file_path=self.model_file,
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
        self.assertEqual(punch["tool_id"], "dummy")
        self.assertEqual(die["tool_id"], "dummy_die")
        self.assertEqual(Path(punch["model_path"]).resolve(), (self.lib_dir / "punch" / "dummy.FCStd").resolve())
        self.assertEqual(Path(die["model_path"]).resolve(), (self.lib_dir / "die" / "dummy_die.FCStd").resolve())

    def test_coordinator_tooling_set_missing_raises(self):
        """Coordinator raises ValueError if tooling_set is not found in library."""
        coordinator = Coordinator(workspace_root=self.temp_dir)
        with self.assertRaises(ValueError) as cm:
            coordinator.start_run(
                source_file_path=self.model_file,
                part_id="part_test",
                machine_config={"tooling_set": "nonexistent_set", "library_path": str(self.lib_dir)},
            )
        self.assertIn("Failed to resolve tooling_set 'nonexistent_set'", str(cm.exception))

    def test_coordinator_tooling_set_conflicts_with_punch_id(self):
        """Coordinator raises ValueError if machine_config mixes tooling_set with punch_id."""
        coordinator = Coordinator(workspace_root=self.temp_dir)
        with self.assertRaises(ValueError) as cm:
            coordinator.start_run(
                source_file_path=self.model_file,
                part_id="part_test",
                machine_config={
                    "tooling_set": "my_set",
                    "punch_id": "dummy",
                    "library_path": str(self.lib_dir),
                },
            )
        self.assertIn("cannot mix tooling_set with punch_id/die_id", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
