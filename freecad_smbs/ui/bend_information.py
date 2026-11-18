"""Bend schedule and flat-pattern geometry for a FreeCAD drawing."""
from __future__ import annotations

HEADERS = ("№ гиба", "Линия", "Упор S, мм", "Полка по развёртке, мм", "Угол, °", "Набор оснастки")


def bend_rows(workspace):
    setup = workspace.tooling_setup
    rows = []
    for index, bend_id in enumerate(workspace.session.bend_order):
        bend = workspace.session.fold_state.bends[bend_id]
        gauge = workspace._backgauge_preview_position(index)
        stop = gauge[0] if gauge else None
        flange = workspace.session.local_bend_dimensions_mm.get(bend_id)
        tools = []
        for kind, label in (("punch", "Пуансон"), ("die", "Матрица")):
            profile = getattr(setup, kind)
            spans = getattr(setup, f"{kind}_section_spans_xm")
            if profile is None:
                continue
            sizes = (" + ".join(f"{right - left:g}" for left, right in spans)
                     if spans else "виртуальная длина" if setup.match_active_bend_length
                     else "цельный инструмент")
            tools.append(f"{label} {profile.tool_id} ({sizes})")
        rows.append((str(index + 1), bend_id,
                     f"{stop.s_mm:.2f}" if stop is not None else "—",
                     f"{flange:.2f}" if flange is not None else "—",
                     f"{bend.angle_deg:+.2f}", " / ".join(tools) or "—"))
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
    for row in rows:
        start, end = session.fold_state.bends[row[1]].axis_xyz
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
        for line, row in enumerate((HEADERS, *rows), 1):
            for column, value in zip("ABCDEF", row):
                sheet.set(f"{column}{line}", value)
        sheet.setStyle("A1:F1", "bold", "add")
        sheet.setColumnWidth("F", 520)
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
