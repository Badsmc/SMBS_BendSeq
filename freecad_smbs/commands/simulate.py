"""simulate.py - Command: bend sequence playback (F.2 + F.3a, S.70)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from freecad_smbs.commands.open_run import get_current_run
from freecad_smbs.integration import smbs_bridge
from freecad_smbs.ui.dialogs import show_error

ICON_PATH = Path(__file__).resolve().parent.parent / "resources" / "icons" / "simulate.svg"


class SimulateCommand:
    def GetResources(self) -> Dict[str, Any]:
        return {
            "Pixmap": str(ICON_PATH),
            "MenuText": "Симуляция",
            "ToolTip": "Воспроизведение процесса гибки (F.2/F.3/P1c; двунаправленное)",
        }

    def Activated(self) -> None:
        artifacts = get_current_run()
        if artifacts is None:
            show_error("SMBS: Симуляция", "Сначала откройте директорию запуска (SMBS → Открыть run…).")
            return
        try:
            player = smbs_bridge.get_player(artifacts)
        except Exception as e:
            show_error("SMBS: Симуляция", f"Не удалось построить плеер:\n{e}")
            return
        try:
            from freecad_smbs.ui.animation_dialog import AnimationPlayerDialog

            dialog = AnimationPlayerDialog(artifacts, player)
            dialog.exec()
        except Exception as e:
            show_error("SMBS: Симуляция", f"Не удалось открыть диалог воспроизведения:\n{e}")

    def IsActive(self) -> bool:
        return get_current_run() is not None
