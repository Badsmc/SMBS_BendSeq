"""
manual_sequence_panel.py — FreeCAD Task Panel for HandMod.

Окно:
  - список выбранных линий гиба (порядок = порядок гибки)
  - кнопки ↑ ↓, Удалить, Очистить, Добавить из Selection
  - кнопка «Рассчитать» → расчёт только ручной последовательности
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

try:
    from PySide import QtCore, QtGui  # type: ignore
    from PySide.QtGui import QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QPushButton, QLabel, QMessageBox
except ImportError:
    try:
        from PySide2 import QtCore, QtGui  # type: ignore
        from PySide2.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QPushButton, QLabel, QMessageBox,
        )
    except ImportError:
        QtCore = QtGui = None  # type: ignore
        QDialog = object  # type: ignore


class ManualSequencePanel:
    """
    FreeCAD TaskDialog-style panel.
    Accepts initial list of (obj, edge) from selection.
    """

    def __init__(self, initial_edges: Optional[List[Tuple[Any, Any]]] = None):
        self.form = None
        self._edges: List[Tuple[Any, Any]] = list(initial_edges or [])
        self._build_ui()

    def _build_ui(self) -> None:
        if QtCore is None:
            return

        self.form = QDialog()
        self.form.setWindowTitle("SMBS HandMod — Ручная последовательность")
        layout = QVBoxLayout(self.form)

        layout.addWidget(QLabel(
            "Выберите линии гиба на развёртке (в нужном порядке).\n"
            "Порядок в списке = порядок гибки."
        ))

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)
        self._refresh_list()

        btn_row = QHBoxLayout()
        self.btn_add = QPushButton("Добавить из Selection")
        self.btn_up = QPushButton("↑")
        self.btn_down = QPushButton("↓")
        self.btn_del = QPushButton("Удалить")
        self.btn_clear = QPushButton("Очистить")
        for b in (self.btn_add, self.btn_up, self.btn_down, self.btn_del, self.btn_clear):
            btn_row.addWidget(b)
        layout.addLayout(btn_row)

        self.btn_calc = QPushButton("Рассчитать")
        self.btn_calc.setDefault(True)
        layout.addWidget(self.btn_calc)

        self.status = QLabel("Готово к выбору линий.")
        layout.addWidget(self.status)

        self.btn_add.clicked.connect(self._on_add)
        self.btn_up.clicked.connect(self._on_up)
        self.btn_down.clicked.connect(self._on_down)
        self.btn_del.clicked.connect(self._on_del)
        self.btn_clear.clicked.connect(self._on_clear)
        self.btn_calc.clicked.connect(self._on_calculate)

    def _refresh_list(self) -> None:
        if not hasattr(self, "list_widget") or self.list_widget is None:
            return
        self.list_widget.clear()
        for i, (obj, edge) in enumerate(self._edges):
            name = getattr(obj, "Label", getattr(obj, "Name", "?"))
            self.list_widget.addItem(f"{i+1}. {name} / Edge")

    def _on_add(self) -> None:
        try:
            import FreeCADGui as Gui
            sel = Gui.Selection.getSelectionEx()
            added = 0
            for s in sel:
                for sub in s.SubObjects:
                    if hasattr(sub, "Curve") or getattr(sub, "ShapeType", "") == "Edge":
                        self._edges.append((s.Object, sub))
                        added += 1
            self._refresh_list()
            self.status.setText(f"Добавлено {added} линий. Всего: {len(self._edges)}")
        except Exception as e:
            self.status.setText(f"Ошибка Selection: {e}")

    def _on_up(self) -> None:
        row = self.list_widget.currentRow()
        if row > 0:
            self._edges[row - 1], self._edges[row] = self._edges[row], self._edges[row - 1]
            self._refresh_list()
            self.list_widget.setCurrentRow(row - 1)

    def _on_down(self) -> None:
        row = self.list_widget.currentRow()
        if 0 <= row < len(self._edges) - 1:
            self._edges[row + 1], self._edges[row] = self._edges[row], self._edges[row + 1]
            self._refresh_list()
            self.list_widget.setCurrentRow(row + 1)

    def _on_del(self) -> None:
        row = self.list_widget.currentRow()
        if 0 <= row < len(self._edges):
            del self._edges[row]
            self._refresh_list()

    def _on_clear(self) -> None:
        self._edges.clear()
        self._refresh_list()
        self.status.setText("Список очищен.")

    def _on_calculate(self) -> None:
        if not self._edges:
            self.status.setText("Сначала добавьте линии гиба.")
            return

        self.status.setText(
            f"Расчёт ручной последовательности из {len(self._edges)} шагов…\n"
            "(Worker в разработке — см. docs/HANDMOD_DESIGN.md)"
        )
        # TODO: вызвать ManualSequenceWorker
        # from freecad_smbs.integration.manual_bridge import run_manual_sequence
        # result = run_manual_sequence(self._edges)
        # → annotations, verify, dialog

        try:
            QMessageBox.information(
                self.form,
                "SMBS HandMod",
                f"Получен порядок из {len(self._edges)} линий.\n\n"
                "Полный расчёт (валидация + backgauge + аннотации) "
                "будет подключён в следующем коммите.\n\n"
                "См. docs/HANDMOD_DESIGN.md",
            )
        except Exception:
            pass

    # FreeCAD TaskDialog protocol
    def getStandardButtons(self):
        return 0  # no standard OK/Cancel — own buttons

    def accept(self):
        pass

    def reject(self):
        try:
            import FreeCADGui as Gui
            Gui.Control.closeDialog()
        except Exception:
            pass
