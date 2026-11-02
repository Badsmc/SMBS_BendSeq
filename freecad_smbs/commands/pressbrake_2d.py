"""PressBrakeSim: isolated 2D UI preview and observable UI diagnostics.

No SheetMetal data, collision detection or validated bend calculations are used.
"""
from __future__ import annotations

import math
import traceback

_DOCK = None


def _qt():
    """Return QtCore, QtGui, QtWidgets for FreeCAD-supported bindings."""
    try:
        from PySide import QtCore, QtGui
        return QtCore, QtGui, QtGui
    except ImportError:
        try:
            from PySide2 import QtCore, QtGui, QtWidgets
            return QtCore, QtGui, QtWidgets
        except ImportError:
            from PySide6 import QtCore, QtGui, QtWidgets
            return QtCore, QtGui, QtWidgets


def _log(message):
    try:
        import FreeCAD
        FreeCAD.Console.PrintMessage("[PressBrake2D] " + str(message) + "\n")
    except ImportError:
        pass


def _error(message):
    try:
        import FreeCAD
        FreeCAD.Console.PrintError("[PressBrake2D] " + str(message) + "\n")
    except ImportError:
        pass


class PressBrake2DCommand:
    def GetResources(self):
        return {
            "MenuText": "PressBrakeSim — 2D интерфейс",
            "ToolTip": "Открыть 2D интерфейс и выполнить UI самопроверку (DEMO)",
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
                QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea |
                QtCore.Qt.BottomDockWidgetArea
            )
            dock.setMinimumWidth(365)
            try:
                dock.setWidget(_PreviewPanel())
                main.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
            except Exception:
                _error(traceback.format_exc())
                dock.deleteLater()
                raise
        _DOCK = dock
        dock.show()
        dock.raise_()
        _log("2D preview opened")


def _button(widgets, label, callback, enabled=True):
    btn = widgets.QPushButton(label)
    btn.setEnabled(enabled)
    btn.clicked.connect(callback)
    return btn


