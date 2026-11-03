"""PressBrakeSim 2.5D — Real SheetMetal Geometry Viewer (Phase 3A).

Uses the existing 2.5D pipeline without introducing a second geometry engine:
  sheetmetal_unfold_reader.read_unfold
  -> panel_builder.build_panels
  -> panel_graph.graph_from_panel_build
  -> panel_kinematics.FoldState (with explicit BendZone)
"""
from __future__ import annotations

import math
import traceback
from dataclasses import dataclass

from smbs_core.kinematics2d.panel_graph import PanelGraph, graph_from_panel_build
from smbs_core.kinematics2d.panel_kinematics import FoldState, KinematicsError

_DOCK = None
_SLIDER_SCALE = 10  # 0.1 degree slider resolution


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


@dataclass
class LoadedPartSession:
    """Active 2.5D session bound to a real SheetMetal part and FoldState."""
    part_label: str
    unfold_data: object
    panel_build: object
    graph: PanelGraph
    panel_vertices_xy: dict
    fixed_panel: str
    fold_state: FoldState
    active_bend_index: int = 0

    @property
    def ordered_bends(self):
        return self.fold_state.ordered_bends

    @property
    def active_bend(self):
        return self.ordered_bends[self.active_bend_index]

    @property
    def active_bend_id(self) -> str:
        return self.active_bend.bend_id

    @property
    def target_angle_deg(self) -> float:
        return float(self.active_bend.angle_deg)

    @property
    def current_angle_deg(self) -> float:
        return float(self.fold_state.angles[self.active_bend_id])

    def select_bend_index(self, index: int) -> None:
        if not 0 <= index < len(self.ordered_bends):
            raise KinematicsError("Bend index out of range: {}".format(index))
        self.active_bend_index = int(index)

    def set_active_angle(self, signed_angle_deg: float) -> None:
        """Atomically update active bend angle via FoldState."""
        self.fold_state.set_angle(self.active_bend_id, float(signed_angle_deg))

    def fold_all_to_target(self) -> None:
        """Set all bends in the rooted kinematic tree to their target fold angles."""
        for rb in self.ordered_bends:
            self.fold_state.set_angle(rb.bend_id, rb.angle_deg)

    def reset_active_bend(self) -> None:
        """Reset the active bend to 0 deg."""
        self.fold_state.set_angle(self.active_bend_id, 0.0)

    def reset_all_bends(self) -> None:
        """Reset all bends to 0 deg (flat pattern)."""
        self.fold_state.reset_all()

    def cross_section(self, segments: int = 24) -> dict:
        return self.fold_state.cross_section_2d(
            self.active_bend_id, self.panel_vertices_xy, segments=segments
        )


def resolve_selected_unfold(doc, selection) -> str:
    """Resolve the SheetMetal unfold object name for the user's FreeCAD selection.

    Fails closed with a clear diagnostic message if no document is open, no
    object is selected, or no matching SheetMetal unfold object exists.
    Never modifies the FreeCAD document.
    """
    if doc is None:
        raise RuntimeError("Нет открытого документа FreeCAD. Откройте FCStd-модель листовой детали.")
    if not selection:
        raise RuntimeError(
            "Деталь не выбрана. Выделите листовую деталь (Body / Bend / Unfold) в дереве модели и нажмите «Деталь»."
        )

    unfolds = [
        o for o in getattr(doc, "Objects", ())
        if hasattr(o, "baseObject") and o.Name.endswith("_Unfold")
    ]
    if not unfolds:
        raise RuntimeError(
            "В документе не найден объект развёртки SheetMetal (*_Unfold). "
            "Создайте развёртку с эскизами в верстаке SheetMetal."
        )

    selected = selection[0]
    sel_name = getattr(selected, "Name", "")

    # 1. Direct selection of the unfold object itself
    for u in unfolds:
        if u.Name == sel_name:
            return u.Name

    # 2. Selection of the source object, parent Body, or associated unfold sketch
    matching = []
    for u in unfolds:
        try:
            linked, subs = u.baseObject
        except Exception:
            continue
        linked_name = getattr(linked, "Name", "")
        sub_prefix = str(subs[0]).split(".", 1)[0] if subs else ""
        body_group = {getattr(g, "Name", "") for g in getattr(selected, "Group", ())}
        if (
            sel_name in (linked_name, sub_prefix)
            or linked_name in body_group
            or sub_prefix in body_group
            or sel_name.endswith("_Unfold_Sketch")
            or sel_name.endswith("_Unfold_Sketch_Bends")
        ):
            matching.append(u.Name)

    if len(matching) == 1:
        return matching[0]
    if len(unfolds) == 1 and hasattr(selected, "Shape"):
        return unfolds[0].Name
    raise RuntimeError(
        "Не удалось однозначно сопоставить выбранный объект «{}» с развёрткой SheetMetal.".format(
            getattr(selected, "Label", sel_name)
        )
    )


