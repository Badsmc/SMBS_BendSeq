"""Manual bending of a single flat outline from an ASCII DXF."""
from dataclasses import asdict, dataclass
from freecad_smbs.ui.localization import tr, tr_message
from freecad_smbs.icon_theme import icon_path as _smbs_icon_path
from itertools import combinations, groupby
from math import copysign, dist, floor, isfinite, tau
from pathlib import Path
from time import perf_counter
from traceback import format_exc
from types import SimpleNamespace
from uuid import uuid4

from freecad_smbs.integration.document_group import group_document_objects
from smbs_core.kinematics2d.developer_debug_log import debug_event
from smbs_core.kinematics2d.tooling_25d import (
    ArcSegment2D, LineSegment2D, _extract_dxf_units, _lwpolyline_to_segments, _parse_dxf_pairs,
)


@dataclass(frozen=True)
class DxfCircle:
    x: float
    y: float
    radius: float


@dataclass(frozen=True)
class DxfCurve:
    edge: object


def _geometry_for_log(line):
    if isinstance(line, DxfCurve):
        return {"type": type(line.edge.Curve).__name__,
                "vertices_xy": [(vertex.Point.x, vertex.Point.y)
                                for vertex in line.edge.Vertexes],
                "closed": line.edge.isClosed(), "length_mm": line.edge.Length}
    if isinstance(line, (DxfCircle, ArcSegment2D)):
        return {"type": type(line).__name__, **asdict(line)}
    return {"type": "LINE", "endpoints_xy": line}


def _native_curve(entity, props, scale):
    """Construct an OCC edge for a planar ellipse or B-spline."""
    import FreeCAD as App
    import Part

    values = dict(props)
    groups = {}
    for code, value in props:
        groups.setdefault(code, []).append(value)

    if entity == "ELLIPSE":
        x, y, major_x, major_y, ratio = (float(values[c]) for c in (10, 20, 11, 21, 40))
        center = App.Vector(x * scale, y * scale, 0)
        major = App.Vector(major_x * scale, major_y * scale, 0)
        minor = App.Vector(-major_y * ratio * scale, major_x * ratio * scale, 0)
        ellipse = Part.Ellipse(center + major, center + minor, center)
        start, end = float(values.get(41, 0)), float(values.get(42, tau))
        if abs(end - start) >= tau - 1e-7:
            return DxfCurve(ellipse.toShape())
        if end <= start:
            end += tau
        return DxfCurve(Part.ArcOfEllipse(ellipse, start, end).toShape())

    if not groups.get(10) or not groups.get(40):
        raise ValueError(tr("spline_has_no_control_points_or_knots"))
    poles = [App.Vector(float(x) * scale, float(y) * scale, 0)
             for x, y in zip(groups[10], groups[20], strict=True)]
    knots, multiplicities = zip(*((k, len(list(run)))
                                   for k, run in groupby(map(float, groups[40]))))
    curve = Part.BSplineCurve()
    flags = int(values.get(70, 0))
    weights = list(map(float, groups.get(41, ()))) or None
    if flags & 2:
        raise ValueError(tr("periodic_spline_curves_are_unsupported"))
    args = (poles, list(multiplicities), list(knots), False, int(values[71]))
    if weights:
        curve.buildFromPolesMultsKnots(*args, weights)
    else:
        curve.buildFromPolesMultsKnots(*args)
    return DxfCurve(curve.toShape())


