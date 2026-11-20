"""Bend schedule and flat-pattern geometry for a FreeCAD drawing."""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
from math import dist
from string import ascii_uppercase

from freecad_smbs.integration.document_group import group_document_objects
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


def _bend_label_wires(session, outline_shape):
    """Make text part of the projected geometry so TechDraw can show it."""
    import FreeCAD as App
    import Part
    from freecad_smbs.commands.pressbrake_2d import _qt

    core, gui, _ = _qt()
    bounds = outline_shape.BoundBox
    height = max(1.8, min(8.0, max(bounds.XLength, bounds.YLength) * 0.018))
    font = gui.QFont()
    font.setPixelSize(24)
    labels = {}
    for index, op in enumerate(session.controller.operation_steps, 1):
        direction = "Down" if op.target_angle_deg < 0 else "Up"
        labels.setdefault(op.bend_id, []).append(
            f"{index}. {OPERATION_NAMES[op.kind]} {op.target_angle_deg:+.2f}° {direction}"
        )

    scale = height / gui.QFontMetricsF(font).height()
    drawn = []
    for bend_id, lines in labels.items():
        start, end = session.fold_state.bends[bend_id].axis_xyz
        x, y, _ = ((a + b) / 2 for a, b in zip(start, end))
        path = gui.QPainterPath()
        for row, line in enumerate(lines, 1):
            path.addText(0, row * height / scale, font, line)
        box = path.boundingRect()
        left, top = x + height * 0.65, -y - height * 0.65
        rect = core.QRectF(left, top, box.width() * scale, box.height() * scale)
        while any(rect.adjusted(-height * 0.2, -height * 0.2,
                                height * 0.2, height * 0.2).intersects(other)
                  for other in drawn):
            rect.translate(0, height)
        drawn.append(rect)
        anchor = App.Vector(rect.left() - height * 0.12,
                            -rect.center().y(), 0)
        yield Part.makeLine(App.Vector(x, y, 0), anchor)
        for polygon in path.toSubpathPolygons():
            points = []
            for point in polygon:
                mapped = App.Vector(
                    rect.left() + (point.x() - box.left()) * scale,
                    -rect.top() - (point.y() - box.top()) * scale, 0,
                )
                if not points or (mapped - points[-1]).Length > 1e-7:
                    points.append(mapped)
            if len(points) >= 3:
                yield Part.makePolygon(points)


def export_bend_information(document, session, rows):
    import FreeCAD as App
    import Part

    outline = tuple(getattr(session.panel_build, "outline_vertices_xy", ()))
    edges = []
    exact_contours = getattr(session.unfold_data, "flat_contours", None)
    if exact_contours is not None:
        edges.extend(exact_contours.Edges)
    elif len(outline) >= 3:
        points = [App.Vector(x, y, 0) for x, y in outline]
        for start, end in zip(points, (*points[1:], points[0])):
            if (end - start).Length > 1e-7:
                edges.append(Part.makeLine(start, end))
    for bend_id in dict.fromkeys(row[1] for row in rows):
        start, end = session.fold_state.bends[bend_id].axis_xyz
        edges.append(Part.makeLine(App.Vector(*start), App.Vector(*end)))
    shape = Part.makeCompound(edges)
    shape = Part.makeCompound([shape, *_bend_label_wires(session, shape)])

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
        drawing.Label = "SMBS — развёртка с последовательностью гибов"
        drawing.Shape = shape
        drawing.Visibility = True
        group_document_objects(document)
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
