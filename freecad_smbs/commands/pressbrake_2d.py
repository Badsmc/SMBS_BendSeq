"""PressBrakeSim 2D UI preview. No machine physics or solver results."""
from __future__ import annotations

import math

_DOCK = None


def _qt():
    try:
        from PySide import QtCore, QtGui
        return QtCore, QtGui, QtGui
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets
        return QtCore, QtGui, QtWidgets


class PressBrake2DCommand:
    def GetResources(self):
        return {
            "MenuText": "PressBrakeSim — 2D интерфейс",
            "ToolTip": "Открыть тестовую 2D-панель (без физического расчёта)",
        }

    def IsActive(self):
        return True

    def Activated(self):
        import FreeCADGui as Gui
        global _DOCK
        QtCore, QtGui, QtWidgets = _qt()
        main = Gui.getMainWindow()
        dock = main.findChild(QtWidgets.QDockWidget, "SMBS_PressBrake2D_Dock")
        if dock is None:
            dock = QtWidgets.QDockWidget("PressBrakeSim — 2D Preview", main)
            dock.setObjectName("SMBS_PressBrake2D_Dock")
            dock.setAllowedAreas(
                QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea
                | QtCore.Qt.BottomDockWidgetArea
            )
            dock.setWidget(_PreviewPanel())
            main.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
        _DOCK = dock
        dock.show()
        dock.raise_()


def _button(QtWidgets, label, handler, enabled=True):
    btn = QtWidgets.QPushButton(label)
    btn.setEnabled(enabled)
    btn.clicked.connect(handler)
    return btn


