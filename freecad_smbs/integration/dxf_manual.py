"""Manual bending of a single flat outline from an ASCII DXF."""
from itertools import groupby
from math import copysign, dist, floor, isfinite, tau
from pathlib import Path
from types import SimpleNamespace
from dataclasses import dataclass


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
        raise ValueError("SPLINE без контрольных точек или узлов.")
    poles = [App.Vector(float(x) * scale, float(y) * scale, 0)
             for x, y in zip(groups[10], groups[20], strict=True)]
    knots, multiplicities = zip(*((k, len(list(run)))
                                   for k, run in groupby(map(float, groups[40]))))
    curve = Part.BSplineCurve()
    flags = int(values.get(70, 0))
    weights = list(map(float, groups.get(41, ()))) or None
    if flags & 2:
        raise ValueError("Периодические SPLINE пока не поддерживаются.")
    args = (poles, list(multiplicities), list(knots), False, int(values[71]))
    if weights:
        curve.buildFromPolesMultsKnots(*args, weights)
    else:
        curve.buildFromPolesMultsKnots(*args)
    return DxfCurve(curve.toShape())


def read_lines(path, *, layers=None, unsupported=None):
    pairs = _parse_dxf_pairs(Path(path).read_text(encoding="utf-8-sig", errors="replace"))
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
                    raise ValueError(f"{entity}: нужна плоскость XY.")
                unsupported.setdefault(layer, set()).add(f"{entity} вне XY")
                continue
        if entity == "LINE":
            if any(abs(float(values.get(axis, 0))) > 1e-6 for axis in (30, 31)):
                raise ValueError("Линии DXF должны лежать в плоскости XY.")
            segments = [LineSegment2D((float(values[10]), float(values[20])),
                                      (float(values[11]), float(values[21])))]
        elif entity == "CIRCLE":
            x, y, radius = (float(values[group]) * scale for group in (10, 20, 40))
            if not all(isfinite(v) for v in (x, y, radius)) or radius <= 0 or abs(float(values.get(30, 0))) > 1e-6:
                raise ValueError("Некорректная окружность в плоскости XY.")
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
                        raise ValueError("Полигоны и 3D POLYLINE не входят в плоский контур.")
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
                raise ValueError(f"Геометрия DXF {entity} пока не поддерживается.")
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
        raise ValueError("В DXF нет линий.")
    return edges


def join_outline(lines, tolerance=0.01):
    """Snap nearby endpoints and remove duplicate edges before OCC builds wires."""
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
    for (x, y), links in neighbours.items():
        if len(links) != 2:
            fault = "Разрыв контура" if len(links) == 1 else "Разветвление контура"
            raise ValueError(f"{fault} возле X={x:.3f}, Y={y:.3f} мм. "
                             "Проверьте линии гиба и контур DXF.")
    return result


def build_session(path, lines, angles, thickness, radius, k_factor):
    import FreeCAD as App
    import Part

    from freecad_smbs.commands.pressbrake_2d import LoadedPartSession
    from freecad_smbs.integration.panel_builder import partition_flat_pattern
    from smbs_core.kinematics2d.gauge_preview import local_bend_dimension_mm
    from smbs_core.kinematics2d.panel_graph import graph_from_panel_build
    from smbs_core.kinematics2d.panel_kinematics import FoldState

    outline = join_outline(line for index, line in enumerate(lines)
                           if index not in angles and not isinstance(line, DxfCircle))
    edges = []
    for line in outline:
        if isinstance(line, DxfCurve):
            edge = line.edge.copy()
            edge.fixTolerance(0.01)
        else:
            points = [App.Vector(x, y, 0) for x, y in line]
            edge = Part.Arc(*points).toShape() if len(points) == 3 else Part.makeLine(*points)
        edges.append(edge)
    wires = [Part.Wire(group) for group in Part.sortEdges(edges)]
    wires.extend(Part.Wire([Part.makeCircle(line.radius, App.Vector(line.x, line.y, 0))])
                 for line in lines if isinstance(line, DxfCircle))
    faces = [Part.Face(wire) for wire in wires if wire.isClosed()]
    if not faces or len(faces) != len(wires) or any(not face.isValid() for face in faces):
        raise ValueError("Не удалось собрать замкнутый контур DXF.")
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
    panels = partition_flat_pattern(wire, axes, axes, bends,
                                    hole_faces=holes)
    graph = graph_from_panel_build(panels)
    fixed = max(panels.panels, key=lambda panel: panel.area_mm2).panel_id
    state = FoldState(graph, fixed)
    vertices = {}
    meshes = {}
    for panel, face in zip(panels.panels, panels.panel_faces):
        rings = []
        for ring in [face.OuterWire, *(wire for wire in face.Wires if not wire.isSame(face.OuterWire))]:
            points = ring.discretize(Deflection=0.05)
            rings.append(tuple((v.x, v.y) for v in points[:-1]))
        vertices[panel.panel_id] = rings[0]
        if holes or any(not isinstance(edge.Curve, Part.Line) for edge in wire.Edges):
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
    group.Label = "Развёртка DXF"
    outline = document.addObject("Part::Feature", "DXFContours")
    outline.Label = "Контур и отверстия"
    outline.Shape = session.unfold_data.flat_contours
    group.addObject(outline)
    bends = document.addObject("Part::Feature", "DXFBendLines")
    bends.Label = "Линии гиба"
    bends.Shape = Part.makeCompound([
        Part.makeLine(App.Vector(*start), App.Vector(*end))
        for bend in session.ordered_bends
        for start, end in (bend.axis_xyz,)
    ])
    group.addObject(bends)
    outline.Visibility = False
    bends.Visibility = False
    return group


