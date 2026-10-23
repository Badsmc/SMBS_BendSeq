"""
test_viewer_replayer_preview.py - Tests for S.13a replayer with honest PREVIEW_ONLY marking.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from smbs_core.geometry_backend.backends.mock_backend import MockBackend
from smbs_core.geometry_backend.base import InvalidRotationError
from smbs_core.viewer.graph_builder import build_graphs
from smbs_core.viewer.replayer import (
    GEOMETRY_PRECISION_PREVIEW,
    GEOMETRY_WARNING_PREVIEW,
    ReplayerError,
    StepView,
    load_brep,
    replay_backward,
)
from smbs_core.viewer.run_loader import RunArtifacts, load_run


def _make_two_bend_artifacts(
    candidate_sequence: Optional[List[Dict[str, Any]]] = None,
    geom_file_exists: bool = True
) -> Tuple[RunArtifacts, Path]:
    tmp_dir = Path(tempfile.mkdtemp(prefix="test_replayer_"))
    geom_path = tmp_dir / "geometry.brep"
    if geom_file_exists:
        geom_path.write_text("DUMMY_BREP_CONTENT")

    if candidate_sequence is None:
        candidate_sequence = [
            {
                "step": 1,
                "bend_id": "B1",
                "planner_status": "SUCCESS",
                "collision_status": "CLEAR",
                "tooling_status": "OK",
                "alignment_status": "VALID",
            },
            {
                "step": 2,
                "bend_id": "B2",
                "planner_status": "SUCCESS",
                "collision_status": "CLEAR",
                "tooling_status": "OK",
                "alignment_status": "VALID",
            },
        ]

    c4 = {
        "schema_name": "canonical_part",
        "schema_version": "1.0",
        "run_id": "run_test_replayer",
        "part_id": "part_replayer",
        "units": "mm",
        "payload": {
            "thickness_mm": {"value": 2.0},
            "geometry_artifact": {"file_path": str(geom_path)},
            "panels": {
                "P1": {"normal": [0.0, 0.0, 1.0]},
                "P2": {"normal": [0.0, 1.0, 0.0]},
                "P3": {"normal": [1.0, 0.0, 0.0]},
            },
            "bends": {
                "B1": {
                    "parent_panel_id": "P1",
                    "child_panel_id": "P2",
                    "axis_point": [0.0, 0.0, 0.0],
                    "axis_direction": [1.0, 0.0, 0.0],
                    "angle_deg": {"value": 90.0},
                    "length_mm": {"value": 50.0},
                    "radius_mm": {"value": 2.0},
                    "direction": {"value": "UP"},
                },
                "B2": {
                    "parent_panel_id": "P2",
                    "child_panel_id": "P3",
                    "axis_point": [0.0, 50.0, 0.0],
                    "axis_direction": [0.0, 1.0, 0.0],
                    "angle_deg": {"value": -45.0},
                    "length_mm": {"value": 30.0},
                    "radius_mm": {"value": 1.5},
                    "direction": {"value": "DOWN"},
                },
            },
        },
    }

    s7 = {
        "schema_name": "search_result",
        "schema_version": "1.0",
        "run_id": "run_test_replayer",
        "part_id": "part_replayer",
        "units": "mm",
        "payload": {
            "planning_status": "SUCCESS",
            "candidate_sequence": candidate_sequence,
        },
    }

    artifacts = RunArtifacts(
        run_dir=tmp_dir,
        canonical_part=c4,
        search_result=s7,
        verification_result=None,
        geometry_shape_path=geom_path,
        flat_pattern_path=None,
    )
    return artifacts, tmp_dir


class SpyBackend(MockBackend):
    def __init__(self):
        super().__init__()
        self.calls: List[Dict[str, Any]] = []
        self.return_none = False
        self.return_identity = False
        self.raise_exc: Optional[Exception] = None

    def rotate_subtree(
        self,
        shape: Any,
        axis_origin: Tuple[float, float, float],
        axis_direction: Tuple[float, float, float],
        signed_angle_deg: float,
        moving_panels: Optional[Set[str]] = None,
        panel_graph: Optional[Any] = None
    ) -> Any:
        self.calls.append({
            "shape": shape,
            "axis_origin": axis_origin,
            "axis_direction": axis_direction,
            "signed_angle_deg": signed_angle_deg,
            "moving_panels": set(moving_panels) if moving_panels else set(),
        })
        if self.raise_exc:
            raise self.raise_exc
        if self.return_none:
            return None
        if self.return_identity:
            return shape
        # Return a modified new dictionary (distinct identity)
        res = dict(shape) if isinstance(shape, dict) else {"type": "mock_shape"}
        res["step"] = len(self.calls)
        res["last_angle"] = signed_angle_deg
        return res


class TestViewerReplayerPreview(unittest.TestCase):
    def setUp(self):
        self.artifacts, self.tmp_dir = _make_two_bend_artifacts()
        self.graphs = build_graphs(self.artifacts)
        self.backend = SpyBackend()
        self.init_shape = {"type": "mock_shape", "id": "init_part"}

    def test_count_is_seq_len_plus_one(self):
        views = list(replay_backward(self.artifacts, self.graphs, self.backend, initial_shape=self.init_shape))
        self.assertEqual(len(views), 3)

    def test_step0_is_initial(self):
        views = list(replay_backward(self.artifacts, self.graphs, self.backend, initial_shape=self.init_shape))
        v0 = views[0]
        self.assertEqual(v0.step_index, 0)
        self.assertIsNone(v0.bend_id)
        self.assertEqual(v0.state.remaining_bends, {"B1", "B2"})
        self.assertEqual(v0.state.folded_bends, {"B1", "B2"})

    def test_step0_status_fields_none(self):
        views = list(replay_backward(self.artifacts, self.graphs, self.backend, initial_shape=self.init_shape))
        v0 = views[0]
        self.assertIsNone(v0.ui_direction)
        self.assertIsNone(v0.planner_status)
        self.assertIsNone(v0.collision_status)
        self.assertIsNone(v0.tooling_status)
        self.assertIsNone(v0.alignment_status)

    def test_monotonic_remaining(self):
        views = list(replay_backward(self.artifacts, self.graphs, self.backend, initial_shape=self.init_shape))
        lens = [len(v.state.remaining_bends) for v in views]
        self.assertEqual(lens, [2, 1, 0])

    def test_monotonic_folded(self):
        views = list(replay_backward(self.artifacts, self.graphs, self.backend, initial_shape=self.init_shape))
        lens = [len(v.state.folded_bends) for v in views]
        self.assertEqual(lens, [2, 1, 0])

    def test_final_both_empty(self):
        views = list(replay_backward(self.artifacts, self.graphs, self.backend, initial_shape=self.init_shape))
        v_final = views[-1]
        self.assertEqual(v_final.state.remaining_bends, set())
        self.assertEqual(v_final.state.folded_bends, set())

    def test_step_view_fields_match_sequence(self):
        views = list(replay_backward(self.artifacts, self.graphs, self.backend, initial_shape=self.init_shape))
        v1 = views[1]
        self.assertEqual(v1.step_index, 1)
        self.assertEqual(v1.bend_id, "B1")
        self.assertEqual(v1.ui_direction, "UP")
        self.assertEqual(v1.planner_status, "SUCCESS")
        self.assertEqual(v1.collision_status, "CLEAR")
        self.assertEqual(v1.tooling_status, "OK")
        self.assertEqual(v1.alignment_status, "VALID")

        v2 = views[2]
        self.assertEqual(v2.step_index, 2)
        self.assertEqual(v2.bend_id, "B2")
        self.assertEqual(v2.ui_direction, "DOWN")

    def test_empty_sequence_yields_only_step0(self):
        empty_artifacts, _ = _make_two_bend_artifacts(candidate_sequence=[])
        empty_graphs = build_graphs(empty_artifacts)
        views = list(replay_backward(empty_artifacts, empty_graphs, self.backend, initial_shape=self.init_shape))
        self.assertEqual(len(views), 1)
        self.assertEqual(views[0].step_index, 0)
        self.assertIsNone(views[0].bend_id)

    def test_geometry_precision_is_preview_only(self):
        views = list(replay_backward(self.artifacts, self.graphs, self.backend, initial_shape=self.init_shape))
        for v in views:
            self.assertEqual(v.geometry_precision, GEOMETRY_PRECISION_PREVIEW)

    def test_geometry_warning_mentions_kl12(self):
        views = list(replay_backward(self.artifacts, self.graphs, self.backend, initial_shape=self.init_shape))
        for v in views:
            self.assertIn("KL-12", v.geometry_warning)
            self.assertIn("preview", v.geometry_warning.lower())

    def test_bend_not_in_graph_raises(self):
        artifacts, _ = _make_two_bend_artifacts(candidate_sequence=[
            {
                "step": 1,
                "bend_id": "UNKNOWN_BEND",
                "planner_status": "SUCCESS",
                "collision_status": "CLEAR",
                "tooling_status": "OK",
                "alignment_status": "VALID",
            }
        ])
        with self.assertRaises(ReplayerError):
            list(replay_backward(artifacts, self.graphs, self.backend, initial_shape=self.init_shape))

    def test_rotate_returns_none_raises(self):
        self.backend.return_none = True
        with self.assertRaises(ReplayerError) as ctx:
            list(replay_backward(self.artifacts, self.graphs, self.backend, initial_shape=self.init_shape))
        self.assertIn("rotate_subtree returned None", str(ctx.exception))

    def test_rotate_returns_unchanged_shape_raises(self):
        self.backend.return_identity = True
        with self.assertRaises(ReplayerError) as ctx:
            list(replay_backward(self.artifacts, self.graphs, self.backend, initial_shape=self.init_shape))
        self.assertIn("unchanged shape identity", str(ctx.exception))

    def test_rotate_failure_wrapped_with_cause(self):
        self.backend.raise_exc = InvalidRotationError("Simulated backend rotation failure")
        with self.assertRaises(ReplayerError) as ctx:
            list(replay_backward(self.artifacts, self.graphs, self.backend, initial_shape=self.init_shape))
        self.assertIsInstance(ctx.exception.__cause__, InvalidRotationError)

    def test_missing_planner_status_raises(self):
        corrupt_seq = [
            {
                "step": 1,
                "bend_id": "B1",
                # missing "planner_status"
                "collision_status": "CLEAR",
                "tooling_status": "OK",
                "alignment_status": "VALID",
            }
        ]
        artifacts, _ = _make_two_bend_artifacts(candidate_sequence=corrupt_seq)
        with self.assertRaises(ReplayerError) as ctx:
            list(replay_backward(artifacts, self.graphs, self.backend, initial_shape=self.init_shape))
        self.assertIn("missing required status field 'planner_status'", str(ctx.exception))

    def test_apply_unfold_uses_moving_panels_from_panel_graph(self):
        views = list(replay_backward(self.artifacts, self.graphs, self.backend, initial_shape=self.init_shape))
        self.assertEqual(len(self.backend.calls), 2)
        # B1 connects P1 -> P2 (moving subtree for B1 includes P2, P3)
        self.assertEqual(self.backend.calls[0]["moving_panels"], {"P2", "P3"})
        # Unfold angle for B1: -90.0
        self.assertEqual(self.backend.calls[0]["signed_angle_deg"], -90.0)

        # B2 connects P2 -> P3 (moving subtree for B2 includes P3)
        self.assertEqual(self.backend.calls[1]["moving_panels"], {"P3"})
        # Unfold angle for B2: -(-45.0) = +45.0
        self.assertEqual(self.backend.calls[1]["signed_angle_deg"], 45.0)

    def test_bend_axes_drifted_on_step2(self):
        views = list(replay_backward(self.artifacts, self.graphs, self.backend, initial_shape=self.init_shape))
        # Initial axis of B2 is at (0, 50, 0), parent panel is P2.
        # Step 1 unfolds B1 around X axis with angle -90.
        # P2 moves, so B2 axis should be transformed by FoldState.apply_unfold.
        initial_b2_orig, initial_b2_dir = views[0].state.bend_axes["B2"]
        after_step1_b2_orig, after_step1_b2_dir = views[1].state.bend_axes["B2"]
        self.assertNotEqual(initial_b2_orig, after_step1_b2_orig)

    def test_load_brep_missing_file_raises(self):
        missing_path = Path("/nonexistent/path/to/missing.brep")
        with self.assertRaises(ReplayerError) as ctx:
            load_brep(missing_path)
        self.assertIn("BREP file not found", str(ctx.exception))

    def test_load_brep_valid_file(self):
        try:
            from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
            from OCC.Core.BRepTools import breptools
        except ImportError:
            self.skipTest("OCC not available in current test environment")

        box_shape = BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape()
        tmp_brep = self.tmp_dir / "valid_box.brep"
        breptools.Write(box_shape, str(tmp_brep))

        loaded = load_brep(tmp_brep)
        self.assertIsNotNone(loaded)
        self.assertFalse(loaded.IsNull())

    def test_replay_backward_real_run_structure(self):
        # Locate first valid real run with 04 and 07
        runs_dir = Path("runs")
        if not runs_dir.is_dir():
            self.skipTest("runs directory not found")

        valid_run_dir: Optional[Path] = None
        for p in sorted(runs_dir.iterdir()):
            if p.is_dir() and (p / "04_canonical_part.json").is_file() and (p / "07_search_result.json").is_file():
                valid_run_dir = p
                break

        if not valid_run_dir:
            self.skipTest("no valid real run directory with 04 and 07 found")

        try:
            from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
            occ_backend = PythonOCCBackend()
        except ImportError:
            self.skipTest("PythonOCCBackend not available in current test environment")

        real_artifacts = load_run(valid_run_dir)
        real_graphs = build_graphs(real_artifacts)

        cand_seq = real_artifacts.search_result["payload"].get("candidate_sequence", [])
        if not cand_seq:
            self.skipTest(f"Real run {valid_run_dir} has empty candidate sequence")

        views = list(replay_backward(real_artifacts, real_graphs, occ_backend))
        self.assertEqual(len(views), len(cand_seq) + 1)
        for v in views:
            self.assertEqual(v.geometry_precision, GEOMETRY_PRECISION_PREVIEW)
            self.assertIn("KL-12", v.geometry_warning)
        self.assertEqual(views[-1].state.remaining_bends, set())


if __name__ == "__main__":
    unittest.main()
