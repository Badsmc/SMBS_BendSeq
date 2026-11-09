"""Regression tests for Phase D: B01 -> B02 nonparallel axes and full 8-bend simulation in FreeCAD."""
import unittest

from smbs_core.demo_paths import demo_part_path
from freecad_smbs.commands.pressbrake_2d import _PreviewPanel, build_part_session
from smbs_core.kinematics2d.press_cycle import CycleExecutionMode, CyclePhase
from smbs_core.kinematics2d.fold_sequence import PlaybackStatus

try:
    import FreeCAD
    from PySide6 import QtWidgets, QtCore
    HAS_FREECAD_QT = True
except (ImportError, ModuleNotFoundError):
    HAS_FREECAD_QT = False

MODEL_PATH = demo_part_path("box.100out-50in-(ok).FCStd", required=False)

class TestFullCyclePhaseDRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAS_FREECAD_QT:
            raise unittest.SkipTest("FreeCAD / PySide6 environment not available")
        if not MODEL_PATH.is_file():
            raise unittest.SkipTest(f"Model file not found: {MODEL_PATH}")
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    def setUp(self):
        self.doc = FreeCAD.openDocument(str(MODEL_PATH))
        obj = next(o for o in self.doc.Objects if o.Name == "Body_Unfold")
        self.session = build_part_session(self.doc, [obj])
        self.panel = _PreviewPanel()
        self.panel.session = self.session
        self.panel.execution_mode = CycleExecutionMode.FULL_CYCLE

    def tearDown(self):
        self.panel._stop_timer()
        self.panel.close()
        self.panel.deleteLater()
        FreeCAD.closeDocument(self.doc.Name)

    def test_nested_draw_keeps_qgraphics_scene_items_alive(self):
        """A nested draw cannot delete QGraphicsPolygonItems in the outer draw.

        This previously risked 'Internal C++ object ... already deleted'
        during a Full Cycle Qt tick.
        """
        original = self.panel._draw_tooling_items
        calls = [0]

        def nested_draw(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 1:
                self.assertTrue(self.panel._draw_in_progress)
                # Force the exact problematic event: draw during draw.
                self.panel.draw()
                self.assertTrue(self.panel._draw_in_progress)
            return original(*args, **kwargs)

        self.panel._draw_tooling_items = nested_draw
        try:
            self.panel.draw()
            self.assertEqual(calls[0], 1)
            self.assertFalse(self.panel._draw_in_progress)
            self.assertGreater(len(self.panel.scene.items()), 0)
        finally:
            self.panel._draw_tooling_items = original

    def test_b01_to_b02_nonparallel_transfer_no_halt(self):
        """B01 and B02 have non-parallel axes in 3D (Z-plane rotation 90 deg).

        Full cycle must transfer seamlessly using 3D cutting plane section without
        triggering KINEMATIC_POSE_HALT or NONPARALLEL_BEND_AXES.
        """
        self.panel.play_all()
        self.assertEqual(self.panel.session.playback_status, PlaybackStatus.PLAYING)

        pc = self.panel._ensure_press_cycle_controller()
        reached_b02 = False

        for step in range(800):
            self.panel.advance()
            self.assertIsNone(
                self.panel._machine_pose_fault,
                f"Unexpected fault at step {step}: {self.panel._machine_pose_fault}"
            )
            if self.panel.session.active_bend_id == "B02":
                reached_b02 = True
                break

        self.assertTrue(reached_b02, "Simulation did not reach B02 within 800 ticks")
        self.assertEqual(self.panel.session.active_bend_id, "B02")

    def test_full_sequence_b01_to_b08_completion(self):
        """Full playback of all 8 bends (B01-B08) runs through all phases to COMPLETED."""
        self.panel.play_all()
        pc = self.panel._ensure_press_cycle_controller()

        seen_bends = set()
        seen_phases = set()

        for step in range(4000):
            self.panel.advance()
            seen_bends.add(self.panel.session.active_bend_id)
            seen_phases.add(pc.phase.value)
            self.assertIsNone(
                self.panel._machine_pose_fault,
                f"Unexpected fault at step {step}: {self.panel._machine_pose_fault}"
            )
            if pc.phase == CyclePhase.COMPLETED:
                break

        self.assertEqual(pc.phase, CyclePhase.COMPLETED)
        expected_bends = {"B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08"}
        self.assertEqual(seen_bends, expected_bends)
        self.assertIn("TRANSFER", seen_phases)

    def test_gui_controls_play_pause_resume_reset(self):
        """Play All -> Pause -> Resume -> Reset lifecycle preserves state and resets properly."""
        # 1. Play All
        self.panel.play_all()
        self.assertEqual(self.panel.session.playback_status, PlaybackStatus.PLAYING)
        for _ in range(40):
            self.panel.advance()
        self.assertIsNone(self.panel._machine_pose_fault)

        # 2. Pause
        self.panel.pause()
        self.assertEqual(self.panel.session.playback_status, PlaybackStatus.PAUSED)

        # 3. Resume
        self.panel.resume()
        self.assertEqual(self.panel.session.playback_status, PlaybackStatus.PLAYING)
        for _ in range(40):
            self.panel.advance()
        self.assertIsNone(self.panel._machine_pose_fault)

        # 4. Reset
        self.panel.reset()
        self.assertEqual(self.panel.session.active_bend_index, 0)
        self.assertEqual(self.panel.session.active_bend_id, "B01")
        self.assertIsNone(self.panel._machine_pose_fault)


if __name__ == "__main__":
    unittest.main()

