"""SMBS 2D v4: cumulative folding viewer for FreeCAD.

Run from Python console:
from freecad_smbs.commands.fold_sequence_viewer import show; show()
Requires an active SheetMetal unfolded document and its bend/outline sketches.
"""
from __future__ import annotations
import traceback

_WINDOW = None

def _qt():
    try:
        from PySide2 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide6 import QtCore, QtGui, QtWidgets
    return QtCore, QtGui, QtWidgets

def _project(p, mode):
    x, y, z = p
    if mode == "XY":
        return x, -y
    if mode == "XZ":
        return x, -z
    if mode == "YZ":
        return y, -z
    # Isometric orthographic projection, Z visually upward.
    return (x-y)*0.7071067811865476, (x+y)*0.4082482904638631-z*0.8164965809277261

def _sketch_name(doc, suffix):
    names = [o.Name for o in doc.Objects if o.Name.endswith(suffix) and
             hasattr(o, "Shape")]
    if len(names) != 1:
        raise RuntimeError("Expected one sketch ending in %s, got %s" % (suffix, names))
    return names[0]

def show():
    """Open a standalone floating viewer, leaving the existing addon UI intact."""
    import FreeCAD as App
    from freecad_smbs.integration.panel_builder import build_panels
    from smbs_core.kinematics2d.panel_graph import graph_from_panel_build
    from smbs_core.kinematics2d.fold_sequence import FoldSequenceController
    QtCore, QtGui, QtWidgets = _qt()
    doc = App.ActiveDocument
    if doc is None:
        raise RuntimeError("Open the unfolded part first")
    outline = _sketch_name(doc, "_Unfold_Sketch")
    bends = _sketch_name(doc, "_Unfold_Sketch_Bends")
    unfold = [o.Name for o in doc.Objects if o.Name.endswith("_Unfold") and
              hasattr(o, "baseObject")]
    if len(unfold) != 1:
        raise RuntimeError("Expected one SheetMetal unfold object: %s" % unfold)
    result = build_panels(unfold_name=unfold[0], outline_name=outline,
                          bends_name=bends, document=doc)
    graph = graph_from_panel_build(result)
    polygons = {}
    for panel, face in zip(result.panels, result.panel_faces):
        polygons[panel.panel_id] = [
            (float(v.Point.x), float(v.Point.y)) for v in face.OuterWire.OrderedVertexes
        ]
    class Viewer(QtWidgets.QDialog):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("SMBS 2D v4 | Cumulative fold preview")
            self.resize(940, 740)
            self.timer = QtCore.QTimer(self)
            self.timer.setInterval(35)
            self.timer.timeout.connect(self.tick)
            self.controller = None
            self.setLayout(QtWidgets.QVBoxLayout())
            self.layout().addWidget(QtWidgets.QLabel(
                "PREVIEW: no bend-radius deformation, collision checks or machine validation"))
            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel("Fixed panel"))
            self.fixed = QtWidgets.QComboBox()
            self.fixed.addItems(graph.panels)
            self.fixed.setCurrentIndex(0)
            row.addWidget(self.fixed)
            row.addWidget(QtWidgets.QLabel("View"))
            self.projection = QtWidgets.QComboBox()
            self.projection.addItems(["ISO", "XY", "XZ", "YZ"])
            row.addWidget(self.projection)
            self.layout().addLayout(row)
            self.scene = QtWidgets.QGraphicsScene(self)
            self.view = QtWidgets.QGraphicsView(self.scene)
            self.view.setRenderHint(QtGui.QPainter.Antialiasing)
            self.view.setDragMode(QtWidgets.QGraphicsView.ScrollHandDrag)
            self.layout().addWidget(self.view, 1)
            row = QtWidgets.QHBoxLayout()
            self.reset_btn = QtWidgets.QPushButton("|< Reset")
            self.play_btn = QtWidgets.QPushButton("Play")
            self.end_btn = QtWidgets.QPushButton("End >|")
            self.fit_btn = QtWidgets.QPushButton("Fit")
            for widget in (self.reset_btn, self.play_btn, self.end_btn, self.fit_btn):
                row.addWidget(widget)
            self.layout().addLayout(row)
            self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            self.slider.setRange(0, len(graph.edges)*100)
            self.layout().addWidget(self.slider)
            self.status = QtWidgets.QLabel()
            self.layout().addWidget(self.status)
            self.log = QtWidgets.QPlainTextEdit()
            self.log.setReadOnly(True)
            self.log.setMaximumHeight(100)
            self.layout().addWidget(self.log)
            self.fixed.currentTextChanged.connect(self.rebuild)
            self.projection.currentTextChanged.connect(self.draw)
            self.slider.valueChanged.connect(self.draw)
            self.reset_btn.clicked.connect(lambda: self.slider.setValue(0))
            self.end_btn.clicked.connect(lambda: self.slider.setValue(self.slider.maximum()))
            self.fit_btn.clicked.connect(self.fit)
            self.play_btn.clicked.connect(self.play)
            self.rebuild()
        def rebuild(self, *args):
            self.timer.stop()
            self.play_btn.setText("Play")
            self.controller = FoldSequenceController(graph, self.fixed.currentText())
            self.slider.setValue(0)
            self.log.setPlainText("Panels: %d | Bends: %d\nPreview order: %s\n"
                                  "NOT a verified manufacturing sequence" %
                                  (len(graph.panels), self.controller.steps,
                                   ", ".join(self.controller.order)))
            self.draw()
        def fit(self):
            bounds = self.scene.itemsBoundingRect()
            if not bounds.isEmpty():
                self.view.fitInView(bounds.adjusted(-15,-15,15,15),
                                    QtCore.Qt.KeepAspectRatio)
        def play(self):
            if self.timer.isActive():
                self.timer.stop()
                self.play_btn.setText("Play")
            else:
                if self.slider.value() == self.slider.maximum():
                    self.slider.setValue(0)
                self.timer.start()
                self.play_btn.setText("Pause")
        def tick(self):
            self.slider.setValue(min(self.slider.maximum(), self.slider.value()+2))
            if self.slider.value() >= self.slider.maximum():
                self.timer.stop()
                self.play_btn.setText("Play")
        def draw(self, *args):
            if self.controller is None:
                return
            frame = self.controller.frame(self.slider.value()/100.0)
            mode = self.projection.currentText()
            self.scene.clear()
            for panel_id, vertices in polygons.items():
                transform = frame.transforms[panel_id]
                points = [_project(transform.point((x,y,0)), mode)
                          for x,y in vertices]
                if len(points) < 3:
                    continue
                path = QtGui.QPainterPath()
                path.moveTo(*points[0])
                for p in points[1:]:
                    path.lineTo(*p)
                path.closeSubpath()
                color = ("#aab5bd" if panel_id == self.fixed.currentText()
                         else "#77b7df")
                pen = QtGui.QPen(QtGui.QColor("#245273"), 0.7)
                self.scene.addPath(path, pen, QtGui.QBrush(QtGui.QColor(color)))
                cx = sum(p[0] for p in points)/len(points)
                cy = sum(p[1] for p in points)/len(points)
                label = self.scene.addSimpleText(panel_id)
                label.setBrush(QtGui.QBrush(QtGui.QColor("#10293b")))
                label.setPos(cx, cy)
            bend = frame.active_bend or "DONE"
            self.status.setText("Step %d/%d | Active %s | %.0f%% | View %s" %
                                (frame.step, self.controller.steps, bend,
                                 frame.fraction*100, mode))
            if self.slider.value() == 0 or self.slider.value() == self.slider.maximum():
                QtCore.QTimer.singleShot(0, self.fit)
        def closeEvent(self, event):
            self.timer.stop()
            super().closeEvent(event)
    global _WINDOW
    if _WINDOW is not None:
        _WINDOW.close()
    _WINDOW = Viewer()
    _WINDOW.show()
    return _WINDOW
