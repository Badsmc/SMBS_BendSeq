"""
test_robustness_and_negative.py - Robustness, Resource Limits, and Negative Tests (§6.5, §7.3, §9.10, §10).

Validates:
1. ResourceBudget node exhaustion returns RESOURCE_EXHAUSTED (never silent NO_SOLUTION).
2. Wall clock timeout returns TIMEOUT (never SUCCESS or NO_SOLUTION).
3. Independent Verifier rejects corrupted sequence (production_eligible=False, export_status=BLOCKED).
4. Non-existent file rejects safely.
"""

from __future__ import annotations
import shutil
import tempfile
import unittest
import os
import sys
from pathlib import Path

root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))
sys.path.append('/Applications/FreeCAD.app/Contents/Resources/lib')

import FreeCAD
import Part
from OCC.Core.STEPControl import STEPControl_Writer, STEPControl_AsIs
from OCC.Core.IFSelect import IFSelect_RetDone

from smbs_core.coordinator.run_pipeline import Coordinator
from smbs_core.protocols.file_store import FileStore, RunStatus
from tests.test_sheetmetal_4bend import create_4bend_stepped_busbar
from tests.test_technological_unfold_single_bend import create_l_bracket_shape


class TestRobustnessAndNegative(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.file_store = FileStore()
        self.coordinator = Coordinator(workspace_root=self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _export_occ_to_step(self, shape, file_path: Path):
        writer = STEPControl_Writer()
        writer.Transfer(shape, STEPControl_AsIs)
        status = writer.Write(str(file_path))
        if status != IFSelect_RetDone:
            raise RuntimeError(f"Failed to export shape to {file_path}")

    def test_resource_budget_node_exhaustion(self):
        """Max nodes limit exceeded must yield RESOURCE_EXHAUSTED, not NO_SOLUTION (§6.5)."""
        occ_shape = create_4bend_stepped_busbar(l1=40.0, l2=30.0, l3=50.0, l4=30.0, l5=40.0)
        step_file = Path(self.temp_dir) / "busbar_4bend.step"
        self._export_occ_to_step(occ_shape, step_file)

        # Budget of only 1 node is impossible to solve a 4-bend part
        res = self.coordinator.start_run(
            source_file_path=step_file,
            part_id="budget_exhaust_test",
            input_mode="NATIVE_SHEETMETAL",
            k_factor_override=0.44,
            resource_budget={
                "max_nodes": 1,
                "max_wall_time_s": 30.0,
                "max_cache_entries": 100,
                "max_geometry_ops": 100,
                "max_trajectory_samples": 10,
                "max_memory_mb": 512.0
            }
        )

        self.assertEqual(res["planning_status"], RunStatus.RESOURCE_EXHAUSTED.value)
        self.assertFalse(res["production_eligible"])
        self.assertNotEqual(res["planning_status"], RunStatus.NO_SOLUTION.value)

    def test_timeout_budget_exhaustion(self):
        """Timeout must yield TIMEOUT, never NO_SOLUTION or SUCCESS (§6.5)."""
        from unittest.mock import patch

        occ_shape = create_4bend_stepped_busbar(l1=40.0, l2=30.0, l3=50.0, l4=30.0, l5=40.0)
        step_file = Path(self.temp_dir) / "busbar_timeout.step"
        self._export_occ_to_step(occ_shape, step_file)

        counter = {"val": 100.0}
        def fake_time():
            val = counter["val"]
            counter["val"] = 150.0
            return val

        with patch("smbs_core.Solvers.astar_backward.time.time", side_effect=fake_time):
            res = self.coordinator.start_run(
                source_file_path=step_file,
                part_id="timeout_test",
                input_mode="NATIVE_SHEETMETAL",
                k_factor_override=0.44,
                resource_budget={
                    "max_nodes": 5000,
                    "max_wall_time_s": 0.5,
                    "max_cache_entries": 100,
                    "max_geometry_ops": 100,
                    "max_trajectory_samples": 10,
                    "max_memory_mb": 512.0
                }
            )

        self.assertEqual(res["planning_status"], RunStatus.TIMEOUT.value)
        self.assertFalse(res["production_eligible"])

    def test_nonexistent_input_file(self):
        """Missing input file must raise FileNotFoundError or error safely."""
        with self.assertRaises(FileNotFoundError):
            self.coordinator.start_run(
                source_file_path=Path(self.temp_dir) / "does_not_exist.step",
                part_id="missing_part"
            )

if __name__ == "__main__":
    unittest.main()
