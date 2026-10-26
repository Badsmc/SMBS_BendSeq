"""tooling.py - Command: tooling library manager (S.71)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from freecad_smbs.integration import smbs_bridge
from freecad_smbs.ui.dialogs import show_error

ICON_PATH = Path(__file__).resolve().parent.parent / "resources" / "icons" / "tooling.svg"


class ToolingCommand:
    def GetResources(self) -> Dict[str, Any]:
        return {
            "Pixmap": str(ICON_PATH),
            "MenuText": "Оснастка",
            "ToolTip": "Библиотека оснастки: пуансоны, матрицы, станина, задний упор, лапки",
        }

    def Activated(self) -> None:
        try:
            library = smbs_bridge.get_tooling_library()
        except Exception as e:
            show_error("SMBS: Оснастка", f"Не удалось загрузить библиотеку оснастки:\n{e}")
            return
        try:
            from freecad_smbs.ui.tooling_panel import ToolingPanel

            ToolingPanel(library).exec()
        except Exception as e:
            show_error("SMBS: Оснастка", f"Не удалось открыть панель оснастки:\n{e}")

    def IsActive(self) -> bool:
        return True  # library is repo-relative, independent of run
