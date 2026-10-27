"""
run_active.py - Command to calculate bend sequence for the active FreeCAD document (R1, R2).

Main entry point for user interaction:
ActiveDocument (.FCStd) -> [Рассчитать последовательность] -> run created ->
annotations rendered on flat pattern -> result dialog -> Simulate/Verify ready.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

from freecad_smbs.commands.open_run import set_current_run
from freecad_smbs.integration import smbs_bridge
from freecad_smbs.ui.annotations import render_flat_pattern_annotations
from freecad_smbs.ui.dialogs import (
    ProgressContext,
    show_error,
    show_pipeline_result,
)

ICON_PATH: Path = Path(__file__).resolve().parent.parent / "resources" / "icons" / "run_active.svg"


def _get_pref_string(name: str, default: str = "") -> str:
    try:
        import FreeCAD
        if FreeCAD is not None and hasattr(FreeCAD, "ParamGet"):
            prefs = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/SMBS")
            return prefs.GetString(name, default)
    except Exception:
        pass
    return default


def _get_pref_bool(name: str, default: bool = False) -> bool:
    try:
        import FreeCAD
        if FreeCAD is not None and hasattr(FreeCAD, "ParamGet"):
            prefs = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/SMBS")
            return prefs.GetBool(name, default)
    except Exception:
        pass
    return default


class RunActiveCommand:
    """Command to calculate bend sequence for the active FreeCAD document."""

    def GetResources(self) -> Dict[str, Any]:
        return {
            "Pixmap": str(ICON_PATH),
            "MenuText": "Рассчитать последовательность",
            "ToolTip": "Запустить планирование для активной детали и показать порядок гибов",
        }

    def IsActive(self) -> bool:
        """Active if FreeCAD is present (validated on click with user-friendly messages)."""
        return True

    def Activated(self) -> None:
        try:
            import FreeCAD as App
        except ImportError:
            App = None

        if App is None or not hasattr(App, "ActiveDocument") or App.ActiveDocument is None:
            show_error(
                "SMBS: Нет активной детали",
                "Откройте деталь листового металла (.FCStd) перед запуском расчёта.",
            )
            return

        doc = App.ActiveDocument
        file_name = getattr(doc, "FileName", "")
        if not file_name or not Path(file_name).is_file():
            show_error(
                "SMBS: Документ не сохранён",
                "Сохраните документ на диск (Файл → Сохранить / Сохранить как) перед запуском расчёта.",
            )
            return

        # Warn if document has uncomputed changes
        if getattr(doc, "MustExecute", False):
            if hasattr(App, "Console"):
                App.Console.PrintWarning(
                    "SMBS: Документ содержит непересчитанные изменения. Рекомендуется пересчитать (Ctrl+R).\n"
                )

        fcstd_path = Path(file_name)

        # Tooling preferences
        use_tooling = _get_pref_bool("UseTooling", False)
        lib_path = _get_pref_string("ToolingLibraryPath", "")
        punch_id = _get_pref_string("PunchId", "")
        die_id = _get_pref_string("DieId", "")

        punch_arg = punch_id if (use_tooling and punch_id) else None
        die_arg = die_id if (use_tooling and die_id) else None
        lib_arg = Path(lib_path) if (use_tooling and lib_path) else None

        # Determine policy: if real tooling is provided, PRODUCTION; otherwise default to EXPLORATORY
        if punch_arg and die_arg:
            default_policy = "PRODUCTION"
        else:
            default_policy = "EXPLORATORY"
        policy = _get_pref_string("UnknownPolicy", default_policy)

        if hasattr(App, "Console"):
            App.Console.PrintMessage(f"SMBS: Запуск расчёта последовательности для '{fcstd_path.name}' (политика: {policy})\n")
            if punch_arg and die_arg:
                App.Console.PrintMessage(f"SMBS: Оснастка: {punch_arg} / {die_arg} (библиотека: {lib_arg})\n")
            else:
                App.Console.PrintMessage("SMBS: Оснастка не задана — геометрический расчёт (EXPLORATORY)\n")

        # Execute pipeline with progress feedback
        with ProgressContext("SMBS: Расчёт", f"Планирование последовательности для {fcstd_path.name}…") as prog:
            prog.update_text("Развёртка детали и канонический анализ…")
            result = smbs_bridge.run_pipeline_on_fcstd(
                fcstd_path=fcstd_path,
                library_path=lib_arg,
                punch_id=punch_arg,
                die_id=die_arg,
                unknown_policy=policy,
            )
            prog.update_text("Обработка результатов…")

        if hasattr(App, "Console"):
            App.Console.PrintMessage(
                f"SMBS: Расчёт завершён со статусом {result.planning_status} "
                f"(verification: {result.verification_status}, eligible: {result.production_eligible}).\n"
            )
            if result.run_dir and Path(result.run_dir).is_dir():
                App.Console.PrintMessage(f"SMBS: Директория запуска: {result.run_dir}\n")
            if result.error_message:
                App.Console.PrintError(f"SMBS: Ошибка: {result.error_message}\n")

        # Bind run state
        if result.artifacts is not None:
            set_current_run(result.artifacts)

        # On SUCCESS or PARTIAL (valid sequence found), render annotations automatically
        if result.planning_status in ("SUCCESS", "PARTIAL") and result.artifacts is not None and result.candidate_sequence:
            try:
                annotations = smbs_bridge.get_annotations(result.artifacts)
                if annotations:
                    render_flat_pattern_annotations(doc, result.artifacts, annotations)
                    if hasattr(App, "Console"):
                        App.Console.PrintMessage(f"SMBS: На раскладку нанесено {len(annotations)} меток гибов.\n")
            except Exception as e:
                if hasattr(App, "Console"):
                    App.Console.PrintWarning(f"SMBS: Не удалось отрисовать 3D-аннотации: {e}\n")

        # Show result dialog
        show_pipeline_result(result)
