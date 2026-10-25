"""
info.py - Command stub for displaying run information dialog.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

ICON_PATH: Path = Path(__file__).resolve().parent.parent / "resources" / "icons" / "info.svg"


class InfoCommand:
    """Command to view metadata, artifacts, and known limitations for loaded run."""

    def GetResources(self) -> Dict[str, Any]:
        return {
            "Pixmap": str(ICON_PATH),
            "MenuText": "Инфо о run",
            "ToolTip": "Информация о загруженном прогоне и артефактах (Реализация: S.69 C3)",
        }

    def Activated(self) -> None:
        pass

    def IsActive(self) -> bool:
        return False
