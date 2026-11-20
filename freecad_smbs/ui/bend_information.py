"""Bend schedule and flat-pattern geometry for a FreeCAD drawing."""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
from math import cos, dist, radians, sin
from pathlib import Path
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


def _flat_techdraw_views(document, drawing):
    """Reuse top views of this source, or make a dedicated TechDraw page."""
    import FreeCAD as App
    import TechDraw  # Registers native TechDraw document types.

    views = [
        (page, view)
        for page in document.Objects if page.TypeId == "TechDraw::DrawPage"
        for view in page.Views
        if (view.TypeId == "TechDraw::DrawViewPart"
            and list(view.Source) == [drawing] and not view.Perspective
            and view.Direction.z > 0
            and abs(view.Direction.x) + abs(view.Direction.y) < 1e-7
            and view.XDirection.x > 0 and abs(view.XDirection.y) < 1e-7)
    ]
    if views:
        return views

    page = document.addObject("TechDraw::DrawPage", "SMBS_BendPage")
    page.Label = "SMBS — чертёж гибки"
    template = document.addObject("TechDraw::DrawSVGTemplate", "SMBS_BendTemplate")
    template.Template = str(Path(App.getResourceDir()) /
                            "Mod/TechDraw/Templates/A3_Landscape_blank.svg")
    page.Template = template
    view = document.addObject("TechDraw::DrawViewPart", "SMBS_BendView")
    view.Label = "Развёртка с линиями гиба"
    view.Direction = App.Vector(0, 0, 1)
    view.XDirection = App.Vector(1, 0, 0)
    view.Source = [drawing]
    view.ScaleType = "Custom"
    bounds = drawing.Shape.BoundBox
    view.Scale = min(1.0, 290 / max(1.0, bounds.XLength),
                     230 / max(1.0, bounds.YLength))
    page.addView(view)
    view.X, view.Y = 210, 148.5
    return [(page, view)]


def _update_bend_callouts(document, page, view, session, center):
    """Anchor native text callouts to the view without adding OCC edges."""
    prefix = f"SMBS_Callout_{view.Name}_"
    old = {obj.Name: obj for obj in page.Views
           if obj.TypeId == "TechDraw::DrawViewBalloon"
           and obj.Name.startswith(prefix) and obj.SourceView == view}
    rotation = radians(view.Rotation.Value)
    c, s = cos(rotation), sin(rotation)
    scale = max(float(view.Scale), 1e-9)
    pitch = 4.0 / scale
    boxes = []
    for index, op in enumerate(session.controller.operation_steps, 1):
        start, end = session.fold_state.bends[op.bend_id].axis_xyz
        dx = (start[0] + end[0]) / 2 - center.x
        dy = (start[1] + end[1]) / 2 - center.y
        x, y = c * dx - s * dy, s * dx + c * dy
        direction = "Down" if op.target_angle_deg < 0 else "Up"
        label = f"{index}. {OPERATION_NAMES[op.kind]} {op.target_angle_deg:+.2f}° {direction}"
        # Reserve generous text extents in view coordinates, with a leader
        # back to the bend midpoint when a label is moved to avoid overlap.
        width = len(label) * 1.6 / scale
        left, baseline = x + pitch, y + pitch
        while any(left < r and left + width > l and abs(baseline - b) < pitch
                  for l, r, b in boxes):
            baseline += pitch
        boxes.append((left, left + width, baseline))

        stem = prefix + op.operation_id.replace(".", "_")
        callout = old.pop(stem, None)
        if callout is None:
            callout = document.addObject("TechDraw::DrawViewBalloon", stem)
            callout.SourceView = view
            page.addView(callout)
        callout.Label = label
        callout.Text = label
        callout.BubbleShape = "None"
        callout.EndType = "Dot"
        callout.TextWrapLen = -1
        callout.ViewObject.Fontsize = 2.5
        callout.OriginX, callout.OriginY = x, y
        callout.X, callout.Y = left + width / 2, baseline
    for callout in old.values():
        page.removeView(callout)
        document.removeObject(callout.Name)


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
        for page, view in _flat_techdraw_views(document, drawing):
            _update_bend_callouts(document, page, view, session, shape.BoundBox.Center)
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
            feedback.setText("Обновлены развёртка, таблица и подписи на листе TechDraw.")

    export.clicked.connect(save)
    embed(workspace, dialog, "Информация о гибах", "bend_information")
