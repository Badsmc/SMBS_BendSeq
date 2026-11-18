"""Manual bending of a single flat outline from an ASCII DXF."""
from math import copysign
from pathlib import Path
from types import SimpleNamespace

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


def build_session(path, lines, angles, thickness, radius, k_factor):
    import FreeCAD as App
    import Part

    from freecad_smbs.commands.pressbrake_2d import LoadedPartSession
    from freecad_smbs.integration.panel_builder import partition_flat_pattern
    from smbs_core.kinematics2d.gauge_preview import local_bend_dimension_mm
    from smbs_core.kinematics2d.panel_graph import graph_from_panel_build
    from smbs_core.kinematics2d.panel_kinematics import FoldState

    outline = [Part.makeLine(*(App.Vector(x, y, 0) for x, y in line))
               for index, line in enumerate(lines) if index not in angles]
    groups = Part.sortEdges(outline)
    if len(groups) != 1:
        raise ValueError("После выбора гибов должен остаться один замкнутый контур без отверстий.")
    wire = Part.Wire(groups[0])
    axes = tuple(lines[index] for index in angles)
    bends = [
        SimpleNamespace(bend_id=f"B{step:02d}", fold_angle_deg=angle,
                        source_angle_deg=copysign(180 - abs(angle), angle),
                        radius_mm=radius if angle > 0 else radius + thickness,
                        thickness_mm=thickness, k_factor_ansi=k_factor)
        for step, angle in enumerate(angles.values(), 1)
    ]
    panels = partition_flat_pattern(wire, axes, axes, bends)
    graph = graph_from_panel_build(panels)
    fixed = max(panels.panels, key=lambda panel: panel.area_mm2).panel_id
    state = FoldState(graph, fixed)
    vertices = {
        panel.panel_id: tuple((v.Point.x, v.Point.y)
                              for v in face.OuterWire.OrderedVertexes)
        for panel, face in zip(panels.panels, panels.panel_faces)
    }
    dimensions = {bend.bend_id: local_bend_dimension_mm(
        axis, panels.outline_vertices_xy, tuple(other for other in axes if other != axis),
    ) for bend, axis in zip(bends, axes)}
    session = LoadedPartSession(Path(path).stem, SimpleNamespace(
        thickness_mm=thickness, dxf_manual=True,
    ), panels, graph, vertices, fixed, state, local_bend_dimensions_mm=dimensions)
    session.set_bend_order(tuple(bend.bend_id for bend in bends))
    return session


class DxfManualCommand:
    def GetResources(self):
        return {"MenuText": "Развёртка DXF",
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

        def assign(point):
            item = next((item for item in view.items(point) if item.data(0) is not None), None)
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
            for i, line in enumerate(items):
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
        for index, ((x, y), (xx, yy)) in enumerate(lines):
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
        result = []

        def calculate():
            if not angles:
                qt.QMessageBox.information(dialog, "Развёртка DXF", "Назначьте хотя бы один гиб.")
                return
            try:
                session = build_session(path, lines, angles, thickness.value(),
                                        radius.value(), k_factor.value())
                folded = FoldState(session.graph, session.fixed_panel)
                for bend_id in session.bend_order:
                    folded.set_angle(bend_id, folded.bends[bend_id].angle_deg)
                shape = build_multi_bend_sheet_solid(folded, session.panel_build).shape
            except (RuntimeError, ValueError, KeyError) as exc:
                qt.QMessageBox.warning(dialog, "Развёртка DXF", str(exc))
                return
            result.append((session, shape))
            dialog.accept()

        button.clicked.connect(calculate)
        core.QTimer.singleShot(0, lambda: view.fitInView(scene.itemsBoundingRect(), core.Qt.KeepAspectRatio))
        run = getattr(dialog, "exec", None) or dialog.exec_
        if not run() or not result:
            return
        session, shape = result[0]
        workspace = get_workspace_widget()
        if workspace is None:
            PressBrake2DCommand().Activated()
            workspace = get_workspace_widget()
        doc = App.newDocument("SMBS_DXF")
        final = doc.addObject("Part::Feature", "FoldedPart")
        final.Label = session.part_label + " — конечная форма"
        final.Shape = shape
        doc.recompute()
        workspace.load_part(document=doc, selection=[], prepared_session=session)
        workspace.set_cycle_mode("BEND_ONLY")
        show_workspace_controls(workspace, tab=2)
        Gui.activeDocument().activeView().viewAxonometric()
        Gui.activeDocument().activeView().fitAll()
