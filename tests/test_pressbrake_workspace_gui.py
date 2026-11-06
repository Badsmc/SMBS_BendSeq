"""Phase GUI-workspace regression (runs only inside FreeCAD's Qt runtime)."""
import unittest


class WorkspaceGuiTests(unittest.TestCase):
    def test_invalid_machine_pose_freezes_without_identity_reset(self):
        try:
            import FreeCAD  # noqa: F401
            from freecad_smbs.commands.pressbrake_2d import _PreviewPanel, _qt
            _QtCore, _QtGui, QtWidgets = _qt()
        except (ImportError, ModuleNotFoundError) as exc:
            self.skipTest("FreeCAD runtime unavailable: {}".format(exc))
        if QtWidgets.QApplication.instance() is None:
            self.skipTest("Qt GUI application is not running")
        panel = _PreviewPanel()
        try:
            # Explicitly preserve previously drawn coordinates and workpiece
            # pose. No bend is silently changed and no identity is fabricated.
            from smbs_core.kinematics2d.workpiece_pose_2d import WorkpiecePose2D
            saved_section = {
                "fixed_polygon": ((-10., 3.), (-1., 3.), (-1., 1.), (-10., 1.))
            }
            saved_pose = WorkpiecePose2D(rotation_deg=-30.0)
            panel._last_valid_machine_section = saved_section
            panel._last_valid_machine_pose = saved_pose
            panel._last_valid_machine_bend = "B01"
            panel._halt_unsupported_machine_pose(
                "NONPARALLEL_BEND_AXES: no shared 2.5D projection"
            )
            self.assertFalse(panel.timer.isActive())
            self.assertEqual(panel._last_valid_machine_section, saved_section)
            self.assertEqual(panel._last_valid_machine_pose, saved_pose)
            self.assertIn("NONPARALLEL_BEND_AXES", panel.status.text())
        finally:
            panel.close()
            panel.deleteLater()

    def test_reset_preserves_open_jsonl_until_playback(self):
        """Reset before Play All must not terminate diagnostics prematurely."""
        try:
            import FreeCAD  # noqa: F401
            from freecad_smbs.commands.pressbrake_2d import _PreviewPanel, _qt
            from smbs_core.kinematics2d.motion_trace import MotionTraceRecorder
            _QtCore, _QtGui, QtWidgets = _qt()
        except (ImportError, ModuleNotFoundError) as exc:
            self.skipTest("FreeCAD runtime unavailable: {}".format(exc))
        if QtWidgets.QApplication.instance() is None:
            self.skipTest("Qt GUI application is not running")
        import tempfile
        import json
        from types import SimpleNamespace
        panel = _PreviewPanel()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                trace = MotionTraceRecorder(tmp)
                path = trace.start()
                panel.motion_trace = trace
                controller = SimpleNamespace(active_index=0)
                panel.session = SimpleNamespace(
                    controller=controller,active_bend_index=0
                )
                fake_cycle = SimpleNamespace(reset=lambda: None)
                panel._ensure_press_cycle_controller = lambda: fake_cycle
                panel._stop_timer = lambda: None
                panel._configure_for_active_bend = lambda: None
                panel._update_tooling_status_label = lambda: None
                panel.draw = lambda: None
                panel.reset()
                self.assertTrue(trace.is_open)
                # Even an explicit stray RESET finalization request must not
                # close a diagnostic recording in the current UI contract.
                panel._finish_motion_trace("RESET")
                self.assertTrue(trace.is_open)
                trace.event("PLAY_ALL")
                trace.flush()
                events=[json.loads(row).get("event") for row in
                        path.read_text(encoding="utf-8").splitlines()]
                self.assertIn("RESET",events)
                self.assertIn("RESET_TRACE_RETAINED",events)
                self.assertIn("PLAY_ALL",events)
                trace.stop("USER_STOP")
        finally:
            panel.timer.stop()
            panel.close()
            panel.deleteLater()

    def test_manual_record_button_creates_jsonl_and_summary(self):
        import tempfile
        from pathlib import Path
        try:
            import FreeCAD  # noqa: F401
            from freecad_smbs.commands.pressbrake_2d import _PreviewPanel, _qt
            QtCore, QtGui, QtWidgets = _qt()
        except (ImportError, ModuleNotFoundError) as exc:
            self.skipTest("FreeCAD runtime unavailable: {}".format(exc))
        if QtWidgets.QApplication.instance() is None:
            self.skipTest("Qt GUI application is not running")
        panel = _PreviewPanel()
        try:
            from types import SimpleNamespace
            panel.session = SimpleNamespace(
                active_bend_id="B01",
                bend_order=("B01",),
                part_label="Recording regression",
            )
            with tempfile.TemporaryDirectory() as tmp:
                panel._trace_directory = Path(tmp) / "new_json_folder"
                panel._start_motion_trace(manual=True)
                self.assertIsNotNone(panel.motion_trace)
                self.assertTrue(panel.motion_trace.is_open)
                self.assertTrue(panel.motion_trace.path.is_file())
                self.assertGreater(panel.motion_trace.path.stat().st_size, 0)
                panel._finish_motion_trace("USER_STOP")
                self.assertTrue(panel.motion_trace.summary_path.is_file())
        finally:
            panel.close()
            panel.deleteLater()

    def test_mode_selection_and_play_all_drive_timer_without_manual_steps(self):
        """Real Qt event loop advances Bend Only automatically in FreeCAD."""
        try:
            import FreeCAD  # noqa: F401
            from freecad_smbs.commands.pressbrake_2d import _PreviewPanel, _qt
            from smbs_core.kinematics2d.fold_sequence import PlaybackStatus
            from smbs_core.kinematics2d.press_cycle import CycleExecutionMode
            QtCore, _QtGui, QtWidgets = _qt()
        except (ImportError, ModuleNotFoundError) as exc:
            self.skipTest("FreeCAD runtime unavailable: {}".format(exc))
        if QtWidgets.QApplication.instance() is None:
            self.skipTest("Qt GUI application is not running")
        panel = _PreviewPanel()
        try:
            from types import SimpleNamespace
            class FakeSession:
                def __init__(self):
                    self.playback_status = PlaybackStatus.IDLE
                    self.active_bend_id = "B01"
                    self.active_bend_index = 0
                    self.current_angle_deg = 0.0
                    self.bend_order = ("B01",)
                    self.canonical_order = self.bend_order
                    self.controller = SimpleNamespace(active_index=0)
                    self.frames = 0

                def play_all(self):
                    self.playback_status = PlaybackStatus.PLAYING

                def advance_time(self, dt):
                    self.frames += 1
                    self.current_angle_deg += dt * 90.0
                    if self.frames >= 4:
                        self.playback_status = PlaybackStatus.COMPLETED

                def can_reorder(self):
                    return False

            stub = FakeSession()
            panel.session = stub
            panel.draw = lambda: None
            panel._configure_for_active_bend = lambda: None
            panel._update_tooling_status_label = lambda: None
            panel._update_press_cycle_labels = lambda: None
            panel._start_motion_trace = lambda: None
            panel._finish_motion_trace = lambda reason: None
            panel._ensure_press_cycle_controller = lambda: None
            self.assertFalse(hasattr(panel, "auto_geometry_btn"))
            panel.set_cycle_mode(CycleExecutionMode.BEND_ONLY)
            self.assertEqual(panel.execution_mode, CycleExecutionMode.BEND_ONLY)
            self.assertEqual(
                panel.cycle_mode_combo.currentData(),
                CycleExecutionMode.BEND_ONLY.value
            )
            panel.play_btn.setEnabled(True)
            panel.play_btn.click()
            self.assertTrue(panel.timer.isActive())
            self.assertEqual(stub.playback_status, PlaybackStatus.PLAYING)
            loop = QtCore.QEventLoop()
            QtCore.QTimer.singleShot(400, loop.quit)
            if hasattr(loop, "exec"):
                loop.exec()
            else:
                loop.exec_()
            self.assertGreaterEqual(stub.frames, 4)
            self.assertEqual(stub.playback_status, PlaybackStatus.COMPLETED)
            self.assertFalse(panel.timer.isActive())
            self.assertEqual(len(panel.findChildren(QtCore.QTimer)), 1)
        finally:
            panel.timer.stop()
            panel.close()
            panel.deleteLater()

    def test_separate_workspace_layout_and_single_timer(self):
        try:
            import FreeCAD  # noqa: F401
            from freecad_smbs.commands.pressbrake_2d import _PreviewPanel, _qt
            QtCore, QtGui, QtWidgets = _qt()
        except (ImportError, ModuleNotFoundError) as exc:
            self.skipTest("FreeCAD runtime unavailable: {}".format(exc))

        application = QtWidgets.QApplication.instance()
        if application is None:
            self.skipTest("Qt GUI application is not running")

        panel = _PreviewPanel()
        try:
            tabs = panel.findChild(QtWidgets.QTabWidget, "SMBS_ViewerTabs")
            controls = panel.findChild(QtWidgets.QTabWidget, "SMBS_ControlTabs")
            self.assertIsNotNone(tabs)
            self.assertIsNotNone(controls)
            self.assertEqual(tabs.count(), 2)
            self.assertEqual(controls.count(), 3)
            # The Simulation tab owns mode selection and one Play All.
            # Duplicate angle arrows, bend arrows and alternate Auto button
            # are forbidden by docs/PRESSBRAKESIM_UI_CONTROLS.md.
            self.assertIs(panel.cycle_mode_combo.parentWidget() is not None, True)
            self.assertFalse(hasattr(panel, "auto_geometry_btn"))
            self.assertFalse(hasattr(panel, "back_btn"))
            self.assertFalse(hasattr(panel, "next_btn"))
            self.assertFalse(hasattr(panel, "end_btn"))
            self.assertFalse(hasattr(panel, "prev_bend_btn"))
            self.assertFalse(hasattr(panel, "next_bend_btn"))
            self.assertEqual(panel.play_btn.text(), "▶ Play All")
            self.assertTrue(panel.prev_step_btn is not None)
            self.assertTrue(panel.next_step_btn is not None)
            self.assertEqual(
                [controls.tabText(i) for i in range(controls.count())],
                ["Симуляция", "Оснастка", "Настройки"],
            )
            from smbs_core.kinematics2d.press_cycle import CycleExecutionMode
            self.assertEqual(panel.execution_mode, CycleExecutionMode.FULL_CYCLE)
            self.assertEqual(
                panel.cycle_mode_combo.currentData(),
                CycleExecutionMode.FULL_CYCLE.value,
            )
            # Switching to Bend Only is still explicit and must not remove
            # the model's only timer or create an independent angle writer.
            panel.set_cycle_mode(CycleExecutionMode.BEND_ONLY)
            self.assertEqual(panel.execution_mode, CycleExecutionMode.BEND_ONLY)
            panel.set_cycle_mode(CycleExecutionMode.FULL_CYCLE)
            self.assertEqual(panel.execution_mode, CycleExecutionMode.FULL_CYCLE)
            self.assertEqual(len(panel.findChildren(QtCore.QTimer)), 1)
            self.assertIs(tabs.widget(0), panel.view)
            self.assertIs(tabs.widget(1), panel._preview_3d)
            self.assertEqual(panel._preview_3d.objectName(), "SMBS_3D_ReadOnly")
            tabs.setCurrentIndex(1)
            self.assertFalse(panel._preview_3d_scene.items() == [])
        finally:
            panel.close()
            panel.deleteLater()


    def test_full_cycle_tick_failure_pauses_controller_and_latches_reason(self):
        """A failed machine tick cannot leave the headless controller PLAYING."""
        try:
            import FreeCAD  # noqa: F401
            from freecad_smbs.commands.pressbrake_2d import _PreviewPanel, _qt
            from smbs_core.kinematics2d.fold_sequence import PlaybackStatus
            from smbs_core.kinematics2d.press_cycle import CycleExecutionMode
            _QtCore, _QtGui, QtWidgets = _qt()
        except (ImportError, ModuleNotFoundError) as exc:
            self.skipTest("FreeCAD runtime unavailable: {}".format(exc))
        if QtWidgets.QApplication.instance() is None:
            self.skipTest("Qt GUI application is not running")

        class FailedCycle:
            def __init__(self, session):
                self.session = session
                self.paused = False
            def advance_time(self, dt):
                raise RuntimeError("TRANSFER_TRAJECTORY_FAILED: fixture B01->B02")
            def pause(self):
                self.paused = True
                self.session.playback_status = PlaybackStatus.PAUSED

        from types import SimpleNamespace
        panel = _PreviewPanel()
        try:
            session = SimpleNamespace(
                playback_status=PlaybackStatus.PLAYING,
                active_bend_index=0, active_bend_id="B01",
                current_angle_deg=90.0, bend_order=("B01", "B02"),
                canonical_order=("B01", "B02"),
                can_reorder=lambda: False,
            )
            pc = FailedCycle(session)
            panel.session = session
            panel.press_cycle = pc
            panel.execution_mode = CycleExecutionMode.FULL_CYCLE
            panel._ensure_press_cycle_controller = lambda: pc
            panel._configure_for_active_bend = lambda: None
            panel._update_tooling_status_label = lambda: None
            panel.draw = lambda: None
            recorded = []
            panel._trace_event = lambda event, **kw: recorded.append(event)
            panel._finish_motion_trace = lambda reason: recorded.append(reason)
            panel.timer.start()
            panel.advance()
            self.assertTrue(pc.paused)
            self.assertEqual(session.playback_status, PlaybackStatus.PAUSED)
            self.assertFalse(panel.timer.isActive())
            self.assertIn("TRANSFER_TRAJECTORY_FAILED", panel._machine_pose_fault)
            self.assertIn("AUTOPLAY_ERROR", recorded)
            self.assertIn("AUTO PLAY ERROR", panel.status.text())
            # Replay is blocked until an explicit user reset/mode change.
            panel.play_all()
            self.assertFalse(panel.timer.isActive())
        finally:
            panel.timer.stop()
            panel.close()
            panel.deleteLater()


if __name__ == "__main__":
    unittest.main()