def build_part_session(doc, selection, fixed_panel: str | None = None) -> LoadedPartSession:
    """Build a LoadedPartSession from the active FreeCAD document and selection."""
    from freecad_smbs.integration.panel_builder import build_panels
    from freecad_smbs.integration.sheetmetal_unfold_reader import read_unfold

    unfold_name = resolve_selected_unfold(doc, selection)
    unfold_data = read_unfold(unfold_name, doc)
    panel_build = build_panels(unfold_name=unfold_name, document=doc)
    graph = graph_from_panel_build(panel_build)

    panel_vertices_xy = {}
    for panel, face in zip(panel_build.panels, panel_build.panel_faces):
        verts = getattr(face.OuterWire, "OrderedVertexes", None) or face.Vertexes
        panel_vertices_xy[panel.panel_id] = tuple(
            (float(v.Point.x), float(v.Point.y)) for v in verts
        )

    if fixed_panel is None or fixed_panel not in graph.panels:
        rc = getattr(unfold_data, "root_centroid_xy", None)
        if rc is not None and panel_build.panels:
            from math import hypot
            fixed_panel = min(
                panel_build.panels,
                key=lambda p: hypot(p.centroid_xy[0] - rc[0], p.centroid_xy[1] - rc[1]),
            ).panel_id
        else:
            fixed_panel = panel_build.connections[0].panel_a

    fold_state = FoldState(graph, fixed_panel)
    for rb in fold_state.ordered_bends:
        fold_state.set_angle(rb.bend_id, rb.angle_deg)
    fold_state.set_angle(fold_state.ordered_bends[0].bend_id, 0.0)
    sel_label = getattr(selection[0], "Label", getattr(selection[0], "Name", unfold_name))
    return LoadedPartSession(
        part_label=str(sel_label),
        unfold_data=unfold_data,
        panel_build=panel_build,
        graph=graph,
        panel_vertices_xy=panel_vertices_xy,
        fixed_panel=fixed_panel,
        fold_state=fold_state,
        active_bend_index=0,
    )


