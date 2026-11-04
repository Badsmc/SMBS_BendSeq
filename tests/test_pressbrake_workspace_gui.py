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


if __name__ == "__main__":
    unittest.main()