class _PreviewPanel:
    """Proxy factory, so Qt is only imported after FreeCAD GUI is available."""

    def __new__(cls):
        QtCore, QtGui, QtWidgets = _qt()

        class Panel(QtWidgets.QWidget):
            def __init__(self):
                super().__init__()
                self.angle = 0
                self.playing = False
                self.timer = QtCore.QTimer(self)
                self.timer.setInterval(45)
                self.timer.timeout.connect(self.advance)
                root = QtWidgets.QVBoxLayout(self)
                root.setContentsMargins(8, 8, 8, 8)
                header = QtWidgets.QLabel("PressBrakeSim  |  INTERFACE TEST")
                header.setStyleSheet("font-weight: bold; font-size: 13px")
                root.addWidget(header)
                self.status = QtWidgets.QLabel(
                    "DEMO ONLY — геометрия и результаты не взяты из детали"
                )
                self.status.setWordWrap(True)
                root.addWidget(self.status)

                row = QtWidgets.QHBoxLayout()
                self.load = _button(QtWidgets, "Деталь (позже)", lambda: None, False)
                self.tooling = _button(QtWidgets, "Оснастка (позже)", lambda: None, False)
                self.calculate = _button(QtWidgets, "Расчёт (позже)", lambda: None, False)
                for b in (self.load, self.tooling, self.calculate):
                    row.addWidget(b)
                root.addLayout(row)

                self.view = QtWidgets.QGraphicsView()
                self.scene = QtWidgets.QGraphicsScene(self.view)
                self.view.setScene(self.scene)
                self.view.setRenderHint(QtGui.QPainter.Antialiasing, True)
                self.view.setMinimumSize(340, 300)
                self.view.setDragMode(QtWidgets.QGraphicsView.ScrollHandDrag)
                root.addWidget(self.view, 1)

                controls = QtWidgets.QHBoxLayout()
                self.reset_btn = _button(QtWidgets, "|<", self.reset)
                self.back_btn = _button(QtWidgets, "<", lambda: self.set_angle(self.angle - 5))
                self.play_btn = _button(QtWidgets, "▶", self.toggle_play)
                self.next_btn = _button(QtWidgets, ">", lambda: self.set_angle(self.angle + 5))
                self.end_btn = _button(QtWidgets, ">|", lambda: self.set_angle(90))
                self.fit_btn = _button(QtWidgets, "Fit", self.fit)
                for b in (self.reset_btn, self.back_btn, self.play_btn,
                          self.next_btn, self.end_btn, self.fit_btn):
                    controls.addWidget(b)
                root.addLayout(controls)

                self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
                self.slider.setRange(0, 90)
                self.slider.valueChanged.connect(self.set_angle)
                root.addWidget(self.slider)

                self.step = QtWidgets.QLabel()
                root.addWidget(self.step)
                title = QtWidgets.QLabel("Операции — демонстрационный сценарий")
                root.addWidget(title)
                self.operations = QtWidgets.QTreeWidget()
                self.operations.setHeaderLabels(["№", "Операция", "Статус"])
                self.operations.setRootIsDecorated(False)
                self.operations.setMaximumHeight(105)
                item = QtWidgets.QTreeWidgetItem(["1", "Гиб 0° → 90°", "DEMO"])
                self.operations.addTopLevelItem(item)
                root.addWidget(self.operations)
                self.draw()
                QtCore.QTimer.singleShot(0, self.fit)

            def set_angle(self, value):
                self.angle = min(90, max(0, int(value)))
                if self.slider.value() != self.angle:
                    self.slider.blockSignals(True)
                    self.slider.setValue(self.angle)
                    self.slider.blockSignals(False)
                self.draw()

            def reset(self):
                self.timer.stop()
                self.playing = False
                self.play_btn.setText("▶")
                self.set_angle(0)

            def toggle_play(self):
                if self.playing:
                    self.timer.stop()
                    self.playing = False
                    self.play_btn.setText("▶")
                else:
                    if self.angle >= 90:
                        self.set_angle(0)
                    self.playing = True
                    self.play_btn.setText("Ⅱ")
                    self.timer.start()

            def advance(self):
                self.set_angle(self.angle + 1)
                if self.angle >= 90:
                    self.timer.stop()
                    self.playing = False
                    self.play_btn.setText("▶")

            def fit(self):
                self.view.fitInView(self.scene.sceneRect(), QtCore.Qt.KeepAspectRatio)

            def draw(self):
                self.scene.clear()
                self.scene.setSceneRect(-170, -145, 340, 290)
                outline = QtGui.QPen(QtGui.QColor("#8d99a6"), 1)
                die_pen = QtGui.QPen(QtGui.QColor("#447da6"), 2)
                punch_pen = QtGui.QPen(QtGui.QColor("#c48a3a"), 2)
                sheet_pen = QtGui.QPen(QtGui.QColor("#23a59a"), 5)
                sheet_pen.setCapStyle(QtCore.Qt.RoundCap)

                # Scene y-axis points down; bend vertex is at (0, 0).
                self.scene.addLine(-140, 110, 140, 110, outline)
                die = QtGui.QPolygonF([
                    QtCore.QPointF(-105, 100), QtCore.QPointF(-105, 36),
                    QtCore.QPointF(-35, 36), QtCore.QPointF(0, 72),
                    QtCore.QPointF(35, 36), QtCore.QPointF(105, 36),
                    QtCore.QPointF(105, 100)])
                self.scene.addPolygon(die, die_pen)
                self.scene.addLine(-19, -112, 0, -80, punch_pen)
                self.scene.addLine(0, -80, 19, -112, punch_pen)
                self.scene.addLine(-19, -112, -19, -140, punch_pen)
                self.scene.addLine(19, -112, 19, -140, punch_pen)
                self.scene.addLine(-135, 0, 0, 0, sheet_pen)
                theta = math.radians(self.angle)
                self.scene.addLine(0, 0, 115 * math.cos(theta),
                                   -115 * math.sin(theta), sheet_pen)
                self.step.setText(
                    "Шаг 1/1  |  Демонстрационный угол: {}°  |  Не проверено".format(
                        self.angle))
        return Panel()
