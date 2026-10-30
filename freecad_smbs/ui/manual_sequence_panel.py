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
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore
    from PySide6.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QPushButton, QLabel, QMessageBox, QProgressBar, QComboBox
    )
except ImportError:
    try:
        from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore
        from PySide2.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QPushButton, QLabel, QMessageBox, QProgressBar, QComboBox
        )
    except ImportError:
        try:
            from PySide import QtCore, QtGui  # type: ignore
            from PySide.QtGui import (
                QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QPushButton, QLabel, QMessageBox, QProgressBar, QComboBox
            )
        except ImportError:
            QtCore = QtGui = QtWidgets = None  # type: ignore
            QDialog = object  # type: ignore
            QProgressBar = object  # type: ignore
            QComboBox = object  # type: ignore


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


from freecad_smbs.manual.geometry_extract import (
    RawBendLine,
    extract_bend_lines_3d,
)



if QtCore is not None and hasattr(QtCore, "QThread"):
    class ManualWorkerThread(QtCore.QThread):
        """
        Worker thread for executing ManualSequenceWorker off the main Qt UI thread (O2).
        Emits step_progress(step_idx, total_steps, bend_id) and finished_result(result).

        To ensure absolute stability and prevent macOS GUI crashes (EXC_BAD_ACCESS in TechDraw/Qt
        when App.openDocument is called from secondary threads while GUI has document open),
        the worker executes the calculation pipeline in an isolated headless python subprocess.
        """
        step_progress = QtCore.Signal(int, int, str)
        finished_result = QtCore.Signal(object)

        def __init__(
            self,
            fcstd_path: Any,
            manual_input: Any,
            out_runs_dir: Optional[Any] = None,
            parent: Optional[Any] = None,
        ) -> None:
            super().__init__(parent)
            self.fcstd_path = fcstd_path
            self.manual_input = manual_input
            self.out_runs_dir = out_runs_dir
            self.cancel_token = threading.Event()
            self.manual_input.cancel_token = self.cancel_token
            self._proc: Optional[Any] = None

        def run(self) -> None:
            import os
            import sys
            import tempfile
            import pickle
            import subprocess
            from pathlib import Path
            from freecad_smbs.manual.manual_worker import ManualSequenceResult

            # Check if FreeCAD GUI is active; if active, spawn isolated headless process
            try:
                import FreeCAD as App
                is_gui = getattr(App, "GuiUp", 0) == 1
            except Exception:
                is_gui = False

            def _on_step_progress(step_idx: int, total_steps: int, bend_id: str) -> None:
                self.step_progress.emit(step_idx, total_steps, bend_id)

            if not is_gui:
                # Direct in-process execution for headless tests & CLI
                from freecad_smbs.manual.manual_runner import run_manual_sequence_on_fcstd
                try:
                    res = run_manual_sequence_on_fcstd(
                        fcstd_path=self.fcstd_path,
                        manual_input=self.manual_input,
                        out_runs_dir=self.out_runs_dir,
                        progress_callback=_on_step_progress,
                    )
                except Exception as e:
                    res = ManualSequenceResult(
                        ok=False,
                        run_dir=Path(self.out_runs_dir or "."),
                        part_id="unknown",
                        planning_status="ERROR",
                        error_message=str(e),
                    )
                self.finished_result.emit(res)
                return

            # Isolated subprocess execution for FreeCAD GUI
            temp_payload = None
            temp_output = None
            try:
                # Prepare picklable copy of manual_input without unpicklable threading.Event
                from dataclasses import replace
                picklable_input = replace(self.manual_input, cancel_token=None)

                with tempfile.NamedTemporaryFile(suffix=".smbs_in.pkl", delete=False) as f_in:
                    temp_payload = f_in.name
                    pickle.dump({
                        "fcstd_path": self.fcstd_path,
                        "manual_input": picklable_input,
                        "out_runs_dir": self.out_runs_dir,
                    }, f_in)

                with tempfile.NamedTemporaryFile(suffix=".smbs_out.pkl", delete=False) as f_out:
                    temp_output = f_out.name

                repo_root = Path(__file__).resolve().parent.parent.parent
                py_bin = sys.executable

                # Construct environment with repo root and FreeCAD lib in PYTHONPATH
                env = dict(os.environ)
                fc_lib = "/Applications/FreeCAD.app/Contents/Resources/lib"
                curr_pp = env.get("PYTHONPATH", "")
                env["PYTHONPATH"] = f"{fc_lib}:{repo_root}:{curr_pp}".rstrip(":")

                cmd = [
                    py_bin,
                    "-m",
                    "freecad_smbs.manual.manual_runner",
                    "--payload",
                    temp_payload,
                    "--output",
                    temp_output,
                ]

                self._proc = subprocess.Popen(
                    cmd,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )

                # Stream stderr for PROGRESS updates
                for line in self._proc.stderr:
                    if self.cancel_token.is_set():
                        self._proc.terminate()
                        break
                    line = line.strip()
                    if line.startswith("PROGRESS:"):
                        parts = line.split(":")
                        if len(parts) >= 4:
                            try:
                                s_idx = int(parts[1])
                                t_steps = int(parts[2])
                                b_id = parts[3]
                                self.step_progress.emit(s_idx, t_steps, b_id)
                            except ValueError:
                                pass

                self._proc.wait()

                if self.cancel_token.is_set():
                    res = ManualSequenceResult(
                        ok=False,
                        run_dir=Path(self.out_runs_dir or "."),
                        part_id="unknown",
                        planning_status="CANCELLED",
                        error_message="Operation cancelled by user",
                    )
                elif Path(temp_output).is_file() and Path(temp_output).stat().st_size > 0:
                    with open(temp_output, "rb") as f_res:
                        res = pickle.load(f_res)
                else:
                    stdout_str, stderr_str = self._proc.communicate() if self._proc.poll() is None else ("", "")
                    res = ManualSequenceResult(
                        ok=False,
                        run_dir=Path(self.out_runs_dir or "."),
                        part_id="unknown",
                        planning_status="ERROR",
                        error_message=f"Subprocess terminated unexpectedly (code {self._proc.returncode}): {stderr_str}",
                    )
            except Exception as e:
                res = ManualSequenceResult(
                    ok=False,
                    run_dir=Path(self.out_runs_dir or "."),
                    part_id="unknown",
                    planning_status="ERROR",
                    error_message=str(e),
                )
            finally:
                if temp_payload and Path(temp_payload).exists():
                    try:
                        Path(temp_payload).unlink()
                    except Exception:
                        pass
                if temp_output and Path(temp_output).exists():
                    try:
                        Path(temp_output).unlink()
                    except Exception:
                        pass
                self._proc = None

            self.finished_result.emit(res)

        def request_cancel(self) -> None:
            self.cancel_token.set()
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
else:
    class ManualWorkerThread:  # type: ignore
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.cancel_token = threading.Event()
        def request_cancel(self) -> None:
            self.cancel_token.set()


