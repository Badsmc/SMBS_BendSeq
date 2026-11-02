"""
End-to-End Pipeline & Qualification Test Suite (technical specification v1.2 §1.3, §4, §5, §6, §7).
Verifies complete 3-stage pipeline from input model to production 10_part_bendseq.json v1.1.
Fixtures tested:
- Fixture L (1 bend)
- Fixture U (2 bends)
- Stepped Busbar (4 bends)
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

from OCC.Core.STEPControl import STEPControl_Writer, STEPControl_AsIs

from smbs_core.coordinator.run_pipeline import Coordinator
from smbs_core.protocols.file_store import FileStore, compute_file_sha256
from tests.test_sheetmetal_4bend import create_4bend_stepped_busbar
from tests.test_technological_unfold_single_bend import create_l_bracket_shape
from tests.test_sheetmetal_ubracket import create_u_bracket_shape_perfect_concentric


class TestEndToEndPipeline(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.coordinator = Coordinator(workspace_root=self.temp_dir)
        self.file_store = FileStore()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _export_occ_to_step(self, occ_shape, filepath: Path):
        writer = STEPControl_Writer()
        writer.Transfer(occ_shape, STEPControl_AsIs)
        writer.Write(str(filepath))

    def test_plumbing_fixture_l_no_tooling_honestly_no_solution(self):
        """Full pipeline execution on Fixture L (1 bend)."""
        l_shape = create_l_bracket_shape(angle_deg=90.0, radius=1.0, thickness=1.5, length=50.0, l1=95.5, l2=47.5)
        step_file = Path(self.temp_dir) / "fixture_l.step"
        self._export_occ_to_step(l_shape, step_file)

        res = self.coordinator.start_run(
            source_file_path=step_file,
            part_id="fixture_l",
            input_mode="NATIVE_SHEETMETAL",
            k_factor_override=0.44
        )

        # Stage 1 Baseline
        self.assertEqual(res["baseline_status"], "VALID")

        # Plumbing test: no machine_config -> SIMPLIFIED synthetic tooling.
        # Pipeline is honest: APPROACH returns UNKNOWN under PRODUCTION (invariant #1),
        # so planning is NO_SOLUTION and export is BLOCKED. Verifier cannot certify
        # without real tooling geometry. Real tooling physics e2e is in
        # test_physics_e2e_with_real_tooling.py (Commit 4).
        #
        # This test verifies PLUMBING: pipeline completes, produces honest artifacts,
        # does NOT falsely certify or export.
        self.assertEqual(
            res["planning_status"], "NO_SOLUTION",
            f"SIMPLIFIED tooling under PRODUCTION must return NO_SOLUTION, "
            f"got {res['planning_status']}"
        )
        self.assertNotEqual(
            res["verification_status"], "VERIFIED",
            f"Verifier must not certify SIMPLIFIED tooling (invariant #1), "
            f"got {res['verification_status']}"
        )
        self.assertFalse(
            res["production_eligible"],
            "production_eligible must be False without real tooling (invariant #1)"
        )
        self.assertEqual(
            res["export_status"], "BLOCKED",
            f"Export must be BLOCKED without VERIFIED status (invariant #2), "
            f"got {res['export_status']}"
        )

        # Plumbing artifacts MUST be produced even on NO_SOLUTION:
        run_dir = Path(res["run_dir"])
        self.assertTrue(
            (run_dir / "07_search_result.json").exists(),
            "search_result must be produced (plumbing)"
        )
        self.assertTrue(
            (run_dir / "11_run_report.json").exists(),
            "run_report must be produced (plumbing)"
        )
        # Final export MUST NOT be produced (only _candidate):
        self.assertFalse(
            (run_dir / "10_part_bendseq.json").exists(),
            "final export must not be produced without VERIFIED status (invariant #2)"
        )

    def test_plumbing_fixture_u_no_tooling_honestly_no_solution(self):
        """Full pipeline execution on Fixture U (2 bends)."""
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

        # Plumbing test: no machine_config -> SIMPLIFIED synthetic tooling.
        # Pipeline is honest: APPROACH returns UNKNOWN under PRODUCTION (invariant #1),
        # so planning is NO_SOLUTION and export is BLOCKED. Verifier cannot certify
        # without real tooling geometry. Real tooling physics e2e is in
        # test_physics_e2e_with_real_tooling.py (Commit 4).
        #
        # This test verifies PLUMBING: pipeline completes, produces honest artifacts,
        # does NOT falsely certify or export.
        self.assertEqual(
            res["planning_status"], "NO_SOLUTION",
            f"SIMPLIFIED tooling under PRODUCTION must return NO_SOLUTION, "
            f"got {res['planning_status']}"
        )
        self.assertNotEqual(
            res["verification_status"], "VERIFIED",
            f"Verifier must not certify SIMPLIFIED tooling (invariant #1), "
            f"got {res['verification_status']}"
        )
        self.assertFalse(
            res["production_eligible"],
            "production_eligible must be False without real tooling (invariant #1)"
        )
        self.assertEqual(
            res["export_status"], "BLOCKED",
            f"Export must be BLOCKED without VERIFIED status (invariant #2), "
            f"got {res['export_status']}"
        )

        # Plumbing artifacts MUST be produced even on NO_SOLUTION:
        run_dir = Path(res["run_dir"])
        self.assertTrue(
            (run_dir / "07_search_result.json").exists(),
            "search_result must be produced (plumbing)"
        )
        self.assertTrue(
            (run_dir / "11_run_report.json").exists(),
            "run_report must be produced (plumbing)"
        )
        # Final export MUST NOT be produced (only _candidate):
        self.assertFalse(
            (run_dir / "10_part_bendseq.json").exists(),
            "final export must not be produced without VERIFIED status (invariant #2)"
        )

    def test_plumbing_stepped_busbar_4bend_no_tooling_honestly_no_solution(self):
        """Full pipeline execution on Stepped Busbar (4 bends)."""
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

        # Plumbing test: no machine_config -> SIMPLIFIED synthetic tooling.
        # Pipeline is honest: APPROACH returns UNKNOWN under PRODUCTION (invariant #1),
        # so planning is NO_SOLUTION and export is BLOCKED. Verifier cannot certify
        # without real tooling geometry. Real tooling physics e2e is in
        # test_physics_e2e_with_real_tooling.py (Commit 4).
        #
        # This test verifies PLUMBING: pipeline completes, produces honest artifacts,
        # does NOT falsely certify or export.
        self.assertEqual(
            res["planning_status"], "NO_SOLUTION",
            f"SIMPLIFIED tooling under PRODUCTION must return NO_SOLUTION, "
            f"got {res['planning_status']}"
        )
        self.assertNotEqual(
            res["verification_status"], "VERIFIED",
            f"Verifier must not certify SIMPLIFIED tooling (invariant #1), "
            f"got {res['verification_status']}"
        )
        self.assertFalse(
            res["production_eligible"],
            "production_eligible must be False without real tooling (invariant #1)"
        )
        self.assertEqual(
            res["export_status"], "BLOCKED",
            f"Export must be BLOCKED without VERIFIED status (invariant #2), "
            f"got {res['export_status']}"
        )

        # Plumbing artifacts MUST be produced even on NO_SOLUTION:
        run_dir = Path(res["run_dir"])
        self.assertTrue(
            (run_dir / "07_search_result.json").exists(),
            "search_result must be produced (plumbing)"
        )
        self.assertTrue(
            (run_dir / "11_run_report.json").exists(),
            "run_report must be produced (plumbing)"
        )
        # Final export MUST NOT be produced (only _candidate):
        self.assertFalse(
            (run_dir / "10_part_bendseq.json").exists(),
            "final export must not be produced without VERIFIED status (invariant #2)"
        )


if __name__ == '__main__':
    unittest.main()
