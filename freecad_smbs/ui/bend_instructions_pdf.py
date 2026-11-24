"""Standalone operator sheets for the current SMBS bend sequence."""
from html import escape
from math import dist
from pathlib import Path
from tempfile import TemporaryDirectory

from freecad_smbs.ui.localization import tr
from smbs_core.kinematics2d.gauge_preview import (
    centered_gauge_stations, gauge_rear_envelope_from_faces, visual_gauge_target,
)
from smbs_core.kinematics2d.machine_pose_contract import compute_machine_pose
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.preview_mesh_3d import build_sheet_preview_faces
from smbs_core.kinematics2d.staged_orientation import reverse_axis_for_operation


def _operation_faces(session, state, bend_id, phase, reverse, rear_frame):
    placement = compute_machine_pose(
        state, bend_id, phase=phase,
        support_plane_zm=state.bends[bend_id].bend_zone.outer_radius_mm,
        reverse_axis=reverse, rear_panel_vertices_xy=rear_frame,
    )
    return build_sheet_preview_faces(
        state, session.panel_vertices_xy, bend_id, None,
        machine_placement=placement,
        panel_meshes=getattr(session.unfold_data, "panel_meshes", None),
    )


def _diagram(qt_core, gui, faces, extent, bend_length, pins=()):
    # Fixed machine viewpoint, shared framing for both images on the page.
    def project(s, x, z):
        return x + 0.55 * s, 0.26 * x - 0.48 * s - z

    left, top, right, bottom = extent
    width, height = 440, 280
    scale = min((width - 28) / max(1, right - left), (height - 28) / max(1, bottom - top))

    def point(vertex):
        x, y = project(*vertex)
        return qt_core.QPointF(width / 2 + (x - (left + right) / 2) * scale,
                              height / 2 + (y - (top + bottom) / 2) * scale)

    image = gui.QImage(width, height, gui.QImage.Format_RGB32)
    image.fill(gui.QColor("#f3f6fa"))
    painter = gui.QPainter(image)
    try:
        painter.setRenderHint(gui.QPainter.Antialiasing)
        for face in sorted(faces, key=lambda f: sum(
            s - 0.4 * x - z for s, x, z in f.vertices
        ) / len(f.vertices), reverse=True):
            path = gui.QPainterPath()
            path.setFillRule(qt_core.Qt.OddEvenFill)
            for ring in (face.vertices, *face.holes):
                path.addPolygon(gui.QPolygonF([point(v) for v in ring]))
                path.closeSubpath()
            painter.setBrush(gui.QColor("#bdd2ea"))
            painter.setPen(gui.QPen(gui.QColor("#476389"), 0.75))
            painter.drawPath(path)

        pen = gui.QPen(gui.QColor("#147363"), 2)
        pen.setStyle(qt_core.Qt.DashLine)
        painter.setPen(pen)
        painter.drawLine(point((0, -bend_length / 2, 0)), point((0, bend_length / 2, 0)))
        painter.setPen(gui.QPen(gui.QColor("#914418"), 1.2))
        painter.setBrush(gui.QColor("#f4ad62"))
        for pin in pins:
            painter.drawEllipse(point(pin), 5, 5)
    finally:
        painter.end()
    return image


def _diagram_extent(before, after, length, pins):
    vertices = [(0, -length / 2, 0), (0, length / 2, 0), *pins]
    vertices.extend(vertex for face in (*before, *after) for vertex in face.vertices)
    xs = [x + 0.55 * s for s, x, _ in vertices]
    ys = [0.26 * x - 0.48 * s - z for s, x, z in vertices]
    return min(xs), min(ys), max(xs), max(ys)


def _tool_line(setup, kind, segments):
    tool = getattr(setup, kind)
    if tool is None:
        return "—"
    title = tool.name if tool.name == tool.tool_id else f"{tool.name} ({tool.tool_id})"
    spans = getattr(setup, f"{kind}_section_spans_xm")
    status = tr("Применённая раскладка") if spans else tr("Предложение по сегментам")
    return f"{title} · {status} — {segments}"


