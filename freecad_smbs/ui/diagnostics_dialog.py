"""
diagnostics_dialog.py - Environment and configuration diagnostics for SMBS FreeCAD Addon.

Inspects:
- FreeCAD installation and version
- SheetMetal addon installation, version, and import status
- OpenCASCADE / PythonOCC availability
- Tooling Library paths, punches, dies, and machine frame (KL-16 honest check)
- Active document status, save state, and objects count

Headless-safe:
- All PySide / Qt imports are lazy.
- collect_environment_diagnostics() is pure Python safe.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

from freecad_smbs.resources.tooling_paths import (
    TOOL_LIBRARY_ROOT,
    resolve_backgauge_fcstd,
    resolve_clamp_fcstd,
)
from freecad_smbs.ui.preferences_dialog import get_smbs_preferences

_DialogClass: Optional[type] = None


def collect_environment_diagnostics() -> Dict[str, Any]:
    """Collect comprehensive environment diagnostics without requiring Qt GUI."""
    # 1. FreeCAD
    fc_avail = False
    fc_version = "Не загружен"
    fc_gui_avail = False
    try:
        import FreeCAD
        if FreeCAD is not None:
            fc_avail = True
            if hasattr(FreeCAD, "Version"):
                ver = FreeCAD.Version()
                fc_version = f"{ver[0]}.{ver[1]}.{ver[2]} (build {ver[3]})"
            else:
                fc_version = "Доступен"
        import FreeCADGui
        if FreeCADGui is not None and hasattr(FreeCADGui, "getMainWindow"):
            fc_gui_avail = True
    except ImportError:
        pass

    # 2. SheetMetal Addon
    sm_avail = False
    sm_path = "Не найден"
    sm_version = "—"
    try:
        import SheetMetal
        if SheetMetal is not None:
            sm_avail = True
            sm_path = str(getattr(SheetMetal, "__file__", "Imported"))
            sm_version = str(getattr(SheetMetal, "__version__", getattr(SheetMetal, "version", "Установлен")))
    except ImportError:
        pass

    # 3. PythonOCC / OpenCASCADE
    occ_avail = False
    occ_version = "Не найден"
    try:
        import OCC
        if OCC is not None:
            occ_avail = True
            occ_version = str(getattr(OCC, "__version__", "Установлен"))
    except ImportError:
        pass

    # 4. Tooling Library
    prefs = get_smbs_preferences()
    configured_lib_path = Path(prefs.get("tooling_library_path", str(TOOL_LIBRARY_ROOT)))
    lib_exists = configured_lib_path.is_dir()

    punches_count = 0
    dies_count = 0
    machine_frame_present = False
    backgauge_fcstd = None
    clamp_fcstd = None

    if lib_exists:
        punch_dir = configured_lib_path / "punch"
        if punch_dir.is_dir():
            punches_count = len(list(punch_dir.glob("*.FCStd")))
        die_dir = configured_lib_path / "die"
        if die_dir.is_dir():
            dies_count = len(list(die_dir.glob("*.FCStd")))

        # Honest KL-16 check: machine_frame.FCStd is missing
        mf_path = configured_lib_path / "machine_frame.FCStd"
        machine_frame_present = mf_path.is_file()

        backgauge_fcstd = resolve_backgauge_fcstd(configured_lib_path)
        clamp_fcstd = resolve_clamp_fcstd(configured_lib_path)

    # 5. Active Document
    active_doc_info = {
        "present": False,
        "name": "—",
        "file_path": "",
        "is_saved": False,
        "objects_count": 0,
        "uncomputed": False,
    }
    try:
        import FreeCAD
        if FreeCAD is not None and hasattr(FreeCAD, "ActiveDocument") and FreeCAD.ActiveDocument is not None:
            doc = FreeCAD.ActiveDocument
            fn = getattr(doc, "FileName", "")
            active_doc_info = {
                "present": True,
                "name": doc.Name,
                "file_path": fn,
                "is_saved": bool(fn and Path(fn).is_file()),
                "objects_count": len(doc.Objects),
                "uncomputed": getattr(doc, "MustExecute", False),
            }
    except Exception:
        pass

    # Determine overall readiness
    ready = fc_avail and sm_avail and lib_exists and punches_count > 0 and dies_count > 0

    issues: list[str] = []
    if not fc_avail:
        issues.append("FreeCAD не обнаружен в Python окружении.")
    if not sm_avail:
        issues.append("Верстак SheetMetal не установлен или не найден.")
    if not occ_avail:
        issues.append("Библиотека PythonOCC (OpenCASCADE) не найдена.")
    if not lib_exists:
        issues.append(f"Директория библиотеки оснастки не существует: {configured_lib_path}")
    elif punches_count == 0 or dies_count == 0:
        issues.append(f"В библиотеке оснастки не найдены пуансоны ({punches_count}) или матрицы ({dies_count}).")
    if not machine_frame_present:
        issues.append("Геометрия станины machine_frame.FCStd отсутствует (KL-16: используется упрощённая модель).")

    return {
        "ready": ready,
        "issues": issues,
        "freecad": {
            "available": fc_avail,
            "version": fc_version,
            "gui_available": fc_gui_avail,
            "python_executable": sys.executable,
        },
        "sheetmetal": {
            "available": sm_avail,
            "version": sm_version,
            "path": sm_path,
        },
        "pythonocc": {
            "available": occ_avail,
            "version": occ_version,
        },
        "toollibrary": {
            "path": str(configured_lib_path),
            "path_exists": lib_exists,
            "punches_count": punches_count,
            "dies_count": dies_count,
            "machine_frame_present": machine_frame_present,
            "backgauge_present": backgauge_fcstd is not None,
            "clamp_present": clamp_fcstd is not None,
        },
        "active_document": active_doc_info,
        "preferences": prefs,
    }


def format_diagnostics_report(diag: Dict[str, Any]) -> str:
    """Format diagnostics into clean markdown text for clipboard / reports."""
    lines = [
        "# Отчёт диагностики SMBS",
        f"Статус: {'ГОТОВ К РАБОТЕ (READY)' if diag['ready'] else 'ЗАМЕЧАНИЯ (ATTENTION)'}",
        "",
        "## FreeCAD & Python",
        f"- FreeCAD: {diag['freecad']['version']} (GUI: {'Да' if diag['freecad']['gui_available'] else 'Нет'})",
        f"- Python: {diag['freecad']['python_executable']}",
        "",
        "## SheetMetal Addon",
        f"- Статус: {'Установлен' if diag['sheetmetal']['available'] else 'Не найден'}",
        f"- Версия: {diag['sheetmetal']['version']}",
        f"- Путь: {diag['sheetmetal']['path']}",
        "",
        "## PythonOCC / OpenCASCADE",
        f"- Статус: {'Доступен' if diag['pythonocc']['available'] else 'Не найден'}",
        f"- Версия: {diag['pythonocc']['version']}",
        "",
        "## Библиотека оснастки",
        f"- Путь: {diag['toollibrary']['path']} ({'существует' if diag['toollibrary']['path_exists'] else 'НЕ НАЙДЕНА'})",
        f"- Пуансоны: {diag['toollibrary']['punches_count']}",
        f"- Матрицы: {diag['toollibrary']['dies_count']}",
        f"- Задний упор: {'Найден' if diag['toollibrary']['backgauge_present'] else 'Не найден'}",
        f"- Прижимы: {'Найдены' if diag['toollibrary']['clamp_present'] else 'Не найдены'}",
        f"- Станина: {'machine_frame.FCStd присутствует' if diag['toollibrary']['machine_frame_present'] else 'Упрощённая модель (KL-16)'}",
        "",
        "## Активный документ",
    ]
    doc = diag["active_document"]
    if doc["present"]:
        lines.extend([
            f"- Имя: {doc['name']}",
            f"- Файл: {doc['file_path'] or 'Не сохранён на диск'}",
            f"- Сохранён: {'Да' if doc['is_saved'] else 'Нет'}",
            f"- Число объектов: {doc['objects_count']}",
            f"- Требует пересчёта: {'Да' if doc['uncomputed'] else 'Нет'}",
        ])
    else:
        lines.append("- Нет активного документа")

    if diag["issues"]:
        lines.append("")
        lines.append("## Обнаруженные замечания")
        for iss in diag["issues"]:
            lines.append(f"- {iss}")

    return "\n".join(lines)


class DiagnosticsDialog:
    """
    Lazy wrapper for FreeCAD Qt Diagnostics Dialog.
    Instantiates PySide6 QDialog when invoked inside FreeCAD GUI.
    """

    def __new__(cls, parent: Any = None) -> Any:
        global _DialogClass
        if _DialogClass is None:
            from PySide6 import QtCore, QtGui, QtWidgets

            class _Dialog(QtWidgets.QDialog):
                def __init__(self, parent: Any = None) -> None:
                    super().__init__(parent)
                    self.setWindowTitle("SMBS: Диагностика окружения")
                    self.resize(620, 520)
                    self.diag_data = collect_environment_diagnostics()
                    self._init_ui()

                def _init_ui(self) -> None:
                    from PySide6 import QtCore, QtGui, QtWidgets

                    layout = QtWidgets.QVBoxLayout(self)
                    layout.setSpacing(10)

                    # 1. Header Banner
                    header_text = (
                        "Среда SMBS готова к работе (READY)"
                        if self.diag_data["ready"]
                        else "Обнаружены замечания в конфигурации среды"
                    )
                    header_bg = "#2E7D32" if self.diag_data["ready"] else "#EF6C00"
                    lbl_banner = QtWidgets.QLabel(header_text)
                    lbl_banner.setAlignment(QtCore.Qt.AlignCenter)
                    lbl_banner.setMinimumHeight(36)
                    lbl_banner.setStyleSheet(
                        f"background-color: {header_bg}; color: white; font-weight: bold; "
                        f"font-size: 13px; border-radius: 6px; padding: 6px 12px;"
                    )
                    layout.addWidget(lbl_banner)

                    # 2. Details Table
                    table = QtWidgets.QTableWidget()
                    table.setColumnCount(2)
                    table.setHorizontalHeaderLabels(["Компонент / Параметр", "Значение / Статус"])
                    table.horizontalHeader().setStretchLastSection(True)
                    table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)

                    fc = self.diag_data["freecad"]
                    sm = self.diag_data["sheetmetal"]
                    occ = self.diag_data["pythonocc"]
                    tl = self.diag_data["toollibrary"]
                    doc = self.diag_data["active_document"]

                    rows = [
                        ("FreeCAD версия", fc["version"]),
                        ("FreeCAD GUI", "Доступен" if fc["gui_available"] else "Нет"),
                        ("SheetMetal верстак", "Установлен" if sm["available"] else "НЕ НАЙДЕН"),
                        ("SheetMetal путь", sm["path"]),
                        ("PythonOCC / OpenCASCADE", occ["version"] if occ["available"] else "НЕ НАЙДЕН"),
                        ("Библиотека оснастки", tl["path"]),
                        ("Библиотека существует", "Да" if tl["path_exists"] else "НЕТ"),
                        ("Доступно пуансонов", str(tl["punches_count"])),
                        ("Доступно матриц", str(tl["dies_count"])),
                        ("Задний упор (Backgauge)", "Найден" if tl["backgauge_present"] else "Не найден"),
                        ("Прижимы (Clamps)", "Найдены" if tl["clamp_present"] else "Не найдены"),
                        ("Станина (Machine Frame)", "machine_frame.FCStd" if tl["machine_frame_present"] else "Упрощённая модель (KL-16)"),
                        ("Активный документ", doc["name"] if doc["present"] else "Отсутствует"),
                        ("Документ сохранён", "Да" if doc["is_saved"] else ("Нет" if doc["present"] else "—")),
                        ("Число объектов в документе", str(doc["objects_count"])),
                    ]

                    table.setRowCount(len(rows))
                    for r, (k, v) in enumerate(rows):
                        table.setItem(r, 0, QtWidgets.QTableWidgetItem(k))
                        val_item = QtWidgets.QTableWidgetItem(v)
                        if "НЕ НАЙДЕН" in v or "НЕТ" in v:
                            val_item.setForeground(QtGui.QBrush(QtGui.QColor("#C62828")))
                        elif "Установлен" in v or "Доступен" in v or "Да" in v:
                            val_item.setForeground(QtGui.QBrush(QtGui.QColor("#2E7D32")))
                        table.setItem(r, 1, val_item)

                    table.resizeColumnToContents(0)
                    layout.addWidget(table, stretch=1)

                    # 3. Issues box if any
                    issues = self.diag_data.get("issues", [])
                    if issues:
                        iss_box = QtWidgets.QGroupBox("Замечания")
                        iss_layout = QtWidgets.QVBoxLayout(iss_box)
                        txt = QtWidgets.QTextEdit()
                        txt.setReadOnly(True)
                        txt.setMaximumHeight(80)
                        txt.setPlainText("• " + "\n• ".join(issues))
                        iss_layout.addWidget(txt)
                        layout.addWidget(iss_box)

                    # 4. Buttons
                    btn_layout = QtWidgets.QHBoxLayout()
                    btn_copy = QtWidgets.QPushButton("Скопировать отчёт")
                    btn_copy.clicked.connect(self._on_copy_report)
                    btn_layout.addWidget(btn_copy)
                    btn_layout.addStretch(1)

                    btn_close = QtWidgets.QPushButton("Закрыть")
                    btn_close.clicked.connect(self.accept)
                    btn_layout.addWidget(btn_close)
                    layout.addLayout(btn_layout)

                def _on_copy_report(self) -> None:
                    from PySide6 import QtWidgets

                    rep_text = format_diagnostics_report(self.diag_data)
                    clipboard = QtWidgets.QApplication.clipboard()
                    if clipboard is not None:
                        clipboard.setText(rep_text)
                        QtWidgets.QMessageBox.information(
                            self, "Диагностика", "Отчёт скопирован в буфер обмена!"
                        )

            _DialogClass = _Dialog

        return _DialogClass(parent)
