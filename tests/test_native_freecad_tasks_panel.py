"""SMBS must use FreeCAD's native Tasks view, never an addon dock.

Headless tests simulate the essential FreeCAD Gui.Control task lifecycle;
actual Qt rendering must still be verified on FreeCAD 1.1.x.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import TestCase, main
from unittest.mock import patch

from freecad_smbs.commands import pressbrake_2d as gui


class Widget:
    def __init__(self, parent=None):
        self.parent = parent
        self.visible = False
        self.name = None
        self.title = None
        self.font_value = SimpleNamespace(setBold=lambda _value: None)
        self.value = ""
    def setParent(self, parent):
        self.parent = parent
    def parentWidget(self):
        return self.parent if hasattr(self.parent, "layout") else None
    def layout(self):
        return None
    def hide(self):
        self.visible = False
    def show(self):
        self.visible = True
    def setObjectName(self, name):
        self.name = name
    def setWindowTitle(self, title):
        self.title = title
    def setText(self, text):
        self.value = text
    def text(self):
        return self.value
    def setWordWrap(self, _value):
        pass
    def setMaximumHeight(self, _height):
        pass
    def setContentsMargins(self, *_args):
        pass
    def font(self):
        return self.font_value
    def setFont(self, _value):
        pass
    def button(self, _which):
        return self


class Stack(Widget):
    def __init__(self):
        super().__init__()
        self.pages = [object(), object(), object()]
        self.index = 0
    def count(self):
        return len(self.pages)
    def setCurrentIndex(self, index):
        self.index = index
    def currentWidget(self):
        return self.pages[self.index]


class Layout:
    def __init__(self, *_args):
        self.items = []
    def setContentsMargins(self, *_args):
        pass
    def setSpacing(self, _value):
        pass
    def addWidget(self, *args):
        self.items.append(args[0])
    def addStretch(self, _weight):
        pass
    def addLayout(self, other):
        self.items.append(other)


class Control:
    def __init__(self):
        self.current = None
        self.shown = []
        self.closed_count = 0
    def activeDialog(self):
        # Older FreeCAD returns a bool, not the task object.
        return bool(self.current)
    def showDialog(self, panel):
        if self.current is not None:
            raise RuntimeError("Another FreeCAD Tasks dialog is active")
        self.shown.append(panel)
        self.current = panel
    def closeDialog(self):
        dialog = self.current
        self.current = None
        self.closed_count += 1
        if dialog is not None and hasattr(dialog, "closed"):
            dialog.closed()


class NativeTasksTests(TestCase):
    def setUp(self):
        qtwidgets = SimpleNamespace(
            QWidget=Widget, QLabel=Widget, QFrame=Widget,
            QVBoxLayout=Layout, QHBoxLayout=Layout,
            QDialogButtonBox=SimpleNamespace(Cancel=0x00400000),
        )
        self.qt = (SimpleNamespace(), SimpleNamespace(), qtwidgets)
        self.control = Control()
        fakegui = ModuleType("FreeCADGui")
        fakegui.Control = self.control
        patchers = [
            patch.object(gui, "_qt", return_value=self.qt),
            patch.dict(sys.modules, {"FreeCADGui": fakegui}),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        self.workspace = SimpleNamespace(
            _task_stack=Stack(),
            status=Widget(),
            task_progress=Widget(),
            _task_status_chip=Widget(),
            _task_progress_caption=Widget(),
            show_task_panel_btn=Widget(),
            _task_page_titles={},
            _native_task_panel=None,
            _task_section_label=None,
            _on_task_status_change=lambda _message: None,
        )
        self.workspace._task_page_titles = {
            item: ("Деталь", "Оснастка", "Manual")[i]
            for i, item in enumerate(self.workspace._task_stack.pages)
        }
        self.workspace.status.setText("Готово")

    def test_opens_native_tasks_not_an_addon_dock(self):
        panel = gui.show_workspace_controls(self.workspace, tab=2)
        self.assertIs(panel, self.control.current)
        self.assertIs(panel.form, self.control.current.form)
        self.assertEqual(panel.form.name, "SMBS_NativeTasksRoot")
        self.assertEqual(panel.form.title, "SMBS — Manual")
        self.assertIs(self.workspace._native_task_panel, panel)
        self.assertFalse(self.workspace.show_task_panel_btn.visible)
        self.assertTrue(panel.isAllowedAlterDocument())
        self.assertTrue(panel.isAllowedAlterSelection())
        self.assertTrue(panel.isAllowedAlterView())
        self.assertEqual(panel.getStandardButtons(),
                         self.qt[2].QDialogButtonBox.Cancel)
        panel.modifyStandardButtons(Widget())

    def test_switches_pages_without_reopening_or_resetting_session(self):
        stack = self.workspace._task_stack
        first = gui.show_workspace_controls(self.workspace, 0)
        again = gui.show_workspace_controls(self.workspace, 2)
        self.assertIs(first, again)
        self.assertEqual(len(self.control.shown), 1)
        self.assertEqual(stack.index, 2)
        self.assertEqual(self.workspace._task_section_label.text(), "Manual")
        self.assertIs(self.workspace._task_stack, stack)

    def test_native_close_reopens_same_stack_and_all_existing_controls(self):
        original = (
            self.workspace._task_stack, self.workspace.status,
            self.workspace.task_progress, self.workspace._task_status_chip,
            self.workspace._task_progress_caption,
        )
        first = gui.show_workspace_controls(self.workspace, tab=2)
        self.assertTrue(first.reject())
        self.assertIsNone(self.workspace._native_task_panel)
        self.assertIsNone(self.control.current)
        self.assertTrue(self.workspace.show_task_panel_btn.visible)
        for child in original:
            self.assertIs(child.parent, self.workspace)
            self.assertFalse(child.visible)
        second = gui.show_workspace_controls(self.workspace, tab=2)
        self.assertIsNot(first, second)
        self.assertIs(self.workspace._task_stack, original[0])
        self.assertIs(self.workspace.status, original[1])
        # Explicit hide() in _detach must be reversed for *every* widget.
        # This is the regression that caused an empty native Tasks pane.
        for child in original:
            self.assertTrue(child.visible)
        self.assertEqual(len(self.control.shown), 2)
        self.assertIs(self.control.current, second)

    def test_external_freecad_task_does_not_get_closed(self):
        another = object()
        self.control.current = another
        result = gui.show_workspace_controls(self.workspace, tab=1)
        self.assertIsNone(result)
        self.assertIs(self.control.current, another)
        self.assertEqual(self.control.closed_count, 0)
        self.assertIn("завершите текущую задачу", self.workspace.status.text())
        self.assertTrue(self.workspace.show_task_panel_btn.visible)

    def test_freecad_external_close_detaches_without_deleting_live_state(self):
        panel = gui.show_workspace_controls(self.workspace, 2)
        stack = self.workspace._task_stack
        self.control.closeDialog()
        self.assertIsNone(self.workspace._native_task_panel)
        self.assertIs(stack.parent, self.workspace)
        self.assertTrue(self.workspace.show_task_panel_btn.visible)
        # Native callback and explicit reject can race: both are idempotent.
        panel.closed()
        again = gui.show_workspace_controls(self.workspace, 1)
        self.assertIs(again, self.control.current)
        self.assertIs(self.workspace._task_stack, stack)

    def test_mdi_close_closes_only_own_tasks_dialog(self):
        gui.show_workspace_controls(self.workspace)
        gui.close_workspace_controls(self.workspace)
        self.assertIsNone(self.control.current)
        self.assertIsNone(self.workspace._native_task_panel)
        self.assertEqual(self.control.closed_count, 1)

    def test_no_parallel_dockwidget_or_modal_task_export_in_gui(self):
        source = (Path(__file__).resolve().parents[1]
                  / "freecad_smbs/commands/pressbrake_2d.py").read_text(
                      encoding="utf-8"
                  )
        self.assertIn("Gui.Control.showDialog(panel)", source)
        self.assertIn("Gui.Control.closeDialog()", source)
        self.assertIn('def getStandardButtons(self):', source)
        self.assertNotIn("QtWidgets.QDockWidget(", source)
        self.assertNotIn("main.addDockWidget(", source)
        self.assertNotIn("PressBrakeSim TaskPanel", source)
        self.assertIn("embed_task_dialog(self, dlg,", source)


if __name__ == "__main__":
    main()