def _decode_dxf_text(raw):
    """Read ASCII DXF without silently merging distinct legacy layer names."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    # DXF group codes and header variable names are ASCII in all supported
    # code pages. Inspect only the HEADER before decoding arbitrary names.
    header = {}
    in_header = False
    variable = None
    lines = raw.splitlines()
    for code, value in zip(lines[::2], lines[1::2]):
        code, value = code.strip(), value.strip()
        if code == b"2" and value == b"HEADER":
            in_header = True
        if code == b"0" and value == b"ENDSEC" and in_header:
            break
        if code == b"2" and value == b"ENTITIES":
            break
        if in_header and code == b"9":
            variable = value
        elif in_header and variable in (b"$ACADVER", b"$DWGCODEPAGE"):
            header[variable] = value
            variable = None
    version = header.get(b"$ACADVER", b"")
    # AutoCAD 2007 (AC1021) and later write UTF-8 regardless of DWGCODEPAGE.
    if version.startswith(b"AC") and version[2:].isdigit() and int(version[2:]) >= 1021:
        return raw.decode("utf-8")
    codepage = header.get(b"$DWGCODEPAGE", b"").decode("ascii").upper()
    if codepage:
        if codepage.startswith("ANSI_") and codepage[5:].isdigit():
            encoding = "cp" + codepage[5:]
        elif codepage.startswith("DOS") and codepage[3:].lstrip("_").isdigit():
            encoding = "cp" + codepage[3:].lstrip("_")
        elif codepage in ("UTF-8", "UTF8"):
            encoding = "utf-8"
        else:
            encoding = codepage
        # Strict decoding: a wrong or unknown encoding must be visible, not
        # substituted with U+FFFD which collapses unrelated layer names.
        return raw.decode(encoding)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        # Older DXF files may omit DWGCODEPAGE; ANSI 1252 is the conventional
        # default. Without the original header the actual encoding is unknown.
        return raw.decode("cp1252")


def read_lines(path, *, layers=None, unsupported=None):
    pairs = _parse_dxf_pairs(_decode_dxf_text(Path(path).read_bytes()))
    _, scale = _extract_dxf_units(pairs, None)
    start = pairs.index((2, "ENTITIES")) + 1
    edges = []
    index = start
    while index < len(pairs):
        code, entity = pairs[index]
        index += 1
        if code != 0:
            continue
        if entity == "ENDSEC":
            break
        end = index
        while end < len(pairs) and pairs[end][0] != 0:
            end += 1
        props = pairs[index:end]
        index = end
        values = dict(props)
        layer = values.get(8, "0")
        if entity not in ("TEXT", "MTEXT", "DIMENSION", "POINT", "HATCH", "LEADER",
                          "MLEADER", "ATTDEF", "ATTRIB", "SEQEND"):
            if (any(abs(float(value)) > 1e-6 for code, value in props if code in (30, 31))
                    or any(abs(float(values.get(code, default)) - default) > 1e-6
                           for code, default in ((210, 0), (220, 0), (230, 1)))):
                if unsupported is None:
                    raise ValueError(tr("the_geometry_must_lie_in_the_xy_plane", entity=entity))
                unsupported.setdefault(layer, set()).add(tr("outside_xy", entity=entity))
                continue
        if entity == "LINE":
            if any(abs(float(values.get(axis, 0))) > 1e-6 for axis in (30, 31)):
                raise ValueError(tr("dxf_lines_must_lie_in_the_xy_plane"))
            segments = [LineSegment2D((float(values[10]), float(values[20])),
                                      (float(values[11]), float(values[21])))]
        elif entity == "CIRCLE":
            x, y, radius = (float(values[group]) * scale for group in (10, 20, 40))
            if not all(isfinite(v) for v in (x, y, radius)) or radius <= 0 or abs(float(values.get(30, 0))) > 1e-6:
                raise ValueError(tr("invalid_circle_in_the_xy_plane"))
            edges.append(DxfCircle(x, y, radius))
            if layers is not None:
                layers.append(layer)
            continue
        elif entity == "ARC":
            segments = [ArcSegment2D.from_dxf_arc(*(float(values[c]) for c in (10, 20, 40, 50, 51)))]
        elif entity in ("LWPOLYLINE", "POLYLINE"):
            if entity == "POLYLINE":
                flags = int(values.get(70, 0))
                props = [(70, str(flags))]
                while index < len(pairs) and pairs[index] == (0, "VERTEX"):
                    index += 1
                    vertex = {}
                    while index < len(pairs) and pairs[index][0] != 0:
                        code, value = pairs[index]
                        vertex[code] = value
                        index += 1
                    if flags & (8 | 16 | 64) or abs(float(vertex.get(30, 0))) > 1e-6:
                        flags |= 8
                    props.extend((code, vertex[code]) for code in (10, 20, 42) if code in vertex)
                if flags & (8 | 16 | 64):
                    if unsupported is None:
                        raise ValueError(tr("polygon_meshes_and_3d_polyline_cannot_form_a_flat"))
                    unsupported.setdefault(layer, set()).add("3D POLYLINE")
                    continue
            segments = _lwpolyline_to_segments(props)
        elif entity in ("ELLIPSE", "SPLINE"):
            try:
                curve = _native_curve(entity, props, scale)
            except (ValueError, RuntimeError, KeyError) as exc:
                if unsupported is None:
                    raise
                unsupported.setdefault(layer, set()).add(f"{entity}: {exc}")
            else:
                edges.append(curve)
                if layers is not None:
                    layers.append(layer)
            continue
        elif entity in ("TEXT", "MTEXT", "DIMENSION", "POINT", "HATCH", "LEADER",
                        "MLEADER", "ATTDEF", "ATTRIB", "SEQEND"):
            continue
        else:
            if unsupported is None:
                raise ValueError(tr("unsupported_dxf_geometry", entity=entity))
            unsupported.setdefault(layer, set()).add(entity)
            continue

        def scaled(point):
            return tuple(value * scale for value in point)

        for segment in segments:
            if isinstance(segment, ArcSegment2D):
                edges.append(ArcSegment2D(scaled(segment.start), scaled(segment.end),
                                          scaled(segment.center), segment.radius * scale,
                                          segment.ccw))
            else:
                edges.append((scaled(segment.start), scaled(segment.end)))
            if layers is not None:
                layers.append(layer)
    if not edges:
        raise ValueError(tr("dxf_has_no_lines"))
    return edges


DEFAULT_OUTLINE_TOLERANCE_MM = 0.01


def join_outline(lines, tolerance=DEFAULT_OUTLINE_TOLERANCE_MM):
    """Snap nearby endpoints and remove duplicate edges before OCC builds wires."""
    if not isfinite(tolerance) or tolerance <= 0:
        raise ValueError(tr("joining_tolerance_must_be_positive"))
    grid = {}
    neighbours = {}
    edges = set()

    def snap(point):
        x, y = (floor(value / tolerance) for value in point)
        candidates = [other for i in range(x - 1, x + 2)
                      for j in range(y - 1, y + 2)
                      for other in grid.get((i, j), ())
                      if dist(point, other) <= tolerance]
        if candidates:
            return min(candidates, key=lambda other: dist(point, other))
        grid.setdefault((x, y), []).append(point)
        return point

    result = []
    for line in lines:
        if isinstance(line, DxfCurve):
            edge = line.edge
            if edge.isClosed():
                result.append(line)
                continue
            start, end = tuple((v.Point.x, v.Point.y) for v in edge.Vertexes)
        elif isinstance(line, ArcSegment2D):
            start, end = line.start, line.end
        else:
            start, end = line
        a, b = snap(start), snap(end)
        if a == b:
            continue
        midpoint = line.midpoint() if isinstance(line, ArcSegment2D) else (
            tuple(line.edge.valueAt((line.edge.FirstParameter + line.edge.LastParameter) / 2))[:2]
            if isinstance(line, DxfCurve) else None)
        edge = (*sorted((a, b)), midpoint)
        if edge in edges:
            continue
        edges.add(edge)
        neighbours.setdefault(a, []).append(b)
        neighbours.setdefault(b, []).append(a)
        result.append((a, midpoint, b) if isinstance(line, ArcSegment2D)
                      else line if isinstance(line, DxfCurve) else (a, b))
    open_ends = [point for point, links in neighbours.items() if len(links) == 1]
    for (x, y), links in neighbours.items():
        if len(links) == 2:
            continue
        if len(links) > 2:
            raise ValueError(tr("outline_branches_near_x_y_mm_check_the_enabled", x=x, y=y))
        message = tr("outline_gap_near_x_y_mm", x=x, y=y)
        other_ends = (point for point in open_ends if point != (x, y))
        nearest = min(other_ends, key=lambda point: dist((x, y), point), default=None)
        if nearest is not None:
            xx, yy = nearest
            gap = dist((x, y), nearest)
            message += tr("nearest_open_endpoint_x_y_mm_gap_mm_tolerance",
                          x=xx, y=yy, gap=gap, tol=tolerance)
        raise ValueError(message + tr("check_that_outline_layers_are_enabled_and_outline_lines"))
    return result


def _outline_face(wire, number, tolerance):
    """Report the failing loop and, where possible, its crossing coordinates."""
    import Part

    bounds = wire.BoundBox
    location = tr("outline_edges_x_y_mm",
                  number=number, edges=len(wire.Edges), xmin=bounds.XMin,
                  xmax=bounds.XMax, ymin=bounds.YMin, ymax=bounds.YMax)
    if not wire.isClosed():
        raise ValueError(tr("did_not_close_after_joining_in_freecad_joining_tolerance",
                            outline=location, tol=tolerance))
    try:
        face = Part.Face(wire)
        if face.isValid():
            return face
        face.check()
    except (RuntimeError, ValueError, Part.OCCError) as exc:
        reason = " ".join(str(exc).split())
    else:
        reason = ""

    for first, second in combinations(wire.Edges, 2):
        try:
            section = first.section(second)
        except Part.OCCError:
            continue
        for vertex in section.Vertexes:
            point = vertex.Point
            shared_endpoint = all(
                any(point.distanceToPoint(end.Point) <= tolerance for end in edge.Vertexes)
                for edge in (first, second)
            )
            if section.Edges or not shared_endpoint:
                raise ValueError(tr("crosses_or_overlaps_itself_near_x_y_mm",
                                    outline=location, x=point.x, y=point.y))
    detail = f" FreeCAD: {reason}" if reason else ""
    raise ValueError(tr("does_not_form_a_valid_planar_face", outline=location, detail=detail))


def build_session(path, lines, angles, thickness, radius, k_factor, *,
                  outline_tolerance=DEFAULT_OUTLINE_TOLERANCE_MM, trace=None):
    import FreeCAD as App
    import Part

    from freecad_smbs.commands.pressbrake_2d import LoadedPartSession
    from freecad_smbs.integration.panel_builder import BendZoneOverlapError, partition_flat_pattern
    from smbs_core.kinematics2d.gauge_preview import local_bend_dimension_mm
    from smbs_core.kinematics2d.panel_graph import graph_from_panel_build
    from smbs_core.kinematics2d.panel_kinematics import FoldState

    if trace:
        trace("join_outline")
    contour = (line for index, line in enumerate(lines)
               if index not in angles and not isinstance(line, DxfCircle))
    outline = join_outline(contour, tolerance=outline_tolerance)
    if trace:
        trace("build_wires", outline_edge_count=len(outline))
    edges = []
    for line in outline:
        if isinstance(line, DxfCurve):
            edge = line.edge.copy()
            edge.fixTolerance(outline_tolerance)
        else:
            points = [App.Vector(x, y, 0) for x, y in line]
            edge = Part.Arc(*points).toShape() if len(points) == 3 else Part.makeLine(*points)
        edges.append(edge)
    wires = [Part.Wire(group) for group in Part.sortEdges(edges)]
    wires.extend(Part.Wire([Part.makeCircle(line.radius, App.Vector(line.x, line.y, 0))])
                 for line in lines if isinstance(line, DxfCircle))
    closed = [wire.isClosed() for wire in wires]
    if trace:
        trace("build_faces", wires=[{"closed": is_closed, "edges": len(wire.Edges)}
                                    for wire, is_closed in zip(wires, closed)])
    if not wires:
        raise ValueError(tr("no_dxf_outline_edges_check_enabled_layers_and_lines"))
    faces = [_outline_face(wire, number, outline_tolerance)
             for number, wire in enumerate(wires, 1)]
    outer = max(faces, key=lambda face: face.Area)
    holes = tuple(face for face in faces if face is not outer)
    wire = outer.OuterWire
    axes = tuple(lines[index] for index in angles)
    bends = [
        SimpleNamespace(bend_id=f"B{step:02d}", fold_angle_deg=angle,
                        source_angle_deg=copysign(180 - abs(angle), angle),
                        radius_mm=radius if angle > 0 else radius + thickness,
                        thickness_mm=thickness, k_factor_ansi=k_factor)
        for step, angle in enumerate(angles.values(), 1)
    ]
    if trace:
        trace("partition_panels", outer_area_mm2=outer.Area,
              hole_areas_mm2=[hole.Area for hole in holes])
    try:
        panels = partition_flat_pattern(wire, axes, axes, bends, hole_faces=holes)
    except BendZoneOverlapError as exc:
        if trace:
            trace("partition_retry", error=str(exc), zone_extension_mm=0.0)
        # The topology cut still bridges DXF endpoint tolerances. Retry the
        # physical bend zones with the actual finite DXF axis lengths.
        def report_slivers(panel_id, areas):
            if trace:
                trace("discard_panel_slivers", panel_id=panel_id, areas_mm2=areas)

        panels = partition_flat_pattern(
            wire, axes, axes, bends, hole_faces=holes, zone_extension_mm=0.0,
            # Bound total discarded corner residue on each rigid panel.
            max_panel_sliver_area_mm2=1e-4, on_panel_slivers=report_slivers,
        )
    if trace:
        trace("prepare_panel_meshes", panel_count=len(panels.panels))
    graph = graph_from_panel_build(panels)
    fixed = max(panels.panels, key=lambda panel: panel.area_mm2).panel_id
    state = FoldState(graph, fixed)
    vertices = {}
    meshes = {}
    needs_preview_meshes = bool(holes) or any(
        not isinstance(edge.Curve, Part.Line) for edge in wire.Edges
    )
    for panel, face in zip(panels.panels, panels.panel_faces):
        rings = []
        for ring in [face.OuterWire, *(wire for wire in face.Wires if not wire.isSame(face.OuterWire))]:
            points = ring.discretize(Deflection=0.05)
            rings.append(tuple((v.x, v.y) for v in points[:-1]))
        vertices[panel.panel_id] = rings[0]
        if needs_preview_meshes:
            # Tessellate a polygonal copy so OCC cannot subdivide each
            # circular edge hundreds of times for every playback frame.
            preview_wires = []
            for ring in rings:
                points = [App.Vector(x, y, 0) for x, y in ring]
                preview_wires.append(Part.makePolygon([*points, points[0]]))
            preview_face = Part.makeFace(preview_wires, "Part::FaceMakerBullseye")
            points, triangles = preview_face.tessellate(0.05)
            meshes[panel.panel_id] = (
                tuple(tuple((points[i].x, points[i].y) for i in triangle)
                      for triangle in triangles), tuple(rings),
            )
    dimensions = {bend.bend_id: local_bend_dimension_mm(
        axis, panels.outline_vertices_xy, tuple(other for other in axes if other != axis),
    ) for bend, axis in zip(bends, axes)}
    session = LoadedPartSession(Path(path).stem, SimpleNamespace(
        thickness_mm=thickness, dxf_manual=True, panel_meshes=meshes,
        flat_contours=Part.makeCompound([wire, *(hole.OuterWire for hole in holes)]),
    ), panels, graph, vertices, fixed, state, local_bend_dimensions_mm=dimensions)
    session.set_bend_order(tuple(bend.bend_id for bend in bends))
    return session


def add_flat_pattern(document, session):
    """Add drawing/export objects directly from the imported DXF geometry."""
    import FreeCAD as App
    import Part

    group = document.addObject("App::DocumentObjectGroup", "DXFUnfold")
    group.Label = tr("dxf_flat_pattern")
    outline = document.addObject("Part::Feature", "DXFContours")
    outline.Label = tr("outline_and_holes")
    outline.Shape = session.unfold_data.flat_contours
    group.addObject(outline)
    bends = document.addObject("Part::Feature", "DXFBendLines")
    bends.Label = tr("bend_lines")
    bends.Shape = Part.makeCompound([
        Part.makeLine(App.Vector(*start), App.Vector(*end))
        for bend in session.ordered_bends
        for start, end in (bend.axis_xyz,)
    ])
    group.addObject(bends)
    outline.Visibility = False
    bends.Visibility = False
    group_document_objects(document)
    return group


def _folded_dxf_preview(session, trace):
    """Return the joined solid or a warning for a confirmed self-intersection."""
    from smbs_core.kinematics2d.native_bend_solid_3d import (
        NativeSheetVolumeMismatch, build_multi_bend_sheet_solid,
    )
    from smbs_core.kinematics2d.panel_kinematics import FoldState

    folded = FoldState(session.graph, session.fixed_panel)
    for bend_id in session.bend_order:
        folded.set_angle(bend_id, folded.bends[bend_id].angle_deg)
    observed = []
    try:
        return build_multi_bend_sheet_solid(
            folded, session.panel_build, component_observer=observed.append,
        ).shape, None
    except NativeSheetVolumeMismatch as exc:
        overlaps = []
        for (left, shape), (right, other) in combinations(observed[0].named_shapes(), 2):
            if shape.BoundBox.intersect(other.BoundBox):
                volume = float(shape.common(other).Volume)
                if volume > 1e-3:
                    overlaps.append((left, right, volume))
        trace("diagnose_folded_solid", overlaps=overlaps,
              expected_mm3=exc.expected_mm3, joined_mm3=exc.joined_mm3)
        if not overlaps:
            raise ValueError(
                tr("final_shape_volume_is_mm_expected_mm_diagnostics_found",
                   actual=exc.joined_mm3, expected=exc.expected_mm3)
            ) from exc
        overlaps.sort(key=lambda row: row[2], reverse=True)
        pairs = ", ".join(f"{a.removeprefix('bend_zone:')} / {b.removeprefix('bend_zone:')}"
                          for a, b, _ in overlaps[:4])
        return None, tr("the_final_shape_self_intersects_components_the_flat_pattern", pairs=pairs)


_active_dxf_dialog = None


class DxfManualCommand:
    def GetResources(self):
        return {"MenuText": tr("dxf_flat_pattern"),
                "Pixmap": str(_smbs_icon_path("control_dxf.svg")),
                "ToolTip": tr("assign_angles_to_bend_lines_in_a_dxf_flat")}

    def IsActive(self):
        return True

    def Activated(self):
        import FreeCAD as App
        import FreeCADGui as Gui
        from freecad_smbs.commands.pressbrake_2d import (
            PressBrake2DCommand, _qt, get_workspace_widget, show_workspace_controls,
        )

        global _active_dxf_dialog
        core, gui, qt = _qt()
        parent = Gui.getMainWindow()
        if _active_dxf_dialog is not None:
            _active_dxf_dialog.raise_()
            _active_dxf_dialog.activateWindow()
            return
        path, _ = qt.QFileDialog.getOpenFileName(parent, tr("dxf_flat_pattern"), "", "DXF (*.dxf)")
        if not path:
            return
        import_id = uuid4().hex

        def log(event, **fields):
            debug_event(event, dxf_import_id=import_id, **fields)

        log("DXF_READ_STARTED", path=path, qt_version=core.qVersion(),
            freecad_version=list(App.Version()))
        layers, unsupported = [], {}
        try:
            lines = read_lines(path, layers=layers, unsupported=unsupported)
        except Exception as exc:
            log("DXF_READ_ERROR", error=str(exc), traceback=format_exc())
            qt.QMessageBox.warning(parent, tr("dxf_flat_pattern"), tr_message(exc))
            return

        log("DXF_READ_COMPLETED", unsupported=unsupported, geometry=[
            {"index": index, "layer": layer, **_geometry_for_log(line)}
            for index, (layer, line) in enumerate(zip(layers, lines))
        ])
        class DxfEditor(qt.QDialog):
            calculating = False

            def reject(self):
                if not self.calculating:
                    super().reject()

            def closeEvent(self, event):
                if self.calculating:
                    event.ignore()
                else:
                    super().closeEvent(event)

        dialog = DxfEditor(parent)
        dialog.setObjectName("SMBS_DxfEditor")
        dialog.setWindowTitle(tr("dxf_bend_lines"))
        screen = parent.screen()
        bounds = screen.availableGeometry() if screen else None
        dialog.resize(min(780, bounds.width() - 40) if bounds else 780,
                      min(620, bounds.height() - 60) if bounds else 620)
        layout = qt.QVBoxLayout(dialog)
        layout.addWidget(qt.QLabel(tr("click_a_line_and_enter_its_angle_zero_clears")))
        class DxfView(qt.QGraphicsView):
            def __init__(self, drawing):
                super().__init__(drawing)
                self._zoom_steps = 0.0
                self._pan_pos = None
                self.setRenderHint(gui.QPainter.Antialiasing)
                self.setTransformationAnchor(qt.QGraphicsView.NoAnchor)
                self.setResizeAnchor(qt.QGraphicsView.AnchorViewCenter)
                self.setHorizontalScrollBarPolicy(core.Qt.ScrollBarAlwaysOff)
                self.setVerticalScrollBarPolicy(core.Qt.ScrollBarAlwaysOff)
                self.setToolTip(tr("mouse_wheel_to_zoom_middle_button_to_pan"))

            def fit_drawing(self):
                bounds = self.scene().itemsBoundingRect()
                if not bounds.isEmpty():
                    # Leave room to keep the cursor anchored near drawing edges.
                    margin = max(bounds.width(), bounds.height())
                    self.setSceneRect(bounds.adjusted(-margin, -margin, margin, margin))
                    self.resetTransform()
                    self.fitInView(bounds, core.Qt.KeepAspectRatio)
                    self._zoom_steps = 0.0

            def wheelEvent(self, event):
                delta = event.angleDelta().y() or event.pixelDelta().y()
                if not delta:
                    event.ignore()
                    return
                position = event.position().toPoint() if hasattr(event, "position") else event.pos()
                anchor = self.mapToScene(position)
                steps = max(-8.0, min(28.0, self._zoom_steps + delta / 120.0))
                factor = 1.2 ** (steps - self._zoom_steps)
                self.scale(factor, factor)
                self._zoom_steps = steps
                self._scroll_by(self.mapFromScene(anchor) - position)
                event.accept()

            def _scroll_by(self, offset):
                horizontal = self.horizontalScrollBar()
                vertical = self.verticalScrollBar()
                horizontal.setValue(horizontal.value() + offset.x())
                vertical.setValue(vertical.value() + offset.y())

            def mousePressEvent(self, event):
                if event.button() == core.Qt.MiddleButton:
                    self._pan_pos = event.pos()
                    self.viewport().setCursor(core.Qt.ClosedHandCursor)
                    event.accept()
                    return
                super().mousePressEvent(event)

            def mouseMoveEvent(self, event):
                if self._pan_pos is None:
                    super().mouseMoveEvent(event)
                    return
                self._scroll_by(self._pan_pos - event.pos())
                self._pan_pos = event.pos()
                event.accept()

            def mouseReleaseEvent(self, event):
                if event.button() == core.Qt.MiddleButton and self._pan_pos is not None:
                    self._pan_pos = None
                    self.viewport().unsetCursor()
                    event.accept()
                    return
                super().mouseReleaseEvent(event)

        scene = qt.QGraphicsScene(dialog)
        view = DxfView(scene)
        fit_button = qt.QPushButton(tr("fit_drawing"))
        fit_button.setToolTip(view.toolTip())
        fit_button.clicked.connect(view.fit_drawing)
        undo_button = qt.QPushButton(tr("back"))
        undo_button.setToolTip(tr("undo_the_last_bend_assignment_or_angle_change"))
        undo_button.setEnabled(False)
        drawing_actions = qt.QHBoxLayout()
        drawing_actions.addWidget(fit_button)
        drawing_actions.addWidget(undo_button)
        drawing_actions.addStretch()
        layout.addLayout(drawing_actions)
        layout.addWidget(view, 1)
        layers_box = qt.QListWidget()
        layers_box.setMaximumHeight(120)
        layer_entries = {}
        for layer in sorted(set(layers) | set(unsupported)):
            count = layers.count(layer)
            suffix = tr("needs_review") if layer in unsupported else ""
            item = qt.QListWidgetItem(f"{layer}  ({count}){suffix}")
            if layer in unsupported:
                item.setToolTip(", ".join(sorted(unsupported[layer])))
            item.setFlags(item.flags() | core.Qt.ItemIsUserCheckable)
            item.setCheckState(core.Qt.Checked)
            layers_box.addItem(item)
            layer_entries[layer] = item
        layout.addWidget(qt.QLabel(tr("layers_to_calculate")))
        layout.addWidget(layers_box)
        angles = {}
        angle_history = []
        geometry_items = []
        feedback = qt.QLabel(tr("select_bend_lines"))
        feedback.setWordWrap(True)

        def assign(point):
            area = core.QRect(point.x() - 4, point.y() - 4, 9, 9)
            item = next((item for item in view.items(area) if item.data(0) is not None), None)
            if item is None:
                point_xy = view.mapToScene(point)
                log("DXF_BEND_PICK_MISSED", xy=[point_xy.x(), -point_xy.y()])
                return
            index = item.data(0)
            value, ok = qt.QInputDialog.getDouble(
                dialog, tr("bend_angle"), tr("angle_from_sheet_plane"),
                angles.get(index, 90.0), -179.9, 179.9, 2,
            )
            if not ok:
                log("DXF_BEND_ANGLE_CANCELLED", index=index, layer=layers[index])
                return
            previous_angle = angles.get(index)
            if previous_angle == (value or None):
                return
            angle_history.append(angles.copy())
            if value:
                angles[index] = value
            else:
                angles.pop(index, None)
            log("DXF_BEND_ANGLE_CHANGED", index=index, layer=layers[index],
                previous_angle_deg=previous_angle, angle_deg=angles.get(index))
            refresh_assignments()

        def refresh_assignments():
            feedback.setText(tr("assigned_bends").format(len(angles)))
            undo_button.setEnabled(bool(angle_history))
            positions = {index: step for step, index in enumerate(angles, 1)}
            for i, line in enumerate(geometry_items):
                if line.data(0) is None:
                    continue
                pen = gui.QPen(gui.QColor("#31845e" if i in angles else "#315d87"))
                pen.setWidth(3 if i in angles else 2)
                pen.setCosmetic(True)
                line.setPen(pen)
                line.setToolTip(f"{positions[i]}. {angles[i]:+g}°" if i in angles else "")

        def undo_assignment():
            if not angle_history:
                return
            angles.clear()
            angles.update(angle_history.pop())
            refresh_assignments()
            log("DXF_BEND_ASSIGNMENT_UNDONE", assignments=list(angles.items()))

        undo_button.clicked.connect(undo_assignment)

        class ClickFilter(core.QObject):
            def eventFilter(self, obj, event):
                if event.type() == core.QEvent.MouseButtonRelease and event.button() == core.Qt.LeftButton:
                    assign(event.pos())
                return False

        click = ClickFilter(view)
        view.viewport().installEventFilter(click)
        pen = gui.QPen(gui.QColor("#315d87"))
        pen.setCosmetic(True)
        pen.setWidth(2)
        for index, line in enumerate(lines):
            if isinstance(line, DxfCircle):
                geometry_items.append(scene.addEllipse(line.x - line.radius, -line.y - line.radius,
                                                       line.radius * 2, line.radius * 2, pen))
                continue
            if isinstance(line, (ArcSegment2D, DxfCurve)):
                points = (line.sample_points() if isinstance(line, ArcSegment2D)
                          else tuple((p.x, p.y) for p in line.edge.discretize(Deflection=0.05)))
                curve_path = gui.QPainterPath(core.QPointF(points[0][0], -points[0][1]))
                for x, y in points[1:]:
                    curve_path.lineTo(x, -y)
                geometry_items.append(scene.addPath(curve_path, pen))
                continue
            (x, y), (xx, yy) = line
            item = scene.addLine(x, -y, xx, -yy, pen)
            item.setData(0, index)
            geometry_items.append(item)

        def layer_enabled(layer):
            return layer_entries[layer].checkState() == core.Qt.Checked

        def enabled_layers():
            return [layer for layer in layer_entries if layer_enabled(layer)]

        def layer_changed():
            for layer, item in zip(layers, geometry_items):
                item.setVisible(layer_enabled(layer))
            log("DXF_LAYERS_CHANGED", enabled_layers=enabled_layers())

        layers_box.itemChanged.connect(layer_changed)
        form = qt.QFormLayout()
        layout.addLayout(form)

        def number(label, default, minimum, maximum):
            spin = qt.QDoubleSpinBox()
            spin.setDecimals(3)
            spin.setRange(minimum, maximum)
            spin.setValue(default)
            form.addRow(label, spin)
            return spin

        thickness = number(tr("thickness_mm"), 1.5, 0.01, 100)
        radius = number(tr("inside_radius_mm"), 1.5, 0.01, 100)
        k_factor = number(tr("k_factor"), 0.4, 0, 1)
        outline_tolerance = number(tr("outline_joining_tolerance_mm"),
                                   DEFAULT_OUTLINE_TOLERANCE_MM, 0.001, 0.5)
        outline_tolerance.setSingleStep(0.01)
        outline_tolerance.setToolTip(tr("increase_the_tolerance_for_small_gaps_between_dxf_outline"))
        button = qt.QPushButton(tr("calculate"))
        layout.addWidget(button)
        layout.addWidget(feedback)

        attempt = 0
        closed = False
        pending = False

        def calculate():
            nonlocal attempt, pending
            pending = False
            if closed or dialog.calculating:
                return
            dialog.calculating = True
            dialog.setEnabled(False)
            attempt += 1
            started = perf_counter()
            stage = "validate_inputs"
            stage_started = started
            doc = None

            def trace(next_stage, **fields):
                nonlocal stage, stage_started
                now = perf_counter()
                log("DXF_CALCULATION_STAGE", attempt=attempt, stage=next_stage,
                    elapsed_ms=round((now - started) * 1000, 2),
                    previous_stage=stage, previous_stage_ms=round((now - stage_started) * 1000, 2),
                    **fields)
                stage, stage_started = next_stage, now

            log("DXF_CALCULATION_STARTED", attempt=attempt,
                enabled_layers=enabled_layers(),
                assigned_bends=[{"index": index, "angle_deg": angle}
                                for index, angle in angles.items()],
                thickness_mm=thickness.value(), radius_mm=radius.value(),
                k_factor=k_factor.value(), outline_tolerance_mm=outline_tolerance.value())
            try:
                blocked = [f"{layer}: {', '.join(sorted(types))}"
                           for layer, types in unsupported.items() if layer_enabled(layer)]
                if blocked:
                    raise ValueError(tr("disable_layers_with_unsupported_geometry") + "; ".join(blocked))
                visible = [i for i, layer in enumerate(layers) if layer_enabled(layer)]
                index_map = {old: new for new, old in enumerate(visible)}
                active_angles = {index_map[i]: angle for i, angle in angles.items() if i in index_map}
                if not active_angles:
                    raise ValueError(tr("assign_a_bend_on_an_enabled_layer"))
                session = build_session(path, [lines[i] for i in visible], active_angles,
                                        thickness.value(), radius.value(), k_factor.value(),
                                        outline_tolerance=outline_tolerance.value(), trace=trace)
                trace("build_folded_solid", bend_order=session.bend_order,
                      fixed_panel=session.fixed_panel)
                shape, warning = _folded_dxf_preview(session, trace)

                trace("open_simulator", solid_count=len(shape.Solids) if shape is not None else 0,
                      folded_preview_warning=warning)
                workspace = get_workspace_widget()
                if workspace is None:
                    PressBrake2DCommand().Activated()
                    workspace = get_workspace_widget()
                if workspace is None:
                    raise RuntimeError(tr("could_not_open_pressbrakesim"))
                trace("create_document")
                doc = App.newDocument("SMBS_DXF")
                if shape is not None:
                    final = doc.addObject("Part::Feature", "FoldedPart")
                    final.Label = session.part_label + tr("final_shape")
                    final.Shape = shape
                flat = add_flat_pattern(doc, session)
                if warning:
                    for obj in flat.Group:
                        obj.Visibility = True
                doc.recompute()
                trace("load_simulation", document=doc.Name)
                workspace.load_part(document=doc, selection=[], prepared_session=session)
                workspace.set_cycle_mode("BEND_ONLY")
                show_workspace_controls(workspace, tab=1)
                App.setActiveDocument(doc.Name)
                view3d = Gui.getDocument(doc.Name).activeView()
                view3d.viewAxonometric()
                view3d.fitAll()
                if warning:
                    workspace.status.setText(warning)
                    App.Console.PrintWarning(warning + "\n")
            except Exception as exc:
                error_trace = format_exc()
                log("DXF_CALCULATION_ERROR", attempt=attempt, stage=stage,
                    elapsed_ms=round((perf_counter() - started) * 1000, 2),
                    document=doc.Name if doc is not None else None,
                    error_type=type(exc).__name__, error=str(exc), traceback=error_trace)
                App.Console.PrintError(error_trace)
                detail = tr_message(exc).strip() or type(exc).__name__
                if doc is not None:
                    detail = tr("model_created_in_could_not_open_simulation", document=doc.Name, detail=detail)
                feedback.setText(detail)
                return
            finally:
                dialog.calculating = False
                dialog.setEnabled(True)
                button.setEnabled(True)
            log("DXF_CALCULATION_COMPLETED", attempt=attempt, document=doc.Name,
                elapsed_ms=round((perf_counter() - started) * 1000, 2))
            dialog.accept()

        def start_calculation():
            nonlocal pending
            if closed or pending or dialog.calculating:
                return
            if not angles:
                log("DXF_CALCULATION_REJECTED", reason="no_bend_angles")
                feedback.setText(tr("assign_an_angle_to_at_least_one_bend_line"))
                return
            pending = True
            button.setEnabled(False)
            feedback.setText(tr("building_shape"))
            core.QTimer.singleShot(0, calculate)

        def on_finished(result):
            nonlocal closed
            global _active_dxf_dialog
            closed = True
            if _active_dxf_dialog is dialog:
                _active_dxf_dialog = None
            log("DXF_DIALOG_CLOSED", result=int(result), attempts=attempt)
            dialog.deleteLater()

        button.clicked.connect(start_calculation)
        dialog.finished.connect(on_finished)
        _active_dxf_dialog = dialog
        dialog.open()
        core.QTimer.singleShot(0, lambda: None if closed else view.fit_drawing())
