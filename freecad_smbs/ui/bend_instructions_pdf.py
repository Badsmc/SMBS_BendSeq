"""Standalone operator sheets for the current SMBS bend sequence."""
from dataclasses import replace
from html import escape
from math import dist
from pathlib import Path
from tempfile import TemporaryDirectory

from freecad_smbs.ui.pdf_export_options import (
    PdfOptions, face_layer, visible_faces, current_collision_hits, collision_box_faces,
)
from freecad_smbs.ui.tool_segment_display import native_display_sections, segment_color, suggested_sections
from freecad_smbs.ui.coin3d_machine import CLAMP_HEIGHT_MM, load_frame_meshes, load_gauge_mesh
from freecad_smbs.ui.localization import tr as _translate, tr_message
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


def tr(source, **values):
    """Operator PDFs use English independently of the FreeCAD interface."""
    return _translate(source, language="en", **values)


def _stage_name(operation):
    return {"SINGLE": "Bend", "PREBEND": "Prebend", "FINISH": "Finish bend"}[operation.kind]


def suggested_pdf_path(workspace):
    """Use the bound FCStd filename, or the part label for an unsaved document."""
    import re

    document = workspace._bound_smbs_document()
    filename = getattr(document, "FileName", "") if document is not None else ""
    if filename:
        return str(Path(filename).with_suffix(".pdf"))
    label = str(getattr(workspace.session, "part_label", "") or "SMBS-Bend-Plan")
    label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", label).strip().rstrip(". ")
    if not label:
        label = "SMBS-Bend-Plan"
    if label.split(".", 1)[0].upper() in {
        "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        label = "_" + label
    return label + ".pdf"


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
    "clamps": "#9aa6b9", "collision": "#e12c2c",
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


def _diagram(core, gui, faces, extent, part_extent, axis, *, sheet_faces, active_bend, options):
    image = gui.QImage(600, 270, gui.QImage.Format_RGB32)
    image.fill(gui.QColor("#f3f6fa"))
    painter = gui.QPainter(image)
    ordered = sorted(visible_faces(faces, options), key=lambda f: sum(
        -s + 0.55 * x + 0.623 * z for s, x, z in f.vertices
    ) / len(f.vertices))
    try:
        painter.setRenderHint(gui.QPainter.Antialiasing)
        inset = options.includes("part_inset") and options.shows("part") and bool(sheet_faces)
        views = [(0, 420 if inset else 600, extent, ordered)]
        if inset:
            views.append((428, 172, part_extent, sorted(visible_faces(sheet_faces, options), key=lambda f: sum(
                -s + 0.55 * x + 0.623 * z for s, x, z in f.vertices
            ) / len(f.vertices))))
        for offset, width, bounds, visible in views:
            left, top, right, bottom = bounds
            scale = min((width - 24) / max(1, right - left), 246 / max(1, bottom - top))

            def point(vertex):
                x, y = _project(vertex)
                return core.QPointF(offset + width / 2 + (x - (left + right) / 2) * scale,
                                    135 + (y - (top + bottom) / 2) * scale)

            painter.setClipRect(offset, 0, width, 270)
            # Draw reported overlap boxes last so their outline stays visible.
            visible = sorted(visible, key=lambda face: face_layer(face) == "collision")
            for face in visible:
                layer = face_layer(face)
                if face.kind.endswith("_seam") and not getattr(options, layer + "_segments", True):
                    continue
                path = gui.QPainterPath()
                path.setFillRule(core.Qt.OddEvenFill)
                for ring in (face.vertices, *face.holes):
                    path.addPolygon(gui.QPolygonF([point(v) for v in ring]))
                    path.closeSubpath()
                kind = face.kind.split("_", 1)[0]
                color = _COLORS.get(kind)
                if kind in ("punch", "die") and getattr(options, kind + "_segments"):
                    color = gui.QColor.fromRgbF(*segment_color(kind, face.section_index)).name()
                highlighted = offset > 0 and face.identifier == active_bend
                fill = gui.QColor("#ee9850" if highlighted else color or "#bdd2ea")
                fill.setAlpha(options.alpha(layer))
                painter.setBrush(fill)
                ink = gui.QColor("#bb1111" if layer == "collision" else "#283748" if color else "#476389")
                ink.setAlpha(options.alpha(layer))
                outline = (layer in ("part", "collision") or
                           layer in ("punch", "die") and getattr(options, layer + "_segments"))
                painter.setPen(gui.QPen(ink, 1.2 if layer == "collision" else 0.6)
                               if outline else core.Qt.NoPen)
                painter.drawPath(path)
            if offset == 0 and options.includes("bend_axis"):
                painter.setPen(gui.QPen(gui.QColor("#147363"), 1.4, core.Qt.DashLine))
                painter.drawLine(*(point(v) for v in axis))
            elif offset > 0:
                painter.setPen(gui.QColor("#914418"))
                painter.drawText(core.QRectF(offset, 2, width, 20), core.Qt.AlignCenter, active_bend)
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
            tuple((-y, x, z + (ram_z if kind == "punch" else 0)) for x, y, z in triangle),
            kind + "_side", kind, section_index=section_index,
        ) for kind, tool, triangles in native_meshes
            for section_index, _, facets in native_display_sections(tool, triangles)
            for triangle in facets)
    return build_tool_preview_faces(
        tooling, active_bend_length_mm=length, punch_ram_z_mm=ram_z,
    )


