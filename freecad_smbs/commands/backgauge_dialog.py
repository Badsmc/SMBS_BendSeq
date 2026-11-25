"""FreeCAD settings for the real FCStd backgauge as visual-only moving geometry.

No gauge collision, penetration, contact or travel check participates in search.
"""
from __future__ import annotations

import json
from smbs_core.kinematics2d.backgauge_2d import (
    BackgaugeModel2D, BackgaugePosition2D,
)

from freecad_smbs.ui.localization import tr, tr_message


def show_backgauge_settings(workspace, *, modal_parent=None):
    from .pressbrake_2d import _qt, activate_task_dialog, embed_task_dialog
    if modal_parent is None and activate_task_dialog(workspace, "backgauge"):
        return
    QtCore, _, W = _qt()
    dlg = W.QDialog(workspace if modal_parent is None else modal_parent)
    dlg.setWindowTitle(tr("SMBS — Задний упор (FCStd, визуализация)"))
    dlg.resize(680, 560)
    layout = W.QVBoxLayout(dlg)
    notice = W.QLabel(
        tr("Используется только реальная модель Backgauge.FCStd. В таблице: «До гиба» — размер по развёртке; «Упор S» — положение лицевой поверхности упора от оси гиба. В автоматическом режиме лапки расставляются симметрично относительно центра задней грани перед каждым гибом. Координаты X доступны в ручном режиме. Для двух пальцев используется общая координата S по дальнему краю. Ручной режим задаёт центр S/Z для каждого гиба. Упор только перемещается при просмотре: столкновения, проникновения, контакт и допустимость хода НЕ проверяются.")
    )
    notice.setWordWrap(True)
    layout.addWidget(notice)
    inspect_btn = W.QPushButton(tr("Проверить CAD-модель Backgauge.FCStd…"))
    layout.addWidget(inspect_btn)

    def inspect_cad():
        from freecad_smbs.commands.backgauge_cad_inspector import (
            inspect_backgauge_cad,
        )
        try:
            result = inspect_backgauge_cad()
        except Exception as exc:
            W.QMessageBox.warning(
                dlg, tr("Backgauge CAD: проверка не пройдена"),
                tr_message(exc) + tr("\nМодель требуется только для визуализации."),
            )
            return
        W.QMessageBox.information(
            dlg, tr("Backgauge CAD — проверены тело и базовая грань"),
            tr("Источник: {source}\nОбъект: {object}\nSolids: {solids} | Faces: {faces}\nКонтактная грань: Face{face}\nСтатус: {status}\n\nПроверены исходная модель и базовая грань. Столкновения и доступность контакта не проверяются.",
                source=result["source"], object=result["object"], solids=result["solids"],
                faces=result["faces"], face=result["stop_face_index"], status=result["status"],
            ),
        )

    inspect_btn.clicked.connect(inspect_cad)
    active = W.QCheckBox(tr("Показывать движущийся упор Backgauge.FCStd"))
    active.setChecked(workspace.backgauge_model is not None)
    layout.addWidget(active)
    auto = W.QCheckBox(
        tr("Авто: учитывать уже согнутую геометрию и толщину"))
    model = workspace.backgauge_model
    auto.setChecked(model.automatic if model else True)
    layout.addWidget(auto)
    # CAD geometry is mandatory in this feature; no 2D rectangle fallback.
    cad_mode = W.QCheckBox(tr("Только настоящая модель Backgauge.FCStd"))
    cad_mode.setChecked(True)
    cad_mode.setEnabled(False)
    layout.addWidget(cad_mode)
    dims = W.QFormLayout()
    width = W.QDoubleSpinBox()
    width.setRange(0.01, 10000)
    width.setDecimals(2)
    width.setSuffix(tr(" мм"))
    height = W.QDoubleSpinBox()
    height.setRange(0.01, 10000)
    height.setDecimals(2)
    height.setSuffix(tr(" мм"))
    clearance = W.QDoubleSpinBox()
    clearance.setRange(0.0, 500.0)
    clearance.setDecimals(2)
    clearance.setSuffix(tr(" мм"))
    clearance.setValue(model.edge_clearance_mm if model else 0.0)
    dims.addRow(tr("Зазор до края детали:"), clearance)
    feed = W.QDoubleSpinBox()
    feed.setRange(0.0, 1000.0)
    feed.setDecimals(1)
    feed.setSuffix(tr(" мм"))
    feed.setValue(model.initial_feed_preview_mm if model else 0.0)
    feed.setToolTip(
        tr("Визуальный подвод без проверок контакта или пересечений."))
    dims.addRow(tr("Начальная подача листа (визуально):"), feed)
    station = W.QDoubleSpinBox()
    station.setRange(-10000.0, 10000.0)
    station.setDecimals(2)
    station.setSuffix(tr(" мм"))
    station.setValue(model.cad_station_xm_mm if model else 0.0)
    station.setToolTip(
        tr("Положение центра одной CAD-модели Backgauge вдоль линии гиба X_m. Второй палец не добавляется автоматически."))
    dims.addRow(tr("CAD упор: станция по Xₘ:"), station)
    second_enabled = W.QCheckBox(tr("Добавить второй CAD упор"))
    second_enabled.setChecked(
        model is not None and model.cad_second_station_xm_mm is not None
    )
    second_station = W.QDoubleSpinBox()
    second_station.setRange(-10000.0, 10000.0)
    second_station.setDecimals(2)
    second_station.setSuffix(tr(" мм"))
    second_station.setValue(
        model.cad_second_station_xm_mm
        if model and model.cad_second_station_xm_mm is not None
        else (station.value() + 100.0 if station.value() <= 9900.0 else station.value() - 100.0)
    )
    dims.addRow(second_enabled)
    dims.addRow(tr("Второй CAD упор: Xₘ:"), second_station)

    def update_station_controls():
        manual = cad_mode.isChecked() and not auto.isChecked()
        station.setEnabled(manual)
        second_enabled.setEnabled(cad_mode.isChecked())
        second_station.setEnabled(manual and second_enabled.isChecked())

    auto.toggled.connect(update_station_controls)
    second_enabled.toggled.connect(update_station_controls)
    update_station_controls()
    width.setValue(model.width_s_mm if model else 10.0)
    height.setValue(model.height_z_mm if model else 10.0)
    dims.addRow(tr("Служебная ширина S (позиция центра):"), width)
    dims.addRow(tr("Служебная высота Z (без геометрии):"), height)
    layout.addLayout(dims)
    table = W.QTableWidget(0, 3, dlg)
    table.setObjectName("SMBS_BackgaugeTargets")
    table.setHorizontalHeaderLabels((tr("Гиб"), tr("Центр s (мм)"), tr("Центр z (мм)")))
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
        warning = W.QLabel(tr("Для задания координат сначала загрузите деталь."))
        layout.addWidget(warning)
    bottom = W.QHBoxLayout()
    save = W.QPushButton(tr("Сохранить"))
    cancel = W.QPushButton(tr("Отмена"))
    bottom.addStretch()
    bottom.addWidget(save)
    bottom.addWidget(cancel)
    layout.addLayout(bottom)

    def accept():
        if not active.isChecked():
            workspace.backgauge_model = None
            workspace._backgauge_settings.setValue("backgauge/enabled", False)
            workspace.status.setText(
                tr("Модель упора скрыта. Расчёты последовательности не зависят от упора.")
            )
            workspace._backgauge_preview_cache.clear()
            workspace._machine_gauge_envelope_cache = None
            workspace._update_operations_visual_state()
            workspace.draw()
            dlg.accept()
            return
        if not bends:
            W.QMessageBox.warning(dlg, tr("Нет детали"), tr("Сначала загрузите деталь."))
            return
        # Catch duplicate stations in the GUI before model construction.
        # Do not silently disable the second gauge or shift it to a
        # fabricated position. The operator must choose a real station.
        if (not auto.isChecked() and cad_mode.isChecked() and second_enabled.isChecked()
                and abs(second_station.value() - station.value()) < 1e-6):
            W.QMessageBox.warning(
                dlg, tr("Два упора на одной позиции"),
                tr("Первый и второй CAD-упоры имеют одинаковую координату Xₘ: {position:.2f} мм.\n\nДля одного упора снимите галочку «Добавить второй CAD упор».\nДля двух упоров укажите реальное положение второго по Xₘ.",
                   position=float(station.value())),
            )
            second_station.setFocus()
            second_station.selectAll()
            return
        second_x = float(second_station.value())
        if auto.isChecked() and abs(second_x - station.value()) < 1e-6:
            second_x = station.value() + (100.0 if station.value() <= 9900.0 else -100.0)
        targets = {}
        try:
            for row in range(table.rowCount() if not auto.isChecked() else 0):
                bid = table.item(row, 0).text()
                item_s, item_z = table.item(row, 1), table.item(row, 2)
                val_s = item_s.text().strip() if item_s else ""
                val_z = item_z.text().strip() if item_z else ""
                if not val_s or not val_z:
                    raise ValueError(tr("Не указаны обе координаты заднего упора для {bend}", bend=bid))
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
                    second_x
                    if cad_mode.isChecked() and second_enabled.isChecked()
                    else None
                ),
            )
        except (ValueError, TypeError) as exc:
            W.QMessageBox.warning(dlg, tr("Недопустимая конфигурация"), tr_message(exc))
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
                    dlg, tr("CAD задний упор не загружен"),
                    tr("Невозможно использовать реальный CAD упор: {error}\nРежим не сохранён. Исправьте CAD-модель.", error=exc),
                )
                return
        workspace.backgauge_model = new_model
        workspace._backgauge_settings.setValue(
            "backgauge/config_json", json.dumps(new_model.to_dict())
        )
        workspace._backgauge_settings.setValue("backgauge/enabled", True)
        workspace.status.setText(
            (tr("Задний упор: визуальная установка по согнутому машинному сечению.")
             if auto.isChecked() else
             tr("Задний упор: ручные координаты для {count} гибов.", count=len(targets)))
            + tr(" Столкновения и контакт не проверяются.")
        )
        workspace._backgauge_preview_cache.clear()
        workspace._machine_gauge_envelope_cache = None
        workspace._ergonomic_loading_cache_key = None
        workspace._update_operations_visual_state()
        workspace.draw()
        dlg.accept()

    save.clicked.connect(accept)
    cancel.clicked.connect(dlg.reject)
    if modal_parent is None:
        embed_task_dialog(workspace, dlg, tr("Задний упор"), "backgauge")
    else:
        try:
            (getattr(dlg, "exec", None) or dlg.exec_)()
        finally:
            dlg.deleteLater()
