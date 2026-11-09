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
    """One long-lived FreeCAD task dialog for the lifetime of an MDI viewer.

    The Close button *hides* the Tasks dock instead of calling closeDialog().
    FreeCAD destroys a closed task dialog and its Qt form, so rebuilding it
    around the same QTabWidget is unsafe.
    """

    def __init__(self, workspace):
        self.workspace = workspace
        self._closing = False
        self._hidden = False
        self._task_dock = None
        _QtCore, _QtGui, QtWidgets = _qt()

        self.form = QtWidgets.QWidget()
        self.form.setObjectName("SMBS_TaskPanelForm")
        layout = QtWidgets.QVBoxLayout(self.form)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(workspace._control_tabs, 1)
        self.close_button = QtWidgets.QPushButton("Закрыть панель")
        self.close_button.setObjectName("SMBS_CloseTaskPanel")
        self.close_button.setToolTip(
            "Скрыть Task Panel, оставив окно симуляции и управление в памяти"
        )
        self.close_button.clicked.connect(self.hide_panel)
        layout.addWidget(self.close_button)

    def getStandardButtons(self):
        return 0

    def accept(self):
        return self.reject()

    def reject(self):
        # Explicit native task cancellation is a true dialog shutdown. Do not
        # mistake it for the custom Hide button.
        self.close_from_workspace()
        return True

    def _find_task_dock(self):
        """Locate the real Qt dock containing FreeCAD's task form."""
        _QtCore, _QtGui, QtWidgets = _qt()
        parent = self.form.parentWidget()
        while parent is not None:
            if isinstance(parent, QtWidgets.QDockWidget):
                self._task_dock = parent
                return parent
            parent = parent.parentWidget()
        return None

    def hide_panel(self):
        """Reversible hide: never destroy the Qt task form or its controls."""
        if self._closing or self._hidden:
            return
        dock = self._find_task_dock()
        if dock is None:
            # Fail visibly rather than pretending that reopening will work.
            message = (
                "Не найден контейнер Tasks FreeCAD. Панель не закрыта; "
                "проверьте журнал PressBrakeSim."
            )
            self.workspace.status.setText(message)
            _log(message)
            return
        dock.hide()
        self._hidden = True
        self.workspace.show_task_panel_btn.show()
        _log("PressBrakeSim Task dock hidden (task dialog preserved)")

    def show_panel(self):
        """Restore the same task dialog and controls, no reparenting/rebuild."""
        if self._closing:
            return False
        dock = self._task_dock or self._find_task_dock()
        if dock is None:
            self.workspace.status.setText(
                "Tasks dock not found; cannot restore the existing task dialog."
            )
            return False
        import FreeCADGui as Gui
        dock.show()
        dock.raise_()
        if hasattr(Gui.Control, "showTaskView"):
            Gui.Control.showTaskView()
        self._hidden = False
        self.workspace.show_task_panel_btn.hide()
        _log("PressBrakeSim Task dock shown (same Qt controls)")
        return True

    def close_from_workspace(self):
        """Actual teardown only when the MDI viewer is being closed."""
        if self._closing:
            return
        self._closing = True
        global _TASK_PANEL
        import FreeCADGui as Gui
        _TASK_PANEL = None
        self.workspace._task_panel_host = None
        try:
            if self._hidden and self._task_dock is not None:
                self._task_dock.show()
            Gui.Control.closeDialog()
        finally:
            _log("PressBrakeSim Task dialog closed with MDI viewer")


def _show_fallback_controls(workspace):
    """Use an owned Qt dock while another native FreeCAD Task Dialog is active.

    This avoids closing a foreign task, and keeps simulator buttons reachable.
    """
    import FreeCADGui as Gui
    QtCore, _QtGui, QtWidgets = _qt()
    dock = getattr(workspace, "_fallback_control_dock", None)
    if dock is not None:
        dock.show()
        dock.raise_()
        workspace.show_task_panel_btn.hide()
        return dock

    main = Gui.getMainWindow()
    dock = QtWidgets.QDockWidget("PressBrakeSim — управление", main)
    dock.setObjectName("SMBS_FallbackControlsDock")
    dock.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea)
    form = QtWidgets.QWidget(dock)
    layout = QtWidgets.QVBoxLayout(form)
    layout.setContentsMargins(4, 4, 4, 4)
    layout.addWidget(workspace._control_tabs, 1)
    close_btn = QtWidgets.QPushButton("Закрыть панель", form)
    close_btn.setObjectName("SMBS_CloseFallbackPanel")
    close_btn.clicked.connect(dock.hide)
    layout.addWidget(close_btn)
    dock.setWidget(form)
    main.addDockWidget(QtCore.Qt.LeftDockWidgetArea, dock)
    workspace._fallback_control_dock = dock

    # When the fallback is hidden, preserve its controls and offer a reopen
    # button directly in the viewer.
    dock.visibilityChanged.connect(
        lambda visible: workspace.show_task_panel_btn.setVisible(not visible)
    )
    dock.show()
    dock.raise_()
    workspace.show_task_panel_btn.hide()
    _log("FreeCAD native task occupied; showing PressBrakeSim controls in dock")
    return dock


def open_task_panel(workspace):
    """Restore our controls using native Task Panel or a non-destructive dock."""
    import FreeCADGui as Gui
    global _TASK_PANEL

    fallback = getattr(workspace, "_fallback_control_dock", None)
    if fallback is not None:
        return _show_fallback_controls(workspace)

    task = getattr(workspace, "_task_panel_host", None)
    if task is not None and not task._closing:
        if not task.show_panel():
            _log("Native Tasks dock not available; switching to owned controls dock")
            # Do not steal controls from a live native form unless that task is
            # closed; otherwise the same widget would be multiply owned.
            raise RuntimeError("FreeCAD Tasks dock cannot be restored")
        _TASK_PANEL = task
        return task

    active = Gui.Control.activeDialog() if hasattr(Gui.Control, "activeDialog") else None
    if active:
        _log("Another FreeCAD Task Dialog is active; using an independent controls dock")
        return _show_fallback_controls(workspace)

    task = PressBrakeTaskPanel(workspace)
    try:
        Gui.Control.showDialog(task)
    except Exception as exc:
        _log("showDialog unavailable ({}); using a dedicated controls dock".format(exc))
        # FreeCAD may have reparented the tab widget into a half-created form.
        workspace._control_tabs.setParent(workspace)
        return _show_fallback_controls(workspace)
    workspace.attach_task_panel(task)
    _TASK_PANEL = task
    task._find_task_dock()
    _log("PressBrakeSim native Task dialog created")
    return task


def close_workspace_controls(workspace):
    """Dispose addon-owned fallback dock or close its native task dialog."""
    dock = getattr(workspace, "_fallback_control_dock", None)
    if dock is not None:
        workspace._control_tabs.setParent(workspace)
        workspace._control_tabs.hide()
        dock.hide()
        dock.setWidget(None)
        dock.deleteLater()
        workspace._fallback_control_dock = None
    task = getattr(workspace, "_task_panel_host", None)
    if task is not None:
        task.close_from_workspace()


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
            open_task_panel(existing.widget())
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

        try:
            open_task_panel(widget)
        except Exception:
            window.close()
            raise

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
