import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smbs_core.coordinator.run_pipeline import Coordinator
from smbs_core.protocols.file_store import FileStore


class TestCoordinatorTargetObjectName(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.coordinator = Coordinator(workspace_root=self.temp_dir)
        self.file_store = FileStore()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_coordinator_passes_target_object_name_to_manifest(self):
        """Verifies start_run(target_object_name='Body') writes 'Body' to 01_input_manifest.json."""
        dummy_file = Path(self.temp_dir) / "test_part.step"
        dummy_file.write_text("dummy")

        with patch.object(self.coordinator.geometry_worker, "execute", side_effect=Exception("stop_after_manifest")):
            try:
                self.coordinator.start_run(
                    source_file_path=dummy_file,
                    part_id="part_test",
                    target_object_name="Body",
                    run_id="run_target_test"
                )
            except Exception as e:
                if str(e) != "stop_after_manifest":
                    raise

        manifest_path = Path(self.temp_dir) / "runs" / "run_target_test" / "01_input_manifest.json"
        self.assertTrue(manifest_path.exists(), "01_input_manifest.json must exist")
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data["payload"]["target_object_name"], "Body")

    def test_coordinator_defaults_target_object_name_to_none(self):
        """Verifies start_run without target_object_name leaves target_object_name as None."""
        dummy_file = Path(self.temp_dir) / "test_part_none.step"
        dummy_file.write_text("dummy")

        with patch.object(self.coordinator.geometry_worker, "execute", side_effect=Exception("stop_after_manifest")):
            try:
                self.coordinator.start_run(
                    source_file_path=dummy_file,
                    part_id="part_test_none",
                    run_id="run_none_test"
                )
            except Exception as e:
                if str(e) != "stop_after_manifest":
                    raise

        manifest_path = Path(self.temp_dir) / "runs" / "run_none_test" / "01_input_manifest.json"
        self.assertTrue(manifest_path.exists(), "01_input_manifest.json must exist")
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertIsNone(data["payload"]["target_object_name"])


if __name__ == "__main__":
    unittest.main()
