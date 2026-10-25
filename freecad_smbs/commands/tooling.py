"""
tooling.py - Command stub for tooling library manager (deferred to S.71).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

ICON_PATH: Path = Path(__file__).resolve().parent.parent / "resources" / "icons" / "tooling.svg"


class ToolingCommand:
    """Command to open tooling library manager dialog (deferred to S.71)."""

    def GetResources(self) -> Dict[str, Any]:
        return {
            "Pixmap": str(ICON_PATH),
            "MenuText": "Оснастка",
            "ToolTip": "Редактор и библиотека оснастки (Реализация: S.71)",
        }

    def Activated(self) -> None:
        pass

    def IsActive(self) -> bool:
        return False
