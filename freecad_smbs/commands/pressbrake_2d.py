"""PressBrakeSim 2.5D — Real SheetMetal Geometry Viewer (Phase 3A).

Uses the existing 2.5D pipeline without introducing a second geometry engine:
  sheetmetal_unfold_reader.read_unfold
  -> panel_builder.build_panels
  -> panel_graph.graph_from_panel_build
  -> panel_kinematics.FoldState (with explicit BendZone)
"""
from __future__ import annotations

import logging
import math
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from smbs_core.kinematics2d.fold_sequence import (
    BendSequenceController,
    PlaybackStatus,
    SequenceError,
)
from smbs_core.kinematics2d.panel_graph import PanelGraph, graph_from_panel_build
from smbs_core.kinematics2d.panel_kinematics import FoldState, KinematicsError
from smbs_core.kinematics2d.press_cycle import (
    ContactBoundaryKind,
    CycleConfig,
    CycleExecutionMode,
    CyclePhase,
    CycleSimulationMode,
    PressCycleController,
    PressCycleError,
    WorkpieceOrientationStatus,
)
from smbs_core.kinematics2d.tooling_25d import (
    DEFAULT_INITIAL_PUNCH_CLEARANCE_MM,
    NOT_SPECIFIED,
    SceneBounds2D,
    ToolingError,
    ToolingSetup2D,
    ToolKind,
    ToolLibrary25D,
    ToolProfile,
    ValidationStatus,
    compute_scene_bounds_2d,
    import_tool_from_dxf,
    import_tool_from_freecad_object,
    import_tool_from_step,
)

# Explicit QGraphicsScene Z-layer order so tooling never obscures the sheet
Z_LAYER_AUXILIARY = 0.0
Z_LAYER_DIE = 10.0
Z_LAYER_PUNCH = 15.0
Z_LAYER_SHEET_OTHER = 20.0
Z_LAYER_SHEET_PANELS = 30.0
Z_LAYER_SHEET_ZONE = 35.0
Z_LAYER_SHEET_SURFACES = 40.0
Z_LAYER_SHEET_NEUTRAL_AND_TANGENTS = 50.0

_DOCK = None
_SLIDER_SCALE = 10  # 0.1 degree slider resolution
_LOGGER: logging.Logger | None = None
_LOG_FILE_PATH = Path.home() / "Library" / "Application Support" / "FreeCAD" / "v1-1" / "pressbrake_2d.log"


def _get_file_logger() -> logging.Logger:
    """Return a non-blocking file logger decoupled from FreeCAD.Console and Qt NotificationArea."""
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER
    logger = logging.getLogger("smbs.pressbrake_2d")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        try:
            _LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(str(_LOG_FILE_PATH), encoding="utf-8")
            handler.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
            )
            logger.addHandler(handler)
        except OSError:
            logger.addHandler(logging.NullHandler())
    _LOGGER = logger
    return logger


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
    """Write diagnostic message to file log without invoking FreeCAD.Console / NotificationArea."""
    try:
        _get_file_logger().info("%s", message)
    except Exception:
        pass


def _warn(message):
    """Write warning to file log without invoking FreeCAD.Console.PrintWarning (avoids NotificationArea deadlock)."""
    try:
        _get_file_logger().warning("%s", message)
    except Exception:
        pass


def _error(message):
    """Write unexpected error to file log without triggering FreeCAD NotificationArea popup deadlock."""
    try:
        _get_file_logger().error("%s", message)
    except Exception:
        pass


class SelectionWarning(RuntimeError):
    """Expected user-selection or document precondition warning (no traceback)."""


def validate_selection_preconditions(doc, selection) -> list:
    """Validate that an active FreeCAD document and a non-empty selection exist.

    Raises SelectionWarning if preconditions are not met before any geometry
    pipeline, unfold reading, or scene update is invoked.
    """
    sel_list = list(selection) if selection else []
    if doc is None and sel_list:
        doc = getattr(sel_list[0], "Document", None)
    if doc is None:
        raise SelectionWarning(
            "Нет открытого документа FreeCAD. Откройте FCStd-модель листовой детали."
        )
    if not sel_list:
        raise SelectionWarning(
            "Деталь не выбрана. Выделите листовую деталь (Body / Bend / Unfold) в дереве модели и нажмите «Деталь»."
        )
    return sel_list


