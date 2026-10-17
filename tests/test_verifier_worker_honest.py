"""
Unit and regression tests for honest VerifierWorker (§7.1, Principles 8, 24).
Validates that VerifierWorker performs physical step-by-step simulation,
computes honest OpenCASCADE geometry metrics (no fabrication),
rejects invalid sequences, and correctly enforces unknown_policy.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

from smbs_core.protocols.file_store import FileStore, VerificationStatus
from smbs_core.workers.verifier.worker import VerifierWorker


class TestVerifierWorkerHonest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace_root = Path(self.temp_dir)
        self.file_store = FileStore()
        self.verifier = VerifierWorker(file_store=self.file_store)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _setup_run_env(self, src_run_dir: Path) -> Path:
        """Copies an existing run directory into isolated test workspace."""
        dst_run = self.workspace_root / "runs" / src_run_dir.name
        dst_run.mkdir(parents=True, exist_ok=True)
        for f in src_run_dir.iterdir():
            if f.is_file():
                shutil.copy(f, dst_run / f.name)
        return dst_run

    def test_verifier_detects_metric_fabrication(self):
        """
        Anti-fabrication test:
        If reference shape is replaced with a rotated/corrupted shape,
        verifier MUST calculate non-zero hausdorff and delta metrics,
        and MUST reject with VERIFICATION_FAILED rather than claiming 0.001 / VERIFIED.
        """
        src_run = Path("/Users/badmc/Desktop/SMBS_BendSeq/runs/run_20261012_034042_cf4c34")
        run_dir = self._setup_run_env(src_run)

        # Distort the geometry.brep reference shape by 15-degree rotation
        ref_brep = run_dir / "geometry.brep"
        orig_shape = self.verifier._load_brep(ref_brep)

        trsf = gp_Trsf()
        trsf.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 0.2618)  # ~15 deg
        distorted_shape = BRepBuilderAPI_Transform(orig_shape, trsf, True).Shape()

        from OCC.Core.BRepTools import breptools
        breptools.Write(distorted_shape, str(ref_brep))

        # Re-compute and update hash in canonical_part
        from smbs_core.protocols.file_store import compute_file_sha256
        new_hash = compute_file_sha256(ref_brep)
        cp_file = run_dir / "04_canonical_part.json"
        cp_data = json.loads(cp_file.read_text())
        cp_data["payload"]["geometry_artifact"]["content_hash"] = new_hash
        self.file_store.write_json_atomic(cp_file, cp_data, schema_name="canonical_part")

        # Execute verifier
        out_file = self.verifier.execute(
            search_result_path=run_dir / "07_search_result.json",
            canonical_part_path=cp_file,
            unknown_policy="EXPLORATORY"
        )
        res = json.loads(out_file.read_text())
        metrics = res["payload"]["independent_metrics"]

        # Honest assertions: hausdorff must reflect actual discrepancy (> 1.0 mm)
        self.assertGreater(metrics["hausdorff_distance_mm"], 1.0)
        self.assertEqual(res["payload"]["verification_status"], VerificationStatus.VERIFICATION_FAILED.value)
        self.assertFalse(res["payload"]["production_eligible"])

    def test_verifier_rejects_invalid_sequence_bend_id(self):
        """
        Sequence validity check:
        If candidate sequence contains a bend_id not in canonical_part,
        verifier must fail step verification with VERIFICATION_FAILED.
        """
        src_run = Path("/Users/badmc/Desktop/SMBS_BendSeq/runs/run_20261012_034042_cf4c34")
        run_dir = self._setup_run_env(src_run)

        res_file = run_dir / "07_search_result.json"
        res_data = json.loads(res_file.read_text())
        res_data["payload"]["candidate_sequence"][0]["bend_id"] = "BEND_NON_EXISTENT_99"
        self.file_store.write_json_atomic(res_file, res_data, schema_name="search_result")

        out_file = self.verifier.execute(
            search_result_path=res_file,
            canonical_part_path=run_dir / "04_canonical_part.json",
            unknown_policy="EXPLORATORY"
        )
        res = json.loads(out_file.read_text())

        self.assertEqual(res["payload"]["verification_status"], VerificationStatus.VERIFICATION_FAILED.value)
        self.assertFalse(res["payload"]["production_eligible"])
        self.assertEqual(res["payload"]["step_verifications"][0]["status"], "VERIFICATION_FAILED")
        self.assertTrue(any(d["code"] == "BEND_NOT_FOUND" for d in res["diagnostics"]))

    def test_verifier_rejects_unknown_under_production(self):
        """
        Policy check (PRODUCTION):
        Under default PRODUCTION policy, if tooling is SIMPLIFIED and collision check
        yields UNKNOWN, step validation fails and verifier returns VERIFICATION_FAILED.
        """
        src_run = Path("/Users/badmc/Desktop/SMBS_BendSeq/runs/run_20261012_034044_3ccc14")  # fixture_u
        run_dir = self._setup_run_env(src_run)

        out_file = self.verifier.execute(
            search_result_path=run_dir / "07_search_result.json",
            canonical_part_path=run_dir / "04_canonical_part.json",
            unknown_policy="PRODUCTION"
        )
        res = json.loads(out_file.read_text())

        self.assertEqual(res["payload"]["verification_status"], VerificationStatus.VERIFICATION_FAILED.value)
        self.assertFalse(res["payload"]["production_eligible"])
        self.assertTrue(any(d["code"] == "STEP_VALIDATION_FAILED" for d in res["diagnostics"]))

    def test_verifier_handles_unknown_under_exploratory(self):
        """
        Policy check (EXPLORATORY):
        Under EXPLORATORY, UNKNOWN steps continue simulation but cannot yield VERIFIED
        production status (schema enum has no PARTIAL, so VERIFICATION_FAILED with
        UNKNOWN_STEPS_UNDER_EXPLORATORY diagnostic is returned).
        """
        src_run = Path("/Users/badmc/Desktop/SMBS_BendSeq/runs/run_20261012_034044_3ccc14")  # fixture_u
        run_dir = self._setup_run_env(src_run)

        out_file = self.verifier.execute(
            search_result_path=run_dir / "07_search_result.json",
            canonical_part_path=run_dir / "04_canonical_part.json",
            unknown_policy="EXPLORATORY"
        )
        res = json.loads(out_file.read_text())

        self.assertEqual(res["payload"]["verification_status"], VerificationStatus.VERIFICATION_FAILED.value)
        self.assertFalse(res["payload"]["production_eligible"])
        self.assertTrue(any(d["code"] == "UNKNOWN_STEPS_UNDER_EXPLORATORY" for d in res["diagnostics"]))

    def test_verifier_rejects_failed_solver_result(self):
        """
        Precheck rejection:
        If solver returned NO_SOLUTION or empty candidate_sequence,
        verifier returns VERIFICATION_FAILED with SOLVER_FAILED diagnostic.
        """
        src_run = Path("/Users/badmc/Desktop/SMBS_BendSeq/runs/run_20261012_034042_cf4c34")
        run_dir = self._setup_run_env(src_run)

        res_file = run_dir / "07_search_result.json"
        res_data = json.loads(res_file.read_text())
        res_data["payload"]["planning_status"] = "NO_SOLUTION"
        res_data["payload"]["candidate_sequence"] = []
        self.file_store.write_json_atomic(res_file, res_data, schema_name="search_result")

        out_file = self.verifier.execute(
            search_result_path=res_file,
            canonical_part_path=run_dir / "04_canonical_part.json"
        )
        res = json.loads(out_file.read_text())

        self.assertEqual(res["payload"]["verification_status"], VerificationStatus.VERIFICATION_FAILED.value)
        self.assertFalse(res["payload"]["production_eligible"])
        self.assertTrue(any(d["code"] == "SOLVER_FAILED" for d in res["diagnostics"]))

    @unittest.expectedFailure
    def test_verifier_worker_2_1_3_4_deferred(self):
        """
        Deferred test for complex sequence 2-1-3-4 (KL-7 / KL-8 limitation):
        Part fails baseline topology mismatch in Stage 1, verifier cannot run yet.
        """
        from smbs_core.coordinator.run_pipeline import Coordinator
        coord = Coordinator()
        res = coord.start_run(
            source_file_path=Path("/Users/badmc/Desktop/SMBS_BendSeq/SMBendTests/2-1-3-4-(ok).FCStd"),
            part_id="part_2134",
            search_algorithm="dfs"
        )
        self.assertTrue(res.get("pass"))


if __name__ == "__main__":
    unittest.main()