def _tooling_positions(tooling, state, bid, angle, clearance):
    model = CoupledAirBendingKinematics.from_tooling_and_fold_state(tooling, state, bid)
    stroke = model.stroke_for_angle(abs(angle))
    positions = ram_cycle_positions(model.contact_z_mm, model.contact_z_mm - stroke, clearance)
    return positions["retract_end_z_mm"], positions["bottom_z_mm"], stroke


def _tool_line(setup, kind, segments, *, segment_text=True):
    tool = getattr(setup, kind)
    if tool is None:
        return "—"
    title = tool.name if tool.name == tool.tool_id else f"{tool.name} ({tool.tool_id})"
    if not segment_text:
        return title
    spans = getattr(setup, f"{kind}_section_spans_xm")
    status = tr("applied_layout") if spans else tr("suggested_segments")
    return f"{title} · {status} — {tr(segments)}"


def operation_page_slots(count, width, height, *, large_first=False):
    """Fill a page in reading order, with an optional full-width first operation."""
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10:
        raise ValueError("count must be an integer from 1 to 10")
    if count == 1:
        return ((0., 0., width, height),)
    slots = []
    top = 0.
    remaining = count
    if large_first:
        top = height * 0.45
        slots.append((0., 0., width, top))
        remaining -= 1
    rows = (remaining + 1) // 2
    row_height = (height - top) / rows
    for index in range(remaining):
        row, column = divmod(index, 2)
        columns = 1 if index == remaining - 1 and remaining % 2 else 2
        slot_width = width / columns
        slots.append((column * slot_width, top + row * row_height, slot_width, row_height))
    return tuple(slots)


def _bend_direction(angle):
    if angle > 0:
        return "↑ " + tr("up")
    if angle < 0:
        return "↓ " + tr("down")
    return "—"


