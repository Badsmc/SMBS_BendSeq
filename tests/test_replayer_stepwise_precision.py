"""
test_replayer_stepwise_precision.py - Tests for per-step precision in Replayer (P0).

Verifies:
1. Degradation isolation: non-monolithic geometry at step k marks only step k as PREVIEW_ONLY,
   while valid monolithic steps before and after preserve VALID_PER_PANEL.
2. Full monolithic sequence yields VALID_PER_PANEL for all steps.
3. Diagnostic warning is present and actionable whenever a step degrades to PREVIEW_ONLY.
"""

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from OCC.Core.BRepTools import breptools
from OCC.Core.BRep import BRep_Builder
from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.gp import gp_Pnt

from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
from smbs_core.viewer.graph_builder import build_graphs
from smbs_core.viewer.replayer import (
    replay_backward,
    _is_valid_solid,
    GEOMETRY_PRECISION_VALID,
    GEOMETRY_PRECISION_PREVIEW,
)
from smbs_core.viewer.run_loader import load_run
import smbs_core.Geometry.sheetmetal_folder as smf


class TestReplayerStepwisePrecision(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="smbs_test_stepwise_"))
        self.backend = PythonOCCBackend()

        root = Path(__file__).resolve().parent.parent
        self.u_run_dir = root / "runs/run_20261018_190131_085a13"
        if not self.u_run_dir.exists():
            self.skipTest(f"Fixture run {self.u_run_dir} not found on disk")

        cp_src = self.u_run_dir / "04_canonical_part.json"
        geom_src = self.u_run_dir / "geometry.brep"
        flat_src = self.u_run_dir / "flat_pattern.brep"

        shutil.copy(geom_src, self.temp_dir / "geometry.brep")
        shutil.copy(flat_src, self.temp_dir / "flat_pattern.brep")

        with open(cp_src, "r") as f:
            cp_data = json.load(f)

        cp_data["payload"]["geometry_artifact"]["file_path"] = str(self.temp_dir / "geometry.brep")
        with open(self.temp_dir / "04_canonical_part.json", "w") as f:
            json.dump(cp_data, f)

        # N=2 candidate sequence (B1, B2) -> 3 steps (step 0, 1, 2)
        self.search_result_data = {
            "schema_name": "search_result",
            "schema_version": "1.1",
            "payload": {
                "candidate_sequence": [
                    {
                        "step": 1,
                        "bend_id": "BEND_01_6ff30710",
                        "planner_status": "VALID",
                        "collision_status": "VALID",
                        "tooling_status": "SIMPLIFIED",
                        "alignment_status": "OK",
                    },
                    {
                        "step": 2,
                        "bend_id": "BEND_02_d7ffaaa9",
                        "planner_status": "VALID",
                        "collision_status": "VALID",
                        "tooling_status": "SIMPLIFIED",
                        "alignment_status": "OK",
                    },
                ]
            },
        }
        with open(self.temp_dir / "07_search_result.json", "w") as f:
            json.dump(self.search_result_data, f)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_all_valid_steps_preserve_valid_precision(self):
        """When all constructive steps are valid monolithic solids, all steps yield VALID_PER_PANEL."""
        artifacts = load_run(self.temp_dir)
        graphs = build_graphs(artifacts)
        views = list(replay_backward(artifacts, graphs, self.backend))

        self.assertEqual(len(views), 3)
        for idx, v in enumerate(views):
            self.assertEqual(v.geometry_precision, GEOMETRY_PRECISION_VALID, f"Step {idx} precision mismatch")
            self.assertIsNone(v.geometry_warning, f"Step {idx} should have no warning")

    def test_degradation_isolation_single_intermediate_step(self):
        """
        When step 1 constructive fold produces a non-monolithic shape (or fails check),
        step 1 is marked PREVIEW_ONLY with diagnostic warning, while step 0 and step 2
        remain VALID_PER_PANEL (isolation of degradation).
        """
        orig_fold = smf.SheetMetalFolder.fold_from_canonical_part
        call_count = [0]

        def mock_fold(canonical_part, flat_candidate):
            call_count[0] += 1
            if call_count[0] == 1:
                # Step 0: genuine solid
                return orig_fold(canonical_part, flat_candidate)
            elif call_count[0] == 2:
                # Step 1: return a compound with 2 disjoint boxes (non-monolithic)
                from OCC.Core.TopoDS import TopoDS_Compound
                from OCC.Core.BRep import BRep_Builder
                b1 = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 10, 10, 10).Shape()
                b2 = BRepPrimAPI_MakeBox(gp_Pnt(20, 20, 20), 10, 10, 10).Shape()
                comp = TopoDS_Compound()
                builder = BRep_Builder()
                builder.MakeCompound(comp)
                builder.Add(comp, b1)
                builder.Add(comp, b2)
                return comp
            else:
                return orig_fold(canonical_part, flat_candidate)

        with patch.object(smf.SheetMetalFolder, "fold_from_canonical_part", side_effect=mock_fold):
            artifacts = load_run(self.temp_dir)
            graphs = build_graphs(artifacts)
            views = list(replay_backward(artifacts, graphs, self.backend))

            self.assertEqual(len(views), 3)

            # Step 0: VALID_PER_PANEL
            self.assertEqual(views[0].step_index, 0)
            self.assertEqual(views[0].geometry_precision, GEOMETRY_PRECISION_VALID)
            self.assertIsNone(views[0].geometry_warning)

            # Step 1: PREVIEW_ONLY + honest diagnostic
            self.assertEqual(views[1].step_index, 1)
            self.assertEqual(views[1].geometry_precision, GEOMETRY_PRECISION_PREVIEW)
            self.assertIsNotNone(views[1].geometry_warning)
            self.assertIn("not a verified monolithic solid", views[1].geometry_warning)

            # Step 2: VALID_PER_PANEL (final flat pattern is a verified solid)
            self.assertEqual(views[2].step_index, 2)
            self.assertEqual(views[2].geometry_precision, GEOMETRY_PRECISION_VALID)
            self.assertIsNone(views[2].geometry_warning)


if __name__ == "__main__":
    unittest.main()