def execute_load_part(
    current_session: LoadedPartSession | None,
    doc,
    selection,
    build_fn=None,
) -> tuple[str, LoadedPartSession | None, str]:
    """Execute part loading with early precondition checks and session preservation.

    Returns (outcome, session_after, message) where outcome is one of:
      - "warning": expected precondition issue (no doc / empty selection);
        current_session is preserved untouched and build_fn is NOT called.
      - "loaded": new LoadedPartSession built and returned.
      - "error": unexpected error during build; current_session is preserved.
    """
    try:
        sel_list = validate_selection_preconditions(doc, selection)
    except SelectionWarning as warn_exc:
        return "warning", current_session, str(warn_exc)

    if doc is None and sel_list:
        doc = getattr(sel_list[0], "Document", None)

    _log("load_part: enter")
    builder = build_part_session if build_fn is None else build_fn
    try:
        new_session = builder(doc, sel_list)
    except SelectionWarning as warn_exc:
        msg = str(warn_exc)
        _log("load_part: exit (selection warning, session preserved)")
        return "warning", current_session, msg
    except Exception as exc:
        msg = "Ошибка загрузки детали: {}".format(exc)
        _error(msg + "\n" + traceback.format_exc())
        _log("load_part: exit (error, session preserved)")
        return "error", current_session, msg

    b = new_session.active_bend
    z = b.bend_zone
    msg = (
        "Загружена деталь «{}»: панелей {}, гибов {} | t={:.2f} мм, R_вн={:.2f} мм, K={:.2f}, BA={:.3f} мм".format(
            new_session.part_label,
            len(new_session.graph.panels),
            len(new_session.ordered_bends),
            z.thickness_mm,
            z.inner_radius_mm,
            z.k_factor_ansi,
            z.bend_allowance_mm,
        )
    )
    _log(
        "Loaded part '{}': {} panels, {} bends; active={} target={:+.1f} deg".format(
            new_session.part_label,
            len(new_session.graph.panels),
            len(new_session.ordered_bends),
            b.bend_id,
            b.angle_deg,
        )
    )
    _log("load_part: exit (loaded)")
    return "loaded", new_session, msg


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
    controller: BendSequenceController = field(init=False)

    def __post_init__(self) -> None:
        self.controller = BendSequenceController(self.fold_state)
        if self.active_bend_index != 0:
            self.controller.select_operation(self.active_bend_index)

    @property
    def ordered_bends(self):
        return tuple(self.controller.bend_for(bid) for bid in self.controller.order)

    @property
    def bend_order(self) -> tuple[str, ...]:
        return self.controller.order

    @property
    def canonical_order(self) -> tuple[str, ...]:
        return self.controller.canonical_order

    def can_reorder(self) -> bool:
        return self.controller.can_reorder()

    def set_bend_order(self, order) -> None:
        self.controller.set_bend_order(order)
        self.active_bend_index = self.controller.active_index

    def move_up(self, index: int | None = None) -> int:
        new_idx = self.controller.move_up(index)
        self.active_bend_index = self.controller.active_index
        return new_idx

    def move_down(self, index: int | None = None) -> int:
        new_idx = self.controller.move_down(index)
        self.active_bend_index = self.controller.active_index
        return new_idx

    def restore_canonical_order(self) -> None:
        self.controller.restore_canonical_order()
        self.active_bend_index = self.controller.active_index

    @property
    def active_bend(self):
        return self.controller.active_bend

    @property
    def active_bend_id(self) -> str:
        return self.controller.active_bend_id

    @property
    def target_angle_deg(self) -> float:
        return self.controller.target_angle_deg

    @property
    def current_angle_deg(self) -> float:
        return self.controller.current_angle_deg

    @property
    def playback_status(self) -> PlaybackStatus:
        return self.controller.status

    def select_bend_index(self, index: int) -> None:
        if not 0 <= index < len(self.ordered_bends):
            raise KinematicsError("Bend index out of range: {}".format(index))
        self.controller.select_operation(int(index))
        self.active_bend_index = self.controller.active_index

    def set_active_angle(self, signed_angle_deg: float) -> None:
        """Atomically update active bend angle via controller & FoldState."""
        self.controller.set_manual_angle(float(signed_angle_deg))
        self.active_bend_index = self.controller.active_index

    def fold_all_to_target(self) -> None:
        """Set all bends in the rooted kinematic tree to their target fold angles."""
        for rb in self.ordered_bends:
            self.fold_state.set_angle(rb.bend_id, rb.angle_deg)
        self.controller._sync_status_when_stopped()
        self.active_bend_index = self.controller.active_index

    def reset_active_bend(self) -> None:
        """Reset the active bend to 0 deg."""
        self.controller.set_manual_angle(0.0)
        self.active_bend_index = self.controller.active_index

    def reset_all_bends(self) -> None:
        """Reset all bends to 0 deg (flat pattern) and return controller to IDLE."""
        self.controller.reset()
        self.active_bend_index = self.controller.active_index

    def play_all(self) -> None:
        self.controller.play_all()
        self.active_bend_index = self.controller.active_index

    def pause(self) -> None:
        self.controller.pause()
        self.active_bend_index = self.controller.active_index

    def resume(self) -> None:
        self.controller.resume()
        self.active_bend_index = self.controller.active_index

    def advance_time(self, dt_sec: float) -> bool:
        changed = self.controller.advance_time(dt_sec)
        self.active_bend_index = self.controller.active_index
        return changed

    def next_step(self) -> None:
        self.controller.next_step()
        self.active_bend_index = self.controller.active_index

    def previous_step(self) -> None:
        self.controller.previous_step()
        self.active_bend_index = self.controller.active_index

    def status_line(self) -> str:
        return self.controller.status_line()

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
    sel_list = validate_selection_preconditions(doc, selection)

    unfolds = [
        o for o in getattr(doc, "Objects", ())
        if hasattr(o, "baseObject") and o.Name.endswith("_Unfold")
    ]
    if not unfolds:
        raise SelectionWarning(
            "В документе не найден объект развёртки SheetMetal (*_Unfold). "
            "Создайте развёртку с эскизами в верстаке SheetMetal."
        )

    selected = sel_list[0]
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
        except (AttributeError, TypeError, ValueError):
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
    raise SelectionWarning(
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
        sel = Gui.Selection.getSelection() if hasattr(Gui, "Selection") else []
        if sel and getattr(widget, "session", None) is None:
            widget.load_part()
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
            def __init__(self, library_root: Path | str | None = None):
                super().__init__()
                self.session: LoadedPartSession | None = None
                self.tool_library = ToolLibrary25D(library_root=library_root)
                self.tooling_setup = ToolingSetup2D()
                self.cycle_config = CycleConfig()
                self.execution_mode = CycleExecutionMode.BEND_ONLY
                self.press_cycle: PressCycleController | None = None
                self.last_scene_bounds: SceneBounds2D | None = None
                self.angle = 0.0
                self.playing = False
                self._last_tick_monotonic: float | None = None
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
                self.tooling = _button(QtWidgets, "Оснастка", self.open_tool_library_dialog, True)
                self.calculate = _button(QtWidgets, "Расчёт", self.unavailable, False)
                self.tooling.setToolTip("Открыть библиотеку пользовательской оснастки (PUNCH / DIE)")
                self.calculate.setToolTip("Автоматический поиск последовательности отключён на этапе Phase 3A")
                for widget in (self.load, self.tooling, self.calculate):
                    row.addWidget(widget)
                root.addLayout(row)

                # Phase 5A: Tooling section (Import / Select / Align / Reset / Hide-Show / Library)
                self.tooling_group = QtWidgets.QGroupBox("Tooling (PUNCH & DIE)")
                t_layout = QtWidgets.QVBoxLayout(self.tooling_group)
                t_layout.setContentsMargins(6, 6, 6, 6)
                t_layout.setSpacing(4)

                t_row1 = QtWidgets.QHBoxLayout()
                self.import_punch_btn = _button(QtWidgets, "Import Punch", self.import_punch_dialog, True)
                self.import_die_btn = _button(QtWidgets, "Import Die", self.import_die_dialog, True)
                self.tool_library_btn = _button(QtWidgets, "Tool Library", self.open_tool_library_dialog, True)
                for w in (self.import_punch_btn, self.import_die_btn, self.tool_library_btn):
                    t_row1.addWidget(w)
                t_layout.addLayout(t_row1)

                t_row2 = QtWidgets.QHBoxLayout()
                t_row2.addWidget(QtWidgets.QLabel("Punch:"))
                self.select_punch_combo = QtWidgets.QComboBox()
                self.select_punch_combo.currentIndexChanged.connect(self._on_punch_combo_changed)
                t_row2.addWidget(self.select_punch_combo, 1)
                self.select_punch_btn = _button(QtWidgets, "Select Punch", self.select_punch_from_combo, True)
                t_row2.addWidget(self.select_punch_btn)
                t_layout.addLayout(t_row2)

                t_row3 = QtWidgets.QHBoxLayout()
                t_row3.addWidget(QtWidgets.QLabel("Die:"))
                self.select_die_combo = QtWidgets.QComboBox()
                self.select_die_combo.currentIndexChanged.connect(self._on_die_combo_changed)
                t_row3.addWidget(self.select_die_combo, 1)
                self.select_die_btn = _button(QtWidgets, "Select Die", self.select_die_from_combo, True)
                t_row3.addWidget(self.select_die_btn)
                t_layout.addLayout(t_row3)

                t_row4 = QtWidgets.QHBoxLayout()
                self.align_tooling_btn = _button(QtWidgets, "Align Tooling", self.align_tooling, True)
                self.reset_tooling_btn = _button(QtWidgets, "Reset Tooling Position", self.reset_tooling_position, True)
                self.toggle_tooling_btn = _button(QtWidgets, "Hide/Show Tooling", self.toggle_tooling_visibility, True)
                for w in (self.align_tooling_btn, self.reset_tooling_btn, self.toggle_tooling_btn):
                    t_row4.addWidget(w)
                t_layout.addLayout(t_row4)

                self.tooling_status = QtWidgets.QLabel(self.tooling_setup.status_line())
                self.tooling_status.setWordWrap(True)
                self.tooling_status.setStyleSheet("font-size: 11px; color: #2c3e50;")
                t_layout.addWidget(self.tooling_status)
                root.addWidget(self.tooling_group)

                # Phase 5B: Press Cycle section (Bend Only / Full Cycle, Ram Position, Cycle Phase, Speed & Config)
                self.press_cycle_group = QtWidgets.QGroupBox("Press Cycle (Phase 5B — KINEMATIC_PREVIEW)")
                pc_layout = QtWidgets.QVBoxLayout(self.press_cycle_group)
                pc_layout.setContentsMargins(6, 6, 6, 6)
                pc_layout.setSpacing(4)

                pc_row1 = QtWidgets.QHBoxLayout()
                pc_row1.addWidget(QtWidgets.QLabel("Mode:"))
                self.cycle_mode_combo = QtWidgets.QComboBox()
                self.cycle_mode_combo.addItem("Bend Only", CycleExecutionMode.BEND_ONLY.value)
                self.cycle_mode_combo.addItem("Full Cycle", CycleExecutionMode.FULL_CYCLE.value)
                self.cycle_mode_combo.currentIndexChanged.connect(self._on_cycle_mode_combo_changed)
                pc_row1.addWidget(self.cycle_mode_combo, 1)

                pc_row1.addWidget(QtWidgets.QLabel("Speed (°/s):"))
                self.cycle_speed_spin = QtWidgets.QDoubleSpinBox()
                self.cycle_speed_spin.setRange(1.0, 720.0)
                self.cycle_speed_spin.setValue(self.cycle_config.bend_rate_deg_s)
                self.cycle_speed_spin.setSingleStep(15.0)
                self.cycle_speed_spin.valueChanged.connect(self._on_cycle_speed_changed)
                pc_row1.addWidget(self.cycle_speed_spin)

                self.cycle_config_btn = _button(QtWidgets, "Cycle Config...", self.open_cycle_config_dialog, True)
                pc_row1.addWidget(self.cycle_config_btn)
                pc_layout.addLayout(pc_row1)

                pc_row2 = QtWidgets.QHBoxLayout()
                self.cycle_phase_label = QtWidgets.QLabel("Cycle Phase: IDLE (KINEMATIC_PREVIEW)")
                self.ram_position_label = QtWidgets.QLabel(
                    "Ram Position: {:+.2f} mm (Stroke: 0.00 mm)".format(self.tooling_setup.ram_z_mm)
                )
                pc_row2.addWidget(self.cycle_phase_label, 1)
                pc_row2.addWidget(self.ram_position_label, 1)
                pc_layout.addLayout(pc_row2)
                root.addWidget(self.press_cycle_group)

                self._refresh_tooling_combos()

                self.view = Canvas()
                self.scene = QtWidgets.QGraphicsScene(self.view)
                self.view.setScene(self.scene)
                root.addWidget(self.view, 1)

                row = QtWidgets.QHBoxLayout()
                self.reset_btn = _button(QtWidgets, "|<", self.reset, False)
                self.back_btn = _button(QtWidgets, "<", lambda: self.step_angle(-5.0), False)
                self.play_btn = _button(QtWidgets, "▶ Play All", self.play_all, False)
                self.pause_btn = _button(QtWidgets, "Ⅱ Pause", self.toggle_pause_resume, False)
                self.next_btn = _button(QtWidgets, ">", lambda: self.step_angle(5.0), False)
                self.end_btn = _button(QtWidgets, ">|", self.to_target_angle, False)
                self.fit_btn = _button(QtWidgets, "Fit", self.fit, True)
                for widget in (
                    self.reset_btn,
                    self.back_btn,
                    self.play_btn,
                    self.pause_btn,
                    self.next_btn,
                    self.end_btn,
                    self.fit_btn,
                ):
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
                bend_nav_row.addWidget(QtWidgets.QLabel("Шаги:"))
                self.prev_step_btn = _button(QtWidgets, "⏮ Previous Step", self.previous_step, False)
                self.next_step_btn = _button(QtWidgets, "Next Step ⏭", self.next_step, False)
                self.prev_bend_btn = _button(QtWidgets, "◀", lambda: self.switch_bend(-1), False)
                self.next_bend_btn = _button(QtWidgets, "▶", lambda: self.switch_bend(1), False)
                for widget in (
                    self.prev_step_btn,
                    self.next_step_btn,
                    self.prev_bend_btn,
                    self.next_bend_btn,
                ):
                    bend_nav_row.addWidget(widget)
                root.addLayout(bend_nav_row)

                order_row = QtWidgets.QHBoxLayout()
                order_row.addWidget(QtWidgets.QLabel("Порядок:"))
                self.move_up_btn = _button(QtWidgets, "↑ Move Up", self.move_selected_up, False)
                self.move_down_btn = _button(QtWidgets, "↓ Move Down", self.move_selected_down, False)
                self.restore_order_btn = _button(QtWidgets, "↺ Restore Order", self.restore_order, False)
                for widget in (self.move_up_btn, self.move_down_btn, self.restore_order_btn):
                    order_row.addWidget(widget)
                root.addLayout(order_row)

                self.operations = QtWidgets.QTreeWidget()
                self.operations.setHeaderLabels(["Step", "Bend", "Target", "Current", "Status"])
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
                pass  # Disabled controls for Phase 5B/6 features.

            def _refresh_tooling_combos(self):
                try:
                    punches = self.tool_library.list_tools(ToolKind.PUNCH)
                    dies = self.tool_library.list_tools(ToolKind.DIE)
                except Exception as exc:
                    _warn(f"Failed to scan tool library: {exc}")
                    punches, dies = (), ()

                cur_p = self.tooling_setup.punch.tool_id if self.tooling_setup.punch else None
                cur_d = self.tooling_setup.die.tool_id if self.tooling_setup.die else None

                self.select_punch_combo.blockSignals(True)
                self.select_punch_combo.clear()
                self.select_punch_combo.addItem("— Не выбран —", None)
                for idx, p in enumerate(punches, 1):
                    self.select_punch_combo.addItem(f"{p.name} ({p.validation_status.value})", p.tool_id)
                    if cur_p == p.tool_id:
                        self.select_punch_combo.setCurrentIndex(idx)
                self.select_punch_combo.blockSignals(False)

                self.select_die_combo.blockSignals(True)
                self.select_die_combo.clear()
                self.select_die_combo.addItem("— Не выбрана —", None)
                for idx, d in enumerate(dies, 1):
                    self.select_die_combo.addItem(f"{d.name} ({d.validation_status.value})", d.tool_id)
                    if cur_d == d.tool_id:
                        self.select_die_combo.setCurrentIndex(idx)
                self.select_die_combo.blockSignals(False)

            def _update_tooling_status_label(self):
                self.tooling_status.setText(self.tooling_setup.status_line())
                self._update_press_cycle_labels()

            def _update_press_cycle_labels(self):
                if self.press_cycle is not None:
                    self.cycle_phase_label.setText(
                        "Cycle Phase: {} ({} | {})".format(
                            self.press_cycle.phase.value,
                            self.press_cycle.simulation_mode.value,
                            self.press_cycle.contact_kind.value,
                        )
                    )
                    self.ram_position_label.setText(
                        "Ram Position: {:+.2f} mm (Stroke: {:.2f} mm)".format(
                            self.press_cycle.ram_z_mm,
                            self.press_cycle.ram_stroke_mm,
                        )
                    )
                else:
                    self.cycle_phase_label.setText(
                        "Cycle Phase: IDLE ({})".format(self.cycle_config.simulation_mode.value)
                    )
                    self.ram_position_label.setText(
                        "Ram Position: {:+.2f} mm (Stroke: {:.2f} mm)".format(
                            self.tooling_setup.ram_z_mm,
                            self.tooling_setup.ram_stroke_mm,
                        )
                    )

            def _ensure_press_cycle_controller(self) -> PressCycleController | None:
                if self.session is None:
                    self.press_cycle = None
                    return None
                if (
                    self.press_cycle is None
                    or self.press_cycle.sequence is not self.session.controller
                ):
                    self.press_cycle = PressCycleController(
                        sequence_controller=self.session.controller,
                        tooling_setup=self.tooling_setup,
                        config=self.cycle_config,
                        cross_section_provider=lambda: (
                            self.session.cross_section(segments=16) if self.session is not None else None
                        ),
                    )
                    self.press_cycle.set_execution_mode(self.execution_mode)
                return self.press_cycle

            def set_cycle_mode(self, mode: CycleExecutionMode | str) -> None:
                resolved = CycleExecutionMode(mode) if isinstance(mode, str) else mode
                if self.timer.isActive() or self.playing:
                    self._stop_timer()
                self.execution_mode = resolved
                if self.press_cycle is not None:
                    self.press_cycle.set_execution_mode(resolved)
                idx = 1 if resolved == CycleExecutionMode.FULL_CYCLE else 0
                if self.cycle_mode_combo.currentIndex() != idx:
                    self.cycle_mode_combo.blockSignals(True)
                    self.cycle_mode_combo.setCurrentIndex(idx)
                    self.cycle_mode_combo.blockSignals(False)
                self._update_press_cycle_labels()
                self.draw()

            def _on_cycle_mode_combo_changed(self, _index: int):
                data = self.cycle_mode_combo.currentData()
                if data in (CycleExecutionMode.BEND_ONLY.value, CycleExecutionMode.FULL_CYCLE.value):
                    self.set_cycle_mode(CycleExecutionMode(data))

            def set_cycle_config(self, config: CycleConfig) -> None:
                self.cycle_config = config
                if self.session is not None:
                    self.session.controller.speed_deg_per_sec = float(config.bend_rate_deg_s)
                if self.press_cycle is not None:
                    self.press_cycle.set_config(config)
                self._update_press_cycle_labels()
                self.draw()

            def _on_cycle_speed_changed(self, val: float):
                try:
                    fval = float(val)
                except (TypeError, ValueError):
                    return
                if not math.isfinite(fval) or fval <= 0.0:
                    return
                new_cfg = CycleConfig(
                    approach_speed_mm_s=self.cycle_config.approach_speed_mm_s,
                    retract_speed_mm_s=self.cycle_config.retract_speed_mm_s,
                    bend_rate_deg_s=fval,
                    bend_duration_s=self.cycle_config.bend_duration_s,
                    initial_clearance_mm=self.cycle_config.initial_clearance_mm,
                    preview_stroke_mm=self.cycle_config.preview_stroke_mm,
                    bottom_dwell_s=self.cycle_config.bottom_dwell_s,
                )
                self.set_cycle_config(new_cfg)

            def open_cycle_config_dialog(self):
                dlg = QtWidgets.QDialog(self)
                dlg.setWindowTitle("Параметры кинематического цикла пресса (KINEMATIC_PREVIEW)")
                vbox = QtWidgets.QVBoxLayout(dlg)

                note = QtWidgets.QLabel(
                    "Режим KINEMATIC_PREVIEW: параметры задают кинематику перемещения пуансона "
                    "и скорость изменения угла без расчёта контактной механики, усилия и упругого возврата."
                )
                note.setWordWrap(True)
                vbox.addWidget(note)

                form = QtWidgets.QFormLayout()
                sp_app = QtWidgets.QDoubleSpinBox()
                sp_app.setRange(0.5, 500.0)
                sp_app.setValue(self.cycle_config.approach_speed_mm_s)
                form.addRow("Approach speed (mm/s):", sp_app)

                sp_ret = QtWidgets.QDoubleSpinBox()
                sp_ret.setRange(0.5, 500.0)
                sp_ret.setValue(self.cycle_config.retract_speed_mm_s)
                form.addRow("Retract speed (mm/s):", sp_ret)

                sp_rate = QtWidgets.QDoubleSpinBox()
                sp_rate.setRange(1.0, 720.0)
                sp_rate.setValue(self.cycle_config.bend_rate_deg_s)
                form.addRow("Bend rate (deg/s):", sp_rate)

                sp_clr = QtWidgets.QDoubleSpinBox()
                sp_clr.setRange(0.5, 200.0)
                sp_clr.setValue(self.cycle_config.initial_clearance_mm)
                form.addRow("Initial clearance (mm):", sp_clr)

                sp_strk = QtWidgets.QDoubleSpinBox()
                sp_strk.setRange(0.5, 150.0)
                sp_strk.setValue(self.cycle_config.preview_stroke_mm)
                form.addRow("Preview stroke (mm):", sp_strk)

                sp_dwell = QtWidgets.QDoubleSpinBox()
                sp_dwell.setRange(0.0, 30.0)
                sp_dwell.setSingleStep(0.05)
                sp_dwell.setValue(self.cycle_config.bottom_dwell_s)
                form.addRow("Bottom dwell (s):", sp_dwell)
                vbox.addLayout(form)

                btn_row = QtWidgets.QHBoxLayout()
                btn_ok = QtWidgets.QPushButton("Применить")
                btn_cancel = QtWidgets.QPushButton("Отмена")
                btn_row.addWidget(btn_ok)
                btn_row.addWidget(btn_cancel)
                vbox.addLayout(btn_row)

                def on_apply():
                    try:
                        cfg = CycleConfig(
                            approach_speed_mm_s=float(sp_app.value()),
                            retract_speed_mm_s=float(sp_ret.value()),
                            bend_rate_deg_s=float(sp_rate.value()),
                            initial_clearance_mm=float(sp_clr.value()),
                            preview_stroke_mm=float(sp_strk.value()),
                            bottom_dwell_s=float(sp_dwell.value()),
                        )
                        self.cycle_speed_spin.blockSignals(True)
                        self.cycle_speed_spin.setValue(cfg.bend_rate_deg_s)
                        self.cycle_speed_spin.blockSignals(False)
                        self.set_cycle_config(cfg)
                        dlg.accept()
                    except PressCycleError as exc:
                        self.status.setText(str(exc))

                btn_ok.clicked.connect(on_apply)
                btn_cancel.clicked.connect(dlg.reject)
                dlg.exec()

            def _sync_tooling_to_active_bend(self):
                """Re-align static tooling to the active bend line if already aligned."""
                if self.session is None:
                    return
                if self.press_cycle is not None:
                    self.press_cycle.on_tooling_changed()
                elif self.tooling_setup.punch is not None or self.tooling_setup.die is not None:
                    try:
                        self.tooling_setup.align_to_cross_section(
                            self.session.cross_section(segments=16),
                            clearance_mm=self.cycle_config.initial_clearance_mm,
                            bend_id=self.session.active_bend_id,
                        )
                    except ToolingError as exc:
                        _warn(f"Tooling alignment warning for bend {self.session.active_bend_id}: {exc}")
                self._update_tooling_status_label()

            def select_punch(self, punch: ToolProfile | None):
                try:
                    self.tooling_setup.select_punch(punch)
                except ToolingError as exc:
                    self.status.setText(str(exc))
                    return
                if self.session is not None and (
                    self.tooling_setup.punch is not None or self.tooling_setup.die is not None
                ):
                    try:
                        self.tooling_setup.align_to_cross_section(
                            self.session.cross_section(segments=16),
                            clearance_mm=self.cycle_config.initial_clearance_mm,
                            bend_id=self.session.active_bend_id,
                        )
                    except ToolingError as exc:
                        self.status.setText(str(exc))
                if self.press_cycle is not None:
                    self.press_cycle.on_tooling_changed()
                self._refresh_tooling_combos()
                self._update_tooling_status_label()
                self.draw()
                QtCore.QTimer.singleShot(0, self.fit)

            def select_die(self, die: ToolProfile | None):
                try:
                    self.tooling_setup.select_die(die)
                except ToolingError as exc:
                    self.status.setText(str(exc))
                    return
                if self.session is not None and (
                    self.tooling_setup.punch is not None or self.tooling_setup.die is not None
                ):
                    try:
                        self.tooling_setup.align_to_cross_section(
                            self.session.cross_section(segments=16),
                            clearance_mm=self.cycle_config.initial_clearance_mm,
                            bend_id=self.session.active_bend_id,
                        )
                    except ToolingError as exc:
                        self.status.setText(str(exc))
                if self.press_cycle is not None:
                    self.press_cycle.on_tooling_changed()
                self._refresh_tooling_combos()
                self._update_tooling_status_label()
                self.draw()
                QtCore.QTimer.singleShot(0, self.fit)

            def _on_punch_combo_changed(self, _index: int):
                self.select_punch_from_combo()

            def _on_die_combo_changed(self, _index: int):
                self.select_die_from_combo()

            def select_punch_from_combo(self):
                tid = self.select_punch_combo.currentData()
                if not tid:
                    self.select_punch(None)
                    return
                try:
                    prof = self.tool_library.load_tool(str(tid), ToolKind.PUNCH)
                    self.select_punch(prof)
                except ToolingError as exc:
                    self.status.setText(str(exc))

            def select_die_from_combo(self):
                tid = self.select_die_combo.currentData()
                if not tid:
                    self.select_die(None)
                    return
                try:
                    prof = self.tool_library.load_tool(str(tid), ToolKind.DIE)
                    self.select_die(prof)
                except ToolingError as exc:
                    self.status.setText(str(exc))

            def align_tooling(self):
                try:
                    sec = self.session.cross_section(segments=16) if self.session is not None else None
                    bend_id = self.session.active_bend_id if self.session is not None else None
                    self.tooling_setup.align_to_cross_section(
                        sec,
                        clearance_mm=self.cycle_config.initial_clearance_mm,
                        bend_id=bend_id,
                    )
                except ToolingError as exc:
                    self.status.setText(str(exc))
                    return
                if self.press_cycle is not None:
                    self.press_cycle.on_tooling_changed()
                self._update_tooling_status_label()
                self.status.setText(
                    "Оснастка выровнена по линии гиба {} (зазор пуансона {:.1f} мм)".format(
                        self.tooling_setup.aligned_bend_id or "0.0",
                        self.tooling_setup.punch_clearance_mm,
                    )
                )
                self.draw()
                QtCore.QTimer.singleShot(0, self.fit)

            def reset_tooling_position(self):
                self.tooling_setup.reset_position()
                self._update_tooling_status_label()
                self.draw()
                QtCore.QTimer.singleShot(0, self.fit)

            def toggle_tooling_visibility(self):
                self.tooling_setup.visible = not self.tooling_setup.visible
                self._update_tooling_status_label()
                self.draw()
                QtCore.QTimer.singleShot(0, self.fit)

            def import_tool_file(
                self,
                file_path: str | Path,
                kind: ToolKind,
                name: str | None = None,
                save_to_library: bool = True,
                select_after_import: bool = True,
            ) -> ToolProfile:
                path = Path(file_path)
                ext = path.suffix.lower()
                if ext == ".dxf":
                    prof = import_tool_from_dxf(path, kind=kind, name=name)
                elif ext in (".step", ".stp"):
                    prof = import_tool_from_step(path, kind=kind, name=name)
                elif ext == ".fcstd":
                    prof = self.tool_library._load_legacy_fcstd_tool(path, kind=kind)
                    if name:
                        prof.name = name
                else:
                    raise ToolingError(f"Unsupported tool file extension: {ext}")

                if save_to_library:
                    self.tool_library.save_tool(prof, overwrite=True)
                self._refresh_tooling_combos()
                if select_after_import:
                    if kind == ToolKind.PUNCH:
                        self.select_punch(prof)
                    else:
                        self.select_die(prof)
                return prof

            def import_punch_dialog(self):
                self._run_import_dialog(ToolKind.PUNCH)

            def import_die_dialog(self):
                self._run_import_dialog(ToolKind.DIE)

            def _run_import_dialog(self, kind: ToolKind):
                label = "пуансона (PUNCH)" if kind == ToolKind.PUNCH else "матрицы (DIE)"
                file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
                    self,
                    f"Импорт профиля {label}",
                    str(self.tool_library.library_root),
                    "Tool Files (*.dxf *.DXF *.step *.STEP *.stp *.STP *.FCStd);;All Files (*)",
                )
                if not file_path:
                    return
                default_name = Path(file_path).stem
                name, ok = QtWidgets.QInputDialog.getText(
                    self,
                    f"Имя {label}",
                    "Введите имя инструмента для библиотеки:",
                    text=default_name,
                )
                if not ok:
                    return
                try:
                    prof = self.import_tool_file(
                        file_path,
                        kind=kind,
                        name=name.strip() or default_name,
                        save_to_library=True,
                        select_after_import=True,
                    )
                    self.status.setText(
                        f"Инструмент «{prof.name}» ({prof.kind.value}) импортирован и сохранён ({prof.validation_status.value})."
                    )
                except Exception as exc:
                    msg = f"Ошибка импорта инструмента: {exc}"
                    _warn(msg)
                    self.status.setText(msg)

            def open_tool_library_dialog(self):
                dlg = QtWidgets.QDialog(self)
                dlg.setWindowTitle("Библиотека пользовательской оснастки 2.5D (PUNCH & DIE)")
                dlg.resize(560, 420)
                vbox = QtWidgets.QVBoxLayout(dlg)

                list_w = QtWidgets.QTreeWidget()
                list_w.setHeaderLabels([
                    "ID", "Name", "Kind", "Format", "Status", "Key Params"
                ])
                list_w.setRootIsDecorated(False)
                vbox.addWidget(list_w, 1)

                details = QtWidgets.QLabel("Выберите инструмент в списке для просмотра параметров.")
                details.setWordWrap(True)
                vbox.addWidget(details)

                def populate():
                    list_w.clear()
                    for t in self.tool_library.list_tools():
                        if t.kind == ToolKind.PUNCH:
                            params = f"R_tip={t.tip_radius_mm}, H={t.height_mm}"
                        else:
                            params = f"V={t.v_opening_mm}, α={t.die_angle_deg}°, R_sh={t.shoulder_radius_mm}"
                        item = QtWidgets.QTreeWidgetItem([
                            t.tool_id,
                            t.name,
                            t.kind.value,
                            t.source_format,
                            t.validation_status.value,
                            params,
                        ])
                        item.setData(0, QtCore.Qt.UserRole, (t.tool_id, t.kind.value))
                        list_w.addTopLevelItem(item)

                def on_sel_changed(cur, _prev):
                    if cur is None:
                        details.setText("Инструмент не выбран.")
                        return
                    tid, k_str = cur.data(0, QtCore.Qt.UserRole)
                    try:
                        prof = self.tool_library.load_tool(tid, k_str)
                        details.setText(
                            f"ID: {prof.tool_id} | Имя: {prof.name} | Тип: {prof.kind.value} | "
                            f"Статус: {prof.validation_status.value} | Ед.: {prof.units} | "
                            f"Длина: {prof.length_mm} | Нагрузка: {prof.maximum_load}"
                        )
                    except Exception as exc:
                        details.setText(str(exc))

                list_w.currentItemChanged.connect(on_sel_changed)
                populate()

                btn_row = QtWidgets.QHBoxLayout()
                btn_imp_p = _button(QtWidgets, "Import Punch...", lambda: (self.import_punch_dialog(), populate()))
                btn_imp_d = _button(QtWidgets, "Import Die...", lambda: (self.import_die_dialog(), populate()))
                btn_use = QtWidgets.QPushButton("Установить в сцену")
                btn_del = QtWidgets.QPushButton("Удалить...")
                btn_close = QtWidgets.QPushButton("Закрыть")

                def on_use():
                    cur = list_w.currentItem()
                    if cur is None:
                        return
                    tid, k_str = cur.data(0, QtCore.Qt.UserRole)
                    prof = self.tool_library.load_tool(tid, k_str)
                    if prof.kind == ToolKind.PUNCH:
                        self.select_punch(prof)
                    else:
                        self.select_die(prof)

                def on_delete():
                    cur = list_w.currentItem()
                    if cur is None:
                        return
                    tid, k_str = cur.data(0, QtCore.Qt.UserRole)
                    ans = QtWidgets.QMessageBox.question(
                        dlg,
                        "Подтверждение удаления",
                        f"Удалить инструмент «{tid}» ({k_str}) из пользовательской библиотеки?",
                        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    )
                    if ans == QtWidgets.QMessageBox.Yes:
                        try:
                            self.tool_library.delete_tool(tid, k_str, confirmed=True)
                            if self.tooling_setup.punch and self.tooling_setup.punch.tool_id == tid:
                                self.select_punch(None)
                            if self.tooling_setup.die and self.tooling_setup.die.tool_id == tid:
                                self.select_die(None)
                            populate()
                            self._refresh_tooling_combos()
                        except Exception as exc:
                            details.setText(f"Ошибка удаления: {exc}")

                btn_use.clicked.connect(on_use)
                btn_del.clicked.connect(on_delete)
                btn_close.clicked.connect(dlg.accept)
                for b in (btn_imp_p, btn_imp_d, btn_use, btn_del, btn_close):
                    btn_row.addWidget(b)
                vbox.addLayout(btn_row)
                dlg.exec()

            def _stop_timer(self):
                self.timer.stop()
                self.playing = False
                self._last_tick_monotonic = None
                self._sync_playback_buttons()

            def _sync_playback_buttons(self):
                if self.session is None:
                    self.play_btn.setText("▶ Play All")
                    self.pause_btn.setText("Ⅱ Pause")
                    self.pause_btn.setEnabled(False)
                    self.move_up_btn.setEnabled(False)
                    self.move_down_btn.setEnabled(False)
                    self.restore_order_btn.setEnabled(False)
                    return
                st = self.session.playback_status
                self.playing = st == PlaybackStatus.PLAYING
                self.play_btn.setText("▶ Play All")
                if st == PlaybackStatus.PLAYING:
                    self.pause_btn.setText("Ⅱ Pause")
                    self.pause_btn.setEnabled(True)
                elif st == PlaybackStatus.PAUSED:
                    self.pause_btn.setText("▶ Resume")
                    self.pause_btn.setEnabled(True)
                else:
                    self.pause_btn.setText("Ⅱ Pause")
                    self.pause_btn.setEnabled(False)

                can_edit_order = self.session.can_reorder() and len(self.session.bend_order) > 1
                idx = self.session.active_bend_index
                total = len(self.session.bend_order)
                self.move_up_btn.setEnabled(can_edit_order and idx > 0)
                self.move_down_btn.setEnabled(can_edit_order and 0 <= idx < total - 1)
                self.restore_order_btn.setEnabled(
                    can_edit_order and self.session.bend_order != self.session.canonical_order
                )

            def _set_playback_controls_enabled(self, enabled: bool):
                for w in (
                    self.reset_btn,
                    self.back_btn,
                    self.play_btn,
                    self.next_btn,
                    self.end_btn,
                    self.slider,
                    self.prev_step_btn,
                    self.next_step_btn,
                ):
                    w.setEnabled(enabled)
                self.fit_btn.setEnabled(
                    enabled or self.tooling_setup.punch is not None or self.tooling_setup.die is not None
                )
                has_multi = enabled and self.session is not None and len(self.session.ordered_bends) > 1
                self.prev_bend_btn.setEnabled(has_multi)
                self.next_bend_btn.setEnabled(has_multi)
                self._sync_playback_buttons()

            def load_part(self):
                """Load the currently selected SheetMetal part in FreeCAD."""
                import FreeCAD as App
                import FreeCADGui as Gui

                doc = getattr(App, "ActiveDocument", None)
                selection = Gui.Selection.getSelection() if hasattr(Gui, "Selection") else []

                # Early precondition check BEFORE touching timer, session, controls, or scene
                outcome, new_session, msg = execute_load_part(self.session, doc, selection)
                if outcome == "warning":
                    self.status.setText(msg)
                    return
                if outcome != "loaded":
                    self.status.setText(msg)
                    self.check_result.setText(msg)
                    return

                self._stop_timer()
                self.session = new_session
                self._ensure_press_cycle_controller()
                self._sync_tooling_to_active_bend()
                self._populate_operations()
                self._configure_for_active_bend()
                self._set_playback_controls_enabled(True)
                self._update_tooling_status_label()
                self.status.setText(msg)
                self.check_result.setText(msg)
                self.draw()
                QtCore.QTimer.singleShot(0, self.fit)

            def _format_operation_row(self, idx: int, b) -> tuple[str, str, str, str, str]:
                cur = self.session.fold_state.angles[b.bend_id]
                done = self.session.controller.is_bend_completed(b.bend_id)
                is_active = (idx - 1) == self.session.active_bend_index
                if done:
                    status_str = "DONE"
                elif is_active:
                    status_str = "ACTIVE"
                else:
                    status_str = "IDLE"
                return (
                    str(idx),
                    "{} ({} → {})".format(b.bend_id, b.fixed_panel, b.moving_panel),
                    "{:+.1f}°".format(b.angle_deg),
                    "{:+.1f}°".format(cur),
                    status_str,
                )

            def _apply_reorder(self, action_fn):
                if self.session is None:
                    return
                try:
                    action_fn()
                except SequenceError as exc:
                    self.status.setText(str(exc))
                    self._sync_playback_buttons()
                    return
                # Synchronize table and slider without triggering geometry redraw in flat state
                self._configure_for_active_bend()
                if self.tooling_setup.aligned:
                    self._sync_tooling_to_active_bend()
                    self.draw()
                self.status.setText(
                    "Порядок гибки (KINEMATICALLY SIMULATED): {}".format(
                        " → ".join(self.session.bend_order)
                    )
                )

            def move_selected_up(self):
                if self.session is None:
                    return
                self._apply_reorder(lambda: self.session.move_up())

            def move_selected_down(self):
                if self.session is None:
                    return
                self._apply_reorder(lambda: self.session.move_down())

            def restore_order(self):
                if self.session is None:
                    return
                self._apply_reorder(lambda: self.session.restore_canonical_order())

            def _populate_operations(self):
                self.operations.blockSignals(True)
                self.operations.clear()
                if self.session is not None:
                    for idx, b in enumerate(self.session.ordered_bends, 1):
                        cols = self._format_operation_row(idx, b)
                        item = QtWidgets.QTreeWidgetItem(list(cols))
                        self.operations.addTopLevelItem(item)
                    if self.operations.topLevelItemCount() > 0:
                        self.operations.setCurrentItem(
                            self.operations.topLevelItem(self.session.active_bend_index)
                        )
                self.operations.blockSignals(False)

            def _update_operations_visual_state(self):
                if self.session is None:
                    return
                self.operations.blockSignals(True)
                raw_count = self.operations.topLevelItemCount()
                count = int(raw_count) if isinstance(raw_count, int) else 0
                for idx, b in enumerate(self.session.ordered_bends, 1):
                    if idx - 1 >= count:
                        break
                    item = self.operations.topLevelItem(idx - 1)
                    if item is None:
                        continue
                    cols = self._format_operation_row(idx, b)
                    for col_i, text in enumerate(cols):
                        if hasattr(item, "setText"):
                            item.setText(col_i, text)
                if 0 <= self.session.active_bend_index < count:
                    target_item = self.operations.topLevelItem(self.session.active_bend_index)
                    if target_item is not None and self.operations.currentItem() is not target_item:
                        self.operations.setCurrentItem(target_item)
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
                self._update_operations_visual_state()
                self._sync_playback_buttons()

            def _on_operation_selected(self, current, _previous):
                if self.session is None or current is None:
                    return
                idx = self.operations.indexOfTopLevelItem(current)
                if idx < 0 or idx == self.session.active_bend_index:
                    return
                self._stop_timer()
                # Switch active bend while preserving the angles already achieved on previous bends
                pc = self._ensure_press_cycle_controller()
                if pc is not None:
                    pc.select_operation(idx)
                    self.session.active_bend_index = self.session.controller.active_index
                else:
                    self.session.select_bend_index(idx)
                self._configure_for_active_bend()
                self._update_tooling_status_label()
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
                """Set the active bend to a signed angle manually (stops auto-playback if active)."""
                if self.session is None:
                    return
                if self.timer.isActive() or self.playing:
                    self._stop_timer()
                try:
                    pc = self._ensure_press_cycle_controller()
                    if pc is not None:
                        pc.set_manual_angle(float(signed_angle_deg))
                        self.session.active_bend_index = self.session.controller.active_index
                    else:
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
                self._update_operations_visual_state()
                self._sync_playback_buttons()
                self._update_tooling_status_label()
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
                self._stop_timer()
                self.set_angle(self.session.target_angle_deg)

            def reset(self):
                self._stop_timer()
                if self.session is not None:
                    pc = self._ensure_press_cycle_controller()
                    if pc is not None:
                        pc.reset()
                        self.session.active_bend_index = self.session.controller.active_index
                    else:
                        self.session.reset_all_bends()
                    self._configure_for_active_bend()
                    self._update_tooling_status_label()
                    self.draw()

            def play_all(self):
                """Start or restart full bend-sequence playback across all bends."""
                if self.session is None:
                    return
                try:
                    pc = self._ensure_press_cycle_controller()
                    if pc is not None and self.execution_mode == CycleExecutionMode.FULL_CYCLE:
                        pc.play_all()
                        self.session.active_bend_index = self.session.controller.active_index
                    else:
                        self.session.play_all()
                        if pc is not None:
                            pc.phase = CyclePhase.BENDING
                            pc.on_tooling_changed()
                except SequenceError as exc:
                    self._stop_timer()
                    self.status.setText(str(exc))
                    return
                self._configure_for_active_bend()
                self._update_tooling_status_label()
                self.playing = True
                self._last_tick_monotonic = time.monotonic()
                self._sync_playback_buttons()
                if not self.timer.isActive():
                    self.timer.start()
                self.draw()
                _log("Play All started at bend {}".format(self.session.active_bend_id))

            def pause(self):
                if self.session is None:
                    return
                pc = self._ensure_press_cycle_controller()
                if pc is not None and self.execution_mode == CycleExecutionMode.FULL_CYCLE:
                    pc.pause()
                else:
                    self.session.pause()
                self._stop_timer()
                self._update_tooling_status_label()
                self.draw()
                _log("Playback paused at {:+.1f} deg".format(self.angle))

            def resume(self):
                if self.session is None:
                    return
                try:
                    pc = self._ensure_press_cycle_controller()
                    if pc is not None and self.execution_mode == CycleExecutionMode.FULL_CYCLE:
                        pc.resume()
                        self.session.active_bend_index = self.session.controller.active_index
                    else:
                        self.session.resume()
                        if pc is not None:
                            pc.phase = CyclePhase.BENDING
                except SequenceError as exc:
                    self._stop_timer()
                    self.status.setText(str(exc))
                    return
                self._configure_for_active_bend()
                self._update_tooling_status_label()
                self.playing = True
                self._last_tick_monotonic = time.monotonic()
                self._sync_playback_buttons()
                if not self.timer.isActive():
                    self.timer.start()
                self.draw()
                _log("Playback resumed at bend {}".format(self.session.active_bend_id))

            def toggle_pause_resume(self):
                if self.session is None:
                    return
                if self.session.playback_status == PlaybackStatus.PLAYING:
                    self.pause()
                elif self.session.playback_status == PlaybackStatus.PAUSED:
                    self.resume()

            def toggle_play(self):
                """Toggle Play All / Pause for backward compatibility."""
                if self.session is None:
                    return
                if self.session.playback_status == PlaybackStatus.PLAYING:
                    self.pause()
                else:
                    self.play_all()

            def next_step(self):
                if self.session is None:
                    return
                self._stop_timer()
                try:
                    pc = self._ensure_press_cycle_controller()
                    if pc is not None:
                        pc.next_step()
                        self.session.active_bend_index = self.session.controller.active_index
                    else:
                        self.session.next_step()
                except SequenceError as exc:
                    self.status.setText(str(exc))
                    return
                self._configure_for_active_bend()
                self._update_tooling_status_label()
                self.draw()
                QtCore.QTimer.singleShot(0, self.fit)

            def previous_step(self):
                if self.session is None:
                    return
                self._stop_timer()
                try:
                    pc = self._ensure_press_cycle_controller()
                    if pc is not None:
                        pc.previous_step()
                        self.session.active_bend_index = self.session.controller.active_index
                    else:
                        self.session.previous_step()
                except SequenceError as exc:
                    self.status.setText(str(exc))
                    return
                self._configure_for_active_bend()
                self._update_tooling_status_label()
                self.draw()
                QtCore.QTimer.singleShot(0, self.fit)

            def advance(self):
                if self.session is None or self.session.playback_status != PlaybackStatus.PLAYING:
                    self._stop_timer()
                    return
                now = time.monotonic()
                if self._last_tick_monotonic is None:
                    dt = 0.035
                else:
                    dt = max(0.005, now - self._last_tick_monotonic)
                self._last_tick_monotonic = now

                prev_bend_idx = self.session.active_bend_index
                pc = self._ensure_press_cycle_controller()
                if pc is not None and self.execution_mode == CycleExecutionMode.FULL_CYCLE:
                    pc.advance_time(dt)
                    self.session.active_bend_index = self.session.controller.active_index
                else:
                    self.session.advance_time(dt)
                    if self.session.active_bend_index != prev_bend_idx and self.tooling_setup.aligned:
                        self._sync_tooling_to_active_bend()
                self._configure_for_active_bend()
                self._update_tooling_status_label()
                self.draw()
                if self.session.active_bend_index != prev_bend_idx:
                    QtCore.QTimer.singleShot(0, self.fit)

                if self.session.playback_status == PlaybackStatus.COMPLETED:
                    self._stop_timer()
                    self.draw()
                    _log("Sequence playback completed")

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
                    "unavailable_controls_disabled": not self.calculate.isEnabled(),
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

            def _draw_tooling_items(
                self,
                to_qpt,
                make_poly,
                punch_pts: list,
                die_pts: list,
            ) -> None:
                """Render cached PUNCH and DIE profiles in Machine -> View coordinates with explicit Z-order."""
                if not self.tooling_setup.visible:
                    return

                # 1. Die (lower tool) — rendered in Z_LAYER_DIE (below sheet geometry)
                die_polys = self.tooling_setup.die_polygons_machine(arc_segments=20)
                if die_polys is not None:
                    die_outer, die_holes = die_polys
                    die_pts.extend(die_outer)
                    die_path = QtGui.QPainterPath()
                    die_path.addPolygon(make_poly(die_outer))
                    die_path.closeSubpath()
                    for h in die_holes:
                        hole_path = QtGui.QPainterPath()
                        hole_path.addPolygon(make_poly(h))
                        hole_path.closeSubpath()
                        die_path = die_path.subtracted(hole_path)
                    die_pen = QtGui.QPen(QtGui.QColor("#2c3e50"), 1.5)
                    die_pen.setCosmetic(True)
                    die_brush = QtGui.QBrush(QtGui.QColor(52, 73, 94, 155))
                    die_item = self.scene.addPath(die_path, die_pen, die_brush)
                    die_item.setZValue(Z_LAYER_DIE)
                    die_item.setData(0, "die")

                    vc = self.tooling_setup.die_v_center_machine()
                    if vc is not None:
                        vp = to_qpt(vc)
                        axis_pen = QtGui.QPen(QtGui.QColor("#16a085"), 1.1, QtCore.Qt.DashDotLine)
                        axis_pen.setCosmetic(True)
                        die_axis_item = self.scene.addLine(
                            vp.x(), vp.y() - 12.0, vp.x(), vp.y() + 25.0, axis_pen
                        )
                        die_axis_item.setZValue(Z_LAYER_AUXILIARY)
                        die_axis_item.setData(0, "die_axis")

                # 2. Punch (upper tool) — rendered in Z_LAYER_PUNCH (below sheet geometry)
                punch_polys = self.tooling_setup.punch_polygons_machine(arc_segments=20)
                if punch_polys is not None:
                    punch_outer, punch_holes = punch_polys
                    punch_pts.extend(punch_outer)
                    punch_path = QtGui.QPainterPath()
                    punch_path.addPolygon(make_poly(punch_outer))
                    punch_path.closeSubpath()
                    for h in punch_holes:
                        hole_path = QtGui.QPainterPath()
                        hole_path.addPolygon(make_poly(h))
                        hole_path.closeSubpath()
                        punch_path = punch_path.subtracted(hole_path)
                    punch_pen = QtGui.QPen(QtGui.QColor("#922b21"), 1.5)
                    punch_pen.setCosmetic(True)
                    punch_brush = QtGui.QBrush(QtGui.QColor(231, 76, 60, 145))
                    punch_item = self.scene.addPath(punch_path, punch_pen, punch_brush)
                    punch_item.setZValue(Z_LAYER_PUNCH)
                    punch_item.setData(0, "punch")

                    ptip = self.tooling_setup.punch_tip_machine()
                    if ptip is not None:
                        tp = to_qpt(ptip)
                        p_axis_pen = QtGui.QPen(QtGui.QColor("#c0392b"), 1.1, QtCore.Qt.DashDotLine)
                        p_axis_pen.setCosmetic(True)
                        punch_axis_item = self.scene.addLine(
                            tp.x(), tp.y() - 25.0, tp.x(), tp.y() + 12.0, p_axis_pen
                        )
                        punch_axis_item.setZValue(Z_LAYER_AUXILIARY)
                        punch_axis_item.setData(0, "punch_axis")

            def draw(self):
                try:
                    t_draw_start = time.perf_counter()
                    self.scene.clear()

                    # Screen mapping: x_screen = s, y_screen = -z so +Z (UP bend) points visually upward
                    def to_qpt(sz):
                        return QtCore.QPointF(float(sz[0]), -float(sz[1]))

                    def make_poly(pts):
                        return QtGui.QPolygonF([to_qpt(p) for p in pts])

                    has_visible_tools = self.tooling_setup.visible and (
                        self.tooling_setup.punch is not None or self.tooling_setup.die is not None
                    )

                    if self.session is None and not has_visible_tools:
                        self.last_scene_bounds = None
                        self.scene.setSceneRect(-120, -60, 240, 120)
                        text = self.scene.addSimpleText("Деталь не загружена — нажмите «Деталь»")
                        text.setBrush(QtGui.QBrush(QtGui.QColor("#6c7a89")))
                        text.setZValue(Z_LAYER_AUXILIARY)
                        text.setData(0, "placeholder")
                        rect = text.boundingRect()
                        text.setPos(-rect.width() / 2.0, -rect.height() / 2.0)
                        self.step.setText("Активный гиб: — | Угол: — / —")
                        return

                    sheet_pts: list = []
                    punch_pts: list = []
                    die_pts: list = []

                    # 1. Custom Punch & Die tooling (rendered in Z_LAYER_DIE / Z_LAYER_PUNCH below sheet)
                    self._draw_tooling_items(to_qpt, make_poly, punch_pts, die_pts)

                    if self.session is not None:
                        sec = self.session.cross_section(segments=32)

                        sheet_pts.extend(sec["inner_surface"])
                        sheet_pts.extend(sec["outer_surface"])
                        for _, opoly in sec.get("other_panel_polygons", ()):
                            sheet_pts.extend(opoly)
                        for _, ozpoly in sec.get("other_zone_polygons", ()):
                            sheet_pts.extend(ozpoly)

                        # 2. Other panels & bend zones in the same cross-section plane
                        other_pen = QtGui.QPen(QtGui.QColor("#1b4f72"), 1.0)
                        other_pen.setCosmetic(True)
                        other_brush = QtGui.QBrush(QtGui.QColor("#85929e"))
                        for _, opoly in sec.get("other_panel_polygons", ()):
                            op_item = self.scene.addPolygon(make_poly(opoly), other_pen, other_brush)
                            op_item.setZValue(Z_LAYER_SHEET_OTHER)
                            op_item.setData(0, "sheet_other_panel")

                        zone_pen = QtGui.QPen(QtGui.QColor("#b9770e"), 1.2)
                        zone_pen.setCosmetic(True)
                        zone_brush = QtGui.QBrush(QtGui.QColor("#f8c471"))
                        for _, ozpoly in sec.get("other_zone_polygons", ()):
                            oz_item = self.scene.addPolygon(make_poly(ozpoly), zone_pen, zone_brush)
                            oz_item.setZValue(Z_LAYER_SHEET_OTHER)
                            oz_item.setData(0, "sheet_other_zone")

                        # 3. Fixed rigid panel
                        panel_pen = QtGui.QPen(QtGui.QColor("#1b4f72"), 1.2)
                        panel_pen.setCosmetic(True)
                        fixed_brush = QtGui.QBrush(QtGui.QColor("#85929e"))
                        fixed_item = self.scene.addPolygon(make_poly(sec["fixed_polygon"]), panel_pen, fixed_brush)
                        fixed_item.setZValue(Z_LAYER_SHEET_PANELS)
                        fixed_item.setData(0, "sheet_fixed")

                        # 4. Moving rigid panel (transformed by FoldState)
                        moving_brush = QtGui.QBrush(QtGui.QColor("#5dade2"))
                        moving_item = self.scene.addPolygon(make_poly(sec["moving_polygon"]), panel_pen, moving_brush)
                        moving_item.setZValue(Z_LAYER_SHEET_PANELS)
                        moving_item.setData(0, "sheet_moving")

                        # 5. Finite-radius bend zone polygon
                        zone_item = self.scene.addPolygon(make_poly(sec["zone_polygon"]), zone_pen, zone_brush)
                        zone_item.setZValue(Z_LAYER_SHEET_ZONE)
                        zone_item.setData(0, "sheet_zone")

                        # 6. Both sheet surfaces (inner concave and outer convex)
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
                            in_item = self.scene.addPath(inner_path, inner_pen)
                            in_item.setZValue(Z_LAYER_SHEET_SURFACES)
                            in_item.setData(0, "sheet_inner_surface")

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
                            out_item = self.scene.addPath(outer_path, outer_pen)
                            out_item.setZValue(Z_LAYER_SHEET_SURFACES)
                            out_item.setData(0, "sheet_outer_surface")

                        # 7. Neutral layer curve (dashed) inside bend zones
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
                            neu_item = self.scene.addPath(neutral_path, neutral_pen)
                            neu_item.setZValue(Z_LAYER_SHEET_NEUTRAL_AND_TANGENTS)
                            neu_item.setData(0, "sheet_neutral")

                        # 8. Tangent boundary lines between rigid panels and bend zones
                        tan_pen = QtGui.QPen(QtGui.QColor("#7d3c98"), 1.2, QtCore.Qt.DotLine)
                        tan_pen.setCosmetic(True)
                        all_tan_pairs = list(zip(sec["tangent_points_top"], sec["tangent_points_bottom"]))
                        all_tan_pairs.extend(sec.get("other_tangent_lines", ()))
                        for t_top, t_bot in all_tan_pairs:
                            p1, p2 = to_qpt(t_top), to_qpt(t_bot)
                            tan_item = self.scene.addLine(p1.x(), p1.y(), p2.x(), p2.y(), tan_pen)
                            tan_item.setZValue(Z_LAYER_SHEET_NEUTRAL_AND_TANGENTS)
                            tan_item.setData(0, "sheet_tangent")

                        pc = self._ensure_press_cycle_controller()
                        if pc is not None and self.execution_mode == CycleExecutionMode.FULL_CYCLE:
                            status_str = pc.status_line()
                        else:
                            status_str = self.session.status_line()
                        self.step.setText(
                            "{} | R_вн={:.2f} мм, t={:.2f} мм".format(
                                status_str,
                                sec["inner_radius_mm"],
                                sec["thickness_mm"],
                            )
                        )
                        self._update_press_cycle_labels()
                    else:
                        self.step.setText("Активный гиб: — | Угол: — / —")
                        self._update_press_cycle_labels()

                    # Compute separated scene bounds (sheet_bounds, punch_bounds, die_bounds, combined_bounds)
                    self.last_scene_bounds = compute_scene_bounds_2d(
                        sheet_pts=sheet_pts,
                        punch_pts=punch_pts,
                        die_pts=die_pts,
                        focus_sheet_when_tall_tooling=True,
                    )
                    rx, ry, rw, rh = self.last_scene_bounds.view_rect
                    self.scene.setSceneRect(rx, ry, rw, rh)
                    self.last_draw_time_ms = (time.perf_counter() - t_draw_start) * 1000.0
                except Exception:
                    _error(traceback.format_exc())
                    raise

        return Panel()
