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

class TestBaselineTopologyRotationNegative(unittest.TestCase):
    """
    Negative test for topology comparison (§5.2, §8):
    Takes u_bracket.brep, rotates the restored part 90 degrees around the X axis.
    Verifies that compare_topologies returns topology_matches == False
    both directly and after candidate symmetry transformations.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.coordinator = Coordinator(workspace_root=self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_u_bracket_rotated_90_x_negative(self):
        fixture_path = (Path.cwd() / "fixtures/synthetic/u_bracket.brep").resolve()
        res = self.coordinator.start_run(
            source_file_path=fixture_path,
            part_id="u_bracket",
            k_factor_override=0.44
        )

        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["baseline_status"], "VALID")
        self.assertTrue(res["pass"])

        run_id = res["run_id"]
        run_dir = Path(self.temp_dir) / "runs" / run_id
        worker = BaselineWorker(self.coordinator.file_store)

        ref_shape = worker._load_brep(run_dir / "geometry.brep")
        restored_shape = worker._load_brep(run_dir / "restored.brep")

        # 1. Canonical unification of reference shape
        u_ref = ShapeUpgrade_UnifySameDomain(ref_shape, True, True, True)
        u_ref.Build()
        can_ref = u_ref.Shape()

        # 2. Rotate restored shape 90 degrees around X axis
        trsf_x90 = gp_Trsf()
        trsf_x90.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(1, 0, 0)), math.radians(90.0))
        rot90_rest = BRepBuilderAPI_Transform(restored_shape, trsf_x90, True).Shape()

        u_rot = ShapeUpgrade_UnifySameDomain(rot90_rest, True, True, True)
        u_rot.Build()
        can_rot = u_rot.Shape()

        # 3. Extract topologies
        top_ref = worker.extract_topology(can_ref)
        top_rot = worker.extract_topology(can_rot)

        # 4. Compare directly
        comp_direct = worker.compare_topologies(top_ref, top_rot)
        self.assertFalse(
            comp_direct["topology_matches"],
            "Direct comparison of 90° X-rotated part must return topology_matches == False"
        )
        self.assertFalse(
            comp_direct["normals_match"],
            "Normals of 90° X-rotated part must not match"
        )

        # 5. Verify that even checking the part symmetries doesn't falsely validate the 90° X rotation
        symmetries_matched = False
        for ax_dir in [gp_Dir(0, 1, 0), gp_Dir(1, 0, 0), gp_Dir(0, 0, 1)]:
            t = gp_Trsf()
            t.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), ax_dir), math.pi)
            sym_rot = BRepBuilderAPI_Transform(can_rot, t, True).Shape()
            sym_top = worker.extract_topology(sym_rot)
            if worker.compare_topologies(top_ref, sym_top)["topology_matches"]:
                symmetries_matched = True
                break

        self.assertFalse(
            symmetries_matched,
            "Symmetry evaluation of 90° X-rotated part must return False"
        )

if __name__ == "__main__":
    unittest.main()
