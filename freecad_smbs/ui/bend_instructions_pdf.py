"""Standalone operator sheets for the current SMBS bend sequence."""
from dataclasses import replace
from html import escape
from math import dist
from pathlib import Path
from tempfile import TemporaryDirectory

from freecad_smbs.ui.coin3d_machine import CLAMP_HEIGHT_MM, load_frame_meshes, load_gauge_mesh
from freecad_smbs.ui.localization import tr
from smbs_core.geometry_backend.io.clamp_validator import ClampValidatorError, load_clamp_config
from smbs_core.kinematics2d.coupled_kinematics_2d import CoupledAirBendingKinematics
from smbs_core.kinematics2d.gauge_preview import (
    centered_gauge_stations, gauge_rear_envelope_from_faces, visual_gauge_target,
)
from smbs_core.kinematics2d.machine_pose_contract import bend_support_plane_zm, compute_machine_pose
from smbs_core.kinematics2d.mesh_sequence_screen import native_tool_meshes
from smbs_core.kinematics2d.physical_validation_25d import _source_path
from smbs_core.kinematics2d.ram_cycle_geometry import ram_cycle_positions
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.preview_mesh_3d import (
    PreviewFace3D, build_sheet_preview_faces, build_tool_preview_faces,
)
from smbs_core.kinematics2d.staged_orientation import reverse_axis_for_operation


def _operation_faces(session, state, bend_id, phase, reverse, rear_frame, support_z):
    placement = compute_machine_pose(
        state, bend_id, phase=phase,
        support_plane_zm=support_z,
        reverse_axis=reverse, rear_panel_vertices_xy=rear_frame,
    )
    return build_sheet_preview_faces(
        state, session.panel_vertices_xy, bend_id, None,
        machine_placement=placement,
        panel_meshes=getattr(session.unfold_data, "panel_meshes", None),
    )


_COLORS = {
    "punch": "#ab6855", "die": "#598b7c", "gauge": "#d58a39", "frame": "#a1a9b5",
}
_CAD_ERRORS = (OSError, ValueError, RuntimeError, TypeError, AttributeError, KeyError)


def _project(vertex):
    s, x, z = vertex
    return x + 0.55 * s, 0.26 * x - 0.48 * s - z


def _extent(faces, axis):
    points = [_project(v) for v in axis]
    points.extend(_project(v) for face in faces for v in face.vertices)
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def _diagram(core, gui, faces, extent, overview_extent, axis):
    image = gui.QImage(600, 270, gui.QImage.Format_RGB32)
    image.fill(gui.QColor("#f3f6fa"))
    painter = gui.QPainter(image)
    ordered = sorted(faces, key=lambda f: sum(
        -s + 0.55 * x + 0.623 * z for s, x, z in f.vertices
    ) / len(f.vertices))
    try:
        painter.setRenderHint(gui.QPainter.Antialiasing)
        for offset, width, bounds in ((0, 420, extent), (428, 172, overview_extent)):
            left, top, right, bottom = bounds
            scale = min((width - 24) / max(1, right - left), 246 / max(1, bottom - top))

            def point(vertex):
                x, y = _project(vertex)
                return core.QPointF(offset + width / 2 + (x - (left + right) / 2) * scale,
                                    135 + (y - (top + bottom) / 2) * scale)

            painter.setClipRect(offset, 0, width, 270)
            for face in ordered:
                path = gui.QPainterPath()
                path.setFillRule(core.Qt.OddEvenFill)
                for ring in (face.vertices, *face.holes):
                    path.addPolygon(gui.QPolygonF([point(v) for v in ring]))
                    path.closeSubpath()
                color = _COLORS.get(face.kind.split("_", 1)[0])
                fill = gui.QColor(color or "#bdd2ea")
                if face.kind == "frame":
                    fill.setAlpha(150)
                painter.setBrush(fill)
                painter.setPen(core.Qt.NoPen if color else gui.QPen(gui.QColor("#476389"), 0.75))
                painter.drawPath(path)
            painter.setPen(gui.QPen(gui.QColor("#147363"), 1.4, core.Qt.DashLine))
            painter.drawLine(*(point(v) for v in axis))
    finally:
        painter.end()
    return image


def _mesh_faces(mesh, kind, offset):
    points, indices = mesh
    s, x, z = offset
    return tuple(PreviewFace3D(
        tuple((s + points[j][0], x + points[j][1], z + points[j][2])
              for j in indices[i:i + 3]),
        kind, kind,
    ) for i in range(0, len(indices), 4))


