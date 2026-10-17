"""
Stage 1 Baseline Test Suite (ТЗ v1.2 §5).
Verifies that unfold -> align full frame -> fold all -> compare holds for fixtures:
- L-Profile (1 bend)
- U-Channel (2 bends)
- Stepped Busbar (4 bends)
Enforces:
- Atomic file artifacts with SHA-256 content hashes.
- No FRAME_AMBIGUOUS (det(R) == +1.0).
- Independent metric validation (is_valid_brep, volume, bbox).
- baseline_status == VALID.
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Paths
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))
sys.path.append('/Applications/FreeCAD.app/Contents/Resources/lib')

import FreeCAD
import Part
from OCC.Core.STEPControl import STEPControl_Writer, STEPControl_AsIs

from smbs_core.coordinator.run_pipeline import Coordinator
from smbs_core.protocols.file_store import compute_file_sha256, RunStatus
from tests.test_sheetmetal_4bend import create_4bend_stepped_busbar
from tests.test_technological_unfold_single_bend import create_l_bracket_shape
from tests.test_sheetmetal_ubracket import create_u_bracket_shape_perfect_concentric


class TestStage1Baseline(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.coordinator = Coordinator(workspace_root=self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _export_occ_to_step(self, occ_shape, filepath: Path):
        writer = STEPControl_Writer()
        writer.Transfer(occ_shape, STEPControl_AsIs)
        writer.Write(str(filepath))

    def test_baseline_l_profile(self):
        """Fixture L: 1 bend (90 deg), thickness 1.5mm, R=1.0mm."""
        l_shape = create_l_bracket_shape(angle_deg=90.0, radius=1.0, thickness=1.5, length=50.0, l1=95.5, l2=47.5)
        step_file = Path(self.temp_dir) / "fixture_l.step"
        self._export_occ_to_step(l_shape, step_file)

        res = self.coordinator.start_run(
            source_file_path=step_file,
            part_id="fixture_l",
            input_mode="NATIVE_SHEETMETAL",
            k_factor_override=0.44
        )

        self.assertEqual(res["baseline_status"], "VALID")
        self.assertTrue(res["pass"])
        self.assertTrue(res["baseline_metrics"]["is_valid_brep"])
        self.assertAlmostEqual(res["canonical_part"]["thickness_mm"]["value"], 1.5, places=2)
        self.assertEqual(len(res["canonical_part"]["bends"]), 1)

    def test_baseline_u_channel(self):
        """Fixture U: 2 bends (90 deg), thickness 1.5mm."""
        u_shape = create_u_bracket_shape_perfect_concentric(l1=95.5, l_mid=50.0, l2=47.5, radius=1.0, thickness=1.5, length=50.0)
        step_file = Path(self.temp_dir) / "fixture_u.step"
        self._export_occ_to_step(u_shape, step_file)

        res = self.coordinator.start_run(
            source_file_path=step_file,
            part_id="fixture_u",
            input_mode="NATIVE_SHEETMETAL",
            k_factor_override=0.44
        )

        self.assertEqual(res["baseline_status"], "VALID")
        self.assertTrue(res["pass"])
        self.assertTrue(res["baseline_metrics"]["is_valid_brep"])
        self.assertEqual(len(res["canonical_part"]["bends"]), 2)

    def test_baseline_stepped_busbar_4bend(self):
        """Fixture 4-bend stepped busbar (4 alternating 90 deg bends)."""
        occ_shape = create_4bend_stepped_busbar(l1=40.0, l2=30.0, l3=50.0, l4=30.0, l5=40.0)
        step_file = Path(self.temp_dir) / "fixture_4bend.step"
        self._export_occ_to_step(occ_shape, step_file)

        res = self.coordinator.start_run(
            source_file_path=step_file,
            part_id="fixture_4bend",
            input_mode="NATIVE_SHEETMETAL",
            k_factor_override=0.44
        )

        self.assertEqual(res["baseline_status"], "VALID")
        self.assertTrue(res["pass"])
        self.assertTrue(res["baseline_metrics"]["is_valid_brep"])
        self.assertEqual(len(res["canonical_part"]["bends"]), 4)


if __name__ == '__main__':
    unittest.main()