def export_bend_instructions_pdf(workspace, path, qt_core, gui, rows):
    """Export one setup page per operation without modifying the active document."""
    session = workspace.session
    operations = session.controller.operation_steps
    prefix = tuple(workspace._manual_sequence_prefix)
    if prefix and not session.controller.has_stages:
        operations = operations[:len(prefix)]
    if not operations:
        raise ValueError(tr("Нет выбранных гибов."))

    orientations = workspace._effective_operation_orientation_map()
    state = FoldState(session.graph, session.fixed_panel)
    document = gui.QTextDocument()
    document.setDefaultStyleSheet(
        "body {font-family: sans-serif; color: #172b44;} "
        "h1 {font-size: 23pt;} h2 {font-size: 12pt;} p {font-size: 10pt;} "
        ".muted {color: #435570;}"
    )
    pages = []
    diagrams = []
    for index, (operation, row) in enumerate(zip(operations, rows[:len(operations)], strict=True)):
        bid = operation.bend_id
        length = dist(*state.bends[bid].axis_xyz)
        reverse = reverse_axis_for_operation(orientations, bid, operation.operation_id)
        rear_frame = workspace._rear_frame_for_preview(session.bend_order.index(bid))
        initial_angle = state.angles[bid]
        before = _operation_faces(session, state, bid, "APPROACH", reverse, rear_frame)
        state.set_angle(bid, operation.target_angle_deg)
        after = _operation_faces(session, state, bid, "BOTTOM", reverse, rear_frame)

        stations, stop, note = (), None, ""
        model = workspace.backgauge_model
        if model is not None and model.cad_geometry_enabled:
            if not session.controller.has_stages:
                target = workspace._backgauge_preview_position(index)
                if target:
                    stop, note = target
                    stations = workspace._gauge_stations_for_index(index)
            elif model.automatic:
                stations = centered_gauge_stations(before, len(workspace._backgauge_stations()))
                envelopes = tuple(gauge_rear_envelope_from_faces(before, x) for x in stations)
                stop, note = visual_gauge_target(model, bid, envelopes)
            elif not model.part_label or model.part_label == session.part_label:
                stations = workspace._backgauge_stations()
                stop, note = visual_gauge_target(model, bid, ())

        pins = tuple((stop.s_mm, x, stop.z_mm) for x in stations) if stop is not None else ()
        extent = _diagram_extent(before, after, length, pins)
        for label, faces, markers in (("before", before, pins), ("after", after, ())):
            name = f"smbs-{index}-{label}.png"
            diagrams.append((name, _diagram(qt_core, gui, faces, extent, length, markers)))

        gauge_text = "—" if stop is None else (
            f"S = {stop.s_mm:.2f} mm · Z = {stop.z_mm:+.2f} mm"
            + (" · X = " + ", ".join(f"{x:+.2f}" for x in stations) + " mm" if stations else "")
        )
        next_page = ' style="page-break-before: always;"' if index else ""
        pages.append(
            f'<h1{next_page}>{escape(tr("Карта гибки"))} · {index + 1}/{len(operations)}</h1>'
            f'<h2>{escape(session.part_label)} · {escape(operation.operation_id)} · {escape(row[3])}</h2>'
            f'<p>{escape(tr("Угол гиба"))} <b>{initial_angle:+.2f}° → {operation.target_angle_deg:+.2f}°</b>'
            f' · {escape(tr("Разворот детали"))} <b>{180 if reverse else 0}°</b></p>'
            f'<p><b>{escape(tr("Пуансон"))}</b> {escape(_tool_line(workspace.tooling_setup, "punch", row[-2]))}</p>'
            f'<p><b>{escape(tr("Матрица"))}</b> {escape(_tool_line(workspace.tooling_setup, "die", row[-1]))}</p>'
            f'<p><b>{escape(tr("Задний упор"))}</b> {escape(gauge_text)} {escape(note)}</p>'
            f'<h2>{escape(tr("Установка перед гибом"))}</h2>'
            f'<p><img width="440" height="280" src="smbs-{index}-before.png"></p>'
            f'<h2>{escape(tr("Форма после гиба"))}</h2>'
            f'<p><img width="440" height="280" src="smbs-{index}-after.png"></p>'
            f'<p class="muted">{escape(tr("Пунктир — линия оснастки. Точки — лапки заднего упора."))}</p>'
            f'<p class="muted">{escape(tr("Схема показывает ориентацию и форму детали и не подтверждает отсутствие коллизий."))}</p>'
        )

    document.setHtml("<html><body>" + "".join(pages) + "</body></html>")
    for name, image in diagrams:
        document.addResource(gui.QTextDocument.ImageResource, qt_core.QUrl(name), image)
    destination = Path(path).absolute()
    with TemporaryDirectory(dir=destination.parent) as tmp:
        output = Path(tmp) / "bend-instructions.pdf"
        writer = gui.QPdfWriter(str(output))
        writer.setResolution(96)
        writer.setPageLayout(gui.QPageLayout(
            gui.QPageSize(gui.QPageSize.A4), gui.QPageLayout.Portrait,
            qt_core.QMarginsF(12, 12, 12, 12),
        ))
        writer.setTitle(tr("Карта гибки") + " · " + session.part_label)
        print_document = getattr(document, "print_", None) or document.print
        print_document(writer)
        del writer
        if not output.is_file() or output.stat().st_size == 0:
            raise OSError(tr("Не удалось сохранить PDF."))
        output.replace(destination)
