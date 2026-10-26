"""Unit tests for P1a: replayer disk-state path for single-bend solids (KL-12-v3 step 1)."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from OCC.Core.BRep import BRep_Builder
from OCC.Core.BRepTools import breptools
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
from OCC.Core.gp import gp_Pnt

from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
from smbs_core.viewer.graph_builder import build_graphs
from smbs_core.viewer.replayer import (
    replay_backward,
    _is_valid_solid,
    GEOMETRY_PRECISION_VALID,
    GEOMETRY_PRECISION_PREVIEW,
)
from smbs_core.viewer.run_loader import load_run, RunArtifacts
from tests.test_technological_unfold_single_bend import create_l_bracket_shape


class TestReplayerSingleBendSolid(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="smbs_test_p1a_"))
        self.backend = PythonOCCBackend()

        # Build solid geometry.brep (L-bracket)
        l_shape = create_l_bracket_shape(angle_deg=90.0, radius=1.0, thickness=1.5, length=50.0, l1=95.5, l2=47.5)
        self.geom_path = self.temp_dir / "geometry.brep"
        breptools.Write(l_shape, str(self.geom_path))

        # Build solid flat_pattern.brep (rectangular slab)
        flat_box = BRepPrimAPI_MakeBox(145.6, 50.0, 1.5).Shape()
        self.flat_path = self.temp_dir / "flat_pattern.brep"
        breptools.Write(flat_box, str(self.flat_path))

        # Write minimal 04_canonical_part.json
        self.canonical_part_data = {
            "schema_name": "canonical_part",
            "schema_version": "1.1",
            "part_id": "test_l",
            "payload": {
                "thickness_mm": {"value": 1.5},
                "geometry_artifact": {"file_path": str(self.geom_path)},
                "panels": {
                    "P1": {"centroid": [47.75, 25.0, 0.75], "normal": [0.0, 0.0, 1.0], "adjacent_bend_ids": ["B1"]},
                    "P2": {"centroid": [97.25, 25.0, 26.25], "normal": [1.0, 0.0, 0.0], "adjacent_bend_ids": ["B1"]},
                },
                "bends": {
                    "B1": {
                        "bend_id": "B1",
                        "axis_point": [95.5, 0.0, 2.5],
                        "axis_direction": [0.0, 1.0, 0.0],
                        "angle_deg": {"value": -90.0},
                        "radius_mm": {"value": 1.0},
                        "length_mm": {"value": 50.0},
                        "parent_panel_id": "P1",
                        "child_panel_id": "P2",
                        "direction": {"value": "DOWN"},
                    }
                },
            },
        }
        with open(self.temp_dir / "04_canonical_part.json", "w") as f:
            json.dump(self.canonical_part_data, f)

        # Write minimal 07_search_result.json (N=1)
        self.search_result_data = {
            "schema_name": "search_result",
            "schema_version": "1.1",
            "payload": {
                "candidate_sequence": [
                    {
                        "step": 1,
                        "bend_id": "B1",
                        "planner_status": "VALID",
                        "collision_status": "VALID",
                        "tooling_status": "SIMPLIFIED",
                        "alignment_status": "OK",
                    }
                ]
            },
        }
        with open(self.temp_dir / "07_search_result.json", "w") as f:
            json.dump(self.search_result_data, f)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_n1_disk_solid_uses_flat_pattern(self):
        """N=1 with valid flat_pattern.brep -> 2 StepViews, VALID_PER_PANEL, both solids."""
        artifacts = load_run(self.temp_dir)
        graphs = build_graphs(artifacts)
        views = list(replay_backward(artifacts, graphs, self.backend))

        self.assertEqual(len(views), 2)
        for v in views:
            self.assertEqual(v.geometry_precision, GEOMETRY_PRECISION_VALID)
            self.assertIsNone(v.geometry_warning)
            self.assertTrue(_is_valid_solid(v.state.shape))

        self.assertEqual(views[0].step_index, 0)
        self.assertIsNone(views[0].bend_id)
        self.assertEqual(views[1].step_index, 1)
        self.assertEqual(views[1].bend_id, "B1")

    def test_n1_fallback_when_flat_missing(self):
        """N=1 with flat_pattern_path=None -> falls to rotate, PREVIEW_ONLY."""
        self.flat_path.unlink()
        artifacts = load_run(self.temp_dir)
        self.assertIsNone(artifacts.flat_pattern_path)

        graphs = build_graphs(artifacts)
        views = list(replay_backward(artifacts, graphs, self.backend))

        self.assertEqual(len(views), 2)
        for v in views:
            self.assertEqual(v.geometry_precision, GEOMETRY_PRECISION_PREVIEW)
            self.assertIsNotNone(v.geometry_warning)

    def test_n1_fallback_when_flat_invalid(self):
        """N=1 with flat_pattern as non-solid (open face) -> falls to rotate, PREVIEW_ONLY."""
        poly = BRepBuilderAPI_MakePolygon()
        poly.Add(gp_Pnt(0, 0, 0))
        poly.Add(gp_Pnt(100, 0, 0))
        poly.Add(gp_Pnt(100, 50, 0))
        poly.Close()
        open_face = BRepBuilderAPI_MakeFace(poly.Wire()).Shape()
        self.assertFalse(_is_valid_solid(open_face))
        breptools.Write(open_face, str(self.flat_path))

        artifacts = load_run(self.temp_dir)
        graphs = build_graphs(artifacts)
        views = list(replay_backward(artifacts, graphs, self.backend))

        self.assertEqual(len(views), 2)
        for v in views:
            self.assertEqual(v.geometry_precision, GEOMETRY_PRECISION_PREVIEW)
            self.assertIsNotNone(v.geometry_warning)

    def test_n2_unchanged(self):
        """N=2 (fixture_u) -> rotate path, 3 StepViews, unchanged behavior."""
        root = Path(__file__).resolve().parent.parent
        run_u = root / "runs/run_20261012_034044_3ccc14"
        artifacts = load_run(run_u)
        graphs = build_graphs(artifacts)
        views = list(replay_backward(artifacts, graphs, self.backend))

        self.assertEqual(len(views), 3)
        # N=2 falls through to rotate_subtree path, yielding PREVIEW_ONLY
        for v in views:
            self.assertEqual(v.geometry_precision, GEOMETRY_PRECISION_PREVIEW)
            self.assertIsNotNone(v.geometry_warning)


if __name__ == "__main__":
    unittest.main()
