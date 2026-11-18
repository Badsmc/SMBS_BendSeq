"""Manual bending of a single flat outline from an ASCII DXF."""
from math import copysign, dist, floor, isfinite
from pathlib import Path
from types import SimpleNamespace
from dataclasses import dataclass


@dataclass(frozen=True)
class DxfCircle:
    x: float
    y: float
    radius: float

from smbs_core.kinematics2d.tooling_25d import (
    LineSegment2D, _extract_dxf_units, _lwpolyline_to_segments, _parse_dxf_pairs,
)


def read_lines(path):
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
            continue
        elif entity == "LWPOLYLINE":
            segments = _lwpolyline_to_segments(props)
            if any(not isinstance(segment, LineSegment2D) for segment in segments):
                raise ValueError("Дуги в полилиниях пока не поддерживаются.")
        elif entity in ("TEXT", "MTEXT", "DIMENSION", "POINT"):
            continue
        else:
            raise ValueError(f"Геометрия DXF {entity} пока не поддерживается.")
        edges.extend(tuple((x * scale, y * scale) for x, y in (segment.start, segment.end))
                     for segment in segments)
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

    for start, end in lines:
        a, b = snap(start), snap(end)
        if a == b:
            continue
        edge = tuple(sorted((a, b)))
        if edge in edges:
            continue
        edges.add(edge)
        neighbours.setdefault(a, set()).add(b)
        neighbours.setdefault(b, set()).add(a)
    for (x, y), links in neighbours.items():
        if len(links) != 2:
            fault = "Разрыв контура" if len(links) == 1 else "Разветвление контура"
            raise ValueError(f"{fault} возле X={x:.3f}, Y={y:.3f} мм. "
                             "Проверьте линии гиба и контур DXF.")
    return sorted(edges)


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
    edges = [Part.makeLine(*(App.Vector(x, y, 0) for x, y in line)) for line in outline]
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
        if holes or len(wire.Edges) == 1:
            points, triangles = face.tessellate(0.05)
            meshes[panel.panel_id] = (
                tuple(tuple((points[i].x, points[i].y) for i in triangle)
                      for triangle in triangles), tuple(rings),
            )
    dimensions = {bend.bend_id: local_bend_dimension_mm(
        axis, panels.outline_vertices_xy, tuple(other for other in axes if other != axis),
    ) for bend, axis in zip(bends, axes)}
    session = LoadedPartSession(Path(path).stem, SimpleNamespace(
        thickness_mm=thickness, dxf_manual=True, panel_meshes=meshes,
    ), panels, graph, vertices, fixed, state, local_bend_dimensions_mm=dimensions)
    session.set_bend_order(tuple(bend.bend_id for bend in bends))
    return session


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
        try:
            lines = read_lines(path)
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
        angles = {}
        items = []
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
                scene.addEllipse(line.x - line.radius, -line.y - line.radius,
                                 line.radius * 2, line.radius * 2, pen)
                items.append(None)
                continue
            (x, y), (xx, yy) = line
            item = scene.addLine(x, -y, xx, -yy, pen)
            item.setData(0, index)
            items.append(item)
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
                session = build_session(path, lines, angles, thickness.value(),
                                        radius.value(), k_factor.value())
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