class ManualSequencePanel:
    """
    TaskDialog-style interactive panel for HandMod manual bend selection.
    Subscribes to FreeCAD Gui.Selection via SelectionObserver.
    """

    def __init__(
        self,
        initial_doc: Optional[Any] = None,
        initial_selection: Optional[List[Any]] = None,
        **kwargs: Any,
    ):
        self.form = None
        self._selections: List[BendSelectionItem] = []
        self._suppress_observer: bool = False
        self._is_running: bool = False
        self._cancel_requested: bool = False
        self._stale: bool = False
        self._last_unfold_hash: Optional[int] = None
        self._observer_attached: bool = False

        # Support both ManualSequencePanel(doc) and ManualSequencePanel(selected_edges)
        if isinstance(initial_doc, list):
            self._doc = None
            preselected = initial_doc
        else:
            self._doc = initial_doc
            preselected = initial_selection or kwargs.get("selected_edges")

        self._worker_thread: Optional[Any] = None
        self._last_result: Optional[Any] = None

        self._build_ui()
        self._attach_observer()

        if preselected:
            self._load_initial_selection(preselected)

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

        tooling_row = QHBoxLayout()
        lbl_tooling = QLabel("Оснастка:")
        self.cmb_tooling = QComboBox()
        self._populate_tooling_sets()
        tooling_row.addWidget(lbl_tooling)
        tooling_row.addWidget(self.cmb_tooling)
        layout.addLayout(tooling_row)

        policy_row = QHBoxLayout()
        lbl_policy = QLabel("Политика:")
        self.cmb_policy = QComboBox()
        self.cmb_policy.addItem("PRODUCTION (Строгая физика)", "PRODUCTION")
        self.cmb_policy.addItem("EXPLORATORY (Геометрическая)", "EXPLORATORY")
        policy_row.addWidget(lbl_policy)
        policy_row.addWidget(self.cmb_policy)
        layout.addLayout(policy_row)

        action_row = QHBoxLayout()
        self.btn_calc = QPushButton("Проверить")
        self.btn_calc.setDefault(True)
        self.btn_cancel = QPushButton("Отмена")
        self.btn_cancel.setEnabled(False)
        action_row.addWidget(self.btn_calc)
        action_row.addWidget(self.btn_cancel)
        layout.addLayout(action_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

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

    def _populate_tooling_sets(self) -> None:
        try:
            from freecad_smbs.resources.tooling_paths import TOOL_LIBRARY_ROOT
            from smbs_core.geometry_backend.io.tooling_library import ToolingLibrary
            lib = ToolingLibrary(TOOL_LIBRARY_ROOT)
            sets = lib.list_sets()
            for s in sets:
                if not s.endswith(" 2"):  # Skip duplicate artifacts if any
                    self.cmb_tooling.addItem(s, s)
        except Exception:
            pass
        if self.cmb_tooling.count() == 0:
            self.cmb_tooling.addItem("standard_90deg_v12", "standard_90deg_v12")


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
        """P4 / O2: Cancel ongoing verification."""
        if self._is_running:
            self._cancel_requested = True
            if self._worker_thread is not None and hasattr(self._worker_thread, "request_cancel"):
                self._worker_thread.request_cancel()
            self._set_status("Отмена расчёта по запросу пользователя...")

    def _load_initial_selection(self, selected_edges: List[Any]) -> None:
        """Loads preselected edges passed from Gui.Selection (Step 1-A)."""
        import FreeCAD as App
        doc = self._doc or App.ActiveDocument
        if doc is None:
            return

        for item in selected_edges:
            obj = None
            edge = None
            sub_name = ""
            if isinstance(item, tuple) and len(item) == 2:
                obj, sub = item
                if hasattr(sub, "ShapeType") and sub.ShapeType == "Edge":
                    edge = sub
            elif hasattr(item, "ShapeType") and item.ShapeType == "Edge":
                edge = item

            if edge is not None:
                # Find matching edge index on object
                if obj is not None and hasattr(obj, "Shape"):
                    for i, e in enumerate(obj.Shape.Edges, start=1):
                        if abs(e.Length - edge.Length) < 1e-3 and abs(e.CenterOfMass.x - edge.CenterOfMass.x) < 1e-3 and abs(e.CenterOfMass.y - edge.CenterOfMass.y) < 1e-3:
                            sub_name = f"Edge{i}"
                            break
                    if sub_name:
                        self._process_edge_selection(doc, obj, sub_name, edge)
                else:
                    # Search across document objects
                    for o in doc.Objects:
                        if hasattr(o, "Shape") and ("unfold" in o.Name.lower() or "body" in o.Name.lower()):
                            for i, e in enumerate(o.Shape.Edges, start=1):
                                if abs(e.Length - edge.Length) < 1e-3 and abs(e.CenterOfMass.x - edge.CenterOfMass.x) < 1e-3 and abs(e.CenterOfMass.y - edge.CenterOfMass.y) < 1e-3:
                                    sub_name = f"Edge{i}"
                                    self._process_edge_selection(doc, o, sub_name, edge)
                                    break
                            if sub_name:
                                break

    def _on_calculate(self) -> None:
        """User clicked 'Проверить' button."""
        tooling_id = "standard_90deg_v12"
        if hasattr(self, "cmb_tooling") and self.cmb_tooling is not None:
            tooling_id = self.cmb_tooling.currentData() or self.cmb_tooling.currentText() or tooling_id
        policy = "PRODUCTION"
        if hasattr(self, "cmb_policy") and self.cmb_policy is not None:
            policy = self.cmb_policy.currentData() or self.cmb_policy.currentText() or policy

        self.start_check(tooling_set_id=tooling_id, unknown_policy=policy)

    def start_check(
        self,
        fcstd_path: Optional[Any] = None,
        tooling_set_id: str = "standard_90deg_v12",
        unknown_policy: str = "PRODUCTION",
        out_runs_dir: Optional[Any] = None,
        machine: Optional[Any] = None,
    ) -> bool:
        """
        Starts verification pipeline asynchronously via ManualWorkerThread (O2).
        Returns True if worker started, False otherwise.
        """
        if not self._selections:
            self._set_status("Сначала выберите линии гиба на развёртке.")
            return False

        if self._stale:
            self._set_status("⚠️ Модель изменилась (recompute). Выберите линии заново.")
            return False

        if self._is_running:
            return False

        if fcstd_path is None:
            if self._doc is not None and hasattr(self._doc, "FileName") and self._doc.FileName:
                fcstd_path = self._doc.FileName
            else:
                try:
                    import FreeCAD as App
                    if App.ActiveDocument and App.ActiveDocument.FileName:
                        fcstd_path = App.ActiveDocument.FileName
                except Exception:
                    pass

        if not fcstd_path:
            self._set_status("⚠️ Документ не сохранён в FCStd. Сначала сохраните модель.")
            return False

        from freecad_smbs.manual.manual_worker import ManualSequenceInput

        manual_input = ManualSequenceInput(
            selections=list(self._selections),
            tooling_set_id=tooling_set_id,
            unknown_policy=unknown_policy,
            machine=machine,
        )

        self._is_running = True
        self._cancel_requested = False
        if hasattr(self, "btn_calc") and self.btn_calc:
            self.btn_calc.setEnabled(False)
        if hasattr(self, "btn_cancel") and self.btn_cancel:
            self.btn_cancel.setEnabled(True)
        if hasattr(self, "progress_bar") and self.progress_bar:
            self.progress_bar.setVisible(True)
            self.progress_bar.setRange(0, len(self._selections))
            self.progress_bar.setValue(0)

        self._set_status(f"Запуск проверки {len(self._selections)} шагов...")

        self._worker_thread = ManualWorkerThread(
            fcstd_path=fcstd_path,
            manual_input=manual_input,
            out_runs_dir=out_runs_dir,
            parent=self.form if hasattr(self, "form") else None,
        )

        if hasattr(self._worker_thread, "step_progress"):
            self._worker_thread.step_progress.connect(self._on_worker_progress)
        if hasattr(self._worker_thread, "finished_result"):
            self._worker_thread.finished_result.connect(self._on_worker_finished)

        if hasattr(self._worker_thread, "start"):
            self._worker_thread.start()
        return True

    def _on_worker_progress(self, step_idx: int, total_steps: int, bend_id: str) -> None:
        """Called on progress signal from worker thread."""
        if hasattr(self, "progress_bar") and self.progress_bar:
            self.progress_bar.setValue(step_idx)
        self._set_status(f"Проверка шага {step_idx} из {total_steps} ({bend_id})...")

    def _on_worker_finished(self, result: Any) -> None:
        """Called when worker thread completes."""
        self._is_running = False
        self._last_result = result
        if hasattr(self, "btn_calc") and self.btn_calc:
            self.btn_calc.setEnabled(True)
        if hasattr(self, "btn_cancel") and self.btn_cancel:
            self.btn_cancel.setEnabled(False)
        if hasattr(self, "progress_bar") and self.progress_bar:
            if hasattr(self.progress_bar, "maximum"):
                self.progress_bar.setValue(self.progress_bar.maximum())

        # Update status message and report view (Step 3)
        try:
            import FreeCAD as App
            console = getattr(App, "Console", None)
        except Exception:
            console = None

        if getattr(result, "ok", False):
            self._set_status("✓ Проверка завершена успешно. Открытие результатов...")
            if console:
                console.PrintMessage(
                    f"SMBS HandMod: Успешно проверено {len(self._selections)} шагов. "
                    f"Директория запуска: {getattr(result, 'run_dir', '—')}\n"
                )
        else:
            err = getattr(result, "error_message", "Проверка завершилась с ошибкой")
            stage = getattr(result, "stage", "validation")
            self._set_status(f"⚠️ [{stage}] {err}")
            if console:
                console.PrintError(
                    f"SMBS HandMod ОШИБКА: [{stage}] {err}\n"
                    f"Директория: {getattr(result, 'run_dir', '—')}\n"
                )

        # Open VerificationDialog (non-blocking)
        try:
            if getattr(self, "auto_show_dialog", True):
                from freecad_smbs.ui.verification_dialog import VerificationDialog
                artifacts = getattr(result, "artifacts", None)
                if artifacts is None:
                    from freecad_smbs.integration.smbs_bridge import load_run
                    run_dir = getattr(result, "run_dir", None)
                    if run_dir:
                        artifacts = load_run(run_dir)
                if artifacts:
                    self._last_dialog = VerificationDialog(artifacts, parent=self.form)
                    if hasattr(self._last_dialog, "show"):
                        self._last_dialog.show()
        except Exception as e:
            self._set_status(f"Ошибка открытия диалога верификации: {e}")

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
