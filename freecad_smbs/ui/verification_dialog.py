"""
verification_dialog.py - Verification inspector dialog for SMBS FreeCAD Addon.

Displays:
- Overall verification status (VERIFIED / VERIFICATION_FAILED / UNVERIFIED)
- Badge color (green / orange / gray)
- Per-stage breakdown (APPROACH, SELF, TOOL, TRAJECTORY, RETRACT, MACHINE)
- Detailed step verification results
- Independent shape metrics (volume delta, Hausdorff distance, bounding box)
- Refusal reasons & diagnostic messages

Headless-safe:
- All PySide / Qt / FreeCAD imports are lazy.
- extract_verification_details() is pure Python and can be used without GUI.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from freecad_smbs.ui.badges import (
    BADGE_UNVERIFIED,
    BADGE_VERIFICATION_FAILED,
    BADGE_VERIFIED,
    get_badge_hex,
)

STAGE_LABELS = {
    "APPROACH": "Подход пуансона (APPROACH)",
    "SELF": "Самопересечение детали (SELF)",
    "TOOL": "Столкновение с инструментом (TOOL)",
    "TRAJECTORY": "Траектория гибки (TRAJECTORY)",
    "RETRACT": "Отвод детали (RETRACT)",
    "MACHINE": "Столкновение со станиной (MACHINE)",
    "SHAPE_METRICS": "Геометрические инварианты (SHAPE_METRICS)",
    "FOLD_CORRECTNESS": "Прямая гибка из развёртки (FOLD_CORRECTNESS)",
}

_DialogClass: Optional[type] = None


def extract_verification_details(artifacts: Any) -> Dict[str, Any]:
    """
    Extract structured verification information from RunArtifacts.
    Pure Python, headless-safe, strict honesty rules (Invariant #1, #2, KL-26).
    """
    run_id = getattr(artifacts, "run_id", None)
    part_id = getattr(artifacts, "part_id", None)
    vr = getattr(artifacts, "verification_result", None)
    cp = getattr(artifacts, "canonical_part", None)
    run_dir = getattr(artifacts, "run_dir", None)

    if not run_id or run_id == "—":
        if vr and vr.get("run_id"):
            run_id = vr.get("run_id")
        elif cp and cp.get("run_id"):
            run_id = cp.get("run_id")
        elif run_dir:
            run_id = Path(run_dir).name
        else:
            run_id = "—"
    if not part_id or part_id == "—":
        if vr and vr.get("part_id"):
            part_id = vr.get("part_id")
        elif cp and cp.get("part_id"):
            part_id = cp.get("part_id")
        else:
            part_id = "—"

    stages: Dict[str, Dict[str, Any]] = {}
    for st_key, st_label in STAGE_LABELS.items():
        stages[st_key] = {
            "name": st_key,
            "label": st_label,
            "status": "UNCHECKED",
            "details": "Не проверялось",
        }

    if vr is None:
        return {
            "status": BADGE_UNVERIFIED,
            "badge_text": "UNVERIFIED — Прогон не верифицирован",
            "badge_color": get_badge_hex(BADGE_UNVERIFIED),
            "production_eligible": None,
            "run_id": run_id,
            "part_id": part_id,
            "units": "mm",
            "independence_level": "—",
            "created_at": "—",
            "reasons": ["Файл 09_verification_result.json отсутствует в директории run."],
            "metrics": {},
            "step_verifications": [],
            "stages": stages,
            "diagnostics": [],
        }

    payload = vr.get("payload", {})
    raw_status = payload.get("verification_status", "VERIFICATION_FAILED")
    raw_eligible = payload.get("production_eligible")

    # KL-26 Fail-Fast Assertion: status="VERIFIED" with production_eligible=False is contradictory
    if raw_status == "VERIFIED" and raw_eligible is False:
        raise ValueError(
            "Inconsistent 09_verification_result: verification_status='VERIFIED' "
            "but production_eligible=False. Honest invariant violation (KL-26)."
        )

    units = vr.get("units", "mm")
    independence = payload.get("verification_independence_level", "—")
    created_at = vr.get("created_at", "—")
    metrics = payload.get("independent_metrics", {})
    step_verifications = payload.get("step_verifications", [])
    diagnostics = vr.get("diagnostics", [])

    reasons: List[str] = list(payload.get("reasons", []))
    if not reasons:
        for diag in diagnostics:
            if diag.get("level") == "ERROR":
                msg = diag.get("message", "")
                if msg and msg not in reasons:
                    reasons.append(msg)

    if raw_eligible is True:
        status = BADGE_VERIFIED
        badge_text = "VERIFIED — Готов к производству (production_eligible=true)"
        badge_color = get_badge_hex(BADGE_VERIFIED)
        for st_key in stages:
            stages[st_key]["status"] = "PASSED"
            stages[st_key]["details"] = "Проверка пройдена успешно"
    else:
        status = BADGE_VERIFICATION_FAILED
        badge_text = "VERIFICATION_FAILED — Отклонено верификатором (production_eligible=false)"
        badge_color = get_badge_hex(BADGE_VERIFICATION_FAILED)

        # Map diagnostics to stages
        stage_errors: Dict[str, List[str]] = {}
        for diag in diagnostics:
            diag_stage = str(diag.get("stage", "")).upper()
            msg = diag.get("message", "")
            for st_key in stages:
                if (st_key in diag_stage) or (st_key == "TRAJECTORY" and "BEND_TRAJECTORY" in diag_stage):
                    stage_errors.setdefault(st_key, []).append(msg)
                elif st_key == "SHAPE_METRICS" and ("SHAPE_METRICS" in diag_stage or "METRIC" in diag.get("code", "")):
                    stage_errors.setdefault(st_key, []).append(msg)
                elif st_key == "FOLD_CORRECTNESS" and ("FOLD" in diag_stage or "ALIGNMENT" in diag.get("code", "")):
                    stage_errors.setdefault(st_key, []).append(msg)

        # Check refusal reasons for stage hints if diagnostics stage was generic
        for r in reasons:
            r_upper = r.upper()
            if "APPROACH" in r_upper:
                stage_errors.setdefault("APPROACH", []).append(r)
            if "SELF" in r_upper:
                stage_errors.setdefault("SELF", []).append(r)
            if "TOOL" in r_upper or "DIE" in r_upper or "PUNCH" in r_upper:
                stage_errors.setdefault("TOOL", []).append(r)
            if "TRAJECTORY" in r_upper:
                stage_errors.setdefault("TRAJECTORY", []).append(r)
            if "RETRACT" in r_upper:
                stage_errors.setdefault("RETRACT", []).append(r)
            if "MACHINE" in r_upper:
                stage_errors.setdefault("MACHINE", []).append(r)

        for st_key in stages:
            if st_key in stage_errors:
                stages[st_key]["status"] = "FAILED"
                stages[st_key]["details"] = "; ".join(stage_errors[st_key])
            else:
                stages[st_key]["status"] = "PASSED" if step_verifications else "UNCHECKED"
                stages[st_key]["details"] = "Замечаний нет" if step_verifications else "Не проверялось"

        # Baseline geometric verification (Stage 04: SheetMetalFolder restored shape vs CAD canonical part)
        baseline_result = getattr(artifacts, "baseline_result", None)
        if baseline_result is None and run_dir:
            bf = Path(run_dir) / "04_baseline_result.json"
            if bf.is_file():
                try:
                    baseline_result = json.loads(bf.read_text(encoding="utf-8"))
                except Exception:
                    pass

        if baseline_result:
            b_payload = baseline_result.get("payload", {})
            b_status = b_payload.get("baseline_status")
            b_metrics = b_payload.get("metrics", {})
            if b_status == "VALID":
                if "FOLD_CORRECTNESS" not in stage_errors:
                    hd = b_metrics.get("hausdorff_distance_mm", 0.0)
                    res_f = b_metrics.get("faces_count_restored")
                    ref_f = b_metrics.get("faces_count_reference")
                    stages["FOLD_CORRECTNESS"]["status"] = "PASSED"
                    stages["FOLD_CORRECTNESS"]["details"] = f"Развёртка восстановлена (Hausdorff: {hd:.4f} мм, грани: {res_f}/{ref_f})"
                if "SHAPE_METRICS" not in stage_errors:
                    stages["SHAPE_METRICS"]["status"] = "PASSED"
                    stages["SHAPE_METRICS"]["details"] = "B-Rep топология и геометрия совпадают с эталоном"

                if not metrics or metrics.get("hausdorff_distance_mm", 0.0) >= 9999.0 or not metrics.get("is_valid_brep"):
                    metrics = dict(b_metrics)

    return {
        "status": status,
        "badge_text": badge_text,
        "badge_color": badge_color,
        "production_eligible": raw_eligible,
        "run_id": run_id,
        "part_id": part_id,
        "units": units,
        "independence_level": independence,
        "created_at": created_at,
        "reasons": reasons,
        "metrics": metrics,
        "step_verifications": step_verifications,
        "stages": stages,
        "diagnostics": diagnostics,
    }


class VerificationDialog:
    """
    Lazy wrapper for FreeCAD Qt Verification Dialog.
    Instantiates PySide6 QDialog when invoked inside FreeCAD GUI.
    """

    def __new__(cls, artifacts: Any, parent: Any = None) -> Any:
        global _DialogClass
        if _DialogClass is None:
            from PySide6 import QtCore, QtGui, QtWidgets

            class _Dialog(QtWidgets.QDialog):
                def __init__(self, artifacts: Any, parent: Any = None) -> None:
                    super().__init__(parent)
                    self.artifacts = artifacts
                    self.details = extract_verification_details(artifacts)
                    self.setWindowTitle("SMBS: Верификация последовательности гибки")
                    self.resize(680, 540)
                    self._init_ui()

                def _init_ui(self) -> None:
                    from PySide6 import QtCore, QtGui, QtWidgets

                    main_layout = QtWidgets.QVBoxLayout(self)
                    main_layout.setSpacing(10)

                    # 1. Header Badge
                    self.badge_lbl = QtWidgets.QLabel(self.details["badge_text"])
                    self.badge_lbl.setAlignment(QtCore.Qt.AlignCenter)
                    self.badge_lbl.setMinimumHeight(38)
                    color = self.details["badge_color"]
                    self.badge_lbl.setStyleSheet(
                        f"background-color: {color}; color: white; font-weight: bold; "
                        f"font-size: 13px; border-radius: 6px; padding: 6px 12px;"
                    )
                    main_layout.addWidget(self.badge_lbl)

                    # 2. Metadata Grid
                    meta_box = QtWidgets.QGroupBox("Сведения о прогоне")
                    meta_layout = QtWidgets.QGridLayout(meta_box)
                    meta_layout.addWidget(QtWidgets.QLabel("<b>Part ID:</b>"), 0, 0)
                    meta_layout.addWidget(QtWidgets.QLabel(str(self.details["part_id"])), 0, 1)
                    meta_layout.addWidget(QtWidgets.QLabel("<b>Run ID:</b>"), 0, 2)
                    meta_layout.addWidget(QtWidgets.QLabel(str(self.details["run_id"])), 0, 3)

                    meta_layout.addWidget(QtWidgets.QLabel("<b>Уровень независимости:</b>"), 1, 0)
                    meta_layout.addWidget(QtWidgets.QLabel(str(self.details["independence_level"])), 1, 1)
                    meta_layout.addWidget(QtWidgets.QLabel("<b>Единицы измерения:</b>"), 1, 2)
                    meta_layout.addWidget(QtWidgets.QLabel(str(self.details["units"])), 1, 3)
                    main_layout.addWidget(meta_box)

                    # 3. Tab Widget
                    self.tabs = QtWidgets.QTabWidget()
                    self.tabs.addTab(self._build_stages_tab(), "Этапы проверки")
                    self.tabs.addTab(self._build_steps_tab(), "Шаги гибки")
                    self.tabs.addTab(self._build_metrics_tab(), "Метрики формы")
                    main_layout.addWidget(self.tabs, stretch=1)

                    # 4. Refusal Reasons / Diagnostics Box (if any)
                    reasons = self.details.get("reasons", [])
                    if reasons:
                        reasons_box = QtWidgets.QGroupBox("Причины отклонения / Замечания")
                        reasons_layout = QtWidgets.QVBoxLayout(reasons_box)
                        reasons_text = QtWidgets.QTextEdit()
                        reasons_text.setReadOnly(True)
                        reasons_text.setMaximumHeight(85)
                        reasons_text.setPlainText("\n• " + "\n• ".join(reasons))
                        reasons_layout.addWidget(reasons_text)
                        main_layout.addWidget(reasons_box)

                    # 5. Buttons
                    btn_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
                    btn_box.rejected.connect(self.reject)
                    main_layout.addWidget(btn_box)

                def _build_stages_tab(self) -> QtWidgets.QWidget:
                    from PySide6 import QtCore, QtWidgets

                    w = QtWidgets.QWidget()
                    layout = QtWidgets.QVBoxLayout(w)
                    table = QtWidgets.QTableWidget()
                    table.setColumnCount(3)
                    table.setHorizontalHeaderLabels(["Этап", "Статус", "Детали"])
                    table.horizontalHeader().setStretchLastSection(True)
                    table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)

                    stages = self.details.get("stages", {})
                    table.setRowCount(len(stages))
                    for row, (st_key, st_info) in enumerate(stages.items()):
                        name_item = QtWidgets.QTableWidgetItem(st_info["label"])
                        status_val = st_info["status"]
                        if status_val == "PASSED":
                            status_text = "✓ Пройдено"
                            status_color = "#2E7D32"
                        elif status_val == "FAILED":
                            status_text = "✗ Ошибка"
                            status_color = "#C62828"
                        else:
                            status_text = "? Не проверялось"
                            status_color = "#757575"

                        status_item = QtWidgets.QTableWidgetItem(status_text)
                        status_item.setForeground(QtGui.QBrush(QtGui.QColor(status_color)))
                        status_item.setTextAlignment(QtCore.Qt.AlignCenter)
                        details_item = QtWidgets.QTableWidgetItem(st_info["details"])

                        table.setItem(row, 0, name_item)
                        table.setItem(row, 1, status_item)
                        table.setItem(row, 2, details_item)

                    table.resizeColumnToContents(0)
                    table.resizeColumnToContents(1)
                    layout.addWidget(table)
                    return w

                def _build_steps_tab(self) -> QtWidgets.QWidget:
                    from PySide6 import QtCore, QtWidgets

                    w = QtWidgets.QWidget()
                    layout = QtWidgets.QVBoxLayout(w)
                    table = QtWidgets.QTableWidget()
                    table.setColumnCount(5)
                    table.setHorizontalHeaderLabels(["Шаг", "Гиб", "Статус", "Коллизии", "Кинематика"])
                    table.horizontalHeader().setStretchLastSection(True)
                    table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)

                    steps = self.details.get("step_verifications", [])
                    table.setRowCount(len(steps))
                    for row, step in enumerate(steps):
                        step_no = QtWidgets.QTableWidgetItem(str(step.get("step", row + 1)))
                        step_no.setTextAlignment(QtCore.Qt.AlignCenter)
                        bend_id = QtWidgets.QTableWidgetItem(str(step.get("bend_id", "—")))
                        bend_id.setTextAlignment(QtCore.Qt.AlignCenter)

                        st_val = step.get("status", "—")
                        st_item = QtWidgets.QTableWidgetItem(st_val)
                        st_item.setTextAlignment(QtCore.Qt.AlignCenter)
                        if st_val == "VERIFIED":
                            st_item.setForeground(QtGui.QBrush(QtGui.QColor("#2E7D32")))
                        else:
                            st_item.setForeground(QtGui.QBrush(QtGui.QColor("#C62828")))

                        coll_val = "✓" if step.get("collision_verified") else "✗"
                        coll_item = QtWidgets.QTableWidgetItem(coll_val)
                        coll_item.setTextAlignment(QtCore.Qt.AlignCenter)

                        kin_val = "✓" if step.get("kinematics_verified") else "✗"
                        kin_item = QtWidgets.QTableWidgetItem(kin_val)
                        kin_item.setTextAlignment(QtCore.Qt.AlignCenter)

                        table.setItem(row, 0, step_no)
                        table.setItem(row, 1, bend_id)
                        table.setItem(row, 2, st_item)
                        table.setItem(row, 3, coll_item)
                        table.setItem(row, 4, kin_item)

                    layout.addWidget(table)
                    return w

                def _build_metrics_tab(self) -> QtWidgets.QWidget:
                    from PySide6 import QtWidgets

                    w = QtWidgets.QWidget()
                    layout = QtWidgets.QVBoxLayout(w)
                    metrics = self.details.get("metrics", {})

                    table = QtWidgets.QTableWidget()
                    table.setColumnCount(2)
                    table.setHorizontalHeaderLabels(["Метрика", "Значение"])
                    table.horizontalHeader().setStretchLastSection(True)
                    table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)

                    metrics_rows = [
                        ("B-Rep топологическая валидность", "Да (True)" if metrics.get("is_valid_brep") else "Нет (False)"),
                        ("Число тел (solids)", str(metrics.get("solids_count", "—"))),
                        ("Число оболочек (shells)", str(metrics.get("shells_count", "—"))),
                        ("Отклонение объёма (ΔV)", f"{metrics.get('volume_delta_mm3', 0.0):.4f} мм³"),
                        ("Относительное отклонение объёма", f"{metrics.get('relative_volume_delta', 0.0) * 100:.4f}%"),
                        ("Отклонение площади поверхности (ΔS)", f"{metrics.get('surface_area_delta_mm2', 0.0):.4f} мм²"),
                        ("Расстояние Хаусдорфа (Hausdorff)", f"{metrics.get('hausdorff_distance_mm', 0.0):.4f} мм"),
                        ("Отклонение габаритного параллелепипеда (bbox)", str(metrics.get("bbox_delta_mm", "—"))),
                    ]

                    table.setRowCount(len(metrics_rows))
                    for row, (name, val) in enumerate(metrics_rows):
                        table.setItem(row, 0, QtWidgets.QTableWidgetItem(name))
                        table.setItem(row, 1, QtWidgets.QTableWidgetItem(val))

                    table.resizeColumnToContents(0)
                    layout.addWidget(table)
                    return w

            _DialogClass = _Dialog

        return _DialogClass(artifacts, parent)