class _PreviewPanel:
    """Lazy Qt widget factory to keep headless command import possible."""

    def __new__(cls):
        QtCore, QtGui, QtWidgets = _qt()

        class Canvas(QtWidgets.QGraphicsView):
            def __init__(self, parent=None):
                super().__init__(parent)
                self.setRenderHint(QtGui.QPainter.Antialiasing, True)
                self.setDragMode(QtWidgets.QGraphicsView.ScrollHandDrag)
                self.setMinimumHeight(220)
                self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
                self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

            def resizeEvent(self, event):
                super().resizeEvent(event)
                if self.scene() is not None and not self.scene().sceneRect().isEmpty():
                    self.fitInView(self.scene().sceneRect(), QtCore.Qt.KeepAspectRatio)

        class Panel(QtWidgets.QWidget):
            def __init__(self):
                super().__init__()
                self.angle = 0
                self.playing = False
                self.timer = QtCore.QTimer(self)
                self.timer.setInterval(45)
                self.timer.timeout.connect(self.advance)
                self.setMinimumWidth(340)

                root = QtWidgets.QVBoxLayout(self)
                root.setContentsMargins(8, 8, 8, 8)
                root.setSpacing(6)
                title = QtWidgets.QLabel("PressBrakeSim  |  2D UI TEST")
                title.setStyleSheet("font-weight: bold; font-size: 13px")
                root.addWidget(title)

                self.status = QtWidgets.QLabel(
                    "DEMO ONLY — нет загрузки детали, расчёта или проверки коллизий"
                )
                self.status.setWordWrap(True)
                root.addWidget(self.status)

                row = QtWidgets.QHBoxLayout()
                self.load = _button(QtWidgets, "Деталь", self.unavailable, False)
                self.tooling = _button(QtWidgets, "Оснастка", self.unavailable, False)
                self.calculate = _button(QtWidgets, "Расчёт", self.unavailable, False)
                for widget in (self.load, self.tooling, self.calculate):
                    row.addWidget(widget)
                root.addLayout(row)

                self.view = Canvas()
                self.scene = QtWidgets.QGraphicsScene(self.view)
                self.view.setScene(self.scene)
                self.scene.setSceneRect(-170, -145, 340, 290)
                root.addWidget(self.view, 1)

                row = QtWidgets.QHBoxLayout()
                self.reset_btn = _button(QtWidgets, "|<", self.reset)
                self.back_btn = _button(QtWidgets, "<", lambda: self.set_angle(self.angle - 5))
                self.play_btn = _button(QtWidgets, "▶", self.toggle_play)
                self.next_btn = _button(QtWidgets, ">", lambda: self.set_angle(self.angle + 5))
                self.end_btn = _button(QtWidgets, ">|", lambda: self.set_angle(90))
                self.fit_btn = _button(QtWidgets, "Fit", self.fit)
                for widget in (self.reset_btn, self.back_btn, self.play_btn,
                               self.next_btn, self.end_btn, self.fit_btn):
                    row.addWidget(widget)
                root.addLayout(row)

                self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
                self.slider.setRange(0, 90)
                self.slider.valueChanged.connect(self.set_angle)
                root.addWidget(self.slider)

                self.step = QtWidgets.QLabel()
                root.addWidget(self.step)
                root.addWidget(QtWidgets.QLabel("Операции — демонстрационный сценарий"))
                self.operations = QtWidgets.QTreeWidget()
                self.operations.setHeaderLabels(["№", "Операция", "Статус"])
                self.operations.setRootIsDecorated(False)
                self.operations.setMaximumHeight(100)
                self.operations.addTopLevelItem(
                    QtWidgets.QTreeWidgetItem(["1", "Гиб 0° → 90°", "DEMO"])
                )
                root.addWidget(self.operations)

                self.check_btn = _button(QtWidgets, "Проверить интерфейс", self.diagnostics)
                root.addWidget(self.check_btn)
                self.check_result = QtWidgets.QLabel("Диагностика ещё не запускалась")
                self.check_result.setWordWrap(True)
                root.addWidget(self.check_result)

                self.draw()
                QtCore.QTimer.singleShot(0, self.fit)
                _log("Panel constructed, scene items: {}".format(len(self.scene.items())))

            def unavailable(self):
                pass  # All unavailable controls are disabled.

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
                    _log("Playback paused at {} deg".format(self.angle))
                else:
                    if self.angle >= 90:
                        self.set_angle(0)
                    self.playing = True
                    self.play_btn.setText("Ⅱ")
                    self.timer.start()
                    _log("Playback started at {} deg".format(self.angle))

            def advance(self):
                self.set_angle(self.angle + 1)
                if self.angle >= 90:
                    self.timer.stop()
                    self.playing = False
                    self.play_btn.setText("▶")
                    _log("Playback completed")

            def fit(self):
                rect = self.scene.sceneRect()
                if not rect.isEmpty():
                    self.view.fitInView(rect, QtCore.Qt.KeepAspectRatio)

            def diagnostics(self):
                checks = {
                    "scene_attached": self.view.scene() is self.scene,
                    "scene_items": len(self.scene.items()) >= 6,
                    "scene_rect": not self.scene.sceneRect().isEmpty(),
                    "viewport_visible": self.view.viewport().width() > 0 and
                                        self.view.viewport().height() > 0,
                    "slider_range": self.slider.minimum() == 0 and
                                    self.slider.maximum() == 90,
                    "slider_sync": self.slider.value() == self.angle,
                    "demo_row": self.operations.topLevelItemCount() == 1,
                    "timer_consistency": self.timer.isActive() == self.playing,
                    "unavailable_controls_disabled": not any(
                        b.isEnabled() for b in
                        (self.load, self.tooling, self.calculate)),
                }
                failures = [key for key, passed in checks.items() if not passed]
                message = (
                    "UI checks: {}/{} PASS{}".format(
                        len(checks) - len(failures), len(checks),
                        "" if not failures else " | FAIL: " + ", ".join(failures)
                    )
                )
                self.check_result.setText(message)
                _log(message)
                return checks

            def closeEvent(self, event):
                self.timer.stop()
                self.playing = False
                super().closeEvent(event)

            def draw(self):
                try:
                    self.scene.clear()
                    outline = QtGui.QPen(QtGui.QColor("#8d99a6"), 1)
                    die_pen = QtGui.QPen(QtGui.QColor("#447da6"), 2)
                    punch_pen = QtGui.QPen(QtGui.QColor("#c48a3a"), 2)
                    sheet_pen = QtGui.QPen(QtGui.QColor("#23a59a"), 5)
                    sheet_pen.setCapStyle(QtCore.Qt.RoundCap)
                    self.scene.addLine(-140, 110, 140, 110, outline)
                    die = QtGui.QPolygonF([
                        QtCore.QPointF(-105, 100), QtCore.QPointF(-105, 36),
                        QtCore.QPointF(-35, 36), QtCore.QPointF(0, 72),
                        QtCore.QPointF(35, 36), QtCore.QPointF(105, 36),
                        QtCore.QPointF(105, 100),
                    ])
                    self.scene.addPolygon(die, die_pen)
                    self.scene.addLine(-19, -112, 0, -80, punch_pen)
                    self.scene.addLine(0, -80, 19, -112, punch_pen)
                    self.scene.addLine(-19, -112, -19, -140, punch_pen)
                    self.scene.addLine(19, -112, 19, -140, punch_pen)
                    self.scene.addLine(-135, 0, 0, 0, sheet_pen)
                    theta = math.radians(self.angle)
                    self.scene.addLine(
                        0, 0, 115 * math.cos(theta), -115 * math.sin(theta),
                        sheet_pen
                    )
                    self.step.setText(
                        "Шаг 1/1 | DEMO угол: {}° | НЕ ВЕРИФИЦИРОВАНО".format(
                            self.angle))
                except Exception:
                    _error(traceback.format_exc())
                    raise

        return Panel()
