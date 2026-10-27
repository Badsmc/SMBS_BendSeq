"""
calculate.py - Command to compute and display bend sequence annotations on flat pattern (B1).

Visualizes candidate_sequence from loaded run using FreeCAD 3D AnnotationLabels.
Does NOT re-solve or modify pipeline contracts.
Honest marking: displays UNVERIFIED / VERIFICATION_FAILED badge per Q5.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from freecad_smbs.commands.open_run import get_current_run
from freecad_smbs.integration import smbs_bridge
from freecad_smbs.ui.annotations import render_flat_pattern_annotations
from freecad_smbs.ui.badges import BADGE_VERIFIED
from freecad_smbs.ui.dialogs import show_error, show_info, show_unverified_warning
from smbs_core.viewer.flat_annotations import FlatAnnotationsError

ICON_PATH: Path = Path(__file__).resolve().parent.parent / "resources" / "icons" / "calculate.svg"


class CalculateCommand:
    """Command to compute and display bend sequence annotations on flat pattern."""

    def GetResources(self) -> Dict[str, Any]:
        return {
            "Pixmap": str(ICON_PATH),
            "MenuText": "Расчёт",
            "ToolTip": "Отобразить / обновить последовательность гибки на раскладке для текущего run",
        }

    def Activated(self) -> None:
        artifacts = get_current_run()
        if artifacts is None:
            show_error(
                "SMBS: Расчёт",
                "Нет загруженного run. Сначала рассчитайте последовательность (кнопка «Рассчитать последовательность») или откройте существующий run (меню SMBS → Открыть run…).",
            )
            return

        try:
            annotations = smbs_bridge.get_annotations(artifacts)
        except FlatAnnotationsError as e:
            show_error("SMBS: Ошибка аннотаций", f"Не удалось вычислить аннотации раскладки:\n\n{e}")
            return
        except Exception as e:
            show_error("SMBS: Ошибка графов/аннотаций", f"Ошибка при построении аннотаций:\n{e}")
            return

        if not annotations:
            show_info("SMBS: Расчёт", "Последовательность пуста (нет шагов гибки для отображения).")
            return

        try:
            import FreeCAD as App
            doc = App.ActiveDocument
            if doc is None:
                doc = App.newDocument("SMBS_FlatPattern")
        except Exception as e:
            show_error("SMBS: Документ FreeCAD", f"Не удалось получить или создать активный документ:\n{e}")
            return

        try:
            created_labels = render_flat_pattern_annotations(doc, artifacts, annotations)
        except Exception as e:
            show_error("SMBS: Ошибка рендеринга", f"Не удалось отобразить раскладку с аннотациями:\n{e}")
            return

        verif = smbs_bridge.present_verification(artifacts)
        status = verif["status"]
        steps_str = ", ".join(str(a.step) for a in annotations)

        if status == BADGE_VERIFIED:
            show_info(
                "SMBS: Расчёт (VERIFIED)",
                f"Успешно добавлено {len(created_labels)} аннотаций на раскладку.\n\n"
                f"Статус: VERIFIED (production_eligible=true)\n"
                f"Шаги: {steps_str}",
            )
        else:
            show_unverified_warning(
                f"SMBS: Расчёт ({status})",
                f"Добавлено {len(created_labels)} аннотаций на раскладку.\n\n"
                f"Внимание: статус {status}.\n"
                f"Последовательность НЕ подтверждена как готовая к производству (Q5).\n"
                f"Шаги: {steps_str}",
            )

    def IsActive(self) -> bool:
        return get_current_run() is not None