def _tools_for_diagram(tooling, length, ram_z, native_meshes):
    if native_meshes:
        return tuple(PreviewFace3D(
            tuple((-y, x, z + (ram_z if kind == "punch" else 0))
                  for x, y, z in triangle),
            kind, kind,
        ) for kind, _, triangles in native_meshes for triangle in triangles)
    return build_tool_preview_faces(
        tooling, active_bend_length_mm=length, punch_ram_z_mm=ram_z,
    )


def _tooling_positions(tooling, state, bid, angle, clearance):
    model = CoupledAirBendingKinematics.from_tooling_and_fold_state(tooling, state, bid)
    stroke = model.stroke_for_angle(abs(angle))
    positions = ram_cycle_positions(model.contact_z_mm, model.contact_z_mm - stroke, clearance)
    return positions["retract_end_z_mm"], positions["bottom_z_mm"], stroke


def _tool_line(setup, kind, segments):
    tool = getattr(setup, kind)
    if tool is None:
        return "—"
    title = tool.name if tool.name == tool.tool_id else f"{tool.name} ({tool.tool_id})"
    spans = getattr(setup, f"{kind}_section_spans_xm")
    status = tr("Применённая раскладка") if spans else tr("Предложение по сегментам")
    return f"{title} · {status} — {segments}"


