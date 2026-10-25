"""
verify.py - Command stub for displaying verification status dialog.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

ICON_PATH: Path = Path(__file__).resolve().parent.parent / "resources" / "icons" / "verify.svg"


class VerifyCommand:
    """Command to inspect verification results and eligibility."""

    def GetResources(self) -> Dict[str, Any]:
        return {
            "Pixmap": str(ICON_PATH),
            "MenuText": "Верификация",
            "ToolTip": "Просмотр статуса верификации и пригодности к производству (Реализация: S.69 C3)",
        }

    def Activated(self) -> None:
        pass

    def IsActive(self) -> bool:
        return False
