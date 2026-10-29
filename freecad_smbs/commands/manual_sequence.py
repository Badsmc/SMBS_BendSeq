"""
manual_sequence.py — HandMod: ручной выбор линий гиба и расчёт фиксированной последовательности.

Пользователь:
  1. Делает Unfold детали (SheetMetal).
  2. Выбирает рёбра/линии гиба на развёртке в нужном порядке.
  3. Открывает эту команду → линии попадают в список.
  4. Нажимает «Рассчитать» → аддон валидирует и считает параметры
     ТОЛЬКО для заданного вручную порядка (без auto-search).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

ICON_PATH: Path = Path(__file__).resolve().parent.parent / "resources" / "icons" / "calculate.svg"


class ManualSequenceCommand:
    """Command: manual bend line selection + calculate fixed sequence."""

    def GetResources(self) -> Dict[str, Any]:
        return {
            "Pixmap": str(ICON_PATH),
            "MenuText": "Ручная последовательность",
            "ToolTip": (
                "Выбрать линии гиба на развёртке вручную и рассчитать "
                "последовательность только по заданному порядку (HandMod)"
            ),
        }

    def IsActive(self) -> bool:
        return True

    def Activated(self) -> None:
        try:
            import FreeCAD as App
            import FreeCADGui as Gui
        except ImportError:
            App = None
            Gui = None

        if App is None or App.ActiveDocument is None:
            from freecad_smbs.ui.dialogs import show_error
            show_error(
                "SMBS HandMod",
                "Откройте деталь и сделайте Unfold перед ручным выбором линий гиба.",
            )
            return

        # Collect current selection (edges preferred)
        selected_edges: List[Any] = []
        try:
            sel = Gui.Selection.getSelectionEx()
            for s in sel:
                for sub in s.SubObjects:
                    # Edge or line-like
                    if hasattr(sub, "Curve") or sub.ShapeType == "Edge":
                        selected_edges.append((s.Object, sub))
        except Exception:
            pass

        # Open task panel / dialog
        try:
            from freecad_smbs.ui.manual_sequence_panel import ManualSequencePanel
            panel = ManualSequencePanel(selected_edges)
            Gui.Control.showDialog(panel)
        except Exception as e:
            from freecad_smbs.ui.dialogs import show_error
            show_error(
                "SMBS HandMod",
                f"Не удалось открыть панель ручного выбора:\n{e}\n\n"
                "(UI ещё в разработке — см. docs/HANDMOD_DESIGN.md)",
            )
