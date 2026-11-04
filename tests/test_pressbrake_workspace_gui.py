"""Phase GUI-workspace regression (runs only inside FreeCAD's Qt runtime)."""
import unittest


class WorkspaceGuiTests(unittest.TestCase):
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
