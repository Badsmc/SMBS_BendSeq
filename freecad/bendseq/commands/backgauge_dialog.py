"""FreeCAD settings for the real FCStd backgauge as visual-only moving geometry.

No gauge collision, penetration, contact or travel check participates in search.
"""
from __future__ import annotations

import json
from freecad.bendseq.core.kinematics2d.backgauge_2d import (
    BackgaugeModel2D, BackgaugePosition2D,
)

from freecad.bendseq.ui.localization import tr, tr_message


def show_backgauge_settings(workspace, *, modal_parent=None):
    from .pressbrake_2d import _qt, activate_task_dialog, embed_task_dialog
    if modal_parent is None and activate_task_dialog(workspace, "backgauge"):
        return
    QtCore, _, W = _qt()
    dlg = W.QDialog(workspace if modal_parent is None else modal_parent)
    dlg.setWindowTitle(tr("smbs_backgauge_fcstd_preview"))
    dlg.resize(680, 560)
    layout = W.QVBoxLayout(dlg)
    notice = W.QLabel(
        tr("the_preview_uses_the_backgauge_fcstd_model_before_bend")
    )
    notice.setWordWrap(True)
    layout.addWidget(notice)
    inspect_btn = W.QPushButton(tr("inspect_backgauge_fcstd_model"))
    layout.addWidget(inspect_btn)

    def inspect_cad():
        from freecad.bendseq.commands.backgauge_cad_inspector import (
            inspect_backgauge_cad,
        )
        try:
            result = inspect_backgauge_cad()
        except Exception as exc:
            W.QMessageBox.warning(
                dlg, tr("backgauge_cad_check_failed"),
                tr_message(exc) + tr("the_model_is_only_needed_for_visualization"),
            )
            return
        W.QMessageBox.information(
            dlg, tr("backgauge_cad_body_and_datum_face_checked"),
            tr("source_object_solids_faces_contact_face_face_status_the",
                source=result["source"], object=result["object"], solids=result["solids"],
                faces=result["faces"], face=result["stop_face_index"], status=result["status"],
            ),
        )

    inspect_btn.clicked.connect(inspect_cad)
    active = W.QCheckBox(tr("show_moving_backgauge_fcstd_model"))
    active.setChecked(workspace.backgauge_model is not None)
    layout.addWidget(active)
    auto = W.QCheckBox(
        tr("automatic_account_for_formed_geometry_and_thickness"))
    model = workspace.backgauge_model
    auto.setChecked(model.automatic if model else True)
    layout.addWidget(auto)
    # CAD geometry is mandatory in this feature; no 2D rectangle fallback.
    cad_mode = W.QCheckBox(tr("use_only_the_backgauge_fcstd_model"))
    cad_mode.setChecked(True)
    cad_mode.setEnabled(False)
    layout.addWidget(cad_mode)
    dims = W.QFormLayout()
    width = W.QDoubleSpinBox()
    width.setRange(0.01, 10000)
    width.setDecimals(2)
    width.setSuffix(tr("mm_bd8bf092"))
    height = W.QDoubleSpinBox()
    height.setRange(0.01, 10000)
    height.setDecimals(2)
    height.setSuffix(tr("mm_bd8bf092"))
    clearance = W.QDoubleSpinBox()
    clearance.setRange(0.0, 500.0)
    clearance.setDecimals(2)
    clearance.setSuffix(tr("mm_bd8bf092"))
    clearance.setValue(model.edge_clearance_mm if model else 0.0)
    dims.addRow(tr("gap_to_part_edge"), clearance)
    z_min = W.QDoubleSpinBox()
    z_max = W.QDoubleSpinBox()
    for control in (z_min, z_max):
        control.setRange(-10000.0, 10000.0)
        control.setDecimals(2)
        control.setSuffix(tr("mm_bd8bf092"))
    z_min.setValue(model.z_min_mm if model else -20.0)
    z_max.setValue(model.z_max_mm if model else 20.0)
    dims.addRow(tr("gauge_z_min"), z_min)
    dims.addRow(tr("gauge_z_max"), z_max)

    feed = W.QDoubleSpinBox()
    feed.setRange(0.0, 1000.0)
    feed.setDecimals(1)
    feed.setSuffix(tr("mm_bd8bf092"))
    feed.setValue(model.initial_feed_preview_mm if model else 0.0)
    feed.setToolTip(
        tr("visual_approach_preview_without_contact_or_collision_checks"))
    dims.addRow(tr("initial_sheet_feed_preview"), feed)
    station = W.QDoubleSpinBox()
    station.setRange(-10000.0, 10000.0)
    station.setDecimals(2)
    station.setSuffix(tr("mm_bd8bf092"))
    station.setValue(model.cad_station_xm_mm if model else 0.0)
    station.setToolTip(
        tr("center_of_one_backgauge_model_along_the_x_bend"))
    dims.addRow(tr("backgauge_station_on_x"), station)
    second_enabled = W.QCheckBox(tr("add_a_second_cad_backgauge"))
    second_enabled.setChecked(
        model is not None and model.cad_second_station_xm_mm is not None
    )
    second_station = W.QDoubleSpinBox()
    second_station.setRange(-10000.0, 10000.0)
    second_station.setDecimals(2)
    second_station.setSuffix(tr("mm_bd8bf092"))
    second_station.setValue(
        model.cad_second_station_xm_mm
        if model and model.cad_second_station_xm_mm is not None
        else (station.value() + 100.0 if station.value() <= 9900.0 else station.value() - 100.0)
    )
    dims.addRow(second_enabled)
    dims.addRow(tr("second_backgauge_on_x"), second_station)

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
    dims.addRow(tr("reference_width_s_center_position"), width)
    dims.addRow(tr("reference_height_z_without_geometry"), height)
    layout.addLayout(dims)
    table = W.QTableWidget(0, 3, dlg)
    table.setObjectName("SMBS_BackgaugeTargets")
    table.setHorizontalHeaderLabels((tr("bend"), tr("s_center_mm"), tr("z_center_mm")))
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
        warning = W.QLabel(tr("load_the_part_before_setting_coordinates"))
        layout.addWidget(warning)
    bottom = W.QHBoxLayout()
    save = W.QPushButton(tr("save"))
    cancel = W.QPushButton(tr("cancel"))
    bottom.addStretch()
    bottom.addWidget(save)
    bottom.addWidget(cancel)
    layout.addLayout(bottom)

    def accept():
        if not active.isChecked():
            workspace.backgauge_model = None
            workspace._backgauge_settings.setValue("backgauge/enabled", False)
            workspace.status.setText(
                tr("backgauge_model_hidden_sequence_calculations_are_independent_of_the")
            )
            workspace._backgauge_preview_cache.clear()
            workspace._machine_gauge_envelope_cache = None
            workspace._update_operations_visual_state()
            workspace.draw()
            dlg.accept()
            return
        if not bends:
            W.QMessageBox.warning(dlg, tr("no_part"), tr("load_a_part_first"))
            return
        # Catch duplicate stations in the GUI before model construction.
        # Do not silently disable the second gauge or shift it to a
        # fabricated position. The operator must choose a real station.
        if (not auto.isChecked() and cad_mode.isChecked() and second_enabled.isChecked()
                and abs(second_station.value() - station.value()) < 1e-6):
            W.QMessageBox.warning(
                dlg, tr("two_backgauges_at_the_same_position"),
                tr("the_two_cad_backgauges_have_the_same_x_coordinate",
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
                    raise ValueError(tr("both_backgauge_coordinates_are_required_for", bend=bid))
                targets[bid] = BackgaugePosition2D(
                    float(val_s.replace(",", ".")),
                    float(val_z.replace(",", ".")),
                )
            new_model = BackgaugeModel2D(
                z_min_mm=float(z_min.value()),
                z_max_mm=float(z_max.value()),
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
            W.QMessageBox.warning(dlg, tr("invalid_configuration"), tr_message(exc))
            return
        if new_model.cad_geometry_enabled:
            try:
                from freecad.bendseq.core.kinematics2d.backgauge_cad_3d import (
                    BackgaugeCAD3D,
                )
                BackgaugeCAD3D(
                    station_xm_mm=new_model.cad_station_xm_mm,
                    second_station_xm_mm=new_model.cad_second_station_xm_mm,
                ).preview_positioned_shapes(100.0, 0.75)
            except Exception as exc:
                W.QMessageBox.warning(
                    dlg, tr("cad_backgauge_not_loaded"),
                    tr("the_real_cad_backgauge_cannot_be_used_the_mode", error=exc),
                )
                return
        workspace.backgauge_model = new_model
        workspace._backgauge_settings.setValue(
            "backgauge/config_json", json.dumps(new_model.to_dict())
        )
        workspace._backgauge_settings.setValue("backgauge/enabled", True)
        workspace.status.setText(
            (tr("backgauge_visual_placement_based_on_the_bent_machine_section")
             if auto.isChecked() else
             tr("backgauge_manual_coordinates_for_bends", count=len(targets)))
            + tr("collisions_and_contact_are_not_checked")
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
        embed_task_dialog(workspace, dlg, tr("backgauge"), "backgauge")
    else:
        try:
            (getattr(dlg, "exec", None) or dlg.exec_)()
        finally:
            dlg.deleteLater()
