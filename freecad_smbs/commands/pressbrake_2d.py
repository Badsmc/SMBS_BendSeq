"""FreeCAD 2.5D command registration entry point.

Compatibility exports keep existing callers/tests unchanged while the session
and Qt workspace are maintained in dedicated modules.
"""
from __future__ import annotations

from freecad_smbs.ui.pressbrake_common import _qt
from freecad_smbs.ui.diagnostics_logging import _log
from freecad_smbs.integration.part_session import (
    SelectionWarning,
    LoadedPartSession,
    validate_selection_preconditions,
    execute_load_part,
    resolve_selected_unfold,
    build_part_session,
)
from freecad_smbs.ui.pressbrake_panel import _PreviewPanel, _button

_DOCK = None  # retired dock compatibility; the active UI uses an MDI window

class PressBrake2DCommand:
    def GetResources(self):
        return {
            "MenuText": "PressBrakeSim — 2D интерфейс",
            "ToolTip": "Открыть 2.5D симулятор гибки листовой детали SheetMetal",
        }

    def IsActive(self):
        return True

    def Activated(self):
        """Open a dedicated FreeCAD document-area workspace, never a side dock."""
        import FreeCADGui as Gui
        global _DOCK
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
            return

        # Retire only this addon's old dock. Never alter FreeCAD's document views.
        old_dock = main.findChild(QtWidgets.QDockWidget, "SMBS_PressBrake2D_Dock")
        if old_dock is not None:
            old_widget = old_dock.widget()
            if old_widget is not None and hasattr(old_widget, "_stop_timer"):
                old_widget._stop_timer()
            old_dock.hide()
            old_dock.setWidget(None)
            old_dock.deleteLater()
        _DOCK = None

        widget = _PreviewPanel()
        window = QtWidgets.QMdiSubWindow()
        window.setObjectName(name)
        window.setWindowTitle("PressBrakeSim 2.5D — рабочее пространство")
        window.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        window.setWidget(widget)
        mdi.addSubWindow(window)
        window.resize(1000, 720)
        window.showMaximized()
        mdi.setActiveSubWindow(window)

        sel = Gui.Selection.getSelection() if hasattr(Gui, "Selection") else []
        if sel:
            widget.load_part()
        _log("2.5D workspace opened in FreeCAD MDI area")


