"""Bend schedule and flat-pattern geometry for a FreeCAD drawing."""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
from math import dist
from string import ascii_uppercase

from smbs_core.geometry_backend.io.clamp_validator import ClampValidatorError
from smbs_core.kinematics2d.segment_assembly_planner import plan_pair_for_bend

HEADERS = ("№ операции", "Линия", "Операция", "Этап", "Упор S, мм",
           "Полка по развёртке, мм", "Целевой угол, °",
           "Пуансон, мм × шт.", "Матрица, мм × шт.")
OPERATION_NAMES = {"SINGLE": "Гиб", "PREBEND": "Предгиб", "FINISH": "Догиб"}


def _format_segments(lengths):
    counts = Counter(round(length, 6) for length in lengths)
    return " + ".join(f"{length:g}×{count}" for length, count in counts.items())


def bend_rows(workspace):
    session = workspace.session
    controller = session.controller
    staged = controller.has_stages
    setup = workspace.tooling_setup
    applied = (setup.punch_section_spans_xm, setup.die_section_spans_xm)
    inputs = None
    if not all(applied):
        try:
            punch_stock, die_stock, config = workspace._tool_assembly_inputs(require_pair=False)
            machine = getattr(getattr(workspace, "_preview_3d", None), "_machine", None)
            if not config.centres_mm and machine is not None:
                config = replace(config, positions_mm=tuple(
                    (left + right) / 2 for left, right in machine.clamp_spans
                ))
            inputs = punch_stock, die_stock, config
        except (OSError, ValueError, ClampValidatorError) as exc:
            error = str(exc)
    layouts = {}
    rows = []
    for index, operation in enumerate(controller.operation_steps):
        bend_id = operation.bend_id
        bend = session.fold_state.bends[bend_id]
        # The gauge preview for the unique bend order does not account for
        # partial angles or a different orientation on each staged operation.
        gauge = None if staged else workspace._backgauge_preview_position(index)
        stop = gauge[0] if gauge else None
        flange = session.local_bend_dimensions_mm.get(bend_id)
        plan = {}
        if inputs is not None:
            length = dist(*bend.axis_xyz)
            if length not in layouts:
                punch_stock, die_stock, config = inputs
                layouts[length] = plan_pair_for_bend(punch_stock, die_stock, length, config)
            plan = layouts[length]
        tools = []
        for kind, spans in zip(("punch", "die"), applied):
            if spans:
                tools.append(_format_segments(right - left for left, right in spans))
                continue
            section_plan = plan.get("punch_preview", plan) if kind == "punch" else plan
            segments = section_plan.get(kind + "_segments", ())
            if segments:
                tools.append(_format_segments(segment["length_mm"] for segment in segments))
            elif getattr(setup, kind) is None:
                tools.append("—")
            else:
                tools.append("Нет раскладки" if inputs is not None else error)
        rows.append((str(index + 1), bend_id, operation.operation_id,
                     OPERATION_NAMES[operation.kind],
                     f"{stop.s_mm:.2f}" if stop is not None else "—",
                     f"{flange:.2f}" if flange is not None else "—",
                     f"{operation.target_angle_deg:+.2f}", *tools))
    return rows


def export_bend_information(document, session, rows):
    import FreeCAD as App
    import Part

    outline = tuple(getattr(session.panel_build, "outline_vertices_xy", ()))
    edges = []
    if len(outline) >= 3:
        points = [App.Vector(x, y, 0) for x, y in outline]
        for start, end in zip(points, (*points[1:], points[0])):
            if (end - start).Length > 1e-7:
                edges.append(Part.makeLine(start, end))
    for bend_id in dict.fromkeys(row[1] for row in rows):
        start, end = session.fold_state.bends[bend_id].axis_xyz
        edges.append(Part.makeLine(App.Vector(*start), App.Vector(*end)))
    shape = Part.makeCompound(edges)

    document.openTransaction("SMBS — таблица гибов и развёртка")
    try:
        sheet = document.getObject("SMBS_BendSchedule")
        if sheet is None:
            sheet = document.addObject("Spreadsheet::Sheet", "SMBS_BendSchedule")
        else:
            sheet.clearAll()
        sheet.Label = "SMBS — таблица гибов"
        columns = ascii_uppercase[:len(HEADERS)]
        for line, row in enumerate((HEADERS, *rows), 1):
            for column, value in zip(columns, row):
                sheet.set(f"{column}{line}", value)
        sheet.setStyle(f"A1:{columns[-1]}1", "bold", "add")
        for column in columns[-2:]:
            sheet.setColumnWidth(column, 200)
        drawing = document.getObject("SMBS_BendDrawing")
        if drawing is None:
            drawing = document.addObject("Part::Feature", "SMBS_BendDrawing")
        drawing.Label = "SMBS — развёртка с линиями гиба"
        drawing.Shape = shape
        drawing.Visibility = False
        document.recompute()
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise


def show_bend_information(workspace, embed):
    from freecad_smbs.commands.pressbrake_2d import _qt

    existing = getattr(workspace, "_task_dialogs", {}).get("bend_information")
    if existing is not None:
        existing.reject()
    _, _, qt = _qt()
    dialog = qt.QDialog(workspace)
    layout = qt.QVBoxLayout(dialog)
    rows = bend_rows(workspace)
    table = qt.QTableWidget(len(rows), len(HEADERS), dialog)
    table.setHorizontalHeaderLabels(HEADERS)
    table.setEditTriggers(qt.QAbstractItemView.NoEditTriggers)
    for row, values in enumerate(rows):
        for col, value in enumerate(values):
            table.setItem(row, col, qt.QTableWidgetItem(value))
    table.resizeColumnsToContents()
    layout.addWidget(table)
    export = qt.QPushButton("Создать развёртку и таблицу для TechDraw")
    layout.addWidget(export)
    feedback = qt.QLabel()
    feedback.setWordWrap(True)
    layout.addWidget(feedback)

    def save():
        try:
            export_bend_information(workspace._bound_smbs_document(), workspace.session,
                                    bend_rows(workspace))
        except (RuntimeError, ValueError, AttributeError) as exc:
            feedback.setText(str(exc))
        else:
            feedback.setText("В документ добавлены развёртка и таблица гибов.")

    export.clicked.connect(save)
    embed(workspace, dialog, "Информация о гибах", "bend_information")
