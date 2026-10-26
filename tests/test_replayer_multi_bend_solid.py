"""Unit tests for P1b: replayer constructive stepwise prefix-fold path for multi-bend (N=2) solids (KL-12-v3 step 2)."""
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from OCC.Core.BRepTools import breptools
from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop

from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
from smbs_core.viewer.graph_builder import build_graphs
from smbs_core.viewer.replayer import (
    replay_backward,
    _is_valid_solid,
    GEOMETRY_PRECISION_VALID,
    GEOMETRY_PRECISION_PREVIEW,
)
from smbs_core.viewer.run_loader import load_run


def _get_shape_volume(shape) -> float:
    props = GProp_GProps()
    brepgprop.VolumeProperties(shape, props)
    return props.Mass()


def _get_face_count(shape) -> int:
    from OCC.Core.TopAbs import TopAbs_FACE
    from OCC.Core.TopExp import TopExp_Explorer
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    count = 0
    while exp.More():
        count += 1
        exp.Next()
    return count


class TestReplayerMultiBendSolid(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="smbs_test_p1b_"))
        self.backend = PythonOCCBackend()

        # Locate U-profile fixture run
        root = Path(__file__).resolve().parent.parent
        self.u_run_dir = root / "runs/run_20261018_190131_085a13"
        if not self.u_run_dir.exists():
            self.skipTest(f"Fixture run {self.u_run_dir} not found on disk")

        # Copy canonical_part, geometry.brep, flat_pattern.brep
        cp_src = self.u_run_dir / "04_canonical_part.json"
        geom_src = self.u_run_dir / "geometry.brep"
        flat_src = self.u_run_dir / "flat_pattern.brep"

        shutil.copy(geom_src, self.temp_dir / "geometry.brep")
        shutil.copy(flat_src, self.temp_dir / "flat_pattern.brep")

        with open(cp_src, "r") as f:
            cp_data = json.load(f)

        # Update geometry_artifact path to temp_dir
        cp_data["payload"]["geometry_artifact"]["file_path"] = str(self.temp_dir / "geometry.brep")
        with open(self.temp_dir / "04_canonical_part.json", "w") as f:
            json.dump(cp_data, f)

        # Create valid 07_search_result.json with N=2 candidate sequence (B1, B2)
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

    def test_replayer_n2_u_profile_constructive_solids(self):
        """N=2 linear chain (U-profile) produces 3 valid solids with strictly decreasing volume in backward replay."""
        artifacts = load_run(self.temp_dir)
        graphs = build_graphs(artifacts)
        views = list(replay_backward(artifacts, graphs, self.backend))

        self.assertEqual(len(views), 3)

        # All 3 steps must be valid B-Rep solids
        for v in views:
            self.assertTrue(_is_valid_solid(v.state.shape), f"Step {v.step_index} is not a valid solid")
            self.assertEqual(v.geometry_precision, GEOMETRY_PRECISION_VALID)
            self.assertIsNone(v.geometry_warning)

        # Backward sequence: Step 0 (folded) -> Step 1 (intermediate) -> Step 2 (flat)
        self.assertEqual(views[0].step_index, 0)
        self.assertIsNone(views[0].bend_id)
        self.assertEqual(views[1].step_index, 1)
        self.assertEqual(views[1].bend_id, "BEND_01_6ff30710")
        self.assertEqual(views[2].step_index, 2)
        self.assertEqual(views[2].bend_id, "BEND_02_d7ffaaa9")

        # Face count progression: 14 -> 10 -> 14
        faces = [_get_face_count(v.state.shape) for v in views]
        self.assertEqual(faces, [14, 10, 14])

        # Volume strictly decreases in backward replay (folded -> flat)
        vols = [_get_shape_volume(v.state.shape) for v in views]
        self.assertGreater(vols[0], vols[1])
        self.assertGreater(vols[1], vols[2])
        self.assertAlmostEqual(vols[0], 6824.67, delta=1.0)
        self.assertAlmostEqual(vols[1], 6803.46, delta=1.0)
        self.assertAlmostEqual(vols[2], 6782.26, delta=1.0)

    def test_replayer_n2_permutation_symmetry(self):
        """Permuting candidate sequence order produces bit-equivalent intermediate volume."""
        # Swap B1 and B2 in search result
        permuted_data = copy.deepcopy(self.search_result_data)
        permuted_data["payload"]["candidate_sequence"][0]["bend_id"] = "BEND_02_d7ffaaa9"
        permuted_data["payload"]["candidate_sequence"][1]["bend_id"] = "BEND_01_6ff30710"
        with open(self.temp_dir / "07_search_result.json", "w") as f:
            json.dump(permuted_data, f)

        artifacts = load_run(self.temp_dir)
        graphs = build_graphs(artifacts)
        views = list(replay_backward(artifacts, graphs, self.backend))

        self.assertEqual(len(views), 3)
        for v in views:
            self.assertTrue(_is_valid_solid(v.state.shape))
            self.assertEqual(v.geometry_precision, GEOMETRY_PRECISION_VALID)

        # Step 1 should unfold BEND_02 first, producing the same physical intermediate volume
        vols = [_get_shape_volume(v.state.shape) for v in views]
        self.assertGreater(vols[0], vols[1])
        self.assertGreater(vols[1], vols[2])
        self.assertAlmostEqual(vols[1], 6803.46, delta=1.0)

    def test_replayer_n2_precision_and_warnings(self):
        """All steps in successful constructive N=2 replay have VALID_PER_PANEL and no warnings."""
        artifacts = load_run(self.temp_dir)
        graphs = build_graphs(artifacts)
        views = list(replay_backward(artifacts, graphs, self.backend))

        for idx, v in enumerate(views):
            self.assertEqual(v.geometry_precision, GEOMETRY_PRECISION_VALID, f"Step {idx} precision mismatch")
            self.assertIsNone(v.geometry_warning, f"Step {idx} warning should be None")

    def test_replayer_n2_fallback_on_invalid_flat(self):
        """When flat_pattern.brep is removed, falls back all-or-nothing to rotate_subtree without crashing."""
        (self.temp_dir / "flat_pattern.brep").unlink()

        artifacts = load_run(self.temp_dir)
        graphs = build_graphs(artifacts)
        views = list(replay_backward(artifacts, graphs, self.backend))

        self.assertEqual(len(views), 3)
        # Fallback to rotate_subtree produces torn shells, not valid solids at intermediate steps
        self.assertTrue(_is_valid_solid(views[0].state.shape))
        self.assertFalse(_is_valid_solid(views[1].state.shape))
        self.assertFalse(_is_valid_solid(views[2].state.shape))

    def test_replayer_n2_fallback_on_constructive_exception(self):
        """When constructive folding raises an exception, falls back all-or-nothing to rotate_subtree without crashing."""
        from unittest.mock import patch
        import smbs_core.Geometry.sheetmetal_folder as smf

        with patch.object(smf.SheetMetalFolder, "fold_from_canonical_part", side_effect=RuntimeError("Simulated OCC crash")):
            artifacts = load_run(self.temp_dir)
            graphs = build_graphs(artifacts)
            views = list(replay_backward(artifacts, graphs, self.backend))

            self.assertEqual(len(views), 3)
            # Replay completes cleanly via kinematic rotate shapes (not valid intermediate solids)
            self.assertTrue(_is_valid_solid(views[0].state.shape))
            self.assertFalse(_is_valid_solid(views[1].state.shape))
            self.assertFalse(_is_valid_solid(views[2].state.shape))

    def test_replayer_n2_fallback_with_unassigned_faces_yields_preview(self):
        """When fallback occurs on run with unassigned faces, precision is honestly marked PREVIEW_ONLY."""
        run_u_old = Path(__file__).resolve().parent.parent / "runs/run_20261012_034044_3ccc14"
        if not run_u_old.exists():
            self.skipTest("Older fixture_u run not found on disk")
        artifacts = load_run(run_u_old)
        graphs = build_graphs(artifacts)
        views = list(replay_backward(artifacts, graphs, self.backend))
        self.assertEqual(len(views), 3)
        for v in views:
            self.assertEqual(v.geometry_precision, GEOMETRY_PRECISION_PREVIEW)
            self.assertIsNotNone(v.geometry_warning)

    def test_replayer_n2_headless_no_folder_import(self):
        """Importing smbs_core.viewer.replayer must NOT import SheetMetalFolder at top-level."""
        cmd = [
            sys.executable,
            "-c",
            "import sys; "
            "import smbs_core.viewer.replayer as rep; "
            "assert 'SheetMetalFolder' not in dir(rep); "
            "assert 'smbs_core.Geometry.sheetmetal_folder' not in sys.modules; "
            "print('HEADLESS_OK')",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent))
        self.assertEqual(res.returncode, 0, f"Headless check failed: stdout={res.stdout}, stderr={res.stderr}")
        self.assertIn("HEADLESS_OK", res.stdout)


if __name__ == "__main__":
    unittest.main()
