"""
open_run.py - Command stub for opening a pipeline run directory.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

ICON_PATH: Path = Path(__file__).resolve().parent.parent / "resources" / "icons" / "open_run.svg"


class OpenRunCommand:
    """Command to select and load an SMBS run directory."""

    def GetResources(self) -> Dict[str, Any]:
        return {
            "Pixmap": str(ICON_PATH),
            "MenuText": "Открыть run…",
            "ToolTip": "Открыть директорию запуска SMBS (Реализация: S.69 C3)",
        }

    def Activated(self) -> None:
        pass

    def IsActive(self) -> bool:
        return False
