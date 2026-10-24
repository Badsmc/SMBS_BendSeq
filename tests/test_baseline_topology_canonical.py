import unittest
import tempfile
import shutil
from pathlib import Path
from smbs_core.coordinator.run_pipeline import Coordinator
from smbs_core.protocols.file_store import FileStore

class TestBaselineTopologyCanonical(unittest.TestCase):
    """
    Validation test for Stage 1 Baseline topology on all 7 fixtures:
    - u_bracket.brep
    - four_bend.brep
    - z_ok/source.FCStd
    - z_10_20_10_ok/source.FCStd
    - 135_ok/source.FCStd
    - u_60deg_30deg_ok/source.FCStd
    - u_90deg_90deg_ok/source.FCStd

    For every fixture, verifies:
    1. ref_faces_raw, ref_faces_canonical, restored_faces_raw, restored_faces_canonical match expected values.
    2. topology_matches == True.
    3. baseline_status == "VALID".
    4. pass == True.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.coordinator = Coordinator(workspace_root=self.temp_dir)
        self.file_store = FileStore()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _run_fixture_test(self, part_id: str, fixture_rel_path: str, expected_metrics: dict, max_hausdorff: float = None):
        source_path = (Path.cwd() / fixture_rel_path).resolve()
        res = self.coordinator.start_run(
            source_file_path=source_path,
            part_id=part_id,
            k_factor_override=0.44
        )

        self.assertEqual(res["baseline_status"], "VALID", f"Baseline status is not VALID for {part_id}")
        self.assertTrue(res["pass"], f"pass flag is not True for {part_id}")

        b_metrics = res["baseline_metrics"]
        self.assertTrue(b_metrics["topology_matches"], f"topology_matches is False for {part_id}")

        for metric_name, expected_val in expected_metrics.items():
            self.assertEqual(
                b_metrics.get(metric_name),
                expected_val,
                f"Metric {metric_name} mismatch for {part_id}: expected {expected_val}, got {b_metrics.get(metric_name)}"
            )

        if max_hausdorff is not None:
            self.assertLess(
                b_metrics.get("hausdorff_distance_mm", 9999.0),
                max_hausdorff,
                f"Hausdorff distance exceeded for {part_id}: got {b_metrics.get('hausdorff_distance_mm')}, expected < {max_hausdorff}"
            )

    def test_u_bracket(self):
        self._run_fixture_test(
            part_id="u_bracket",
            fixture_rel_path="fixtures/synthetic/u_bracket.brep",
            max_hausdorff=0.2,
            expected_metrics={
                "ref_faces_raw": 14,
                "ref_faces_canonical": 14,
                "restored_faces_raw": 14,
                "restored_faces_canonical": 14,
                "ref_cyl_faces_raw": 4,
                "ref_cyl_faces_canonical": 4,
                "restored_cyl_faces_raw": 4,
                "restored_cyl_faces_canonical": 4,
            }
        )

    def test_four_bend(self):
        self._run_fixture_test(
            part_id="four_bend",
            fixture_rel_path="fixtures/synthetic/four_bend.brep",
            expected_metrics={
                "ref_faces_raw": 22,
                "ref_faces_canonical": 22,
                "restored_faces_raw": 22,
                "restored_faces_canonical": 22,
                "ref_cyl_faces_raw": 8,
                "ref_cyl_faces_canonical": 8,
                "restored_cyl_faces_raw": 8,
                "restored_cyl_faces_canonical": 8,
            }
        )

    def test_z_ok(self):
        self._run_fixture_test(
            part_id="z_ok",
            fixture_rel_path="fixtures/freecad/z_ok/source.FCStd",
            expected_metrics={
                "ref_faces_raw": 22,
                "ref_faces_canonical": 14,
                "restored_faces_raw": 14,
                "restored_faces_canonical": 14,
                "ref_cyl_faces_raw": 4,
                "ref_cyl_faces_canonical": 4,
                "restored_cyl_faces_raw": 4,
                "restored_cyl_faces_canonical": 4,
            }
        )

    def test_z_10_20_10_ok(self):
        self._run_fixture_test(
            part_id="z_10_20_10_ok",
            fixture_rel_path="fixtures/freecad/z_10_20_10_ok/source.FCStd",
            expected_metrics={
                "ref_faces_raw": 22,
                "ref_faces_canonical": 14,
                "restored_faces_raw": 14,
                "restored_faces_canonical": 14,
                "ref_cyl_faces_raw": 4,
                "ref_cyl_faces_canonical": 4,
                "restored_cyl_faces_raw": 4,
                "restored_cyl_faces_canonical": 4,
            }
        )

    def test_135_ok(self):
        self._run_fixture_test(
            part_id="135_ok",
            fixture_rel_path="fixtures/freecad/135_ok/source.FCStd",
            expected_metrics={
                "ref_faces_raw": 22,
                "ref_faces_canonical": 14,
                "restored_faces_raw": 14,
                "restored_faces_canonical": 14,
                "ref_cyl_faces_raw": 4,
                "ref_cyl_faces_canonical": 4,
                "restored_cyl_faces_raw": 4,
                "restored_cyl_faces_canonical": 4,
            }
        )

    def test_u_60deg_30deg_ok(self):
        self._run_fixture_test(
            part_id="u_60deg_30deg_ok",
            fixture_rel_path="fixtures/freecad/u_60deg_30deg_ok/source.FCStd",
            expected_metrics={
                "ref_faces_raw": 22,
                "ref_faces_canonical": 14,
                "restored_faces_raw": 14,
                "restored_faces_canonical": 14,
                "ref_cyl_faces_raw": 4,
                "ref_cyl_faces_canonical": 4,
                "restored_cyl_faces_raw": 4,
                "restored_cyl_faces_canonical": 4,
            }
        )

    def test_u_90deg_90deg_ok(self):
        self._run_fixture_test(
            part_id="u_90deg_90deg_ok",
            fixture_rel_path="fixtures/freecad/u_90deg_90deg_ok/source.FCStd",
            expected_metrics={
                "ref_faces_raw": 22,
                "ref_faces_canonical": 14,
                "restored_faces_raw": 14,
                "restored_faces_canonical": 14,
                "ref_cyl_faces_raw": 4,
                "ref_cyl_faces_canonical": 4,
                "restored_cyl_faces_raw": 4,
                "restored_cyl_faces_canonical": 4,
            }
        )

if __name__ == "__main__":
    unittest.main()
