"""Native Tasks editor for preparing station layouts for the current part."""
from dataclasses import replace
from uuid import uuid4

from freecad.bendseq.core.kinematics2d.bending_stations import BendingStation, StationSetup
from freecad.bendseq.core.kinematics2d.segment_assembly_planner import read_inventory
from freecad.bendseq.core.kinematics2d.station_layout import (
    auto_layout_stations, calculate_station_segments, validate_station_layout,
)
from freecad.bendseq.ui.localization import tr
from freecad.bendseq.core.kinematics2d.developer_debug_log import debug_event


def show_station_editor(workspace, embed):
    from freecad.bendseq.commands.pressbrake_2d import _qt
    from freecad.bendseq.user_data_migration import relocated_path

    core, gui, qt = _qt()
    session = workspace.session
    if session is None:
        workspace.status.setText(tr("load_a_part_before_calculating_a_layout"))
        return
    dialog = qt.QDialog(workspace)
    layout = qt.QVBoxLayout(dialog)
    notice = qt.QLabel(tr("stations_draft_notice"))
    notice.setWordWrap(True)
    layout.addWidget(notice)
    form = qt.QFormLayout()
    layout.addLayout(form)

    def number(value=0.0, minimum=0.0):
        field = qt.QDoubleSpinBox()
        field.setRange(minimum, 100000.0)
        field.setDecimals(2)
        field.setValue(value)
        return field

    saved = session.station_draft
    x_min = number(saved.machine_x_mm[0] if saved else 0.0, -100000.0)
    x_max = number(saved.machine_x_mm[1] if saved else 0.0, -100000.0)
    gap = number(saved.minimum_gap_mm if saved else 0.0)
    for label, field in (("stations_machine_left", x_min), ("stations_machine_right", x_max),
                         ("stations_min_gap", gap)):
        form.addRow(tr(label), field)
    selected = qt.QComboBox()
    form.addRow(tr("stations_station"), selected)
    name = qt.QLineEdit()
    x_position = number(minimum=-100000.0)
    working_length = number(minimum=0.01)
    punch, die = qt.QComboBox(), qt.QComboBox()
    reverse, enabled = qt.QCheckBox(), qt.QCheckBox()
    enabled.setChecked(True)
    for kind, field in (("punch", punch), ("die", die)):
        for tool in workspace.tool_library.list_tools(kind.upper()):
            field.addItem(tool.name, tool)
    for label, field in (("name", name), ("punch", punch), ("die", die),
                         ("stations_x", x_position), ("stations_span", working_length),
                         ("stations_reverse_punch", reverse), ("enabled", enabled)):
        form.addRow(tr(label), field)
    section_info = qt.QLabel()
    section_info.setWordWrap(True)
    layout.addWidget(section_info)

    scene = qt.QGraphicsScene(dialog)
    class StationChart(qt.QGraphicsView):
        drag_start = None

        def mousePressEvent(self, event):
            point = event.position().toPoint() if hasattr(event, "position") else event.pos()
            item = self.itemAt(point)
            if event.button() == core.Qt.LeftButton and item is not None and item.data(0):
                self.drag_start = item.data(0), self.mapToScene(point).x()
                event.accept()
                return
            super().mousePressEvent(event)

        def mouseReleaseEvent(self, event):
            if self.drag_start is None:
                super().mouseReleaseEvent(event)
                return
            station_id, start = self.drag_start
            self.drag_start = None
            point = event.position().toPoint() if hasattr(event, "position") else event.pos()
            index = selected.findData(station_id)
            if index >= 0:
                selected.setCurrentIndex(index)
                lo, hi = session.station_draft.machine_x_mm
                delta = (self.mapToScene(point).x() - start) * (hi - lo) / 900
                if abs(delta) >= 0.01:
                    x_position.setValue(x_position.value() + delta)
                    save("move")
            event.accept()

        def resizeEvent(self, event):
            super().resizeEvent(event)
            if session.station_draft is not None:
                self.fitInView(scene.sceneRect(), core.Qt.KeepAspectRatio)

    chart = StationChart(scene)
    chart.setMinimumHeight(110)
    chart.setMaximumHeight(140)
    layout.addWidget(chart)

    operation = qt.QComboBox()
    for step in session.controller.operation_steps:
        operation.addItem(step.operation_id, step.operation_id)
    assigned = qt.QComboBox()
    assignment_form = qt.QFormLayout()
    layout.addLayout(assignment_form)
    assignment_form.addRow(tr("operation"), operation)
    assignment_form.addRow(tr("stations_station"), assigned)
    feedback = qt.QLabel()
    feedback.setWordWrap(True)
    layout.addWidget(feedback)

    def current_station():
        setup = session.station_draft
        station_id = selected.currentData()
        return next((s for s in setup.stations if s.station_id == station_id), None) if setup else None

    def draw():
        scene.clear()
        setup = session.station_draft
        if setup is None:
            return
        start, end = setup.machine_x_mm
        scale = 900 / (end - start)
        scene.setSceneRect(0, -4, 900, 88)
        scene.addLine(0, 67, 900, 67)
        for station in setup.stations:
            color = gui.QColor(station.color if station.enabled else "#888888")
            for kind, y in (("punch", 22), ("die", 46)):
                for lo, hi in station.spans_xm(kind):
                    rect = scene.addRect((lo - start) * scale, y, (hi - lo) * scale,
                                         13, gui.QPen(color), gui.QBrush(color))
                    rect.setData(0, station.station_id)
                    rect.setToolTip(f"{station.name} · {tr(kind)} · {lo:g}–{hi:g} mm")
        try:
            cfg = workspace._current_clamp_config()
        except (OSError, ValueError) as exc:
            feedback.setText(str(exc))
            return
        for center in cfg.centres_mm:
            lo = max(start, center - cfg.width_mm / 2)
            hi = min(end, center + cfg.width_mm / 2)
            if hi > lo:
                scene.addRect((lo - start) * scale, 1, (hi - lo) * scale, 9)
        chart.fitInView(scene.sceneRect(), core.Qt.KeepAspectRatio)

    def refresh_assignment(*_args):
        setup = session.station_draft
        assigned.clear()
        assigned.addItem("—", None)
        if setup is not None:
            for station in setup.stations:
                if station.enabled:
                    assigned.addItem(station.name, station.station_id)
            station = setup.assigned_station(operation.currentData())
            if station is not None:
                assigned.setCurrentIndex(assigned.findData(station.station_id))

    def load_fields(*_args):
        refresh_assignment()
        station = current_station()
        if station is None:
            section_info.clear()
            return
        name.setText(station.name)
        x_position.setValue(station.x_mm)
        coverage = station.working_intervals()
        working_length.setValue(max((hi - lo for lo, hi in coverage), default=0.01))
        reverse.setChecked(station.punch_reversed)
        enabled.setChecked(station.enabled)
        for field, tool_id in ((punch, station.punch_id), (die, station.die_id)):
            field.setCurrentIndex(next((i for i in range(field.count())
                if field.itemData(i).tool_id == tool_id), -1))
        rows = []
        for kind in ("punch", "die"):
            sections = ", ".join(f"{size:g}×{count}"
                                 for size, count in station.segments(kind).items())
            rows.append(f"{tr(kind)}  {sections}")
        rows.append(", ".join(op for op, station_id in session.station_draft.assignments
                              if station_id == station.station_id))
        section_info.setText("\n".join(rows))

    def refresh(station_id=None):
        selected.blockSignals(True)
        selected.clear()
        for station in session.station_draft.stations:
            selected.addItem(station.name, station.station_id)
        index = selected.findData(station_id)
        if index >= 0:
            selected.setCurrentIndex(index)
        selected.blockSignals(False)
        load_fields()
        draw()

    def draft():
        old = session.station_draft
        return StationSetup(
            (x_min.value(), x_max.value()), old.stations if old else (),
            gap.value(), old.assignments if old else (),
        )

    def stock():
        return {
            (kind, field.itemData(i).tool_id): read_inventory(
                relocated_path(field.itemData(i).source_file))
            for kind, field in (("punch", punch), ("die", die))
            for i in range(field.count())
        }

    def save(action):
        if workspace.session is not session:
            dialog.reject()
            return
        try:
            setup = draft()
            previous = current_station()
            selected_id = selected.currentData()
            inventory = stock()
            clamps = workspace._current_clamp_config()
            if action == "layout":
                setup = auto_layout_stations(setup, inventory, clamps)
            elif action == "remove":
                setup = replace(setup,
                    stations=tuple(s for s in setup.stations if s.station_id != selected_id),
                    assignments=tuple(pair for pair in setup.assignments if pair[1] != selected_id))
            elif action == "move":
                if previous is None:
                    return
                setup = replace(setup, stations=tuple(
                    replace(station, x_mm=x_position.value()) if station.station_id == selected_id
                    else station for station in setup.stations))
            elif action == "assign":
                op_id = operation.currentData()
                if op_id not in {step.operation_id for step in session.controller.operation_steps}:
                    raise ValueError(tr("stations_stale_operation"))
                assignments = dict(setup.assignments)
                assignments.pop(op_id, None)
                if assigned.currentData() is not None:
                    assignments[op_id] = assigned.currentData()
                setup = replace(setup, assignments=tuple(assignments.items()))
            else:
                tools = punch.currentData(), die.currentData()
                if not all(tools):
                    raise ValueError(tr("stations_choose_tools"))
                p, d = tools
                duplicate = action == "duplicate"
                new = previous is None or action == "add" or duplicate
                selected_id = uuid4().hex if new else previous.station_id
                span = working_length.value()
                # An empty station is never committed. The nominal interval
                # below is only an input to the real stock planner.
                nominal = ((-span / 2, span / 2),)
                label = name.text().strip() or f"S{len(setup.stations) + 1}"
                setup = replace(setup, assignments=tuple(
                    pair for pair in setup.assignments
                    if pair[1] != selected_id or enabled.isChecked()))
                if duplicate and previous is not None:
                    station = replace(previous, station_id=selected_id, name=label,
                                      x_mm=x_position.value(), enabled=enabled.isChecked())
                else:
                    station = BendingStation(
                        selected_id, label, p.tool_id, d.tool_id, x_position.value(),
                        nominal, nominal, reverse.isChecked(), enabled.isChecked(),
                    )
                    station = calculate_station_segments(setup, station, span, inventory, clamps)
                stations = setup.stations + (station,) if new else tuple(
                    station if s.station_id == selected_id else s for s in setup.stations)
                setup = replace(setup, stations=stations)
            validate_station_layout(setup, inventory, clamps)
        except (ValueError, TypeError, OSError) as exc:
            debug_event("STATION_LAYOUT_REJECTED", action=action, error=str(exc))
            feedback.setText(str(exc))
            return
        session.station_draft = setup
        workspace._update_station_assignment_column()
        refresh(selected_id)
        feedback.setText(tr("stations_layout_saved"))

    for key, action in (
        ("stations_add", "add"), ("stations_duplicate", "duplicate"),
        ("stations_calculate", "calculate"), ("stations_move", "move"), ("remove", "remove"),
        ("stations_auto_layout", "layout"), ("stations_assign", "assign"),
    ):
        button = qt.QPushButton(tr(key))
        button.clicked.connect(lambda _checked=False, action=action: save(action))
        layout.addWidget(button)
    transfer_timer = core.QTimer(dialog)

    def stop_transfer(*_args):
        transfer_timer.stop()
        if getattr(workspace, "_station_transfer_timer", None) is transfer_timer:
            workspace._station_transfer_timer = None

    dialog.finished.connect(stop_transfer)

    def preview(animate=False, whole_cycle=False):
        if workspace.session is not session:
            dialog.reject()
            return
        try:
            feedback.setText(workspace._show_installed_station_preview(
                operation.currentData(), transfer_timer=transfer_timer if animate else None,
                cycle_timer=transfer_timer if whole_cycle else None,
            ))
        except Exception as exc:
            feedback.setText(str(exc))

    preview_button = qt.QPushButton(tr("stations_preview"))
    preview_button.clicked.connect(lambda: preview())
    layout.addWidget(preview_button)
    transfer_button = qt.QPushButton(tr("stations_transfer_preview"))
    transfer_button.clicked.connect(lambda: preview(True))
    layout.addWidget(transfer_button)

    def toggle_cycle():
        if transfer_timer.isActive():
            stop_transfer()
        else:
            preview(whole_cycle=True)

    cycle_button = qt.QPushButton(tr("stations_cycle_preview"))
    cycle_button.clicked.connect(toggle_cycle)
    layout.addWidget(cycle_button)
    selected.currentIndexChanged.connect(load_fields)
    operation.currentIndexChanged.connect(refresh_assignment)
    if saved is not None:
        refresh()
    embed(workspace, dialog, tr("stations_title"), "station_editor")
    draw()
