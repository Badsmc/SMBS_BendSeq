"""
Unit and integration tests verifying direct fold_flat_input behavior (S.48b / Component 2b).
Ensures U-channels and linear chains are physically folded directly from flat pattern
without artificial displacement, producing Hausdorff distance < 0.001 mm.
"""

import sys
import tempfile
import unittest
from pathlib import Path

from OCC.Core.STEPControl import STEPControl_Writer, STEPControl_AsIs

from smbs_core.coordinator.run_pipeline import Coordinator
from smbs_core.workers.baseline.worker import BaselineWorker
from tests.test_sheetmetal_ubracket import create_u_bracket_shape_perfect_concentric


class TestBaselineFoldFlatInput(unittest.TestCase):
    def setUp(self):
        self.bw = BaselineWorker()
        self.repo_root = Path(__file__).parent.parent.resolve()

    def test_stage1_u_channel_hausdorff_zero(self):
        """Fixture U (2 bends): direct flat input eliminates 51.36mm shift, hausdorff < 0.001 mm."""
        with tempfile.TemporaryDirectory() as td:
            u_shape = create_u_bracket_shape_perfect_concentric(
                l1=95.5, l_mid=50.0, l2=47.5, radius=1.0, thickness=1.5, length=50.0
            )
            step_file = Path(td) / "fixture_u.step"
            writer = STEPControl_Writer()
            writer.Transfer(u_shape, STEPControl_AsIs)
            writer.Write(str(step_file))

            coordinator = Coordinator(workspace_root=Path(td))
            res = coordinator.start_run(
                source_file_path=step_file,
                part_id="fixture_u",
                input_mode="NATIVE_SHEETMETAL",
                k_factor_override=0.44
            )

            self.assertEqual(res["baseline_status"], "VALID")
            self.assertTrue(res["pass"])

            run_dir = list(Path(td).glob("runs/run_*"))[0]
            ref_shape = self.bw._load_brep(run_dir / "geometry.brep")
            rest_shape = self.bw._load_brep(run_dir / "restored.brep")
            h = BaselineWorker._compute_hausdorff_distance(ref_shape, rest_shape)
            self.assertLess(h, 0.001, f"Expected hausdorff < 0.001 mm, got {h:.6f}")

    def test_u_60deg_hausdorff_zero(self):
        """FCStd u_60deg_30deg_ok: regression pin verifying hausdorff < 0.001 mm."""
        fixture_path = self.repo_root / "fixtures/freecad/u_60deg_30deg_ok/source.FCStd"
        with tempfile.TemporaryDirectory() as td:
            coordinator = Coordinator(workspace_root=Path(td))
            res = coordinator.start_run(
                source_file_path=fixture_path,
                part_id="u_60deg_30deg_ok",
                k_factor_override=0.44
            )

            self.assertEqual(res["baseline_status"], "VALID")
            self.assertTrue(res["pass"])

            run_dir = list(Path(td).glob("runs/run_*"))[0]
            ref_shape = self.bw._load_brep(run_dir / "geometry.brep")
            rest_shape = self.bw._load_brep(run_dir / "restored.brep")
            h = BaselineWorker._compute_hausdorff_distance(ref_shape, rest_shape)
            self.assertLess(h, 0.001, f"Expected hausdorff < 0.001 mm, got {h:.6f}")


if __name__ == "__main__":
    unittest.main()