class DxfManualCommand:
    def GetResources(self):
        return {"MenuText": "Развёртка DXF",
                "Pixmap": str(Path(__file__).resolve().parents[1] / "resources" / "icons" / "control_folder.svg"),
                "ToolTip": "Назначить углы линиям гиба на развёртке DXF"}

    def IsActive(self):
        return True

    def Activated(self):
        import FreeCAD as App
        import FreeCADGui as Gui
        from freecad_smbs.commands.pressbrake_2d import (
            PressBrake2DCommand, _qt, get_workspace_widget, show_workspace_controls,
        )
        from smbs_core.kinematics2d.native_bend_solid_3d import build_multi_bend_sheet_solid
        from smbs_core.kinematics2d.panel_kinematics import FoldState

        core, gui, qt = _qt()
        parent = Gui.getMainWindow()
        path, _ = qt.QFileDialog.getOpenFileName(parent, "Развёртка DXF", "", "DXF (*.dxf)")
        if not path:
            return
        layers, unsupported = [], {}
        try:
            lines = read_lines(path, layers=layers, unsupported=unsupported)
        except (OSError, ValueError, KeyError) as exc:
            qt.QMessageBox.warning(parent, "Развёртка DXF", str(exc))
            return

        dialog = qt.QDialog(parent)
        dialog.setWindowTitle("Линии гиба DXF")
        dialog.resize(780, 620)
        layout = qt.QVBoxLayout(dialog)
        layout.addWidget(qt.QLabel("Щёлкните по линии и задайте угол. Ноль снимает назначение. "
                                  "Порядок назначения задаёт порядок гибов."))
        scene = qt.QGraphicsScene(dialog)
        view = qt.QGraphicsView(scene)
        layout.addWidget(view, 1)
        layers_box = qt.QListWidget()
        layers_box.setMaximumHeight(120)
        layer_entries = {}
        for layer in sorted(set(layers) | set(unsupported)):
            count = layers.count(layer)
            suffix = " · требуется проверка" if layer in unsupported else ""
            item = qt.QListWidgetItem(f"{layer}  ({count}){suffix}")
            if layer in unsupported:
                item.setToolTip(", ".join(sorted(unsupported[layer])))
            item.setFlags(item.flags() | core.Qt.ItemIsUserCheckable)
            item.setCheckState(core.Qt.Checked)
            layers_box.addItem(item)
            layer_entries[layer] = item
        layout.addWidget(qt.QLabel("Слои для расчёта"))
        layout.addWidget(layers_box)
        angles = {}
        items = []
        geometry_items = []
        feedback = qt.QLabel("Выберите линии гиба.")
        feedback.setWordWrap(True)

        def assign(point):
            area = core.QRect(point.x() - 4, point.y() - 4, 9, 9)
            item = next((item for item in view.items(area) if item.data(0) is not None), None)
            if item is None:
                return
            index = item.data(0)
            value, ok = qt.QInputDialog.getDouble(
                dialog, "Угол гиба", "Угол от плоскости листа, °",
                angles.get(index, 90.0), -179.9, 179.9, 2,
            )
            if not ok:
                return
            if value:
                angles[index] = value
            else:
                angles.pop(index, None)
            feedback.setText("Назначено гибов: {}".format(len(angles)))
            for i, line in enumerate(items):
                if line is None:
                    continue
                pen = gui.QPen(gui.QColor("#31845e" if i in angles else "#315d87"))
                pen.setWidth(3 if i in angles else 2)
                pen.setCosmetic(True)
                line.setPen(pen)
                line.setToolTip(f"{list(angles).index(i)+1}. {angles[i]:+g}°" if i in angles else "")

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
                items.append(None)
                continue
            if isinstance(line, (ArcSegment2D, DxfCurve)):
                points = (line.sample_points() if isinstance(line, ArcSegment2D)
                          else tuple((p.x, p.y) for p in line.edge.discretize(Deflection=0.05)))
                curve_path = gui.QPainterPath(core.QPointF(points[0][0], -points[0][1]))
                for x, y in points[1:]:
                    curve_path.lineTo(x, -y)
                geometry_items.append(scene.addPath(curve_path, pen))
                items.append(None)
                continue
            (x, y), (xx, yy) = line
            item = scene.addLine(x, -y, xx, -yy, pen)
            item.setData(0, index)
            items.append(item)
            geometry_items.append(item)

        def layer_enabled(layer):
            return layer_entries[layer].checkState() == core.Qt.Checked

        def update_layers():
            for layer, item in zip(layers, geometry_items):
                item.setVisible(layer_enabled(layer))

        layers_box.itemChanged.connect(update_layers)
        form = qt.QFormLayout()
        layout.addLayout(form)

        def number(label, default, minimum, maximum):
            spin = qt.QDoubleSpinBox()
            spin.setDecimals(3)
            spin.setRange(minimum, maximum)
            spin.setValue(default)
            form.addRow(label, spin)
            return spin

        thickness = number("Толщина, мм", 1.5, 0.01, 100)
        radius = number("Внутренний радиус, мм", 1.5, 0.01, 100)
        k_factor = number("K-фактор", 0.4, 0, 1)
        button = qt.QPushButton("Рассчитать")
        layout.addWidget(button)
        layout.addWidget(feedback)

        def calculate():
            doc = None
            try:
                blocked = [f"{layer}: {', '.join(sorted(types))}"
                           for layer, types in unsupported.items() if layer_enabled(layer)]
                if blocked:
                    raise ValueError("Отключите слои с неподдерживаемой геометрией: " + "; ".join(blocked))
                visible = [i for i, layer in enumerate(layers) if layer_enabled(layer)]
                index_map = {old: new for new, old in enumerate(visible)}
                active_angles = {index_map[i]: angle for i, angle in angles.items() if i in index_map}
                if not active_angles:
                    raise ValueError("Назначьте гиб на включённом слое.")
                session = build_session(path, [lines[i] for i in visible], active_angles,
                                        thickness.value(), radius.value(), k_factor.value())
                folded = FoldState(session.graph, session.fixed_panel)
                for bend_id in session.bend_order:
                    folded.set_angle(bend_id, folded.bends[bend_id].angle_deg)
                shape = build_multi_bend_sheet_solid(folded, session.panel_build).shape

                workspace = get_workspace_widget()
                if workspace is None:
                    PressBrake2DCommand().Activated()
                    workspace = get_workspace_widget()
                if workspace is None:
                    raise RuntimeError("Не удалось открыть симулятор PressBrakeSim.")
                doc = App.newDocument("SMBS_DXF")
                final = doc.addObject("Part::Feature", "FoldedPart")
                final.Label = session.part_label + " — конечная форма"
                final.Shape = shape
                add_flat_pattern(doc, session)
                doc.recompute()
                workspace.load_part(document=doc, selection=[], prepared_session=session)
                workspace.set_cycle_mode("BEND_ONLY")
                show_workspace_controls(workspace, tab=2)
                App.setActiveDocument(doc.Name)
                view3d = Gui.getDocument(doc.Name).activeView()
                view3d.viewAxonometric()
                view3d.fitAll()
            except Exception as exc:
                import traceback
                App.Console.PrintError(traceback.format_exc())
                detail = str(exc).strip() or type(exc).__name__
                if doc is not None:
                    detail = f"Модель создана в {doc.Name}. Не удалось открыть симуляцию: {detail}"
                feedback.setText(detail)
                return
            finally:
                button.setEnabled(True)
            dialog.accept()

        def start_calculation():
            if not angles:
                feedback.setText("Назначьте угол хотя бы одной линии гиба.")
                return
            button.setEnabled(False)
            feedback.setText("Расчёт формы…")
            core.QTimer.singleShot(0, calculate)

        button.clicked.connect(start_calculation)
        core.QTimer.singleShot(0, lambda: view.fitInView(scene.itemsBoundingRect(), core.Qt.KeepAspectRatio))
        run = getattr(dialog, "exec", None) or dialog.exec_
        run()
