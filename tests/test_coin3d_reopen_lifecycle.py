"""Headless regression tests for closed and hidden FreeCAD Coin3D MDI windows."""
from __future__ import annotations

import ast
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "freecad_smbs/commands/pressbrake_2d.py"
COIN = ROOT / "freecad_smbs/ui/coin3d_viewer.py"


def gui_symbols():
    tree = ast.parse(GUI.read_text(encoding="utf-8"))
    names = {"_workspace_is_live", "get_workspace_widget", "PressBrake2DCommand"}
    nodes = [n for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    assert {n.name for n in nodes} == names
    return compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
                   str(GUI), "exec")


class Subwindow:
    def __init__(self, widget=None):
        self._widget = widget
        self._name = "SMBS_PressBrake2D_Workspace"
        self._visible = bool(widget)
        self.deleted = False

    def objectName(self): return self._name
    def setObjectName(self, name): self._name = name
    def widget(self): return self._widget
    def setWidget(self, widget): self._widget = widget
    def setWindowTitle(self, *_): pass
    def setAttribute(self, *_): pass
    def installEventFilter(self, *_): pass
    def resize(self, *_): pass
    def show(self): self._visible = True
    def hide(self): self._visible = False
    def isVisible(self): return self._visible
    def deleteLater(self): self.deleted = True


class MdiArea:
    def __init__(self):
        self.windows = []
        self.removed = []
        self.active = None

    def subWindowList(self): return list(self.windows)
    def removeSubWindow(self, win):
        self.windows.remove(win)
        self.removed.append(win)
    def addSubWindow(self, win): self.windows.append(win)
    def setActiveSubWindow(self, win): self.active = win


class Workspace:
    def __init__(self, closing=False):
        self._closing = closing
        self._qt_is_valid = lambda obj: True
        self._task_progress_busy = False
        self._preview_3d = SimpleNamespace(_native=True, _native_doc=None)
        self.render_count = 0

    def installEventFilter(self, *_): pass
    def _open_coin_preview(self): self.render_count += 1
    def _update_3d_preview(self, force=False): self.render_count += 1


class WorkspaceReopenTests(unittest.TestCase):
    def setUp(self):
        self.mdi = MdiArea()
        self.created = []
        self.events = []
        self.tasks = []

        main = SimpleNamespace(findChild=lambda cls: self.mdi)
        gui = ModuleType("FreeCADGui")
        gui.getMainWindow = lambda: main
        lifetime = ModuleType("freecad_smbs.ui.lifecycle_debug")
        lifetime.install = lambda *_: None
        mocked_modules = patch.dict(sys.modules, {
            "FreeCADGui": gui, "freecad_smbs.ui.lifecycle_debug": lifetime,
        })
        mocked_modules.start()
        self.addCleanup(mocked_modules.stop)

        class QObject:
            def __init__(self, parent=None): pass

        class QtCore:
            pass
        QtCore.QObject = QObject
        QtCore.Qt = SimpleNamespace(WA_DeleteOnClose=1)
        QtCore.QEvent = SimpleNamespace(DeferredDelete=42)

        class QtWidgets:
            QMdiArea = MdiArea
            QMdiSubWindow = Subwindow

        def make_workspace():
            widget = Workspace()
            self.created.append(widget)
            return widget

        scope = {
            "_qt": lambda: (QtCore, object, QtWidgets),
            "_selection_snapshot": lambda: (None, []),
            "_desktop_debug_event": lambda *a, **kw: self.events.append((a, kw)),
            "_PreviewPanel": make_workspace,
            "show_workspace_controls": lambda widget, tab=None:
                self.tasks.append((widget, tab)),
        }
        exec(gui_symbols(), scope)
        self.find = scope["get_workspace_widget"]
        self.is_live = scope["_workspace_is_live"]
        self.command = scope["PressBrake2DCommand"]()

    def test_closed_workspace_is_not_considered_active(self):
        old = Workspace(closing=True)
        self.mdi.windows.append(Subwindow(old))
        self.assertIsNone(self.find())
        self.assertFalse(self.is_live(old))

    def test_reopen_creates_new_viewer_and_discards_old(self):
        stale = Subwindow(Workspace(closing=True))
        self.mdi.windows.append(stale)
        self.command.Activated()
        self.assertTrue(stale.deleted)
        self.assertEqual(self.mdi.removed, [stale])
        self.assertEqual(len(self.created), 1)
        self.assertIs(self.find(), self.created[0])
        self.assertEqual(self.created[0].render_count, 1)
        self.assertEqual(self.tasks, [(self.created[0], 1)])
        self.assertIn(("WORKSPACE_STALE_REPLACED",), [a for a, _ in self.events])

    def test_hidden_live_workspace_is_reshown_without_reset(self):
        active = Workspace()
        win = Subwindow(active)
        win.hide()
        self.mdi.windows.append(win)
        self.command.Activated()
        self.assertFalse(self.created)
        self.assertTrue(win.isVisible())
        self.assertIs(self.mdi.active, win)
        self.assertIs(self.find(), active)
        self.assertEqual(active.render_count, 1)

    def test_invalid_qt_wrapper_is_rejected(self):
        workspace = Workspace()
        def deleted(_):
            raise RuntimeError("wrapped C++ object deleted")
        workspace._qt_is_valid = deleted
        self.assertFalse(self.is_live(workspace))

    def test_repeated_workspace_close_is_idempotent(self):
        tree = ast.parse(GUI.read_text(encoding="utf-8"))
        methods = [n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "closeEvent"
                   and isinstance(n.body[0], ast.If)]
        self.assertEqual(len(methods), 1)
        guard = methods[0].body[0]
        self.assertIsInstance(guard, ast.If)
        self.assertEqual(guard.test.attr, "_closing")
        self.assertTrue(any(isinstance(n, ast.Return) for n in guard.body))

    def test_closed_native_doc_root_is_never_referenced(self):
        src = COIN.read_text(encoding="utf-8")
        self.assertIn("if self._native_root is not None and native_still_open:", src)
        self.assertIn("if name != source_name and native_still_open:", src)


