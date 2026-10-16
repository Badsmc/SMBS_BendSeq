import unittest
import tempfile
import shutil
import json
from pathlib import Path

from smbs_core.coordinator.run_pipeline import Coordinator
from smbs_core.workers.baseline.worker import BaselineWorker

class TestAmbiguousTreeReturnsUnknown(unittest.TestCase):
    """
    Validation test (§3, Task 1.0.1b-3a):
    Synthetic canonical_part with 2 hubs of the same max degree >= 3 and NO reference_panel_id.
    Verifies that the pipeline and BaselineWorker return:
    - baseline_status == INVALID
    - pass == False
    - diagnostic code == AMBIGUOUS_TOPOLOGY_ROOT
    Also tests NON_TREE_TOPOLOGY and DISCONNECTED_PANEL_GRAPH checks.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.coordinator = Coordinator(workspace_root=self.temp_dir)
        self.file_store = self.coordinator.file_store

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_geom_result(self, run_id, part_id, bends_count=4, faces_count=10):
        geom_path = Path(self.temp_dir) / "runs" / run_id / "02_geometry_result.json"
        geom_res = {
            "schema_name": "geometry_result",
            "schema_version": "1.1",
            "run_id": run_id,
            "part_id": part_id,
            "input_fingerprint": "0" * 64,
            "producer": {"name": "test", "version": "1.0"},
            "units": "mm",
            "frame_convention": "STANDARD_CARTESIAN_MM",
            "status": "SUCCESS",
            "diagnostics": [],
            "created_at": "2026-10-16T12:00:00Z",
            "payload": {
                "geometry_artifact": {"file_path": f"runs/{run_id}/geometry.brep", "format": "BREP", "content_hash": "0" * 64},
                "flat_pattern_artifact": {"file_path": f"runs/{run_id}/flat_pattern.brep", "format": "BREP", "content_hash": "0" * 64},
                "thickness_mm": {"value": 1.5, "source": "SPEC", "confidence": "HIGH"},
                "detected_faces_count": faces_count,
                "detected_bends_count": bends_count,
                "geometry_status": "SUCCESS"
            }
        }
        self.file_store.write_json_atomic(geom_path, geom_res, schema_name="geometry_result")
        return geom_path

    def test_ambiguous_two_hubs_returns_invalid(self):
        run_id = "test_ambig_run"
        run_dir = Path(self.temp_dir) / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        geom_res_path = self._make_geom_result(run_id, "synthetic_ambiguous", bends_count=6, faces_count=8)
        cp_path = run_dir / "04_canonical_part.json"

        canonical_part = {
            "schema_name": "canonical_part",
            "schema_version": "1.1",
            "run_id": run_id,
            "part_id": "synthetic_ambiguous",
            "payload": {
                "reference_panel_id": None,  # No root specified!
                "thickness_mm": {"value": 1.5},
                "panels": {
                    "P1": {"panel_id": "P1", "adjacent_bend_ids": ["B1", "B2", "B3"], "area_mm2": 1000.0},
                    "P2": {"panel_id": "P2", "adjacent_bend_ids": ["B4", "B5", "B6"], "area_mm2": 1000.0},
                    "P3": {"panel_id": "P3", "adjacent_bend_ids": ["B1"], "area_mm2": 200.0},
                    "P4": {"panel_id": "P4", "adjacent_bend_ids": ["B2"], "area_mm2": 200.0},
                    "P5": {"panel_id": "P5", "adjacent_bend_ids": ["B3"], "area_mm2": 200.0},
                    "P6": {"panel_id": "P6", "adjacent_bend_ids": ["B4"], "area_mm2": 200.0},
                    "P7": {"panel_id": "P7", "adjacent_bend_ids": ["B5"], "area_mm2": 200.0},
                    "P8": {"panel_id": "P8", "adjacent_bend_ids": ["B6"], "area_mm2": 200.0},
                },
                "bends": {
                    f"B{i}": {"bend_id": f"B{i}", "angle_deg": {"value": 90.0}} for i in range(1, 7)
                }
            }
        }
        with open(cp_path, "w") as f:
            json.dump(canonical_part, f)

        worker = BaselineWorker(self.file_store)
        res_file = worker.execute(geom_res_path, cp_path)
        with open(res_file) as f:
            res = json.load(f)

        self.assertEqual(res["payload"]["baseline_status"], "INVALID")
        self.assertFalse(res["payload"]["pass"])
        diag_codes = [d.get("code") for d in res.get("diagnostics", [])]
        self.assertIn("AMBIGUOUS_TOPOLOGY_ROOT", diag_codes)

    def test_non_tree_cycle_returns_invalid(self):
        run_id = "test_cycle_run"
        run_dir = Path(self.temp_dir) / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        geom_res_path = self._make_geom_result(run_id, "synthetic_cycle", bends_count=4, faces_count=4)
        cp_path = run_dir / "04_canonical_part.json"

        canonical_part = {
            "schema_name": "canonical_part",
            "schema_version": "1.1",
            "run_id": run_id,
            "part_id": "synthetic_cycle",
            "payload": {
                "reference_panel_id": "P1",
                "thickness_mm": {"value": 1.5},
                "panels": {f"P{i}": {"panel_id": f"P{i}", "adjacent_bend_ids": []} for i in range(1, 5)},
                "bends": {f"B{i}": {"bend_id": f"B{i}", "angle_deg": {"value": 90.0}} for i in range(1, 5)}
            }
        }
        with open(cp_path, "w") as f:
            json.dump(canonical_part, f)

        worker = BaselineWorker(self.file_store)
        res_file = worker.execute(geom_res_path, cp_path)
        with open(res_file) as f:
            res = json.load(f)

        self.assertEqual(res["payload"]["baseline_status"], "INVALID")
        diag_codes = [d.get("code") for d in res.get("diagnostics", [])]
        self.assertIn("NON_TREE_TOPOLOGY", diag_codes)

    def test_disconnected_graph_returns_invalid(self):
        run_id = "test_disconn_run"
        run_dir = Path(self.temp_dir) / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        geom_res_path = self._make_geom_result(run_id, "synthetic_disconn", bends_count=3, faces_count=5)
        cp_path = run_dir / "04_canonical_part.json"

        canonical_part = {
            "schema_name": "canonical_part",
            "schema_version": "1.1",
            "run_id": run_id,
            "part_id": "synthetic_disconn",
            "payload": {
                "reference_panel_id": "P1",
                "thickness_mm": {"value": 1.5},
                "panels": {
                    "P1": {"panel_id": "P1", "adjacent_bend_ids": ["B1", "B2", "B3"]},
                    "P2": {"panel_id": "P2", "adjacent_bend_ids": ["B1"]},
                    "P3": {"panel_id": "P3", "adjacent_bend_ids": ["B2"]},
                    "P4": {"panel_id": "P4", "adjacent_bend_ids": ["B3"]},
                    "P5": {"panel_id": "P5", "adjacent_bend_ids": []}, # Disconnected!
                },
                "bends": {
                    "B1": {"bend_id": "B1", "angle_deg": {"value": 90.0}},
                    "B2": {"bend_id": "B2", "angle_deg": {"value": 90.0}},
                    "B3": {"bend_id": "B3", "angle_deg": {"value": 90.0}},
                }
            }
        }
        with open(cp_path, "w") as f:
            json.dump(canonical_part, f)

        worker = BaselineWorker(self.file_store)
        res_file = worker.execute(geom_res_path, cp_path)
        with open(res_file) as f:
            res = json.load(f)

        self.assertEqual(res["payload"]["baseline_status"], "INVALID")
        diag_codes = [d.get("code") for d in res.get("diagnostics", [])]
        self.assertIn("DISCONNECTED_PANEL_GRAPH", diag_codes)

if __name__ == "__main__":
    unittest.main()
