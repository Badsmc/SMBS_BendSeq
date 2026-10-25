"""
simulate.py - Command stub for simulation playback (deferred to S.70).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

ICON_PATH: Path = Path(__file__).resolve().parent.parent / "resources" / "icons" / "simulate.svg"


class SimulateCommand:
    """Command to launch bend sequence animation player (deferred to S.70)."""

    def GetResources(self) -> Dict[str, Any]:
        return {
            "Pixmap": str(ICON_PATH),
            "MenuText": "Симуляция",
            "ToolTip": "Запуск симуляции процесса гибки (Реализация: S.70)",
        }

    def Activated(self) -> None:
        pass

    def IsActive(self) -> bool:
        return False
