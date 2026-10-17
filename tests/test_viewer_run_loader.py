"""
Unit tests for smbs_core.viewer.run_loader (Stage 4, S.11).
Pure Python tests without OCC / FreeCAD dependencies.
"""
import json
import tempfile
import unittest
from pathlib import Path

from smbs_core.viewer.run_loader import load_run, RunArtifacts, RunLoaderError


class TestViewerRunLoader(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.temp_dir.name)

        # Base minimal valid fixtures
        self.cp_data = {
            "schema_name": "canonical_part",
            "payload": {
                "input_mode": "CANONICAL_PART",
                "thickness_mm": {"value": 1.5, "source": "USER_INPUT", "confidence": "CONFIRMED"},
                "reference_panel_id": "P01",
                "panels": {
                    "P01": {"panel_id": "P01", "area_mm2": 100.0, "centroid": [0, 0, 0], "normal": [0, 0, 1]}
                },
                "bends": {
                    "B01": {
                        "bend_id": "B01",
                        "parent_panel_id": "P01",
                        "child_panel_id": "P02",
                        "angle_deg": {"value": 90.0, "source": "GEOMETRIC_INFERENCE", "confidence": "CONFIRMED"},
                        "radius_mm": {"value": 1.0, "source": "GEOMETRIC_INFERENCE", "confidence": "CONFIRMED"},
                        "length_mm": {"value": 50.0, "source": "GEOMETRIC_INFERENCE", "confidence": "CONFIRMED"},
                        "direction": {"value": "UP", "source": "GEOMETRIC_INFERENCE", "confidence": "CONFIRMED"},
                        "k_factor": {"value": 0.44, "source": "SHEETMETAL_NATIVE", "confidence": "CONFIRMED"},
                        "axis_point": [0.0, 0.0, 0.0],
                        "axis_direction": [1.0, 0.0, 0.0]
                    }
                },
                "geometry_artifact": {
                    "file_path": "geometry.brep",
                    "format": "BREP",
                    "content_hash": "a" * 64
                }
            }
        }

        self.sr_data = {
            "schema_name": "search_result",
            "status": "SUCCESS",
            "payload": {
                "planning_status": "SUCCESS",
                "candidate_sequence": [
                    {
                        "step": 1,
                        "bend_id": "B01",
                        "direction": {"value": "UP"},
                        "angle_deg": {"value": 90.0},
                        "radius_mm": {"value": 1.0},
                        "backgauge": {"x_mm": 10.0, "r_mm": 0.0, "confidence": "CONFIRMED"},
                        "tool_id": "PUNCH_STD",
                        "planner_status": "VALID",
                        "collision_status": "VALID",
                        "tooling_status": "REAL",
                        "alignment_status": "OK"
                    }
                ]
            }
        }

        self.vr_data = {
            "schema_name": "verification_result",
            "payload": {
                "verification_status": "VERIFIED",
                "production_eligible": True,
                "step_verifications": [{"step": 1, "bend_id": "B01", "status": "VERIFIED"}],
                "independent_metrics": {}
            }
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_file(self, rel_path: str, content: str = "") -> Path:
        target = self.run_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def _write_json(self, rel_path: str, data: dict) -> Path:
        return self._write_file(rel_path, json.dumps(data))

    def test_load_valid_run(self):
        """Minimal valid run with 04, 07, geometry.brep; 09 and flat_pattern absent."""
        self._write_json("04_canonical_part.json", self.cp_data)
        self._write_json("07_search_result.json", self.sr_data)
        self._write_file("geometry.brep", "dummy brep content")

        artifacts = load_run(self.run_dir)
        self.assertIsInstance(artifacts, RunArtifacts)
        self.assertEqual(artifacts.run_dir, self.run_dir.resolve())
        self.assertEqual(artifacts.canonical_part["schema_name"], "canonical_part")
        self.assertEqual(artifacts.search_result["schema_name"], "search_result")
        self.assertIsNone(artifacts.verification_result)
        self.assertIsNone(artifacts.flat_pattern_path)
        self.assertTrue(artifacts.geometry_shape_path.exists())

    def test_load_with_09_and_flat(self):
        """Full valid run including 09_verification_result.json and flat_pattern.brep."""
        self._write_json("04_canonical_part.json", self.cp_data)
        self._write_json("07_search_result.json", self.sr_data)
        self._write_json("09_verification_result.json", self.vr_data)
        self._write_file("geometry.brep", "dummy brep")
        self._write_file("flat_pattern.brep", "dummy flat")

        artifacts = load_run(self.run_dir)
        self.assertIsNotNone(artifacts.verification_result)
        self.assertEqual(artifacts.verification_result["payload"]["verification_status"], "VERIFIED")
        self.assertIsNotNone(artifacts.flat_pattern_path)
        self.assertEqual(artifacts.flat_pattern_path, (self.run_dir / "flat_pattern.brep").resolve())

    def test_missing_04_raises(self):
        """Missing 04_canonical_part.json must raise RunLoaderError."""
        self._write_json("07_search_result.json", self.sr_data)
        self._write_file("geometry.brep", "dummy")
        with self.assertRaises(RunLoaderError) as ctx:
            load_run(self.run_dir)
        self.assertIn("04_canonical_part.json", str(ctx.exception))

    def test_missing_07_raises(self):
        """Missing 07_search_result.json must raise RunLoaderError."""
        self._write_json("04_canonical_part.json", self.cp_data)
        self._write_file("geometry.brep", "dummy")
        with self.assertRaises(RunLoaderError) as ctx:
            load_run(self.run_dir)
        self.assertIn("07_search_result.json", str(ctx.exception))

    def test_missing_09_is_none(self):
        """Missing 09_verification_result.json must not raise; verification_result is None."""
        self._write_json("04_canonical_part.json", self.cp_data)
        self._write_json("07_search_result.json", self.sr_data)
        self._write_file("geometry.brep", "dummy")

        artifacts = load_run(self.run_dir)
        self.assertIsNone(artifacts.verification_result)

    def test_missing_geometry_raises(self):
        """Referenced geometry artifact file does not exist -> RunLoaderError."""
        self._write_json("04_canonical_part.json", self.cp_data)
        self._write_json("07_search_result.json", self.sr_data)
        # Note: geometry.brep NOT created

        with self.assertRaises(RunLoaderError) as ctx:
            load_run(self.run_dir)
        self.assertIn("geometry_artifact", str(ctx.exception))

    def test_missing_geometry_artifact_field_raises(self):
        """Missing payload.geometry_artifact.file_path in 04 -> RunLoaderError."""
        bad_cp = dict(self.cp_data)
        bad_cp["payload"] = dict(bad_cp["payload"])
        del bad_cp["payload"]["geometry_artifact"]
        self._write_json("04_canonical_part.json", bad_cp)
        self._write_json("07_search_result.json", self.sr_data)
        self._write_file("geometry.brep", "dummy")

        with self.assertRaises(RunLoaderError) as ctx:
            load_run(self.run_dir)
        self.assertIn("geometry_artifact.file_path missing", str(ctx.exception))

    def test_malformed_json_raises(self):
        """Malformed JSON in 04 must raise RunLoaderError with json.JSONDecodeError cause."""
        self._write_file("04_canonical_part.json", "INVALID { JSON content :::")
        self._write_json("07_search_result.json", self.sr_data)
        self._write_file("geometry.brep", "dummy")

        with self.assertRaises(RunLoaderError) as ctx:
            load_run(self.run_dir)
        self.assertIsInstance(ctx.exception.__cause__, json.JSONDecodeError)

    def test_run_dir_not_a_directory_raises(self):
        """Passing a file path instead of a directory must raise RunLoaderError."""
        file_path = self._write_file("some_file.txt", "hello")
        with self.assertRaises(RunLoaderError) as ctx:
            load_run(file_path)
        self.assertIn("not a directory", str(ctx.exception))

    def test_paths_are_resolved(self):
        """All Path objects in RunArtifacts must be resolved and absolute."""
        self._write_json("04_canonical_part.json", self.cp_data)
        self._write_json("07_search_result.json", self.sr_data)
        self._write_file("geometry.brep", "dummy")
        self._write_file("flat_pattern.brep", "dummy flat")

        artifacts = load_run(self.run_dir)
        self.assertTrue(artifacts.run_dir.is_absolute())
        self.assertTrue(artifacts.geometry_shape_path.is_absolute())
        self.assertTrue(artifacts.geometry_shape_path.exists())
        self.assertTrue(artifacts.flat_pattern_path.is_absolute())
        self.assertTrue(artifacts.flat_pattern_path.exists())


if __name__ == "__main__":
    unittest.main()
