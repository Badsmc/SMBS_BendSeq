"""
test_p1c_full_fixture_sweep.py - P1c Verification: Full fixture sweep + bidirectional Player + FreeCAD UI integration.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
from smbs_core.viewer.graph_builder import build_graphs
from smbs_core.viewer.player import Player
from smbs_core.viewer.replayer import (
    GEOMETRY_PRECISION_PREVIEW,
    GEOMETRY_PRECISION_VALID,
    _is_valid_solid,
)
from smbs_core.viewer.run_loader import load_run
from smbs_core.viewer.fc_animation import (
    format_step_header,
    format_precision_badge,
    COLOR_GREEN,
    COLOR_YELLOW,
)


class TestP1cFullFixtureSweep(unittest.TestCase):
    """Systematic sweep of fixtures for forward/backward playback & precision."""

    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[1]
        self.backend = PythonOCCBackend()
        # Canonical fixture run directories
        self.real_l_run = self.repo_root / "runs" / "run_20261012_034042_cf4c34"

    def test_l_fixture_bidirectional_valid_solids(self):
        """L-profile (N=1) yields genuine VALID_PER_PANEL solids in both backward and forward directions."""
        if not self.real_l_run.is_dir():
            self.skipTest(f"Run dir not found: {self.real_l_run}")

        artifacts = load_run(self.real_l_run)
        graphs = build_graphs(artifacts)
        player = Player(artifacts, graphs, self.backend)

        # 1. Backward playback (unfold)
        backward_frames = list(player.frames)
        self.assertEqual(len(backward_frames), 2)  # Step 0 (folded) + Step 1 (flat)
        self.assertEqual(player.direction, "backward")

        for f in backward_frames:
            self.assertEqual(f.step_view.geometry_precision, GEOMETRY_PRECISION_VALID)
            shape = f.step_view.state.shape
            self.assertTrue(_is_valid_solid(shape), f"Frame {f.frame_index} must be valid solid")
            badge_text, badge_color = format_precision_badge(f)
            self.assertEqual(badge_text, "VALID_PER_PANEL")
            self.assertEqual(badge_color, COLOR_GREEN)

        header_b0 = format_step_header(backward_frames[0], direction="backward")
        self.assertIn("initial (fully folded)", header_b0)

        # 2. Forward playback (fold)
        player.switch_direction("forward")
        self.assertEqual(player.direction, "forward")
        forward_frames = list(player.frames)
        self.assertEqual(len(forward_frames), len(backward_frames))

        for f in forward_frames:
            self.assertEqual(f.step_view.geometry_precision, GEOMETRY_PRECISION_VALID)
            shape = f.step_view.state.shape
            self.assertTrue(_is_valid_solid(shape), f"Forward frame {f.frame_index} must be valid solid")

        header_f0 = format_step_header(forward_frames[0], direction="forward")
        self.assertEqual(header_f0, "Step 0 — flat pattern")
        header_f1 = format_step_header(forward_frames[1], direction="forward")
        self.assertIn("Step 1", header_f1)

    def test_player_navigation_forward(self):
        """Forward mode step navigation through frames."""
        if not self.real_l_run.is_dir():
            self.skipTest(f"Run dir not found: {self.real_l_run}")

        artifacts = load_run(self.real_l_run)
        graphs = build_graphs(artifacts)
        player = Player(artifacts, graphs, self.backend)
        player.switch_direction("forward")

        f0 = player.reset()
        self.assertEqual(f0.frame_index, 0)
        self.assertEqual(player.current().frame_index, 0)

        f1 = player.next()
        self.assertIsNotNone(f1)
        self.assertEqual(f1.frame_index, 1)

        f_end = player.next()
        self.assertIsNone(f_end)
        self.assertEqual(player.current().frame_index, 1)

        f_prev = player.prev()
        self.assertIsNotNone(f_prev)
        self.assertEqual(f_prev.frame_index, 0)

    def test_animation_dialog_direction_ui(self):
        """AnimationPlayerDialog combo box switches player direction."""
        try:
            from smbs_core.geometry_backend.freecad_bootstrap import ensure_freecad_paths
            ensure_freecad_paths()
            import FreeCAD as App
            from PySide6 import QtWidgets
        except Exception as e:
            self.skipTest(f"FreeCAD or PySide6 not available in this environment: {e}")

        app = QtWidgets.QApplication.instance()
        if app is None:
            app = QtWidgets.QApplication([])

        from freecad_smbs.ui.animation_dialog import AnimationPlayerDialog

        if not self.real_l_run.is_dir():
            self.skipTest(f"Run dir not found: {self.real_l_run}")

        artifacts = load_run(self.real_l_run)
        graphs = build_graphs(artifacts)
        player = Player(artifacts, graphs, self.backend)

        dialog = AnimationPlayerDialog(artifacts, player)
        self.assertIsNotNone(dialog)
        self.assertEqual(dialog.cmb_direction.count(), 2)
        self.assertEqual(dialog.cmb_direction.currentText(), "Unfold (backward)")

        # Switch to Fold (forward)
        dialog.cmb_direction.setCurrentIndex(1)
        self.assertEqual(player.direction, "forward")
        self.assertIn("flat pattern", dialog.lbl_header.text())

        # Switch back to Unfold (backward)
        dialog.cmb_direction.setCurrentIndex(0)
        self.assertEqual(player.direction, "backward")
        self.assertIn("initial (fully folded)", dialog.lbl_header.text())
        dialog.close()

    def test_u_fixture_bidirectional_reversal(self):
        """U-profile (2 bends) bidirectional reversal and frame mirroring."""
        u_run = self.repo_root / "runs" / "run_20261012_034044_3ccc14"
        if not u_run.is_dir():
            self.skipTest(f"Run dir not found: {u_run}")

        artifacts = load_run(u_run)
        graphs = build_graphs(artifacts)
        player = Player(artifacts, graphs, self.backend)

        backward_frames = list(player.frames)
        self.assertEqual(len(backward_frames), 3)

        player.switch_direction("forward")
        forward_frames = list(player.frames)
        self.assertEqual(len(forward_frames), 3)

        # Mirrored bend ordering
        self.assertEqual(forward_frames[0].step_view.bend_id, backward_frames[-1].step_view.bend_id)
        self.assertEqual(forward_frames[-1].step_view.bend_id, backward_frames[0].step_view.bend_id)

        # Frame indices renumbered cleanly
        for i, f in enumerate(forward_frames):
            self.assertEqual(f.frame_index, i)

    def test_4bend_fixture_bidirectional_sweep(self):
        """4-bend stepped busbar bidirectional reversal and step headers."""
        four_bend_run = self.repo_root / "runs" / "run_20261012_034045_9a351d"
        if not four_bend_run.is_dir():
            self.skipTest(f"Run dir not found: {four_bend_run}")

        artifacts = load_run(four_bend_run)
        graphs = build_graphs(artifacts)
        player = Player(artifacts, graphs, self.backend)

        backward_frames = list(player.frames)
        self.assertEqual(len(backward_frames), 5)  # Step 0..4

        player.switch_direction("forward")
        forward_frames = list(player.frames)
        self.assertEqual(len(forward_frames), 5)

        # First forward frame is flat pattern
        h0 = format_step_header(forward_frames[0], direction="forward")
        self.assertEqual(h0, "Step 0 — flat pattern")

        # Intermediate forward frames indicate step numbers
        h2 = format_step_header(forward_frames[2], direction="forward")
        self.assertTrue(h2.startswith("Step 2"))


if __name__ == "__main__":
    unittest.main()
