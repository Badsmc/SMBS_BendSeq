import unittest
import tempfile
import shutil
from pathlib import Path
from smbs_core.coordinator.run_pipeline import Coordinator
from smbs_core.protocols.file_store import FileStore
from smbs_core.workers.baseline.worker import BaselineWorker
import FreeCAD, Part

class TestBaselineTopologyMismatch(unittest.TestCase):
    """
    Negative test for Stage 1 Baseline topology verification.
    Verifies that shape tampering (e.g., substituting restored shape with a shape having
    different topology/face count) strictly results in:
    1. TOPOLOGY_MISMATCH diagnostic error.
    2. topology_matches == False in baseline metrics.
    3. baseline_status == "INVALID".
    4. pass == False.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.coordinator = Coordinator(workspace_root=self.temp_dir)
        self.file_store = FileStore()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_tampered_shape_triggers_topology_mismatch(self):
        source_path = Path("fixtures/freecad/z_ok/source.FCStd").resolve()
        res = self.coordinator.start_run(
            source_file_path=source_path,
            part_id="z_ok",
            k_factor_override=0.44
        )

        self.assertEqual(res["baseline_status"], "VALID")
        run_dir = Path(res["run_dir"])
        baseline_result_path = run_dir / "04_baseline_result.json"
        geom_result_path = run_dir / "02_geometry_result.json"
        canonical_part_path = run_dir / "04_canonical_part.json"

        # Tamper with geometry.brep (reference shape) by replacing it with a simple 6-face box
        ref_brep_path = run_dir / "geometry.brep"
        box = Part.makeBox(200.0, 50.0, 1.5)
        box.exportBrep(str(ref_brep_path))

        # Re-run BaselineWorker against the tampered reference shape
        baseline_worker = BaselineWorker(self.file_store)
        out_baseline = baseline_worker.execute(
            geometry_result_path=geom_result_path,
            canonical_part_path=canonical_part_path
        )

        tampered_result = self.file_store.read_json_verified(
            out_baseline, expected_schema_name="baseline_result"
        )

        payload = tampered_result["payload"]
        metrics = payload["metrics"]
        diagnostics = tampered_result["diagnostics"]

        # Check negative test invariants
        self.assertEqual(payload["baseline_status"], "INVALID")
        self.assertFalse(payload["pass"])
        self.assertFalse(metrics["topology_matches"])

        # Check that TOPOLOGY_MISMATCH diagnostic is logged
        diag_codes = [d["code"] for d in diagnostics]
        self.assertIn("TOPOLOGY_MISMATCH", diag_codes)

if __name__ == "__main__":
    unittest.main()
