"""
info.py - Command to display run metadata, artifacts inventory, and known limitations.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from freecad_smbs.commands.open_run import get_current_run
from freecad_smbs.integration import smbs_bridge
from freecad_smbs.ui.dialogs import show_error, show_info

ICON_PATH: Path = Path(__file__).resolve().parent.parent / "resources" / "icons" / "info.svg"


class InfoCommand:
    """Command to view metadata, artifacts, and known limitations for loaded run."""

    def GetResources(self) -> Dict[str, Any]:
        return {
            "Pixmap": str(ICON_PATH),
            "MenuText": "Инфо о run",
            "ToolTip": "Информация о загруженном прогоне и артефактах",
        }

    def Activated(self) -> None:
        artifacts = get_current_run()
        if artifacts is None:
            show_error(
                "SMBS: Инфо о run",
                "Сначала откройте директорию запуска (меню SMBS → Открыть run…).",
            )
            return

        info = smbs_bridge.present_info(artifacts)

        lines = [
            f"Директория: {info['run_dir']}",
            "",
            "Артефакты:",
            f"  • 04_canonical_part.json: {'✓' if info['has_canonical_part'] else '✗'}",
            f"  • 07_search_result.json: {'✓' if info['has_search_result'] else '✗'}",
            f"  • 09_verification_result.json: {'✓' if info['has_verification_result'] else '✗ (UNVERIFIED)'}",
            f"  • geometry.brep: {'✓' if info['has_geometry_brep'] else '✗'}",
            f"  • flat_pattern.brep: {'✓' if info['has_flat_pattern_brep'] else '✗'}",
            "",
            f"Количество шагов гибки: {info['step_count']}",
            f"Гибы: {', '.join(info['bend_ids']) if info['bend_ids'] else 'нет'}",
            "",
            "Ограничения (Honesty):",
        ]
        for lim in info["limitations"]:
            lines.append(f"  ⚠ {lim}")

        show_info("SMBS: Инфо о run", "\n".join(lines))

    def IsActive(self) -> bool:
        return get_current_run() is not None
