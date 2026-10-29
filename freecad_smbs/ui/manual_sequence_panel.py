"""
manual_sequence_panel.py — FreeCAD Task Panel for HandMod (Phase 3).

Features:
- SelectionObserver subscribing to FreeCAD edge clicks on Unfold.Shape with recursion suppression (P1).
- Filters clicks to match internal/valid bend edges, ignoring faces/vertices or non-bend edges (P1, P3).
- Immutable RawBendLineSnapshot stored in BendSelection (Decision B2-A).
- Re-clicking an already selected bend toggles/removes it from the sequence.
- Controls: ↑, ↓, Удалить, Очистить, Проверить / Рассчитать, Отмена (P4).
- Stale detection & document recompute invalidation (P2).
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from PySide import QtCore, QtGui  # type: ignore
    from PySide.QtGui import (
        QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QPushButton, QLabel, QMessageBox
    )
except ImportError:
    try:
        from PySide2 import QtCore, QtGui  # type: ignore
        from PySide2.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QPushButton, QLabel, QMessageBox,
        )
    except ImportError:
        QtCore = QtGui = None  # type: ignore
        QDialog = object  # type: ignore

from freecad_smbs.manual.geometry_extract import (
    RawBendLine,
    extract_bend_lines_3d,
)


class RawBendLineSnapshot:
    """Immutable snapshot of edge geometry captured at click time (Decision B2-A)."""

    def __init__(
        self,
        midpoint: Tuple[float, float, float],
        direction: Tuple[float, float, float],
        normal: Tuple[float, float, float],
        radius_mm: float,
        length_mm: float,
        adjacent_panel_areas: Tuple[float, float] = (0.0, 0.0),
        source_edge_ref: str = "",
    ):
        self.midpoint = tuple(midpoint)
        self.direction = tuple(direction)
        self.normal = tuple(normal)
        self.radius_mm = float(radius_mm)
        self.length_mm = float(length_mm)
        self.adjacent_panel_areas = tuple(adjacent_panel_areas)
        self.source_edge_ref = str(source_edge_ref)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "midpoint": list(self.midpoint),
            "direction": list(self.direction),
            "normal": list(self.normal),
            "radius_mm": self.radius_mm,
            "length_mm": self.length_mm,
            "adjacent_panel_areas": list(self.adjacent_panel_areas),
            "source_edge_ref": self.source_edge_ref,
        }


class BendSelectionItem:
    """Represents a single user selection in the manual sequence."""

    def __init__(
        self,
        order: int,
        edge_ref: str,
        object_name: str,
        raw_snapshot: RawBendLineSnapshot,
        bend_id: Optional[str] = None,
    ):
        self.order = order
        self.edge_ref = edge_ref
        self.object_name = object_name
        self.raw_snapshot = raw_snapshot
        self.bend_id = bend_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order": self.order,
            "edge_ref": self.edge_ref,
            "object_name": self.object_name,
            "raw_snapshot": self.raw_snapshot.to_dict(),
            "bend_id": self.bend_id,
        }


class ManualSequencePanel:
    """
    TaskDialog-style interactive panel for HandMod manual bend selection.
    Subscribes to FreeCAD Gui.Selection via SelectionObserver.
    """

    def __init__(self, initial_doc: Optional[Any] = None):
        self.form = None
        self._selections: List[BendSelectionItem] = []
        self._suppress_observer: bool = False
        self._is_running: bool = False
        self._cancel_requested: bool = False
        self._stale: bool = False
        self._last_unfold_hash: Optional[int] = None
        self._observer_attached: bool = False
        self._doc = initial_doc

        self._build_ui()
        self._attach_observer()

    def _build_ui(self) -> None:
        if QtCore is None:
            return

        self.form = QDialog()
        self.form.setWindowTitle("SMBS HandMod — Ручная последовательность")
        self.form.setMinimumWidth(380)
        layout = QVBoxLayout(self.form)

        info_lbl = QLabel(
            "<b>Ручной выбор линий гиба (HandMod)</b><br>"
            "1. Кликайте по линиям гиба на развёртке (Unfold).<br>"
            "2. Порядок в списке = порядок гибки.<br>"
            "3. Повторный клик по выбранной линии удаляет её."
        )
        info_lbl.setWordWrap(True)
        layout.addWidget(info_lbl)

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)

        btn_row = QHBoxLayout()
        self.btn_up = QPushButton("↑")
        self.btn_down = QPushButton("↓")
        self.btn_del = QPushButton("Удалить")
        self.btn_clear = QPushButton("Очистить")
        for b in (self.btn_up, self.btn_down, self.btn_del, self.btn_clear):
            btn_row.addWidget(b)
        layout.addLayout(btn_row)

        action_row = QHBoxLayout()
        self.btn_calc = QPushButton("Проверить")
        self.btn_calc.setDefault(True)
        self.btn_cancel = QPushButton("Отмена")
        self.btn_cancel.setEnabled(False)
        action_row.addWidget(self.btn_calc)
        action_row.addWidget(self.btn_cancel)
        layout.addLayout(action_row)

        self.status = QLabel("Готово. Выбирайте линии гиба на развёртке.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        # Wire UI signals
        self.btn_up.clicked.connect(self._on_up)
        self.btn_down.clicked.connect(self._on_down)
        self.btn_del.clicked.connect(self._on_del)
        self.btn_clear.clicked.connect(self._on_clear)
        self.btn_calc.clicked.connect(self._on_calculate)
        self.btn_cancel.clicked.connect(self._on_cancel)

    def _attach_observer(self) -> None:
        try:
            import FreeCADGui as Gui
            if not self._observer_attached:
                Gui.Selection.addObserver(self)
                self._observer_attached = True
        except Exception:
            pass

    def _detach_observer(self) -> None:
        try:
            import FreeCADGui as Gui
            if self._observer_attached:
                Gui.Selection.removeObserver(self)
                self._observer_attached = False
        except Exception:
            pass

    # FreeCAD SelectionObserver protocol
    def addSelection(self, doc_name: str, obj_name: str, sub_name: str, pnt: Any) -> None:
        """Callback when an element is clicked in 3D viewer."""
        if self._suppress_observer or self._is_running:
            return

        # P1: Filter sub_name to accept only Edge selections
        if not sub_name or not sub_name.startswith("Edge"):
            return

        try:
            import FreeCAD as App
            doc = App.getDocument(doc_name) if doc_name else App.ActiveDocument
            if doc is None:
                return
            obj = doc.getObject(obj_name)
            if obj is None or not hasattr(obj, "Shape"):
                return

            # Check if selection is on Unfold shape (or valid sheet object)
            edge = obj.Shape.getElement(sub_name)
            if edge is None or getattr(edge, "ShapeType", "") != "Edge":
                return

            self._process_edge_selection(doc, obj, sub_name, edge)
        except Exception as e:
            self._set_status(f"Ошибка выбора: {e}")

    def _process_edge_selection(self, doc: Any, obj: Any, sub_name: str, edge: Any) -> None:
        """Processes clicked edge, checks if bend line, toggles if already selected."""
        # 1. Candidate extraction on the object
        raw_bends = extract_bend_lines_3d(obj.Shape)
        if not raw_bends:
            # Fallback check on Body if clicked on part
            for o in doc.Objects:
                if "body" in o.Name.lower() or "unfold" in o.Name.lower():
                    raw_bends.extend(extract_bend_lines_3d(o.Shape))

        # 2. Check if edge matches a bend line (tolerance 1.0mm)
        mid = edge.CenterOfMass
        midpoint = (float(mid.x), float(mid.y), float(mid.z))

        matched_raw: Optional[RawBendLine] = None
        min_dist = float("inf")
        for rb in raw_bends:
            d = math.sqrt(sum((midpoint[k] - rb.midpoint[k])**2 for k in range(3)))
            if d < min_dist:
                min_dist = d
                matched_raw = rb

        # P3: If not a bend line (distance > 1.0mm), ignore with short toast
        if matched_raw is None or min_dist > 1.5:
            self._set_status("⚠️ Not a bend line (выберите линию гиба на развёртке)")
            return

        # 3. Check if edge is already in sequence -> Toggle (remove it)
        existing_idx = -1
        for idx, item in enumerate(self._selections):
            d_prev = math.sqrt(sum((midpoint[k] - item.raw_snapshot.midpoint[k])**2 for k in range(3)))
            if d_prev < 1.0:
                existing_idx = idx
                break

        if existing_idx >= 0:
            removed = self._selections.pop(existing_idx)
            self._reindex_selections()
            self._refresh_list()
            self._set_status(f"Удалена линия гиба (ранее шаг {removed.order})")
            return

        # 4. Add new BendSelection with immutable RawBendLineSnapshot
        snapshot = RawBendLineSnapshot(
            midpoint=midpoint,
            direction=matched_raw.direction,
            normal=matched_raw.normal,
            radius_mm=matched_raw.radius_mm,
            length_mm=matched_raw.length_mm,
            adjacent_panel_areas=matched_raw.adjacent_panel_areas,
            source_edge_ref=sub_name,
        )

        new_order = len(self._selections) + 1
        item = BendSelectionItem(
            order=new_order,
            edge_ref=sub_name,
            object_name=obj.Name,
            raw_snapshot=snapshot,
            bend_id=None,
        )
        self._selections.append(item)
        self._refresh_list()
        self._set_status(f"Добавлен шаг {new_order}: {sub_name} (L={matched_raw.length_mm:.1f}мм)")

    def _reindex_selections(self) -> None:
        for idx, sel in enumerate(self._selections):
            sel.order = idx + 1

    def _refresh_list(self) -> None:
        if not hasattr(self, "list_widget") or self.list_widget is None:
            return
        self.list_widget.clear()
        for sel in self._selections:
            bid_label = f" -> {sel.bend_id}" if sel.bend_id else ""
            txt = (
                f"{sel.order}. {sel.object_name}:{sel.edge_ref} "
                f"(L={sel.raw_snapshot.length_mm:.0f}мм){bid_label}"
            )
            self.list_widget.addItem(txt)

    def _set_status(self, text: str) -> None:
        if hasattr(self, "status") and self.status is not None:
            self.status.setText(text)

    # UI actions
    def _on_up(self) -> None:
        row = self.list_widget.currentRow()
        if row > 0:
            self._selections[row - 1], self._selections[row] = self._selections[row], self._selections[row - 1]
            self._reindex_selections()
            self._refresh_list()
            self.list_widget.setCurrentRow(row - 1)

    def _on_down(self) -> None:
        row = self.list_widget.currentRow()
        if 0 <= row < len(self._selections) - 1:
            self._selections[row + 1], self._selections[row] = self._selections[row], self._selections[row + 1]
            self._reindex_selections()
            self._refresh_list()
            self.list_widget.setCurrentRow(row + 1)

    def _on_del(self) -> None:
        row = self.list_widget.currentRow()
        if 0 <= row < len(self._selections):
            del self._selections[row]
            self._reindex_selections()
            self._refresh_list()
            self._set_status("Выбранный шаг удалён.")

    def _on_clear(self) -> None:
        self._selections.clear()
        self._refresh_list()
        self._set_status("Последовательность очищена.")

    def _on_cancel(self) -> None:
        """P4: Cancel ongoing verification."""
        if self._is_running:
            self._cancel_requested = True
            self._set_status("Отмена расчёта по запросу пользователя...")

    def _on_calculate(self) -> None:
        if not self._selections:
            self._set_status("Сначала выберите линии гиба на развёртке.")
            return

        if self._stale:
            self._set_status("⚠️ Модель изменилась (recompute). Выберите линии заново.")
            return

        self._is_running = True
        self._cancel_requested = False
        self.btn_calc.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self._set_status(f"Проверка {len(self._selections)} шагов... (ManualSequenceWorker)")

        # Trigger worker or callback
        try:
            # Phase 4 hook
            pass
        finally:
            self._is_running = False
            self.btn_calc.setEnabled(True)
            self.btn_cancel.setEnabled(False)

    def mark_stale(self, new_shape: Optional[Any] = None) -> bool:
        """
        P2/Y5: Evaluates whether topological shape change occurred before marking stale.
        Prevents false invalidation on recompute triggered by UI annotations or camera actions.
        Returns True if panel was actually marked stale.
        """
        if new_shape is not None and hasattr(new_shape, "hashCode"):
            current_hash = new_shape.hashCode()
            if self._last_unfold_hash is not None and current_hash == self._last_unfold_hash:
                # Shape is topologically identical; do not invalidate
                return False
            self._last_unfold_hash = current_hash

        self._stale = True
        for sel in self._selections:
            sel.bend_id = None
        self._set_status("⚠️ Модель была перестроена. Нажмите Очистить и выберите заново.")
        return True

    def get_selections(self) -> List[BendSelectionItem]:
        return list(self._selections)

    # FreeCAD TaskDialog protocol
    def getStandardButtons(self) -> int:
        return 0

    def reject(self) -> None:
        self._detach_observer()
        try:
            import FreeCADGui as Gui
            Gui.Control.closeDialog()
        except Exception:
            pass
