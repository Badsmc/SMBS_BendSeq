"""
diagnostics.py - Command to inspect SMBS environment diagnostics.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from freecad_smbs.ui.dialogs import show_info

ICON_PATH: Path = Path(__file__).resolve().parent.parent / "resources" / "icons" / "diagnostics.svg"


class DiagnosticsCommand:
    """Command to inspect SMBS environment and setup diagnostics."""

    def GetResources(self) -> Dict[str, Any]:
        return {
            "Pixmap": str(ICON_PATH),
            "MenuText": "Диагностика",
            "ToolTip": "Диагностика и проверка готовности среды: FreeCAD, SheetMetal, PythonOCC, оснастка",
        }

    def Activated(self) -> None:
        try:
            from PySide6 import QtWidgets
            app = QtWidgets.QApplication.instance()
            if app is not None:
                from freecad_smbs.ui.diagnostics_dialog import DiagnosticsDialog
                dlg = DiagnosticsDialog()
                dlg.exec()
                return
        except Exception:
            pass

        from freecad_smbs.ui.diagnostics_dialog import (
            collect_environment_diagnostics,
            format_diagnostics_report,
        )
        diag = collect_environment_diagnostics()
        show_info("SMBS: Диагностика", format_diagnostics_report(diag))

    def IsActive(self) -> bool:
        return True
