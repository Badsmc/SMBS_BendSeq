import unittest
import tempfile
import shutil
import math
from pathlib import Path

from OCC.Core.gp import gp_Trsf, gp_Ax1, gp_Pnt, gp_Dir
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.BRepTools import breptools

from smbs_core.coordinator.run_pipeline import Coordinator
from smbs_core.Geometry.sheetmetal_folder import SheetMetalFolder

class TestFoldTreeNegative(unittest.TestCase):
    """
    Integration negative test (§3, Task 1.0.1b-3a):
    Runs pipeline on sqin_200_25_10_ok with a corrupted/rotated restored.brep
    (e.g. hub rotated 90 degrees around X axis via monkeypatching fold_tree_from_flat_pattern).
    Verifies that baseline_status == INVALID and TOPOLOGY_MISMATCH is present in diagnostics.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.coordinator = Coordinator(workspace_root=self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_tampered_tree_hub_rotation_triggers_invalid(self):
        fixture_path = (Path.cwd() / "fixtures/freecad/sqin_200_25_10_ok/part_3d.brep").resolve()

        original_fold_tree = SheetMetalFolder.fold_tree_from_flat_pattern

        def tampered_fold_tree(flat_candidate, canonical_part):
            orig_shape = original_fold_tree(flat_candidate, canonical_part)
            # Rotate entire folded shape 90 degrees around X axis
            trsf = gp_Trsf()
            trsf.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(1, 0, 0)), math.radians(90.0))
            return BRepBuilderAPI_Transform(orig_shape, trsf, True).Shape()

        try:
            SheetMetalFolder.fold_tree_from_flat_pattern = tampered_fold_tree
            res = self.coordinator.start_run(
                source_file_path=fixture_path,
                part_id="sqin_200_25_10_ok",
                k_factor_override=0.40
            )

            # Verification: pipeline must fail with INVALID baseline status
            self.assertEqual(res["baseline_status"], "INVALID")
            self.assertFalse(res["pass"])
            diag_codes = [d.get("code") for d in res.get("diagnostics", [])]
            self.assertIn("TOPOLOGY_MISMATCH", diag_codes)
        finally:
            SheetMetalFolder.fold_tree_from_flat_pattern = original_fold_tree

if __name__ == "__main__":
    unittest.main()