def export_bend_instructions_pdf(workspace, path, qt_core, gui, rows, *, positions_per_page=1):
    """Export one setup page per operation without modifying the active document."""
    if isinstance(positions_per_page, bool) or not isinstance(positions_per_page, int) or not 1 <= positions_per_page <= 10:
        raise ValueError("positions_per_page must be an integer from 1 to 10")
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
    tooling = replace(workspace.tooling_setup, visible=True)
    library = workspace.tool_library.library_root
    warnings = []
    try:
        frame, beam, rest_z, _ = load_frame_meshes(library)
    except _CAD_ERRORS:
        frame = beam = None
        warnings.append(tr("Модель станины недоступна."))
    model = workspace.backgauge_model
    gauge = None
    if model is not None and model.cad_geometry_enabled:
        try:
            gauge = load_gauge_mesh(library)
        except _CAD_ERRORS:
            warnings.append(tr("Модель заднего упора недоступна."))
    try:
        clamp_height = load_clamp_config(library).height_mm
    except (OSError, ValueError, ClampValidatorError):
        clamp_height = CLAMP_HEIGHT_MM
    die_outline, _ = tooling.die_polygons_machine() if tooling.die is not None else ((), ())
    punch_outline, _ = tooling.punch_polygons_machine() if tooling.punch is not None else ((), ())
    bed_z = min((z for _, z in die_outline), default=0.0)
    punch_top = max((z for _, z in punch_outline), default=0.0) - tooling.ram_z_mm
    bed_faces = _mesh_faces(frame, "frame", (0, 0, bed_z)) if frame else ()
    native_by_length = {}
    pages = []
    diagrams = []
    for index, (operation, row) in enumerate(zip(operations, rows[:len(operations)], strict=True)):
        bid = operation.bend_id
        length = dist(*state.bends[bid].axis_xyz)
        reverse = reverse_axis_for_operation(orientations, bid, operation.operation_id)
        rear_frame = workspace._rear_frame_for_preview(session.bend_order.index(bid))
        initial_angle = state.angles[bid]
        upper_z, bottom_z, stroke = _tooling_positions(
            tooling, state, bid, operation.target_angle_deg,
            workspace.cycle_config.initial_clearance_mm,
        )
        outer_radius = state.bends[bid].bend_zone.outer_radius_mm
        before = _operation_faces(session, state, bid, "APPROACH", reverse, rear_frame, outer_radius)
        state.set_angle(bid, operation.target_angle_deg)
        after = _operation_faces(session, state, bid, "BOTTOM", reverse, rear_frame,
                                 bend_support_plane_zm(outer_radius, stroke))

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

        virtual_length = length if tooling.match_active_bend_length else None
        if virtual_length not in native_by_length:
            native_by_length[virtual_length] = ()
            if _source_path(tooling.punch) is not None and _source_path(tooling.die) is not None:
                try:
                    native_by_length[virtual_length] = tuple(native_tool_meshes(tooling, virtual_length))
                except _CAD_ERRORS:
                    pass  # Use the same profile preview as for non-native tooling.
        meshes = native_by_length[virtual_length]
        gauge_faces = tuple(face for x in stations
                            for face in _mesh_faces(gauge, "gauge", (stop.s_mm, x, stop.z_mm))
                            ) if gauge and stop is not None else ()
        axis = ((0, -length / 2, 0), (0, length / 2, 0))
        extent = _extent((*before, *after, *gauge_faces), axis)
        scenes = []
        for sheet, ram_z in ((before, upper_z), (after, bottom_z)):
            mounts = ()
            if beam:
                beam_z = ram_z + punch_top + clamp_height if punch_outline else bed_z + rest_z
                mounts = _mesh_faces(beam, "frame", (0, 0, beam_z))
            tools = _tools_for_diagram(tooling, length, ram_z, meshes)
            scenes.append((*bed_faces, *mounts, *tools, *gauge_faces, *sheet))
        overview = _extent((*scenes[0], *scenes[1]), axis)
        for label, faces in zip(("before", "after"), scenes):
            name = f"smbs-{index}-{label}.png"
            diagrams.append((name, _diagram(qt_core, gui, faces, extent, overview, axis)))

        gauge_text = "—" if stop is None else (
            f"S = {stop.s_mm:.2f} mm · Z = {stop.z_mm:+.2f} mm"
            + (" · X = " + ", ".join(f"{x:+.2f}" for x in stations) + " mm" if stations else "")
        )
        next_page = ""
        pages.append(
            f'<h1{next_page}>{escape(tr("Карта гибки"))} · {index + 1}/{len(operations)}</h1>'
            f'<h2>{escape(session.part_label)} · {escape(operation.operation_id)} · {escape(row[3])}</h2>'
            f'<p>{escape(tr("Угол гиба"))} <b>{initial_angle:+.2f}° → {operation.target_angle_deg:+.2f}°</b>'
            f' · {escape(tr("Разворот детали"))} <b>{180 if reverse else 0}°</b></p>'
            f'<p><b>{escape(tr("Пуансон"))}</b> {escape(_tool_line(workspace.tooling_setup, "punch", row[-2]))}</p>'
            f'<p><b>{escape(tr("Матрица"))}</b> {escape(_tool_line(workspace.tooling_setup, "die", row[-1]))}</p>'
            f'<p><b>{escape(tr("Задний упор"))}</b> {escape(gauge_text)} {escape(note)}</p>'
            f'<h2>{escape(tr("Установка перед гибом"))}</h2>'
            f'<p><img width="600" height="270" src="smbs-{index}-before.png"></p>'
            f'<h2>{escape(tr("Форма после гиба"))}</h2>'
            f'<p><img width="600" height="270" src="smbs-{index}-after.png"></p>'
            f'<p class="muted">{escape(tr("Слева — рабочая зона, справа — общий вид. Пуансон — коричневый, матрица — зелёная, упор — оранжевый, станина — серая."))}</p>'
            + (f'<p class="muted">{escape(" ".join(warnings))}</p>' if warnings else "")
            + f'<p class="muted">{escape(tr("Схема показывает ориентацию и форму детали и не подтверждает отсутствие коллизий."))}</p>'
        )

    document.setHtml("<html><body></body></html>")
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
        document.setTextWidth(700)
        painter = gui.QPainter(writer)
        if not painter.isActive():
            raise OSError(tr("Не удалось сохранить PDF."))
        try:
            available = writer.pageLayout().paintRectPixels(writer.resolution())
            slot_height = available.height() / positions_per_page
            for index, html in enumerate(pages):
                slot = index % positions_per_page
                if index and slot == 0 and not writer.newPage():
                    raise OSError(tr("Не удалось сохранить PDF."))
                document.setHtml("<html><body>" + html + "</body></html>")
                size = document.size()
                scale = min(available.width() / size.width(),
                            (slot_height - 8) / size.height())
                painter.save()
                painter.translate((available.width() - size.width() * scale) / 2,
                                  slot * slot_height + 4)
                painter.scale(scale, scale)
                document.drawContents(painter)
                painter.restore()
        finally:
            painter.end()
        del writer
        if not output.is_file() or output.stat().st_size == 0:
            raise OSError(tr("Не удалось сохранить PDF."))
        output.replace(destination)
