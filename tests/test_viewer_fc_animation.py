"""F.2: headless AnimationController + badge helpers. No FreeCAD, no Qt."""
import unittest

from smbs_core.viewer.fc_animation import (
    AnimationController,
    format_verification_badge,
    format_precision_badge,
    format_step_header,
    format_machine_frame_badge,
    format_tooling_badge,
    COLOR_GREEN, COLOR_ORANGE, COLOR_GRAY, COLOR_YELLOW,
)
from smbs_core.viewer.player import Player
from smbs_core.geometry_backend.backends.mock_backend import MockBackend
from smbs_core.viewer.graph_builder import build_graphs

from tests.test_viewer_player import _make_artifacts_with_verification


def _make_player() -> Player:
    artifacts, _ = _make_artifacts_with_verification(verification_payload=None)
    graphs = build_graphs(artifacts)
    init_shape = {"type": "mock_shape"}
    return Player(artifacts, graphs, MockBackend(), initial_shape=init_shape)


class TestAnimationController(unittest.TestCase):
    def test_initial_state(self):
        c = AnimationController(_make_player())
        self.assertFalse(c.is_playing())
        self.assertIsNone(c.current())

    def test_reset_returns_step_0(self):
        c = AnimationController(_make_player())
        f = c.reset()
        self.assertEqual(f.frame_index, 0)
        self.assertFalse(c.is_playing())
        self.assertIsNotNone(c.current())

    def test_play_pause(self):
        c = AnimationController(_make_player())
        c.reset()
        c.play()
        self.assertTrue(c.is_playing())
        c.pause()
        self.assertFalse(c.is_playing())

    def test_tick_advances_and_stops_at_end(self):
        c = AnimationController(_make_player())
        c.reset()
        c.play()
        # _make_artifacts has 1 candidate step -> total=2 frames (step 0 + step 1)
        f1 = c.tick()
        self.assertEqual(f1.frame_index, 1)
        self.assertTrue(c.is_playing())
        # At end: tick returns None, playing stops
        f_end = c.tick()
        self.assertIsNone(f_end)
        self.assertFalse(c.is_playing())

    def test_step_forward_backward(self):
        c = AnimationController(_make_player())
        c.reset()
        f = c.step_forward()
        self.assertEqual(f.frame_index, 1)
        f = c.step_backward()
        self.assertEqual(f.frame_index, 0)
        # Backward at start returns None
        f = c.step_backward()
        self.assertIsNone(f)

    def test_badges_mapping(self):
        c = AnimationController(_make_player())
        frame = c.reset()
        # Mock artifacts with no 09: verification_status = UNVERIFIED
        vt, vc = format_verification_badge(frame)
        self.assertEqual(vt, "UNVERIFIED")
        self.assertEqual(vc, COLOR_GRAY)
        # geometry_precision: mock shape -> not topods -> PREVIEW_ONLY
        pt, pc = format_precision_badge(frame)
        self.assertEqual(pt, "PREVIEW_ONLY")
        self.assertEqual(pc, COLOR_YELLOW)

    def test_step_header_formatting(self):
        c = AnimationController(_make_player())
        f0 = c.reset()
        self.assertIn("Step 0", format_step_header(f0))
        f1 = c.step_forward()
        self.assertIn("Step 1", format_step_header(f1))
        self.assertIn("B1", format_step_header(f1))

    def test_format_machine_frame_badge_loaded(self):
        txt, col = format_machine_frame_badge(True)
        self.assertEqual(txt, "MACHINE: LOADED")
        self.assertEqual(col, COLOR_GREEN)

    def test_format_machine_frame_badge_simplified(self):
        txt, col = format_machine_frame_badge(False)
        self.assertEqual(txt, "MACHINE: SIMPLIFIED (KL-16 pending)")
        self.assertEqual(col, COLOR_YELLOW)

    def test_format_machine_frame_badge_returns_tuple(self):
        res = format_machine_frame_badge(False)
        self.assertIsInstance(res, tuple)
        self.assertEqual(len(res), 2)
        self.assertIsInstance(res[0], str)
        self.assertEqual(len(res[1]), 3)

    def test_format_tooling_badge_both_present(self):
        txt, col = format_tooling_badge(True, True)
        self.assertEqual(txt, "TOOLING: PUNCH + DIE")
        self.assertEqual(col, COLOR_GREEN)

    def test_format_tooling_badge_partial(self):
        txt_p, col_p = format_tooling_badge(True, False)
        self.assertEqual(txt_p, "TOOLING: PARTIAL")
        self.assertEqual(col_p, COLOR_ORANGE)
        txt_d, col_d = format_tooling_badge(False, True)
        self.assertEqual(txt_d, "TOOLING: PARTIAL")
        self.assertEqual(col_d, COLOR_ORANGE)

    def test_format_tooling_badge_none(self):
        txt, col = format_tooling_badge(False, False)
        self.assertEqual(txt, "TOOLING: NONE")
        self.assertEqual(col, COLOR_GRAY)


if __name__ == "__main__":
    unittest.main()

