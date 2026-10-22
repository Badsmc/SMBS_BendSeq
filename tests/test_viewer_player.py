"""
test_viewer_player.py - Tests for S.14 Player state-machine.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from smbs_core.geometry_backend.backends.mock_backend import MockBackend
from smbs_core.viewer.graph_builder import build_graphs
from smbs_core.viewer.player import (
    Frame,
    Player,
    PlayerError,
    VERIFICATION_UNVERIFIED,
)
from smbs_core.viewer.run_loader import RunArtifacts, load_run


def _make_artifacts_with_verification(
    verification_payload: Optional[Dict[str, Any]] = None,
    diagnostics: Optional[List[Any]] = None,
    top_level_missing_diags: bool = False
) -> Tuple[RunArtifacts, Path]:
    tmp_dir = Path(tempfile.mkdtemp(prefix="test_player_"))
    geom_path = tmp_dir / "geometry.brep"
    geom_path.write_text("DUMMY_BREP")

    c4 = {
        "schema_name": "canonical_part",
        "schema_version": "1.0",
        "run_id": "run_test_player",
        "part_id": "part_player",
        "units": "mm",
        "payload": {
            "thickness_mm": {"value": 2.0},
            "geometry_artifact": {"file_path": str(geom_path)},
            "panels": {
                "P1": {"normal": [0.0, 0.0, 1.0]},
                "P2": {"normal": [0.0, 1.0, 0.0]},
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
            },
        },
    }

    s7 = {
        "schema_name": "search_result",
        "schema_version": "1.0",
        "run_id": "run_test_player",
        "part_id": "part_player",
        "units": "mm",
        "payload": {
            "planning_status": "SUCCESS",
            "candidate_sequence": [
                {
                    "step": 1,
                    "bend_id": "B1",
                    "planner_status": "SUCCESS",
                    "collision_status": "CLEAR",
                    "tooling_status": "OK",
                    "alignment_status": "VALID",
                }
            ],
        },
    }

    v9 = None
    if verification_payload is not None or diagnostics is not None or top_level_missing_diags:
        v9 = {
            "schema_name": "verification_result",
            "schema_version": "1.0",
            "run_id": "run_test_player",
            "part_id": "part_player",
            "payload": verification_payload if verification_payload is not None else {
                "verification_status": "VERIFIED",
                "production_eligible": True,
                "step_verifications": [
                    {
                        "step": 1,
                        "bend_id": "B1",
                        "status": "VERIFIED",
                        "collision_verified": True,
                        "kinematics_verified": True
                    }
                ]
            }
        }
        if not top_level_missing_diags:
            v9["diagnostics"] = diagnostics if diagnostics is not None else []

    artifacts = RunArtifacts(
        run_dir=tmp_dir,
        canonical_part=c4,
        search_result=s7,
        verification_result=v9,
        geometry_shape_path=geom_path,
        flat_pattern_path=None,
    )
    return artifacts, tmp_dir


def _get_occ_backend():
    try:
        from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
        return PythonOCCBackend()
    except ImportError:
        return None


class TestViewerPlayer(unittest.TestCase):
    def setUp(self):
        self.backend = MockBackend()
        self.init_shape = {"type": "mock_shape", "id": "init_part"}

    def test_load_real_run_frames_count(self):
        occ = _get_occ_backend()
        if occ is None:
            self.skipTest("PythonOCCBackend not available")

        real_dir = Path("runs/run_20261012_034042_cf4c34")
        if not real_dir.is_dir():
            self.skipTest("real run run_20261012_034042_cf4c34 not found")

        artifacts = load_run(real_dir)
        graphs = build_graphs(artifacts)
        player = Player(artifacts, graphs, occ)
        cand_seq = artifacts.search_result["payload"]["candidate_sequence"]
        self.assertEqual(player.total, len(cand_seq) + 1)
        self.assertEqual(len(player.frames), len(cand_seq) + 1)

    def test_unverified_when_09_missing(self):
        artifacts, _ = _make_artifacts_with_verification(verification_payload=None)
        graphs = build_graphs(artifacts)
        player = Player(artifacts, graphs, self.backend, initial_shape=self.init_shape)
        f0 = player.frames[0]
        self.assertEqual(f0.verification_status, VERIFICATION_UNVERIFIED)
        self.assertIsNone(f0.production_eligible)
        self.assertEqual(f0.diagnostics, ())

    def test_verification_failed_from_09(self):
        occ = _get_occ_backend()
        if occ is None:
            self.skipTest("PythonOCCBackend not available")

        real_dir = Path("runs/run_20261017_203828_511545")
        if not real_dir.is_dir():
            self.skipTest("real run run_20261017_203828_511545 not found")

        artifacts = load_run(real_dir)
        graphs = build_graphs(artifacts)
        player = Player(artifacts, graphs, occ)
        f0 = player.frames[0]
        self.assertEqual(f0.verification_status, "VERIFICATION_FAILED")
        self.assertFalse(f0.production_eligible)

    def test_verified_production_eligible_true(self):
        occ = _get_occ_backend()
        if occ is None:
            self.skipTest("PythonOCCBackend not available")

        real_dir = Path("runs/run_20261012_034042_cf4c34")
        if not real_dir.is_dir():
            self.skipTest("real run run_20261012_034042_cf4c34 not found")

        artifacts = load_run(real_dir)
        graphs = build_graphs(artifacts)
        player = Player(artifacts, graphs, occ)
        f0 = player.frames[0]
        self.assertEqual(f0.verification_status, "VERIFIED")
        self.assertTrue(f0.production_eligible)

    def test_production_eligible_never_computed(self):
        artifacts, _ = _make_artifacts_with_verification(verification_payload=None)
        graphs = build_graphs(artifacts)
        player = Player(artifacts, graphs, self.backend, initial_shape=self.init_shape)
        for f in player.frames:
            self.assertIsNone(f.production_eligible)

    def test_diagnostics_from_top_level(self):
        diag_item = {"code": "TEST_DIAG", "message": "Simulated error"}
        artifacts, _ = _make_artifacts_with_verification(
            verification_payload={
                "verification_status": "VERIFICATION_FAILED",
                "production_eligible": False,
                "step_verifications": []
            },
            diagnostics=[diag_item]
        )
        graphs = build_graphs(artifacts)
        player = Player(artifacts, graphs, self.backend, initial_shape=self.init_shape)
        f0 = player.frames[0]
        self.assertEqual(f0.diagnostics, (diag_item,))

    def test_missing_diagnostics_in_09_raises(self):
        artifacts, _ = _make_artifacts_with_verification(
            verification_payload={
                "verification_status": "VERIFIED",
                "production_eligible": True,
                "step_verifications": []
            },
            top_level_missing_diags=True
        )
        graphs = build_graphs(artifacts)
        player = Player(artifacts, graphs, self.backend, initial_shape=self.init_shape)
        with self.assertRaises(PlayerError) as ctx:
            _ = player.frames
        self.assertIn("missing top-level 'diagnostics'", str(ctx.exception))

    def test_missing_verification_status_in_09_raises(self):
        artifacts, _ = _make_artifacts_with_verification(
            verification_payload={
                # missing "verification_status"
                "production_eligible": True,
                "step_verifications": []
            },
            diagnostics=[]
        )
        graphs = build_graphs(artifacts)
        player = Player(artifacts, graphs, self.backend, initial_shape=self.init_shape)
        with self.assertRaises(PlayerError) as ctx:
            _ = player.frames
        self.assertIn("missing required payload field", str(ctx.exception))

    def test_step_verification_matched_by_bend_id(self):
        step_entry = {
            "step": 1,
            "bend_id": "B1",
            "status": "VERIFIED",
            "collision_verified": True,
            "kinematics_verified": True
        }
        artifacts, _ = _make_artifacts_with_verification(
            verification_payload={
                "verification_status": "VERIFIED",
                "production_eligible": True,
                "step_verifications": [step_entry]
            },
            diagnostics=[]
        )
        graphs = build_graphs(artifacts)
        player = Player(artifacts, graphs, self.backend, initial_shape=self.init_shape)
        f1 = player.frames[1]
        self.assertEqual(f1.step_verification, step_entry)

    def test_step_verification_none_for_step0(self):
        artifacts, _ = _make_artifacts_with_verification(
            verification_payload={
                "verification_status": "VERIFIED",
                "production_eligible": True,
                "step_verifications": [{"step": 1, "bend_id": "B1"}]
            },
            diagnostics=[]
        )
        graphs = build_graphs(artifacts)
        player = Player(artifacts, graphs, self.backend, initial_shape=self.init_shape)
        f0 = player.frames[0]
        self.assertIsNone(f0.step_verification)

    def test_step_verification_missing_entry_is_none(self):
        artifacts, _ = _make_artifacts_with_verification(
            verification_payload={
                "verification_status": "VERIFIED",
                "production_eligible": True,
                "step_verifications": [{"step": 99, "bend_id": "OTHER_BEND"}]
            },
            diagnostics=[]
        )
        graphs = build_graphs(artifacts)
        player = Player(artifacts, graphs, self.backend, initial_shape=self.init_shape)
        f1 = player.frames[1]
        self.assertIsNone(f1.step_verification)

    def test_reset_next_prev_current(self):
        artifacts, _ = _make_artifacts_with_verification(verification_payload=None)
        graphs = build_graphs(artifacts)
        player = Player(artifacts, graphs, self.backend, initial_shape=self.init_shape)

        self.assertIsNone(player.current())
        r0 = player.reset()
        self.assertEqual(r0.frame_index, 0)
        self.assertEqual(player.current().frame_index, 0)

        n1 = player.next()
        self.assertIsNotNone(n1)
        self.assertEqual(n1.frame_index, 1)
        self.assertEqual(player.current().frame_index, 1)

        p0 = player.prev()
        self.assertIsNotNone(p0)
        self.assertEqual(p0.frame_index, 0)
        self.assertEqual(player.current().frame_index, 0)

    def test_next_at_end_returns_none(self):
        artifacts, _ = _make_artifacts_with_verification(verification_payload=None)
        graphs = build_graphs(artifacts)
        player = Player(artifacts, graphs, self.backend, initial_shape=self.init_shape)
        player.reset()
        player.next()  # to frame 1 (end)
        self.assertIsNone(player.next())
        self.assertEqual(player.current().frame_index, 1)

    def test_prev_at_start_returns_none(self):
        artifacts, _ = _make_artifacts_with_verification(verification_payload=None)
        graphs = build_graphs(artifacts)
        player = Player(artifacts, graphs, self.backend, initial_shape=self.init_shape)
        player.reset()  # at frame 0
        self.assertIsNone(player.prev())
        self.assertEqual(player.current().frame_index, 0)

    def test_switch_direction_forward_works(self):
        artifacts, _ = _make_artifacts_with_verification(verification_payload=None)
        graphs = build_graphs(artifacts)
        player = Player(artifacts, graphs, self.backend, initial_shape=self.init_shape)
        backward = list(player.frames)
        player.switch_direction("forward")
        self.assertEqual(player.direction, "forward")
        forward = list(player.frames)
        self.assertEqual(len(forward), len(backward))
        self.assertEqual(forward[0].step_view.bend_id, backward[-1].step_view.bend_id)

    def test_switch_direction_backward_noop(self):
        artifacts, _ = _make_artifacts_with_verification(verification_payload=None)
        graphs = build_graphs(artifacts)
        player = Player(artifacts, graphs, self.backend, initial_shape=self.init_shape)
        player.switch_direction("backward")
        self.assertEqual(player.direction, "backward")

    def test_lazy_replay(self):
        class SpyCountingBackend(MockBackend):
            def __init__(self):
                super().__init__()
                self.calls_count = 0

            def rotate_subtree(self, *args, **kwargs):
                self.calls_count += 1
                return {"type": "mock_shape", "call": self.calls_count}

        spy = SpyCountingBackend()
        artifacts, _ = _make_artifacts_with_verification(verification_payload=None)
        graphs = build_graphs(artifacts)
        player = Player(artifacts, graphs, spy, initial_shape=self.init_shape)
        self.assertEqual(spy.calls_count, 0)

        # First call triggers replay
        _ = player.frames
        self.assertEqual(spy.calls_count, 1)

        # Second call uses cache
        _ = player.frames
        self.assertEqual(spy.calls_count, 1)


if __name__ == "__main__":
    unittest.main()
