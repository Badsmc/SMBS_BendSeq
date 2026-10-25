"""
calculate.py - Command stub for displaying bend annotations on flat pattern (B1).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

ICON_PATH: Path = Path(__file__).resolve().parent.parent / "resources" / "icons" / "calculate.svg"


class CalculateCommand:
    """Command to compute and display bend sequence annotations on flat pattern."""

    def GetResources(self) -> Dict[str, Any]:
        return {
            "Pixmap": str(ICON_PATH),
            "MenuText": "Расчёт",
            "ToolTip": "Отобразить последовательность гибки на раскладке (Реализация: S.69 C4)",
        }

    def Activated(self) -> None:
        pass

    def IsActive(self) -> bool:
        return False