if __name__ == "__main__":
    unittest.main()

class CloseShellTests(unittest.TestCase):
    def test_retained_shell_is_hidden_even_after_native_activation(self):
        tree = ast.parse(GUI.read_text(encoding='utf-8'))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == '_hide_closed_workspace')
        scope = {}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(GUI), 'exec'), scope)
        shell = Subwindow(Workspace())
        owner = SimpleNamespace(_mdi_window=shell, _qt_is_valid=lambda _: True,
                                hide=lambda: None)
        scope['_hide_closed_workspace'](owner)
        self.assertFalse(shell.isVisible())
        shell.show()  # native document activation during a repeated Close
        scope['_hide_closed_workspace'](owner)
        self.assertFalse(shell.isVisible())

    def test_close_handler_hides_shell_on_initial_and_repeated_close(self):
        tree = ast.parse(GUI.read_text(encoding='utf-8'))
        handler = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                       and n.name == 'closeEvent' and isinstance(n.body[0], ast.If) and any(
                           isinstance(x, ast.Attribute) and x.attr == '_closing'
                           for x in ast.walk(n)))
        calls = [n for n in ast.walk(handler) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name)
                 and n.func.id == '_hide_closed_workspace']
        self.assertEqual(len(calls), 2)


class MdiCloseDispatchTests(unittest.TestCase):
    def test_native_activation_cannot_leave_manual_sequence_shell_visible(self):
        tree = ast.parse(GUI.read_text(encoding="utf-8"))
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)
                    and n.name == "WorkspaceSubWindow")
        owner = SimpleNamespace(_closing=False)
        class NativeMdi(Subwindow):
            def closeEvent(self, event):
                owner._closing = True
                self.show()  # FreeCAD activates views during child cleanup
        callbacks = []
        mdi = MdiArea()
        scope = {"QtCore": SimpleNamespace(QTimer=SimpleNamespace(
                     singleShot=lambda delay, callback: callbacks.append(callback))),
                 "mdi": mdi,
                 "QtWidgets": SimpleNamespace(QMdiSubWindow=NativeMdi),
                 "widget": owner, "_desktop_debug_event": lambda *a, **k: None}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(GUI), "exec"), scope)
        window = scope["WorkspaceSubWindow"](owner)
        mdi.windows.append(window)
        accepted = []
        event = SimpleNamespace(accept=lambda: accepted.append(True))
        for _ in range(2):
            window.closeEvent(event)
            self.assertFalse(window.isVisible())
        self.assertEqual(len(accepted), 2)
        self.assertEqual(len(callbacks), 1)
        self.assertIn(window, mdi.subWindowList())
        callbacks[0]()
        self.assertNotIn(window, mdi.subWindowList())
        self.assertTrue(window.deleted)