def _flat_pattern_image(session, operations, core, gui):
    """Draw the same unfolded contours and bend axes used by TechDraw export."""
    contours = getattr(session.unfold_data, "flat_contours", None)
    if contours is not None:
        rings = [tuple((v.x, v.y) for v in edge.discretize(Deflection=0.05))
                 for edge in contours.Edges]
    else:
        outline = tuple(session.panel_build.outline_vertices_xy)
        rings = [(*outline, outline[0])] if outline else []
    axes = {op.bend_id: session.fold_state.bends[op.bend_id].axis_xyz for op in operations}
    points = [p for ring in rings for p in ring]
    points.extend((p[0], p[1]) for axis in axes.values() for p in axis)
    height = max(620, ((len(operations) + 1) // 2) * 38 + 40)
    image = gui.QImage(1400, height, gui.QImage.Format_RGB32)
    image.fill(gui.QColor("white"))
    if not points:
        return image
    xs, ys = zip(*points)
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    scale = min(640 / max(1, max(xs) - min(xs)), (height - 100) / max(1, max(ys) - min(ys)))
    def point(p):
        return core.QPointF(700 + (p[0] - cx) * scale, height / 2 - (p[1] - cy) * scale)
    painter = gui.QPainter(image)
    try:
        painter.setRenderHint(gui.QPainter.Antialiasing)
        painter.setPen(gui.QPen(gui.QColor("#172b44"), 2))
        for ring in rings:
            painter.drawPolyline(gui.QPolygonF([point(p) for p in ring]))
        painter.setPen(gui.QPen(gui.QColor("#bb5a20"), 2, core.Qt.DashLine))
        for axis in axes.values():
            painter.drawLine(point(axis[0]), point(axis[1]))
        font = painter.font()
        font.setPixelSize(19)
        painter.setFont(font)
        labels = [[], []]
        for index, op in enumerate(operations, 1):
            axis = axes[op.bend_id]
            anchor = point(tuple((a + b) / 2 for a, b in zip(*axis)))
            side = 0 if index % 2 else 1
            labels[side].append((anchor, index, op))
        for side, items in enumerate(labels):
            items.sort(key=lambda item: item[0].y())
            for row, (anchor, index, op) in enumerate(items):
                text = f"{index}. {op.bend_id}  {op.target_angle_deg:+.2f}°  {_bend_direction(op.target_angle_deg)}"
                rect = core.QRectF(8 if side == 0 else 1052,
                                   20 + row * (height - 40) / max(1, len(items)), 340, 30)
                painter.setPen(gui.QPen(gui.QColor("#914418"), 1))
                endpoint = core.QPointF(rect.right() if side == 0 else rect.left(), rect.center().y())
                painter.drawLine(anchor, endpoint)
                painter.drawEllipse(anchor, 3, 3)
                painter.drawText(rect, core.Qt.AlignCenter, text)
    finally:
        painter.end()
    return image


def _overview_html(session, operations, rows, tooling, core, gui, diagrams, options):
    from freecad_smbs.ui.tool_section_preview import draw_tool_section
    html = f'<h1>{escape(tr("flat_pattern_and_tooling"))}</h1><h2>{escape(session.part_label)}</h2>'
    if options.includes("flat_pattern"):
        diagrams.append(("smbs-flat.png", _flat_pattern_image(session, operations, core, gui)))
        html += (f'<p><img width="650" height="{diagrams[-1][1].height()*650/diagrams[-1][1].width():.0f}" src="smbs-flat.png"></p>'
                 f'<p>{escape(tr("numbers_indicate_operation_order_indicate_the_signed_flat_pattern"))}</p>')
    if options.includes("schedule"):
        html += ('<table border="1" cellspacing="0" cellpadding="4" width="100%">'
                 f'<tr><th>{escape(tr("operation_no"))}</th><th>{escape(tr("line"))}</th>'
                 f'<th>{escape(tr("stage"))}</th><th>{escape(tr("bend_angle"))}</th>'
                 f'<th>{escape(tr("bend_direction"))}</th>')
        if options.includes("gauge_text"):
            html += '<th>Backgauge S, mm</th><th>Flat flange, mm</th>'
        html += '</tr>'
        for index,(op,row) in enumerate(zip(operations,rows,strict=True),1):
            html += (f'<tr><td>{index}</td><td>{escape(op.bend_id)}</td>'
                     f'<td>{escape(_stage_name(op))}</td><td>{op.target_angle_deg:+.2f}°</td>'
                     f'<td>{escape(_bend_direction(op.target_angle_deg))}</td>')
            if options.includes("gauge_text"):
                html += f'<td>{escape(row[4])}</td><td>{escape(row[5])}</td>'
            html += '</tr>'
        html += '</table>'
    if options.includes("tool_details"):
        kinds = [kind for kind in ("punch","die") if options.shows(kind)]
        if kinds:
            html += '<table width="100%"><tr>'
        for kind in kinds:
            tool = getattr(tooling,kind)
            html += f'<td width="{100//len(kinds)}%"><h2>{escape(tr(kind))}</h2>'
            if tool is not None:
                outer,holes = getattr(tooling,kind+"_polygons_machine")()
                if outer:
                    image = draw_tool_section((outer,*holes),gui.QColor(_COLORS[kind]),core,gui)
                    name = f"smbs-{kind}-section.png"
                    diagrams.append((name,image.toImage()))
                    html += f'<p><img width="220" height="145" src="{name}"></p>'
                column = -2 if kind=="punch" else -1
                if options.includes("segment_text"):
                    for segments in dict.fromkeys(row[column] for row in rows):
                        numbers = ", ".join(str(i) for i,row in enumerate(rows,1) if row[column]==segments)
                        html += f'<p>{escape(tr("operation_no"))}: {numbers}<br>{escape(_tool_line(tooling,kind,segments))}</p>'
                else:
                    html += f'<p>{escape(_tool_line(tooling,kind,"",segment_text=False))}</p>'
            else:
                html += '<p>—</p>'
            html += '</td>'
        if kinds:
            html += '</tr></table>'
    return html


def _clamp_faces(spans, height, depth, beam_z):
    faces = []
    for left,right in spans:
        bounds = dict(xmin=left,xmax=right,ymin=-depth/2,ymax=depth/2,
                      zmin=beam_z-height,zmax=beam_z)
        faces.extend(replace(face,kind="clamps",identifier="clamp") for face in collision_box_faces(bounds))
    return tuple(faces)


def _scene_extent(scenes, sheets, axis, options):
    # Keep the working-area crop by default, but fit every enabled layer
    # including the full machine when explicitly requested.
    faces = tuple(f for scene in scenes for f in visible_faces(scene,options)
                  if options.fit_machine or face_layer(f) != "frame")
    if not faces:
        faces = tuple(f for sheet in sheets for f in sheet)
    return _extent(faces,axis)


def _collision_page(workspace, hit, tooling, options, core, gui, diagrams, index):
    from smbs_core.kinematics2d.workpiece_placement_3d import PlacementTransform3D
    session = workspace.session
    pose = hit["sampled_machine_pose"]
    bid = hit["bend"]
    state = FoldState(session.graph,session.fixed_panel)
    for bend,angle in pose["angles_deg"].items():
        state.set_angle(bend,angle)
    placement = PlacementTransform3D(rotation=tuple(tuple(row) for row in pose["rotation"]),
                                     translation=tuple(pose["translation"]))
    sheet = build_sheet_preview_faces(state,session.panel_vertices_xy,bid,None,
        machine_placement=placement,panel_meshes=getattr(session.unfold_data,"panel_meshes",None))
    length = dist(*state.bends[bid].axis_xyz)
    axis = ((0,-length/2,0),(0,length/2,0))
    tools = build_tool_preview_faces(tooling,active_bend_length_mm=length,punch_ram_z_mm=float(hit["ram_z_mm"]))
    box = collision_box_faces(hit.get("overlap_bbox_xyz_mm"))
    faces = (*sheet,*tools,*box)
    name = f"smbs-collision-{index}.png"
    diagrams.append((name,_diagram(core,gui,faces,_extent(visible_faces(faces,options),axis),
                                   _extent(sheet,axis),axis,sheet_faces=sheet,active_bend=bid,options=options)))
    status = "Conditional finding" if hit.get("conditional") else "Confirmed sampled collision"
    return (f'<h1>{escape(status)} · {escape(str(hit["operation"]))}</h1>'
            f'<p>{escape(str(hit["phase"]))} · {escape(str(hit["tool"]))} · {escape(str(hit["component"]))}</p>'
            f'<p>Overlap volume: {float(hit["occ_overlap_volume_mm3"]):.6g} mm³ · Ram Z: {float(hit["ram_z_mm"]):.3f} mm</p>'
            f'<p><img width="600" height="270" src="{name}"></p>'
            '<p>Red box: recorded overlap bounding box, not exact intersection solid. '
            'Sheet pose is the recorded Mesh sample; tooling is a profile preview. '
            'Machine and backgauge are omitted on collision evidence pages. '
            'Sampled findings do not certify continuous collision clearance.</p>')


def export_bend_instructions_pdf(workspace, path, qt_core, gui, rows, *, positions_per_page=1, large_first=False, options=None):
    """Export one setup page per operation without modifying the active document."""
    options = options or PdfOptions(positions_per_page=positions_per_page,large_first=large_first)
    positions_per_page,large_first = options.positions_per_page,options.large_first
    if isinstance(positions_per_page, bool) or not isinstance(positions_per_page, int) or not 1 <= positions_per_page <= 10:
        raise ValueError("positions_per_page must be an integer from 1 to 10")
    session = workspace.session
    operations = session.controller.operation_steps
    prefix = tuple(workspace._manual_sequence_prefix)
    if prefix and not session.controller.has_stages:
        operations = operations[:len(prefix)]
    if not operations:
        raise ValueError(tr("no_bends_selected"))

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
    frame = beam = None
    rest_z = ram_width = 0.0
    if options.shows("frame") or options.shows("clamps"):
        try:
            frame, beam, rest_z, ram_width = load_frame_meshes(library)
        except _CAD_ERRORS:
            if options.shows("frame"):
                warnings.append(tr("machine_frame_model_unavailable"))
    model = workspace.backgauge_model
    gauge = None
    if options.shows("gauge") and model is not None and model.cad_geometry_enabled:
        try:
            gauge = load_gauge_mesh(library)
        except _CAD_ERRORS:
            warnings.append(tr("backgauge_model_unavailable"))
    clamp_height = CLAMP_HEIGHT_MM
    clamp_depth = 50.0
    clamp_spans = ()
    try:
        config = getattr(workspace,"_project_clamp_config",None) or load_clamp_config(library)
        if not config.centres_mm and ram_width > 0:
            config = replace(config,clamp_count=int((ram_width+config.gap_between_mm)/(config.width_mm+config.gap_between_mm)))
        from smbs_core.kinematics2d.tooling_assembly import clamp_spans_for_centres
        clamp_spans = clamp_spans_for_centres(config.centres_mm,config.width_mm)
        clamp_height,clamp_depth = config.height_mm,config.depth_mm
    except (OSError, ValueError, ClampValidatorError):
        if options.shows("clamps"):
            warnings.append("Clamp configuration unavailable.")
    die_outline, _ = tooling.die_polygons_machine() if tooling.die is not None else ((), ())
    punch_outline, _ = tooling.punch_polygons_machine() if tooling.punch is not None else ((), ())
    bed_z = min((z for _, z in die_outline), default=0.0)
    punch_top = max((z for _, z in punch_outline), default=0.0) - tooling.ram_z_mm
    bed_faces = _mesh_faces(frame, "frame", (0, 0, bed_z)) if frame else ()
    native_by_length = {}
    pages = []
    diagrams = []
    overview = (_overview_html(session, operations, rows[:len(operations)], tooling,
                              qt_core, gui, diagrams, options) if options.includes("overview") else "")
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
                stop, note = visual_gauge_target(model, bid, envelopes, translate=tr)
            elif not model.part_label or model.part_label == session.part_label:
                stations = workspace._backgauge_stations()
                stop, note = visual_gauge_target(model, bid, (), translate=tr)

        note = " · ".join(tr_message(part, language="en") for part in note.split(" · "))
        virtual_length = length if tooling.match_active_bend_length else None
        if virtual_length not in native_by_length:
            native_by_length[virtual_length] = ()
            if _source_path(tooling.punch) is not None and _source_path(tooling.die) is not None:
                try:
                    native_by_length[virtual_length] = tuple(native_tool_meshes(tooling, virtual_length))
                except _CAD_ERRORS:
                    pass  # Use the same profile preview as for non-native tooling.
        meshes = native_by_length[virtual_length]
        section_preview = suggested_sections(workspace, length)
        gauge_faces = tuple(face for x in stations
                            for face in _mesh_faces(gauge, "gauge", (stop.s_mm, x, stop.z_mm))
                            ) if gauge and stop is not None else ()
        axis = ((0, -length / 2, 0), (0, length / 2, 0))
        scenes = []
        for sheet, ram_z in ((before, upper_z), (after, bottom_z)):
            mounts = ()
            beam_z = ram_z + punch_top + clamp_height if punch_outline else bed_z + rest_z
            if beam:
                mounts = _mesh_faces(beam, "frame", (0, 0, beam_z))
            clamps = _clamp_faces(clamp_spans,clamp_height,clamp_depth,beam_z) if options.shows("clamps") else ()
            tools = (build_tool_preview_faces(
                tooling, active_bend_length_mm=length, punch_ram_z_mm=ram_z,
                section_preview=section_preview,
            ) if section_preview else _tools_for_diagram(tooling, length, ram_z, meshes))
            scenes.append((*bed_faces, *mounts, *clamps, *tools, *gauge_faces, *sheet))
        extent = _scene_extent(scenes,(before,after),axis,options)
        part_extent = _extent((*before, *after), axis)
        for label, faces, sheet in zip(("before", "after"), scenes, (before, after)):
            if not options.includes(label):
                continue
            name = f"smbs-{index}-{label}.png"
            diagrams.append((name, _diagram(qt_core, gui, faces, extent, part_extent, axis,
                                             sheet_faces=sheet, active_bend=bid, options=options)))

        gauge_text = "—" if stop is None else (
            f"S = {stop.s_mm:.2f} mm · Z = {stop.z_mm:+.2f} mm"
            + (" · X = " + ", ".join(f"{x:+.2f}" for x in stations) + " mm" if stations else "")
        )
        html = (f'<h1>{escape(tr("bend_instructions"))} · {index+1}/{len(operations)}</h1>'
                f'<h2>{escape(session.part_label)} · {escape(operation.operation_id)} · {escape(_stage_name(operation))}</h2>'
                f'<p>{escape(tr("bend_angle"))} <b>{initial_angle:+.2f}° → {operation.target_angle_deg:+.2f}°</b>'
                f' · {escape(tr("part_rotation"))} <b>{180 if reverse else 0}°</b></p>')
        if options.includes("tool_details"):
            for kind,column in (("punch",-2),("die",-1)):
                if options.shows(kind):
                    html += f'<p><b>{escape(tr(kind))}</b> {escape(_tool_line(tooling,kind,row[column],segment_text=options.includes("segment_text")))}</p>'
        if options.includes("gauge_text"):
            html += f'<p><b>{escape(tr("backgauge"))}</b> {escape(gauge_text)} {escape(tr(note))}</p>'
        for label,title in (("before","setup_before_bending"),("after","shape_after_bending")):
            if options.includes(label):
                html += f'<h2>{escape(tr(title))}</h2><p><img width="600" height="270" src="smbs-{index}-{label}.png"></p>'
        if warnings:
            html += f'<p class="muted">{escape(" ".join(warnings))}</p>'
        html += f'<p class="muted">{escape(tr("the_diagrams_show_part_orientation_and_shape_verify_collisions"))}</p>'
        pages.append(html)
    collision_pages = []
    if options.shows("collision"):
        hits = current_collision_hits(workspace)
        selected_ids = {op.operation_id for op in operations}
        for index, hit in enumerate(hits):
            if hit.get("operation") not in selected_ids:
                continue
            try:
                collision_pages.append(_collision_page(workspace,hit,tooling,options,qt_core,gui,diagrams,index))
            except _CAD_ERRORS as exc:
                collision_pages.append(f'<h1>Sampled collision · {escape(str(hit.get("operation","")))}</h1>'
                                       f'<p>Evidence image unavailable: {escape(str(exc))}</p>')
        if not collision_pages:
            collision_pages.append('<h1>Collision evidence</h1><p>No matching recorded findings for the current setup. This is not a clearance verdict. Run Mesh to obtain current sampled evidence.</p>')

    document.setHtml("<html><body></body></html>")
    for name, image in diagrams:
        document.addResource(gui.QTextDocument.ImageResource, qt_core.QUrl(name), image)
    destination = Path(path).absolute()
    with TemporaryDirectory(dir=destination.parent) as tmp:
        output = Path(tmp) / "bend-instructions.pdf"
        writer = gui.QPdfWriter(str(output))
        writer.setResolution(96)
        writer.setPageLayout(gui.QPageLayout(
            gui.QPageSize(gui.QPageSize.A4), gui.QPageLayout.Landscape if options.landscape else gui.QPageLayout.Portrait,
            qt_core.QMarginsF(12, 12, 12, 12),
        ))
        writer.setTitle(tr("bend_instructions") + " · " + session.part_label)
        document.setTextWidth(700)
        painter = gui.QPainter(writer)
        if not painter.isActive():
            raise OSError(tr("could_not_save_the_pdf"))
        try:
            available = writer.pageLayout().paintRectPixels(writer.resolution())
            if overview:
                document.setHtml("<html><body>" + overview + "</body></html>")
                size = document.size()
                scale = min((available.width() - 8) / size.width(),
                            (available.height() - 8) / size.height())
                painter.save()
                painter.scale(scale, scale)
                document.drawContents(painter)
                painter.restore()
                if not writer.newPage():
                    raise OSError(tr("could_not_save_the_pdf"))
            for index, html in enumerate(pages):
                slot = index % positions_per_page
                if index and slot == 0 and not writer.newPage():
                    raise OSError(tr("could_not_save_the_pdf"))
                start = index - slot
                page_count = min(positions_per_page, len(pages) - start)
                slots = operation_page_slots(
                    page_count, available.width(), available.height(), large_first=large_first,
                )
                x, y, slot_width, slot_height = slots[slot]
                document.setHtml("<html><body>" + html + "</body></html>")
                size = document.size()
                scale = min((slot_width - 8) / size.width(),
                            (slot_height - 8) / size.height())
                painter.save()
                painter.translate(x + (slot_width - size.width() * scale) / 2,
                                  y + (slot_height - size.height() * scale) / 2)
                painter.scale(scale, scale)
                document.drawContents(painter)
                painter.restore()
            for html in collision_pages:
                if not writer.newPage():
                    raise OSError(tr("could_not_save_the_pdf"))
                document.setHtml("<html><body>"+html+"</body></html>")
                size = document.size()
                scale = min((available.width()-8)/size.width(),(available.height()-8)/size.height())
                painter.save()
                painter.scale(scale,scale)
                document.drawContents(painter)
                painter.restore()
        finally:
            painter.end()
        del writer
        if not output.is_file() or output.stat().st_size == 0:
            raise OSError(tr("could_not_save_the_pdf"))
        output.replace(destination)
