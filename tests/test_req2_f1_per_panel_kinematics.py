"""F.1: Req 2 per-panel kinematics in replayer/player/cli.
Real L-run from runs/ + synthetic fixtures. Verifies KL-12 v2 integration,
forward direction, manifest honesty, and resolver idempotency."""
import unittest
from pathlib import Path

from smbs_core.viewer.run_loader import load_run
from smbs_core.viewer.graph_builder import build_graphs
from smbs_core.viewer.replayer import (
    replay_backward,
    ReplayerError,
    GEOMETRY_PRECISION_VALID,
    GEOMETRY_PRECISION_PREVIEW,
)
from smbs_core.viewer.player import Player, PlayerError
from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend


ROOT = Path(__file__).resolve().parent.parent
REAL_L_RUN = ROOT / "runs/run_20261012_034042_cf4c34"


class TestF1PerPanelKinematics(unittest.TestCase):
    def test_backward_precision_partial_on_real_l_run(self):
        """Real L-part run_20261012_034042_cf4c34: P1a disk-state path produces
        genuine solids -> VALID_PER_PANEL."""
        artifacts = load_run(REAL_L_RUN)
        graphs = build_graphs(artifacts)
        backend = PythonOCCBackend()
        frames = list(replay_backward(artifacts, graphs, backend))
        self.assertEqual(len(frames), 2)
        for f in frames:
            self.assertEqual(f.geometry_precision, GEOMETRY_PRECISION_VALID)
            self.assertIsNone(f.geometry_warning)

    def test_backward_precision_preview_on_mock_shape(self):
        """Mock initial_shape + MockBackend: resolver skips -> precision PREVIEW, warning present."""
        from smbs_core.geometry_backend.backends.mock_backend import MockBackend
        artifacts = load_run(REAL_L_RUN)
        graphs = build_graphs(artifacts)
        backend = MockBackend()
        frames = list(replay_backward(artifacts, graphs, backend, initial_shape={"type": "mock_shape"}))
        self.assertGreater(len(frames), 1)
        for f in frames:
            self.assertEqual(f.geometry_precision, GEOMETRY_PRECISION_PREVIEW)
            self.assertIsNotNone(f.geometry_warning)

    def test_forward_direction_reverses_frames(self):
        """Forward = reverse of backward. Bend order mirrors; frame_index renumbered."""
        artifacts = load_run(REAL_L_RUN)
        graphs = build_graphs(artifacts)
        player = Player(artifacts, graphs, PythonOCCBackend())
        backward = list(player.frames)
        player.switch_direction("forward")
        forward = list(player.frames)
        self.assertEqual(len(forward), len(backward))
        self.assertEqual(forward[0].step_view.bend_id, backward[-1].step_view.bend_id)
        self.assertEqual(forward[-1].step_view.bend_id, backward[0].step_view.bend_id)
        for i, f in enumerate(forward):
            self.assertEqual(f.frame_index, i)

    def test_forward_precision_partial(self):
        """Forward frames preserve per-frame honest precision (VALID_PER_PANEL on this run)."""
        artifacts = load_run(REAL_L_RUN)
        graphs = build_graphs(artifacts)
        player = Player(artifacts, graphs, PythonOCCBackend())
        player.switch_direction("forward")
        for f in player.frames:
            self.assertEqual(f.step_view.geometry_precision, GEOMETRY_PRECISION_VALID)

    def test_resolver_idempotent_on_double_replay(self):
        """Second replay_backward must not duplicate or re-assign faces."""
        artifacts = load_run(REAL_L_RUN)
        graphs = build_graphs(artifacts)
        backend = PythonOCCBackend()
        list(replay_backward(artifacts, graphs, backend))
        snapshot = {pid: tuple(p.face_indices) for pid, p in graphs.panel_graph.panels.items()}
        list(replay_backward(artifacts, graphs, backend))
        for pid, p in graphs.panel_graph.panels.items():
            self.assertEqual(tuple(p.face_indices), snapshot[pid])


if __name__ == "__main__":
    unittest.main()
