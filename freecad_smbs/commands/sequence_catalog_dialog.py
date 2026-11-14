"""Catalog window: import SMBS diagnostic JSON and assign a replayed sequence."""
from __future__ import annotations
from pathlib import Path
import json

from smbs_core.kinematics2d.sequence_catalog import load_catalog_file, parse_diagnostic


def show_sequence_catalog(workspace):
    """Open a standalone modal UI, sharing the existing viewer and controller."""
    from .pressbrake_2d import _qt, activate_task_dialog, embed_task_dialog
    if activate_task_dialog(workspace, "catalog"):
        return
    QtCore, _, QtWidgets = _qt()
    dlg = QtWidgets.QDialog(workspace)
    dlg.setObjectName("SMBS_SequenceCatalog")
    dlg.setWindowTitle("SMBS — Последовательности")
    dlg.resize(1050, 630)
    root = QtWidgets.QVBoxLayout(dlg)
    intro = QtWidgets.QLabel(
        "Загрузите JSON автоматического/ручного поиска. Штрафы относятся к весам "
        "на момент расчёта. Назначение перепроверяет порядок на текущей оснастке. "
        "2D не подтверждает безопасность в 3D.")
    intro.setWordWrap(True)
    root.addWidget(intro)
    buttons = QtWidgets.QHBoxLayout()
    load_btn = QtWidgets.QPushButton("Загрузить JSON…")
    folder_btn = QtWidgets.QPushButton("Загрузить папку…")
    refresh_btn = QtWidgets.QPushButton("Обновить")
    buttons.addWidget(load_btn)
    buttons.addWidget(folder_btn)
    buttons.addWidget(refresh_btn)
    buttons.addStretch()
    root.addLayout(buttons)
    table = QtWidgets.QTableWidget(0, 6)
    table.setObjectName("SMBS_SequenceCatalogTable")
    table.setHorizontalHeaderLabels((
        "Тип", "Последовательность", "Штраф", "Предупреждения", "2D статус", "Файл"))
    table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
    table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
    table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    table.setColumnWidth(0, 65)
    table.setColumnWidth(1, 365)
    table.setColumnWidth(2, 80)
    table.setColumnWidth(3, 135)
    table.setColumnWidth(4, 220)
    table.horizontalHeader().setStretchLastSection(True)
    root.addWidget(table, 1)
    details = QtWidgets.QPlainTextEdit()
    details.setObjectName("SMBS_SequenceCatalogDetails")
    details.setReadOnly(True)
    details.setMaximumHeight(175)
    root.addWidget(details)
    footer = QtWidgets.QHBoxLayout()
    summary = QtWidgets.QLabel()
    assign_btn = QtWidgets.QPushButton("Назначить для симуляции")
    close_btn = QtWidgets.QPushButton("Закрыть")
    footer.addWidget(summary, 1)
    footer.addWidget(assign_btn)
    footer.addWidget(close_btn)
    root.addLayout(footer)

    settings = QtCore.QSettings("SMBS", "BendSeq")
    saved = settings.value("sequence_catalog/json_paths", [], type=list)
    paths = [str(p) for p in saved if p]
    entries = []

    def record_path(file_path):
        path = str(Path(file_path).expanduser().resolve())
        if path not in paths:
            paths.append(path)
            settings.setValue("sequence_catalog/json_paths", paths)

    def selected():
        row = table.currentRow()
        return entries[row] if 0 <= row < len(entries) else None

    def display_details():
        entry = selected()
        if entry is None:
            return
        details.setPlainText("\n".join((
            "Порядок: " + " → ".join(entry.sequence),
            "Источник: " + entry.source,
            "Статус: " + entry.evidence,
            "Штраф: " + (str(entry.cost) if entry.cost is not None else "неизвестен")
            + (" (полный)" if entry.cost_complete else " (не подтверждён)"),
            "Веса: " + json.dumps(entry.weights or {}, ensure_ascii=False),
            "Предупреждения: " + ("; ".join(entry.warnings) or "нет в отчёте"),
            "Штрафы шагов: " + json.dumps(
                entry.cost_breakdown, ensure_ascii=False)[:12000],
        )))
    table.itemSelectionChanged.connect(display_details)

    def reload():
        entries.clear()
        failures = []
        for path in paths:
            try:
                entries.extend(load_catalog_file(path))
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                failures.append(Path(path).name + ": " + str(exc))
        if workspace.last_planar_search_report is not None:
            entries.extend(parse_diagnostic(
                workspace.last_planar_search_report.to_dict(),
                "Текущий автоматический поиск"))
        manual_report = getattr(workspace, "_last_manual_sequence_report", None)
        if manual_report is not None:
            entries.extend(parse_diagnostic(
                manual_report, "Текущий ручной порядок"))
        entries[:] = list(dict((e.key, e) for e in entries).values())
        table.setRowCount(0)
        for entry in entries:
            row = table.rowCount()
            table.insertRow(row)
            values = (
                entry.origin,
                " → ".join(entry.sequence),
                "{:.2f}".format(entry.cost) if entry.cost_complete
                else "н/д",
                ", ".join(entry.warning_bends) or "нет",
                entry.evidence,
                entry.source,
            )
            for col, value in enumerate(values):
                table.setItem(row, col, QtWidgets.QTableWidgetItem(value))
        summary.setText("Вариантов: {} | источников: {} | ошибок: {}"
                        .format(len(entries), len(paths), len(failures)))
        if failures:
            details.setPlainText("Не удалось прочитать:\n" + "\n".join(failures[:12]))
        elif entries:
            table.selectRow(0)
        else:
            details.setPlainText("Добавьте JSON отчёты поиска или ручной проверки.")

    def import_files():
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            dlg, "Выбрать отчёты SMBS", str(Path.home()), "JSON (*.json)")
        for path in files:
            record_path(path)
        reload()

    def import_folder():
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            dlg, "Папка с диагностикой JSON", str(Path.home()))
        if folder:
            for path in sorted(Path(folder).glob("*.json")):
                record_path(path)
            reload()

    def assign_selected():
        entry = selected()
        if entry is None:
            return
        try:
            workspace.assign_catalog_sequence(entry)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                dlg, "Порядок не назначен", str(exc))
            return
        dlg.accept()

    load_btn.clicked.connect(import_files)
    folder_btn.clicked.connect(import_folder)
    refresh_btn.clicked.connect(reload)
    assign_btn.clicked.connect(assign_selected)
    table.itemDoubleClicked.connect(lambda _item: assign_selected())
    close_btn.clicked.connect(dlg.reject)
    reload()
    embed_task_dialog(workspace, dlg, "Последовательности", "catalog")
