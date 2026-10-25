"""
verify.py - Command to inspect verification results and eligibility.

Honest status presentation (Q5 / inv #1, #2, #10):
- VERIFIED (green): production_eligible=true
- VERIFICATION_FAILED (orange): production_eligible=false
- UNVERIFIED (gray): 09_verification_result.json is None
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from freecad_smbs.commands.open_run import get_current_run
from freecad_smbs.integration import smbs_bridge
from freecad_smbs.ui.badges import (
    BADGE_UNVERIFIED,
    BADGE_VERIFICATION_FAILED,
    BADGE_VERIFIED,
)
from freecad_smbs.ui.dialogs import show_error, show_info, show_unverified_warning

ICON_PATH: Path = Path(__file__).resolve().parent.parent / "resources" / "icons" / "verify.svg"


class VerifyCommand:
    """Command to inspect verification results and eligibility."""

    def GetResources(self) -> Dict[str, Any]:
        return {
            "Pixmap": str(ICON_PATH),
            "MenuText": "Верификация",
            "ToolTip": "Просмотр статуса верификации и пригодности к производству",
        }

    def Activated(self) -> None:
        artifacts = get_current_run()
        if artifacts is None:
            show_error(
                "SMBS: Верификация",
                "Сначала откройте директорию запуска (меню SMBS → Открыть run…).",
            )
            return

        verif = smbs_bridge.present_verification(artifacts)
        status = verif["status"]

        if status == BADGE_VERIFIED:
            show_info("SMBS: Верификация (VERIFIED)", verif["summary"])
        elif status == BADGE_UNVERIFIED:
            show_unverified_warning("SMBS: Верификация (UNVERIFIED)", verif["summary"])
        else:
            show_error("SMBS: Верификация (VERIFICATION_FAILED)", verif["summary"])

    def IsActive(self) -> bool:
        return get_current_run() is not None
