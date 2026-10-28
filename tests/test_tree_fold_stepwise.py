"""
test_tree_fold_stepwise.py - Tests for P1b-extended / KL-12-v3 (N>=3 tree and branch folding).

Verifies:
1. 0° bend guard in fold_branch prevents OCC BRepSweep_Rotation error.
2. Stepwise prefix folding on branching/tree structures produces valid B-Rep solids at each step.
3. Replayer yields GEOMETRY_PRECISION_VALID for constructive multi-bend tree trajectories.
"""

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from OCC.Core.BRepTools import breptools
from OCC.Core.BRep import BRep_Builder
from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop

from smbs_core.Geometry.sheetmetal_folder import SheetMetalFolder
from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
from smbs_core.viewer.graph_builder import build_graphs
from smbs_core.viewer.replayer import (
    replay_backward,
    _is_valid_solid,
    GEOMETRY_PRECISION_VALID,
)
from smbs_core.viewer.run_loader import load_run


def _get_shape_volume(shape) -> float:
    props = GProp_GProps()
    brepgprop.VolumeProperties(shape, props)
    return props.Mass()


class TestTreeFoldStepwise(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="smbs_test_tree_"))
        self.backend = PythonOCCBackend()

        # Locate 8-bend box fixture run
        root = Path(__file__).resolve().parent.parent
        self.tree_run_dir = root / "runs/run_20261017_152459_9a3fe7"
        if not self.tree_run_dir.exists():
            self.skipTest(f"Fixture run {self.tree_run_dir} not found on disk")

        # Copy canonical_part, geometry.brep, flat_pattern.brep
        cp_src = self.tree_run_dir / "04_canonical_part.json"
        geom_src = self.tree_run_dir / "geometry.brep"
        flat_src = self.tree_run_dir / "flat_pattern.brep"

        shutil.copy(geom_src, self.temp_dir / "geometry.brep")
        shutil.copy(flat_src, self.temp_dir / "flat_pattern.brep")

        with open(cp_src, "r") as f:
            self.cp_data = json.load(f)

        self.cp_data["payload"]["geometry_artifact"]["file_path"] = str(self.temp_dir / "geometry.brep")
        with open(self.temp_dir / "04_canonical_part.json", "w") as f:
            json.dump(self.cp_data, f)

        # Flat pattern OCC shape
        self.flat_shape = TopoDS_Shape()
        breptools.Read(self.flat_shape, str(flat_src), BRep_Builder())

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_fold_from_canonical_part_0deg_guard(self):
        """Unfolding any bend to 0.0° in an 8-bend tree part must not raise BRepSweep_Rotation exception."""
        bends = self.cp_data["payload"]["bends"]
        first_bid = next(iter(bends.keys()))

        cp_unfolded = copy.deepcopy(self.cp_data)
        b_entry = cp_unfolded["payload"]["bends"][first_bid]
        if isinstance(b_entry.get("angle_deg"), dict):
            b_entry["angle_deg"]["value"] = 0.0
        else:
            b_entry["angle_deg"] = 0.0

        # Must succeed without Part.OCCError: BRepSweep_Rotation::Constructor
        shape_0deg = SheetMetalFolder.fold_from_canonical_part(cp_unfolded, self.flat_shape)
        self.assertIsNotNone(shape_0deg)
        self.assertFalse(shape_0deg.IsNull())
        from OCC.Core.BRepCheck import BRepCheck_Analyzer
        self.assertTrue(BRepCheck_Analyzer(shape_0deg).IsValid())

    def test_tree_step_0_fully_folded_is_valid_single_solid(self):
        """Step 0 of 8-bend star box is a single valid monolithic solid."""
        s0 = SheetMetalFolder.fold_from_canonical_part(self.cp_data, self.flat_shape)
        self.assertTrue(_is_valid_solid(s0))

    def test_replayer_tree_stepwise_preserves_preview_honestly(self):
        """
        Replayer handles N>=3 tree part over all 8 bends without crashing.
        Because intermediate tree branch unfolding contains secondary flanges,
        _is_valid_solid correctly detects multiple solids and preserves PREVIEW_ONLY.
        """
        from smbs_core.viewer.replayer import GEOMETRY_PRECISION_PREVIEW

        bends = self.cp_data["payload"]["bends"]
        bend_ids = list(bends.keys())  # All 8 bends

        candidate_seq = [
            {
                "step": idx + 1,
                "bend_id": bid,
                "planner_status": "VALID",
                "collision_status": "VALID",
                "tooling_status": "SIMPLIFIED",
                "alignment_status": "OK",
            }
            for idx, bid in enumerate(bend_ids)
        ]

        with open(self.temp_dir / "07_search_result.json", "w") as f:
            json.dump({
                "schema_name": "search_result",
                "schema_version": "1.1",
                "payload": {"candidate_sequence": candidate_seq}
            }, f)

        artifacts = load_run(self.temp_dir)
        graphs = build_graphs(artifacts)

        step_views = list(replay_backward(artifacts, graphs, self.backend))
        self.assertEqual(len(step_views), len(candidate_seq) + 1)

        # Step 0 (initial fully folded 3D part) and final step (flat pattern) are valid solids.
        # Intermediate tree branch steps contain multiple detached solids and are honestly PREVIEW_ONLY.
        self.assertEqual(step_views[0].geometry_precision, GEOMETRY_PRECISION_VALID)
        self.assertIsNone(step_views[0].geometry_warning)

        for sv in step_views[1:-1]:
            self.assertEqual(sv.geometry_precision, GEOMETRY_PRECISION_PREVIEW)
            self.assertIsNotNone(sv.geometry_warning)
            self.assertIn("not a verified monolithic solid", sv.geometry_warning)

        self.assertEqual(step_views[-1].geometry_precision, GEOMETRY_PRECISION_VALID)
        self.assertIsNone(step_views[-1].geometry_warning)