class PressBrake2DCommand:
    def GetResources(self):
        return {
            "MenuText": "PressBrakeSim — 2D интерфейс",
            "ToolTip": "Открыть 2.5D симулятор гибки листовой детали SheetMetal",
        }

    def IsActive(self):
        return True

    def Activated(self):
        import FreeCADGui as Gui
        global _DOCK
        QtCore, QtGui, QtWidgets = _qt()
        main = Gui.getMainWindow()
        dock = main.findChild(QtWidgets.QDockWidget, "SMBS_PressBrake2D_Dock")
        if dock is None or dock.widget() is None:
            if dock is not None:
                dock.deleteLater()
            dock = QtWidgets.QDockWidget("PressBrakeSim — 2.5D Viewer", main)
            dock.setObjectName("SMBS_PressBrake2D_Dock")
            dock.setAllowedAreas(
                QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea |
                QtCore.Qt.BottomDockWidgetArea
            )
            dock.setMinimumWidth(380)
            try:
                panel_widget = _PreviewPanel()
                dock.setWidget(panel_widget)
                main.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
            except Exception:
                _error(traceback.format_exc())
                dock.deleteLater()
                raise
        _DOCK = dock
        dock.show()
        dock.raise_()
        # If the user already selected a part before clicking the command, load it automatically
        widget = dock.widget()
        try:
            sel = Gui.Selection.getSelection() if hasattr(Gui, "Selection") else []
            if sel and getattr(widget, "session", None) is None:
                widget.load_part()
        except Exception:
            pass
        _log("2.5D viewer opened")


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
                self.setMinimumHeight(240)
                self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
                self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

            def resizeEvent(self, event):
                super().resizeEvent(event)
                if self.scene() is not None and not self.scene().sceneRect().isEmpty():
                    self.fitInView(self.scene().sceneRect(), QtCore.Qt.KeepAspectRatio)

        class Panel(QtWidgets.QWidget):
            def __init__(self):
                super().__init__()
                self.session: LoadedPartSession | None = None
                self.angle = 0.0
                self.playing = False
                self.timer = QtCore.QTimer(self)
                self.timer.setInterval(35)
                self.timer.timeout.connect(self.advance)
                self.setMinimumWidth(360)

                root = QtWidgets.QVBoxLayout(self)
                root.setContentsMargins(8, 8, 8, 8)
                root.setSpacing(6)
                title = QtWidgets.QLabel("PressBrakeSim 2.5D  |  Реальная геометрия гиба")
                title.setStyleSheet("font-weight: bold; font-size: 13px")
                root.addWidget(title)

                self.status = QtWidgets.QLabel(
                    "Выделите листовую деталь в FreeCAD и нажмите «Деталь»."
                )
                self.status.setWordWrap(True)
                root.addWidget(self.status)

                row = QtWidgets.QHBoxLayout()
                self.load = _button(QtWidgets, "Деталь", self.load_part, True)
                self.tooling = _button(QtWidgets, "Оснастка", self.unavailable, False)
                self.calculate = _button(QtWidgets, "Расчёт", self.unavailable, False)
                self.tooling.setToolTip("Выбор оснастки отключён на этапе Phase 3A")
                self.calculate.setToolTip("Автоматический поиск последовательности отключён на этапе Phase 3A")
                for widget in (self.load, self.tooling, self.calculate):
                    row.addWidget(widget)
                root.addLayout(row)

                self.view = Canvas()
                self.scene = QtWidgets.QGraphicsScene(self.view)
                self.view.setScene(self.scene)
                root.addWidget(self.view, 1)

                row = QtWidgets.QHBoxLayout()
                self.reset_btn = _button(QtWidgets, "|<", self.reset, False)
                self.back_btn = _button(QtWidgets, "<", lambda: self.step_angle(-5.0), False)
                self.play_btn = _button(QtWidgets, "▶", self.toggle_play, False)
                self.next_btn = _button(QtWidgets, ">", lambda: self.step_angle(5.0), False)
                self.end_btn = _button(QtWidgets, ">|", self.to_target_angle, False)
                self.fit_btn = _button(QtWidgets, "Fit", self.fit, False)
                for widget in (self.reset_btn, self.back_btn, self.play_btn,
                               self.next_btn, self.end_btn, self.fit_btn):
                    row.addWidget(widget)
                root.addLayout(row)

                self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
                self.slider.setRange(0, 90 * _SLIDER_SCALE)
                self.slider.setEnabled(False)
                self.slider.valueChanged.connect(self._on_slider_changed)
                root.addWidget(self.slider)

                self.step = QtWidgets.QLabel("Активный гиб: — | Угол: — / —")
                self.step.setWordWrap(True)
                root.addWidget(self.step)

                bend_nav_row = QtWidgets.QHBoxLayout()
                bend_nav_row.addWidget(QtWidgets.QLabel("Гибы детали:"))
                self.prev_bend_btn = _button(QtWidgets, "◀ Пред. гиб", lambda: self.switch_bend(-1), False)
                self.next_bend_btn = _button(QtWidgets, "След. гиб ▶", lambda: self.switch_bend(1), False)
                bend_nav_row.addWidget(self.prev_bend_btn)
                bend_nav_row.addWidget(self.next_bend_btn)
                root.addLayout(bend_nav_row)

                self.operations = QtWidgets.QTreeWidget()
                self.operations.setHeaderLabels(["№", "Гиб / Панели", "Целевой угол / Параметры"])
                self.operations.setRootIsDecorated(False)
                self.operations.setMaximumHeight(110)
                self.operations.currentItemChanged.connect(self._on_operation_selected)
                root.addWidget(self.operations)

                self.check_btn = _button(QtWidgets, "Проверить интерфейс", self.diagnostics)
                root.addWidget(self.check_btn)
                self.check_result = QtWidgets.QLabel("Диагностика ещё не запускалась")
                self.check_result.setWordWrap(True)
                root.addWidget(self.check_result)

                self.draw()

            def unavailable(self):
                pass  # Disabled controls for Phase 5/6 features.

            def _set_playback_controls_enabled(self, enabled: bool):
                for w in (
                    self.reset_btn,
                    self.back_btn,
                    self.play_btn,
                    self.next_btn,
                    self.end_btn,
                    self.fit_btn,
                    self.slider,
                ):
                    w.setEnabled(enabled)
                has_multi = enabled and self.session is not None and len(self.session.ordered_bends) > 1
                self.prev_bend_btn.setEnabled(has_multi)
                self.next_bend_btn.setEnabled(has_multi)

            def load_part(self):
                """Load the currently selected SheetMetal part in FreeCAD."""
                self.timer.stop()
                self.playing = False
                self.play_btn.setText("▶")
                try:
                    import FreeCAD as App
                    import FreeCADGui as Gui
                    doc = App.ActiveDocument
                    selection = Gui.Selection.getSelection() if hasattr(Gui, "Selection") else []
                    self.session = build_part_session(doc, selection)
                except Exception as exc:
                    self.session = None
                    self._set_playback_controls_enabled(False)
                    self.operations.clear()
                    msg = "Ошибка загрузки детали: {}".format(exc)
                    self.status.setText(msg)
                    self.check_result.setText(msg)
                    _error(msg + "\n" + traceback.format_exc())
                    self.draw()
                    return

                self._populate_operations()
                self._configure_for_active_bend()
                self._set_playback_controls_enabled(True)
                b = self.session.active_bend
                z = b.bend_zone
                self.status.setText(
                    "Загружена деталь «{}»: панелей {}, гибов {} | t={:.2f} мм, R_вн={:.2f} мм, K={:.2f}, BA={:.3f} мм".format(
                        self.session.part_label,
                        len(self.session.graph.panels),
                        len(self.session.ordered_bends),
                        z.thickness_mm,
                        z.inner_radius_mm,
                        z.k_factor_ansi,
                        z.bend_allowance_mm,
                    )
                )
                _log(
                    "Loaded part '{}': {} panels, {} bends; active={} target={:+.1f} deg".format(
                        self.session.part_label,
                        len(self.session.graph.panels),
                        len(self.session.ordered_bends),
                        b.bend_id,
                        b.angle_deg,
                    )
                )
                self.draw()
                QtCore.QTimer.singleShot(0, self.fit)

            def _populate_operations(self):
                self.operations.blockSignals(True)
                self.operations.clear()
                if self.session is not None:
                    for idx, b in enumerate(self.session.ordered_bends, 1):
                        z = b.bend_zone
                        item = QtWidgets.QTreeWidgetItem([
                            str(idx),
                            "{} ({} → {})".format(b.bend_id, b.fixed_panel, b.moving_panel),
                            "0° → {:+.1f}° | R={:.2f}, t={:.2f}".format(
                                b.angle_deg, z.inner_radius_mm, z.thickness_mm
                            ),
                        ])
                        self.operations.addTopLevelItem(item)
                    if self.operations.topLevelItemCount() > 0:
                        self.operations.setCurrentItem(
                            self.operations.topLevelItem(self.session.active_bend_index)
                        )
                self.operations.blockSignals(False)

            def _configure_for_active_bend(self):
                if self.session is None:
                    return
                target_mag = abs(self.session.target_angle_deg)
                max_steps = max(1, int(round(target_mag * _SLIDER_SCALE)))
                self.angle = self.session.current_angle_deg
                slider_pos = int(round(abs(self.angle) * _SLIDER_SCALE))
                self.slider.blockSignals(True)
                self.slider.setRange(0, max_steps)
                self.slider.setValue(slider_pos)
                self.slider.blockSignals(False)

            def _on_operation_selected(self, current, _previous):
                if self.session is None or current is None:
                    return
                idx = self.operations.indexOfTopLevelItem(current)
                if idx < 0 or idx == self.session.active_bend_index:
                    return
                self.timer.stop()
                self.playing = False
                self.play_btn.setText("▶")
                # Keep previously selected bend at its target angle while resetting the newly selected bend to 0 deg
                self.session.fold_all_to_target()
                self.session.select_bend_index(idx)
                self.session.set_active_angle(0.0)
                self._configure_for_active_bend()
                self.draw()
                QtCore.QTimer.singleShot(0, self.fit)

            def switch_bend(self, delta: int):
                if self.session is None or not self.session.ordered_bends:
                    return
                count = len(self.session.ordered_bends)
                new_idx = (self.session.active_bend_index + delta) % count
                item = self.operations.topLevelItem(new_idx)
                if item is not None:
                    self.operations.setCurrentItem(item)

            def _on_slider_changed(self, slider_val: int):
                if self.session is None:
                    return
                target = self.session.target_angle_deg
                sign = 1.0 if target >= 0 else -1.0
                mag = min(abs(target), max(0.0, float(slider_val) / _SLIDER_SCALE))
                signed_angle = 0.0 if mag < 1e-9 else sign * mag
                self.set_angle(signed_angle)

            def set_angle(self, signed_angle_deg: float):
                """Set the active bend to a signed angle via FoldState."""
                if self.session is None:
                    return
                try:
                    self.session.set_active_angle(float(signed_angle_deg))
                except Exception as exc:
                    _error("Отклонено недопустимое изменение угла: {}".format(exc))
                    raise
                self.angle = self.session.current_angle_deg
                slider_pos = int(round(abs(self.angle) * _SLIDER_SCALE))
                if self.slider.value() != slider_pos:
                    self.slider.blockSignals(True)
                    self.slider.setValue(slider_pos)
                    self.slider.blockSignals(False)
                self.draw()

            def step_angle(self, delta_mag_deg: float):
                if self.session is None:
                    return
                target = self.session.target_angle_deg
                sign = 1.0 if target >= 0 else -1.0
                new_mag = min(abs(target), max(0.0, abs(self.angle) + float(delta_mag_deg)))
                self.set_angle(0.0 if new_mag < 1e-9 else sign * new_mag)

            def to_target_angle(self):
                if self.session is None:
                    return
                self.timer.stop()
                self.playing = False
                self.play_btn.setText("▶")
                self.set_angle(self.session.target_angle_deg)

            def reset(self):
                self.timer.stop()
                self.playing = False
                self.play_btn.setText("▶")
                if self.session is not None:
                    self.set_angle(0.0)

            def toggle_play(self):
                if self.session is None:
                    return
                if self.playing:
                    self.timer.stop()
                    self.playing = False
                    self.play_btn.setText("▶")
                    _log("Playback paused at {:+.1f} deg".format(self.angle))
                else:
                    if abs(self.angle) >= abs(self.session.target_angle_deg) - 1e-6:
                        self.set_angle(0.0)
                    self.playing = True
                    self.play_btn.setText("Ⅱ")
                    self.timer.start()
                    _log("Playback started at {:+.1f} deg".format(self.angle))

            def advance(self):
                if self.session is None:
                    self.timer.stop()
                    self.playing = False
                    return
                target_mag = abs(self.session.target_angle_deg)
                step = max(0.5, target_mag / 60.0)
                self.step_angle(step)
                if abs(self.angle) >= target_mag - 1e-6:
                    self.timer.stop()
                    self.playing = False
                    self.play_btn.setText("▶")
                    _log("Playback completed at {:+.1f} deg".format(self.angle))

            def fit(self):
                rect = self.scene.sceneRect()
                if not rect.isEmpty():
                    self.view.fitInView(rect, QtCore.Qt.KeepAspectRatio)

            def diagnostics(self):
                has_part = self.session is not None
                checks = {
                    "scene_attached": self.view.scene() is self.scene,
                    "part_loaded": has_part,
                    "scene_items": len(self.scene.items()) >= (6 if has_part else 1),
                    "scene_rect": not self.scene.sceneRect().isEmpty(),
                    "viewport_visible": self.view.viewport().width() > 0 and
                                        self.view.viewport().height() > 0,
                    "timer_consistency": self.timer.isActive() == self.playing,
                    "unavailable_controls_disabled": not any(
                        b.isEnabled() for b in (self.tooling, self.calculate)
                    ),
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
                    if self.session is None:
                        self.scene.setSceneRect(-120, -60, 240, 120)
                        text = self.scene.addSimpleText("Деталь не загружена — нажмите «Деталь»")
                        text.setBrush(QtGui.QBrush(QtGui.QColor("#6c7a89")))
                        rect = text.boundingRect()
                        text.setPos(-rect.width() / 2.0, -rect.height() / 2.0)
                        self.step.setText("Активный гиб: — | Угол: — / —")
                        return

                    sec = self.session.cross_section(segments=32)

                    # Screen mapping: x_screen = s, y_screen = -z so +Z (UP bend) points visually upward
                    def to_qpt(sz):
                        return QtCore.QPointF(float(sz[0]), -float(sz[1]))

                    def make_poly(pts):
                        return QtGui.QPolygonF([to_qpt(p) for p in pts])

                    all_pts = list(sec["inner_surface"]) + list(sec["outer_surface"])
                    for _, opoly in sec.get("other_panel_polygons", ()):
                        all_pts.extend(opoly)
                    for _, ozpoly in sec.get("other_zone_polygons", ()):
                        all_pts.extend(ozpoly)

                    # 1. Other panels & bend zones in the same cross-section plane
                    other_pen = QtGui.QPen(QtGui.QColor("#1b4f72"), 1.0)
                    other_pen.setCosmetic(True)
                    other_brush = QtGui.QBrush(QtGui.QColor("#85929e"))
                    for _, opoly in sec.get("other_panel_polygons", ()):
                        self.scene.addPolygon(make_poly(opoly), other_pen, other_brush)

                    zone_pen = QtGui.QPen(QtGui.QColor("#b9770e"), 1.2)
                    zone_pen.setCosmetic(True)
                    zone_brush = QtGui.QBrush(QtGui.QColor("#f8c471"))
                    for _, ozpoly in sec.get("other_zone_polygons", ()):
                        self.scene.addPolygon(make_poly(ozpoly), zone_pen, zone_brush)

                    # 2. Fixed rigid panel
                    panel_pen = QtGui.QPen(QtGui.QColor("#1b4f72"), 1.2)
                    panel_pen.setCosmetic(True)
                    fixed_brush = QtGui.QBrush(QtGui.QColor("#85929e"))
                    self.scene.addPolygon(make_poly(sec["fixed_polygon"]), panel_pen, fixed_brush)

                    # 3. Moving rigid panel (transformed by FoldState)
                    moving_brush = QtGui.QBrush(QtGui.QColor("#5dade2"))
                    self.scene.addPolygon(make_poly(sec["moving_polygon"]), panel_pen, moving_brush)

                    # 4. Finite-radius bend zone polygon
                    self.scene.addPolygon(make_poly(sec["zone_polygon"]), zone_pen, zone_brush)

                    # 5. Both sheet surfaces (inner concave and outer convex)
                    inner_pen = QtGui.QPen(QtGui.QColor("#0e6251"), 2.0)
                    inner_pen.setCosmetic(True)
                    for curve in (sec["inner_surface"],) + tuple(sec.get("other_inner_curves", ())):
                        if not curve:
                            continue
                        inner_path = QtGui.QPainterPath()
                        inner_pts = [to_qpt(p) for p in curve]
                        inner_path.moveTo(inner_pts[0])
                        for pt in inner_pts[1:]:
                            inner_path.lineTo(pt)
                        self.scene.addPath(inner_path, inner_pen)

                    outer_pen = QtGui.QPen(QtGui.QColor("#154360"), 2.0)
                    outer_pen.setCosmetic(True)
                    for curve in (sec["outer_surface"],) + tuple(sec.get("other_outer_curves", ())):
                        if not curve:
                            continue
                        outer_path = QtGui.QPainterPath()
                        outer_pts = [to_qpt(p) for p in curve]
                        outer_path.moveTo(outer_pts[0])
                        for pt in outer_pts[1:]:
                            outer_path.lineTo(pt)
                        self.scene.addPath(outer_path, outer_pen)

                    # 6. Neutral layer curve (dashed) inside bend zones
                    neutral_pen = QtGui.QPen(QtGui.QColor("#c0392b"), 1.4, QtCore.Qt.DashLine)
                    neutral_pen.setCosmetic(True)
                    for curve in (sec["neutral_curve"],) + tuple(sec.get("other_neutral_curves", ())):
                        if not curve:
                            continue
                        neutral_path = QtGui.QPainterPath()
                        n_pts = [to_qpt(p) for p in curve]
                        neutral_path.moveTo(n_pts[0])
                        for pt in n_pts[1:]:
                            neutral_path.lineTo(pt)
                        self.scene.addPath(neutral_path, neutral_pen)

                    # 7. Tangent boundary lines between rigid panels and bend zones
                    tan_pen = QtGui.QPen(QtGui.QColor("#7d3c98"), 1.2, QtCore.Qt.DotLine)
                    tan_pen.setCosmetic(True)
                    all_tan_pairs = list(zip(sec["tangent_points_top"], sec["tangent_points_bottom"]))
                    all_tan_pairs.extend(sec.get("other_tangent_lines", ()))
                    for t_top, t_bot in all_tan_pairs:
                        p1, p2 = to_qpt(t_top), to_qpt(t_bot)
                        self.scene.addLine(p1.x(), p1.y(), p2.x(), p2.y(), tan_pen)

                    # Compute bounding rect with comfortable margin
                    xs = [p[0] for p in all_pts]
                    ys = [-p[1] for p in all_pts]
                    min_x, max_x = min(xs), max(xs)
                    min_y, max_y = min(ys), max(ys)
                    span = max(max_x - min_x, max_y - min_y, 10.0)
                    pad = 0.15 * span
                    self.scene.setSceneRect(
                        min_x - pad,
                        min_y - pad,
                        (max_x - min_x) + 2 * pad,
                        (max_y - min_y) + 2 * pad,
                    )

                    idx = self.session.active_bend_index + 1
                    total = len(self.session.ordered_bends)
                    self.step.setText(
                        "Гиб {}/{} ({}: {} → {}) | Текущий угол: {:+.1f}° | Целевой: {:+.1f}° | R_вн={:.2f} мм, t={:.2f} мм".format(
                            idx,
                            total,
                            sec["bend_id"],
                            sec["fixed_panel"],
                            sec["moving_panel"],
                            sec["angle_deg"],
                            sec["target_angle_deg"],
                            sec["inner_radius_mm"],
                            sec["thickness_mm"],
                        )
                    )
                except Exception:
                    _error(traceback.format_exc())
                    raise

        return Panel()
