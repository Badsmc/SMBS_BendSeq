"""FreeCAD 2.5D command registration entry point.

Compatibility exports keep existing callers/tests unchanged while the session
and Qt workspace are maintained in dedicated modules.
"""
from __future__ import annotations

from pathlib import Path

_ICON_ROOT = Path(__file__).resolve().parents[1] / 'resources' / 'icons'

from freecad_smbs.ui.pressbrake_common import _qt
from freecad_smbs.ui.diagnostics_logging import _log
from freecad_smbs.integration.part_session import (
    SelectionWarning,
    LoadedPartSession,
    validate_selection_preconditions,
    execute_load_part,
    resolve_selected_unfold,
    build_part_session,
    selected_unfold_name,
)
from freecad_smbs.ui.pressbrake_panel import _PreviewPanel, _button

_TASK_PANEL = None


class PressBrakeTaskPanel:
    """FreeCAD Task Panel host for controls of the dedicated MDI viewer."""

    def __init__(self, workspace):
        self.workspace = workspace
        self.form = workspace._control_tabs
        self._closing = False

    def getStandardButtons(self):
        # Task dialog has no implicit Accept/Cancel actions; switching tabs
        # must not accept edits or terminate playback.
        return 0

    def accept(self):
        return self.reject()

    def reject(self):
        global _TASK_PANEL
        if self._closing:
            return True
        self._closing = True
        _TASK_PANEL = None
        self.workspace._task_panel_host = None
        # Closing the Task Panel closes its associated MDI viewer, not the
        # document and not an arbitrary active FreeCAD subwindow.
        viewer_window = self.workspace.window()
        if viewer_window is not None:
            viewer_window.close()
        return True

    def close_from_workspace(self):
        if self._closing:
            return
        self._closing = True
        import FreeCADGui as Gui
        global _TASK_PANEL
        try:
            Gui.Control.closeDialog()
        finally:
            _TASK_PANEL = None


def get_workspace_widget():
    """Find the existing 2.5D MDI viewer, without creating another instance."""
    import FreeCADGui as Gui
    QtCore, QtGui, QtWidgets = _qt()
    main = Gui.getMainWindow()
    mdi = main.findChild(QtWidgets.QMdiArea)
    if mdi is None:
        return None
    for win in mdi.subWindowList():
        if win.objectName() == "SMBS_PressBrake2D_Workspace":
            return win.widget()
    return None


def _selection_snapshot():
    """Capture the selected FreeCAD objects before MDI/Task Panel gets focus."""
    try:
        import FreeCAD as App
        import FreeCADGui as Gui
        return getattr(App, "ActiveDocument", None), list(Gui.Selection.getSelection())
    except (ImportError, AttributeError):
        return None, []


class PressBrake2DCommand:
    def GetResources(self):
        return {
            "Pixmap": str(_ICON_ROOT / "gui_simulation.svg"),
            "MenuText": "Симуляция",
            "ToolTip": "Открыть отдельное окно симуляции и панель задач FreeCAD",
        }

    def IsActive(self):
        return True

    def Activated(self):
        import FreeCADGui as Gui
        global _TASK_PANEL
        selected_doc, selected_objects = _selection_snapshot()
        QtCore, QtGui, QtWidgets = _qt()
        main = Gui.getMainWindow()
        mdi = main.findChild(QtWidgets.QMdiArea)
        if mdi is None:
            raise RuntimeError("FreeCAD MDI area was not found; source document left unchanged.")

        name = "SMBS_PressBrake2D_Workspace"
        existing = next(
            (win for win in mdi.subWindowList() if win.objectName() == name), None
        )
        if existing is not None and existing.widget() is not None:
            mdi.setActiveSubWindow(existing)
            existing.show()
            existing.raise_()
            if _TASK_PANEL is None:
                task = PressBrakeTaskPanel(existing.widget())
                Gui.Control.showDialog(task)
                _TASK_PANEL = task
                existing.widget().attach_task_panel(task)
            return

        # Retire only this addon's old dock, never the source document view.
        old_dock = main.findChild(QtWidgets.QDockWidget, "SMBS_PressBrake2D_Dock")
        if old_dock is not None:
            old_widget = old_dock.widget()
            if old_widget is not None and hasattr(old_widget, "_stop_timer"):
                old_widget._stop_timer()
            old_dock.hide()
            old_dock.setWidget(None)
            old_dock.deleteLater()

        widget = _PreviewPanel()
        window = QtWidgets.QMdiSubWindow()
        window.setObjectName(name)
        window.setWindowTitle("PressBrakeSim 2.5D — симуляция")
        window.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        window.setWidget(widget)
        mdi.addSubWindow(window)
        window.resize(1000, 720)
        window.showMaximized()
        mdi.setActiveSubWindow(window)

        task = PressBrakeTaskPanel(widget)
        try:
            Gui.Control.showDialog(task)
        except Exception:
            window.close()
            raise
        _TASK_PANEL = task
        widget.attach_task_panel(task)

        if selected_unfold_name(selected_doc, selected_objects) is not None:
            widget.load_part(selection=selected_objects, document=selected_doc)
        _log("2.5D viewer opened in FreeCAD MDI; controls hosted by Task Panel")


class LoadPartCommand:
    def GetResources(self):
        return {"Pixmap": str(_ICON_ROOT / "gui_part.svg"),
            "MenuText": "Деталь", "ToolTip": "Загрузить выделенную SheetMetal деталь"}

    def IsActive(self):
        doc, selection = _selection_snapshot()
        return selected_unfold_name(doc, selection) is not None

    def Activated(self):
        doc, selection = _selection_snapshot()
        if selected_unfold_name(doc, selection) is None:
            _log("Part command rejected: select a SheetMetal Unfold")
            return
        workspace = get_workspace_widget()
        just_opened = workspace is None
        if just_opened:
            PressBrake2DCommand().Activated()
            workspace = get_workspace_widget()
            if workspace is None:
                raise RuntimeError("PressBrakeSim viewer did not open")
        # Opening the simulation viewer already loads an existing selection;
        # do not rebuild the same session twice on one toolbar click.
        if not just_opened:
            workspace.load_part(selection=selection, document=doc)
        workspace.show_control_tab(0)


class ToolingTabCommand:
    def GetResources(self):
        return {"Pixmap": str(_ICON_ROOT / "gui_tooling.svg"),
            "MenuText": "Оснастка", "ToolTip": "Открыть вкладку оснастки в FreeCAD Task Panel"}

    def IsActive(self):
        return True

    def Activated(self):
        workspace = get_workspace_widget()
        if workspace is None:
            PressBrake2DCommand().Activated()
            workspace = get_workspace_widget()
            if workspace is None:
                raise RuntimeError("PressBrakeSim viewer did not open")
        workspace.show_control_tab(1)


class CalculateCommand:
    def GetResources(self):
        return {
            "Pixmap": str(_ICON_ROOT / "gui_calculate.svg"),
            "MenuText": "Расчёт",
            "ToolTip": "Автоматический расчёт пока не прошёл приёмку — команда отключена",
        }

    def IsActive(self):
        # Fail closed until the solver is integrated and physically validated.
        return False

    def Activated(self):
        raise RuntimeError("Automatic bend sequence calculation is not yet validated")
