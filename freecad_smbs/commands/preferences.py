"""
preferences.py - Command to configure SMBS user preferences.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from freecad_smbs.ui.dialogs import show_info

ICON_PATH: Path = Path(__file__).resolve().parent.parent / "resources" / "icons" / "preferences.svg"


class PreferencesCommand:
    """Command to configure SMBS preferences."""

    def GetResources(self) -> Dict[str, Any]:
        return {
            "Pixmap": str(ICON_PATH),
            "MenuText": "Настройки…",
            "ToolTip": "Настройки параметров SMBS (библиотека оснастки, алгоритм, политика коллизий)",
        }

    def Activated(self) -> None:
        try:
            from PySide6 import QtWidgets
            app = QtWidgets.QApplication.instance()
            if app is not None:
                from freecad_smbs.ui.preferences_dialog import PreferencesDialog
                dlg = PreferencesDialog()
                dlg.exec()
                return
        except Exception:
            pass

        from freecad_smbs.ui.preferences_dialog import get_smbs_preferences
        prefs = get_smbs_preferences()
        show_info(
            "SMBS: Настройки",
            f"Текущие настройки:\n"
            f"• Библиотека оснастки: {prefs['tooling_library_path']}\n"
            f"• Алгоритм поиска: {prefs['search_algorithm']}\n"
            f"• Политика коллизий: {prefs['unknown_policy']}"
        )

    def IsActive(self) -> bool:
        return True
