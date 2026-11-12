"""FreeCAD settings for the real FCStd backgauge as visual-only moving geometry.

No gauge collision, penetration, contact or travel check participates in search.
"""
from __future__ import annotations

import json
from smbs_core.kinematics2d.backgauge_2d import (
    BackgaugeModel2D, BackgaugePosition2D,
)


def show_backgauge_settings(workspace):
    from .pressbrake_2d import _qt, activate_task_dialog, embed_task_dialog
    if activate_task_dialog(workspace, "backgauge"):
        return
    QtCore, _, W = _qt()
    dlg = W.QDialog(workspace)
    dlg.setWindowTitle("SMBS — Задний упор (FCStd, визуализация)")
    dlg.resize(680, 560)
    layout = W.QVBoxLayout(dlg)
    notice = W.QLabel(
        "Используется только реальная модель Backgauge.FCStd. "
        "В таблице: «До гиба» — размер по развёртке; "
        "«Упор S» — положение лицевой поверхности упора от оси гиба. "
        "В автоматическом режиме оно рассчитывается по машинному "
        "сечению детали с учётом толщины и уже выполненных гибов. "
        "Ручной режим задаёт центр S/Z для каждого гиба. "
        "Упор только перемещается при просмотре: столкновения, "
        "проникновения, контакт и допустимость хода НЕ проверяются."
    )
    notice.setWordWrap(True)
    layout.addWidget(notice)
    inspect_btn = W.QPushButton("Проверить CAD-модель Backgauge.FCStd…")
    layout.addWidget(inspect_btn)

    def inspect_cad():
        from freecad_smbs.commands.backgauge_cad_inspector import (
            inspect_backgauge_cad,
        )
        try:
            result = inspect_backgauge_cad()
        except Exception as exc:
            W.QMessageBox.warning(
                dlg, "Backgauge CAD: проверка не пройдена",
                str(exc) + "\nМодель требуется только для визуализации.",
            )
            return
        W.QMessageBox.information(
            dlg, "Backgauge CAD — проверены тело и базовая грань",
            "Источник: {}\n"
            "Объект: {}\n"
            "Solids: {} | Faces: {}\n"
            "Контактная грань: Face{}\n"
            "Статус: {}\n\n"
            "Проверены исходная модель и базовая грань. "
            "Столкновения и доступность контакта не проверяются."
            .format(
                result["source"], result["object"], result["solids"],
                result["faces"], result["stop_face_index"], result["status"],
            ),
        )

    inspect_btn.clicked.connect(inspect_cad)
    active = W.QCheckBox("Показывать движущийся упор Backgauge.FCStd")
    active.setChecked(workspace.backgauge_model is not None)
    layout.addWidget(active)
    auto = W.QCheckBox(
        "Авто: учитывать уже согнутую геометрию и толщину")
    model = workspace.backgauge_model
    auto.setChecked(model.automatic if model else True)
    layout.addWidget(auto)
    # CAD geometry is mandatory in this feature; no 2D rectangle fallback.
    cad_mode = W.QCheckBox("Только настоящая модель Backgauge.FCStd")
    cad_mode.setChecked(True)
    cad_mode.setEnabled(False)
    layout.addWidget(cad_mode)
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
        "Визуальный подвод без проверок контакта или пересечений.")
    dims.addRow("Начальная подача листа (визуально):", feed)
    station = W.QDoubleSpinBox()
    station.setRange(-10000.0, 10000.0)
    station.setDecimals(2)
    station.setSuffix(" мм")
    station.setValue(model.cad_station_xm_mm if model else 0.0)
    station.setToolTip(
        "Положение центра одной CAD-модели Backgauge вдоль линии гиба X_m. "
        "Второй палец не добавляется автоматически.")
    dims.addRow("CAD упор: станция по Xₘ:", station)
    second_enabled = W.QCheckBox("Добавить второй CAD упор")
    second_enabled.setChecked(
        model is not None and model.cad_second_station_xm_mm is not None
    )
    second_station = W.QDoubleSpinBox()
    second_station.setRange(-10000.0, 10000.0)
    second_station.setDecimals(2)
    second_station.setSuffix(" мм")
    second_station.setValue(
        model.cad_second_station_xm_mm
        if model and model.cad_second_station_xm_mm is not None
        else 0.0
    )
    dims.addRow(second_enabled)
    dims.addRow("Второй CAD упор: Xₘ:", second_station)
    station.setEnabled(cad_mode.isChecked())
    second_enabled.setEnabled(cad_mode.isChecked())
    second_station.setEnabled(
        cad_mode.isChecked() and second_enabled.isChecked()
    )
    cad_mode.toggled.connect(station.setEnabled)
    cad_mode.toggled.connect(second_enabled.setEnabled)
    cad_mode.toggled.connect(
        lambda state: second_station.setEnabled(
            state and second_enabled.isChecked()
        )
    )
    second_enabled.toggled.connect(
        lambda state: second_station.setEnabled(
            state and cad_mode.isChecked()
        )
    )
    width.setValue(model.width_s_mm if model else 10.0)
    height.setValue(model.height_z_mm if model else 10.0)
    dims.addRow("Служебная ширина S (позиция центра):", width)
    dims.addRow("Служебная высота Z (без геометрии):", height)
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
                "Модель упора скрыта. Расчёты последовательности не зависят от упора."
            )
            workspace._backgauge_preview_cache.clear()
            workspace._machine_gauge_envelope_cache = None
            workspace._update_operations_visual_state()
            workspace.draw()
            dlg.accept()
            return
        if not bends:
            W.QMessageBox.warning(dlg, "Нет детали", "Сначала загрузите деталь.")
            return
        # Catch duplicate stations in the GUI before model construction.
        # Do not silently disable the second gauge or shift it to a
        # fabricated position. The operator must choose a real station.
        if (cad_mode.isChecked() and second_enabled.isChecked()
                and abs(second_station.value() - station.value()) < 1e-6):
            W.QMessageBox.warning(
                dlg, "Два упора на одной позиции",
                "Первый и второй CAD-упоры имеют одинаковую "
                "координату Xₘ: {:.2f} мм.\n\n"
                "Для одного упора снимите галочку «Добавить второй CAD упор».\n"
                "Для двух упоров укажите реальное положение второго "
                "по Xₘ."
                .format(float(station.value())),
            )
            second_station.setFocus()
            second_station.selectAll()
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
                cad_geometry_enabled=cad_mode.isChecked(),
                cad_station_xm_mm=float(station.value()),
                cad_second_station_xm_mm=(
                    float(second_station.value())
                    if cad_mode.isChecked() and second_enabled.isChecked()
                    else None
                ),
            )
        except (ValueError, TypeError) as exc:
            W.QMessageBox.warning(dlg, "Недопустимая конфигурация", str(exc))
            return
        if new_model.cad_geometry_enabled:
            try:
                from smbs_core.kinematics2d.backgauge_cad_3d import (
                    BackgaugeCAD3D,
                )
                BackgaugeCAD3D(
                    station_xm_mm=new_model.cad_station_xm_mm,
                    second_station_xm_mm=new_model.cad_second_station_xm_mm,
                ).preview_positioned_shapes(100.0, 0.75)
            except Exception as exc:
                W.QMessageBox.warning(
                    dlg, "CAD задний упор не загружен",
                    "Невозможно использовать реальный CAD упор: {}\n"
                    "Режим не сохранён. Исправьте CAD-модель.".format(exc),
                )
                return
        workspace.backgauge_model = new_model
        workspace._backgauge_settings.setValue(
            "backgauge/config_json", json.dumps(new_model.to_dict())
        )
        workspace._backgauge_settings.setValue("backgauge/enabled", True)
        workspace.status.setText(
            ("Задний упор: визуальная установка по согнутому машинному сечению."
             if auto.isChecked() else
             "Задний упор: ручные координаты для {} гибов.".format(len(targets)))
            + " Столкновения и контакт не проверяются."
        )
        workspace._backgauge_preview_cache.clear()
        workspace._machine_gauge_envelope_cache = None
        workspace._ergonomic_loading_cache_key = None
        workspace._update_operations_visual_state()
        workspace.draw()
        dlg.accept()

    save.clicked.connect(accept)
    cancel.clicked.connect(dlg.reject)
    embed_task_dialog(workspace, dlg, "Задний упор", "backgauge")
