import unittest
import tempfile
import shutil
import math
from pathlib import Path

from OCC.Core.gp import gp_Trsf, gp_Ax1, gp_Pnt, gp_Dir
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

from smbs_core.coordinator.run_pipeline import Coordinator
from smbs_core.workers.baseline.worker import BaselineWorker

class TestBaselineTopologyTree(unittest.TestCase):
    """
    Validation test suite for Task 1.0.1b-3a (Tree & Branch Part Support).
    Tests all 4 tree fixtures across:
    1. RAW topology comparison
    2. CANONICAL topology comparison (unifier domain)
    3. ROTATED negative test (90 deg rotation breaks topology match)
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.coordinator = Coordinator(workspace_root=self.temp_dir)
        self.file_store = self.coordinator.file_store

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _run_tree_fixture(self, part_id: str, brep_rel_path: str):
        fixture_path = (Path.cwd() / brep_rel_path).resolve()
        res = self.coordinator.start_run(
            source_file_path=fixture_path,
            part_id=part_id,
            k_factor_override=0.40
        )

        self.assertEqual(res["baseline_status"], "VALID", f"Baseline status is not VALID for {part_id}")
        self.assertTrue(res["pass"], f"pass flag is not True for {part_id}")

        run_id = res["run_id"]
        run_dir = Path(self.temp_dir) / "runs" / run_id
        worker = BaselineWorker(self.file_store)

        ref_shape = worker._load_brep(run_dir / "geometry.brep")
        restored_shape = worker._load_brep(run_dir / "restored.brep")

        # 1. CANONICAL Match
        u_ref = ShapeUpgrade_UnifySameDomain(ref_shape, True, True, True)
        u_ref.Build()
        can_ref = u_ref.Shape()

        u_rest = ShapeUpgrade_UnifySameDomain(restored_shape, True, True, True)
        u_rest.Build()
        can_rest = u_rest.Shape()

        top_ref_can = worker.extract_topology(can_ref)
        top_rest_can = worker.extract_topology(can_rest)
        cmp_can = worker.compare_topologies(top_ref_can, top_rest_can, 0.1, 0.5)

        self.assertTrue(cmp_can["topology_matches"], f"CANONICAL topology mismatch for {part_id}: {cmp_can}")

        # 2. RAW Comparison
        top_ref_raw = worker.extract_topology(ref_shape)
        top_rest_raw = worker.extract_topology(restored_shape)
        self.assertGreater(top_ref_raw["counts"]["Total"], 0)
        self.assertGreater(top_rest_raw["counts"]["Total"], 0)

        # 3. ROTATED Negative Test (90 deg around X axis must FAIL topology match)
        trsf_x90 = gp_Trsf()
        trsf_x90.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(1, 0, 0)), math.radians(90.0))
        rot90_rest = BRepBuilderAPI_Transform(can_rest, trsf_x90, True).Shape()
        top_rot_can = worker.extract_topology(rot90_rest)
        cmp_rot = worker.compare_topologies(top_ref_can, top_rot_can, 0.1, 0.5)

        self.assertFalse(cmp_rot["topology_matches"], f"ROTATED 90 deg unexpectedly matched topology for {part_id}!")

    def test_sqin_200_25_10_ok(self):
        self._run_tree_fixture("sqin_200_25_10_ok", "fixtures/freecad/sqin_200_25_10_ok/part_3d.brep")

    def test_sqout_200_25_10_ok(self):
        self._run_tree_fixture("sqout_200_25_10_ok", "fixtures/freecad/sqout_200_25_10_ok/part_3d.brep")

    def test_box_100out_50in_ok(self):
        self._run_tree_fixture("box_100out_50in_ok", "fixtures/freecad/box_100out_50in_ok/part_3d.brep")

    def test_box_100in_50out_false(self):
        self._run_tree_fixture("box_100in_50out_false", "fixtures/freecad/box_100in_50out_false/part_3d.brep")

if __name__ == "__main__":
    unittest.main()
