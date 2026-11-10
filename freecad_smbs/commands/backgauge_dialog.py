"""FreeCAD Qt settings for automatic and explicit backgauge section targets.

Automatic mode derives the fixed panel edge from the active bend geometry.
Geometry is a machine (s,z) rectangle;
a successful 2D screen does not verify finger travel, clamp interaction, or 3D.
"""
from __future__ import annotations

import json
from smbs_core.kinematics2d.backgauge_2d import (
    BackgaugeModel2D, BackgaugePosition2D,
)


def show_backgauge_settings(workspace):
    from .pressbrake_2d import _qt
    QtCore, _, W = _qt()
    dlg = W.QDialog(workspace)
    dlg.setWindowTitle("SMBS — Задний упор (2D)")
    dlg.resize(680, 560)
    layout = W.QVBoxLayout(dlg)
    notice = W.QLabel(
        "Направления станка: СЛЕВА (−s) оператор и свободная часть; "
        "СПРАВА (+s) внутренняя часть станка и задний упор. "
        "Линия гиба и оси пуансона/матрицы находятся на s=0. "
        "2D-сечение заднего упора в координатах станка (s,z), мм. "
        "АВТО: положение вычисляется от текущего гиба до края опорной панели. "
        "РУЧНОЙ: задайте центр упора для каждого гиба. "
        "Проверка траектории перемещения "
        "между гибами и реальная 3D-геометрия пока не реализованы.")
    notice.setWordWrap(True)
    layout.addWidget(notice)
    active = W.QCheckBox("Проверять задний упор при поиске и ручной проверке")
    active.setChecked(workspace.backgauge_model is not None)
    layout.addWidget(active)
    auto = W.QCheckBox(
        "Автоматически вычислять положение по краю опорной панели")
    model = workspace.backgauge_model
    auto.setChecked(model.automatic if model else True)
    layout.addWidget(auto)
    dims = W.QFormLayout()
    width = W.QDoubleSpinBox()
    width.setRange(0.01, 10000)
    width.setDecimals(2)
    width.setSuffix(" мм")
    height = W.QDoubleSpinBox()
    height.setRange(0.01, 10000)
    height.setDecimals(2)
    height.setSuffix(" мм")
    clearance = W.QDoubleSpinBox()
    clearance.setRange(0.0, 500.0)
    clearance.setDecimals(2)
    clearance.setSuffix(" мм")
    clearance.setValue(model.edge_clearance_mm if model else 0.0)
    dims.addRow("Зазор до края детали:", clearance)
    feed = W.QDoubleSpinBox()
    feed.setRange(0.0, 1000.0)
    feed.setDecimals(1)
    feed.setSuffix(" мм")
    feed.setValue(model.initial_feed_preview_mm if model else 0.0)
    feed.setToolTip(
        "Только визуальный начальный подвод заготовки слева к упору. "
        "0 отключает фазу. Траектория не проверяется на коллизии.")
    dims.addRow("Начальная подача листа (визуально):", feed)
    width.setValue(model.width_s_mm if model else 10.0)
    height.setValue(model.height_z_mm if model else 10.0)
    dims.addRow("Ширина прямоугольника по s:", width)
    dims.addRow("Высота прямоугольника по z:", height)
    layout.addLayout(dims)
    table = W.QTableWidget(0, 3, dlg)
    table.setObjectName("SMBS_BackgaugeTargets")
    table.setHorizontalHeaderLabels(("Гиб", "Центр s (мм)", "Центр z (мм)"))
    table.horizontalHeader().setStretchLastSection(True)
    table.setColumnWidth(0, 100)
    table.setColumnWidth(1, 160)
    layout.addWidget(table, 1)
    table.setEnabled(not auto.isChecked())
    auto.toggled.connect(lambda enabled: table.setEnabled(not enabled))
    bends = tuple(workspace.session.canonical_order) if workspace.session else ()
    for bid in bends:
        row = table.rowCount()
        table.insertRow(row)
        bid_item = W.QTableWidgetItem(bid)
        bid_item.setFlags(bid_item.flags() & ~QtCore.Qt.ItemIsEditable)
        table.setItem(row, 0, bid_item)
        point = model.positions.get(bid) if model else None
        for col, value in (
            (1, str(point.s_mm) if point else ""),
            (2, str(point.z_mm) if point else ""),
        ):
            table.setItem(row, col, W.QTableWidgetItem(value))
    if not bends:
        warning = W.QLabel("Для задания координат сначала загрузите деталь.")
        layout.addWidget(warning)
    bottom = W.QHBoxLayout()
    save = W.QPushButton("Сохранить")
    cancel = W.QPushButton("Отмена")
    bottom.addStretch()
    bottom.addWidget(save)
    bottom.addWidget(cancel)
    layout.addLayout(bottom)

    def accept():
        if not active.isChecked():
            workspace.backgauge_model = None
            workspace._backgauge_settings.setValue("backgauge/enabled", False)
            workspace.status.setText(
                "Задний упор отключён. Коллизии и перемещения упора не проверяются."
            )
            dlg.accept()
            return
        if not bends:
            W.QMessageBox.warning(dlg, "Нет детали", "Сначала загрузите деталь.")
            return
        targets = {}
        try:
            for row in range(table.rowCount() if not auto.isChecked() else 0):
                bid = table.item(row, 0).text()
                item_s, item_z = table.item(row, 1), table.item(row, 2)
                val_s = item_s.text().strip() if item_s else ""
                val_z = item_z.text().strip() if item_z else ""
                if not val_s or not val_z:
                    raise ValueError("Не указаны обе координаты заднего упора для " + bid)
                targets[bid] = BackgaugePosition2D(
                    float(val_s.replace(",", ".")),
                    float(val_z.replace(",", ".")),
                )
            new_model = BackgaugeModel2D(
                width_s_mm=float(width.value()),
                height_z_mm=float(height.value()),
                positions=targets,
                part_label=workspace.session.part_label,
                automatic=auto.isChecked(),
                edge_clearance_mm=float(clearance.value()),
                initial_feed_preview_mm=float(feed.value()),
            )
        except (ValueError, TypeError) as exc:
            W.QMessageBox.warning(dlg, "Недопустимая конфигурация", str(exc))
            return
        workspace.backgauge_model = new_model
        workspace._backgauge_settings.setValue(
            "backgauge/config_json", json.dumps(new_model.to_dict())
        )
        workspace._backgauge_settings.setValue("backgauge/enabled", True)
        workspace.status.setText(
            ("Задний упор: автоматическая постановка по краю опорной панели."
             if auto.isChecked() else
             "Задний упор: ручные координаты для {} гибов.".format(len(targets)))
            + " Путь перемещения НЕ проверен."
        )
        dlg.accept()

    save.clicked.connect(accept)
    cancel.clicked.connect(dlg.reject)
    dlg.exec()
