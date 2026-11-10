"""PressBrakeSim 2.5D — Real SheetMetal Geometry Viewer (Phase 3A).

Uses the existing 2.5D pipeline without introducing a second geometry engine:
  sheetmetal_unfold_reader.read_unfold
  -> panel_builder.build_panels
  -> panel_graph.graph_from_panel_build
  -> panel_kinematics.FoldState (with explicit BendZone)
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import sys
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
from smbs_core.kinematics2d.motion_trace import MotionTraceRecorder, make_frame_record
from smbs_core.kinematics2d.preview_mesh_3d import (
    build_sheet_preview_faces, PreviewMeshError,
)
from smbs_core.kinematics2d.machine_section_3d import compute_machine_cutting_plane_section
from smbs_core.kinematics2d.workpiece_placement_3d import PlacementTransform3D, WorkpiecePlacement3D
from smbs_core.kinematics2d.workpiece_pose_2d import (
    WorkpiecePose2D, WorkpiecePoseError, animated_machine_pose_for_cycle,
    remap_section_to_reference, symmetric_v_pose, transform_sheet_section,
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
def _pressbrake_user_data_directory():
    """Platform-specific writable diagnostics home, not the plugin checkout.

    FreeCAD installations may be under Program Files, a read-only AppImage,
    or a macOS .app bundle. Traces must never depend on repository permissions.
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(
            os.environ.get("APPDATA")
            or os.environ.get("LOCALAPPDATA")
            or str(Path.home() / "AppData" / "Roaming")
        )
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or
                    str(Path.home() / ".local" / "share"))
    return base / "FreeCAD" / "v1-1"


_LOG_FILE_PATH = _pressbrake_user_data_directory() / "pressbrake_2d.log"


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


def get_runtime_git_info() -> dict:
    """Return actual module path, repository root, git branch, and git commit hash."""
    import subprocess

    mod_path = Path(__file__).resolve()
    repo_root = mod_path.parents[2]
    branch = "UNKNOWN"
    commit = "UNKNOWN"
    try:
        branch = subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
        ).strip()
        commit = subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-parse", "--short=12", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
        ).strip()
    except Exception:
        pass
    return {
        "module_file": str(mod_path),
        "repo_root": str(repo_root),
        "git_branch": branch,
        "git_commit": commit,
    }


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
        """Open a dedicated FreeCAD document-area workspace, never a side dock."""
        import FreeCADGui as Gui
        global _DOCK
        QtCore, QtGui, QtWidgets = _qt()
        main = Gui.getMainWindow()
        mdi = main.findChild(QtWidgets.QMdiArea)
        if mdi is None:
            raise RuntimeError("FreeCAD MDI area was not found; source document left unchanged.")

        name = "SMBS_PressBrake2D_Workspace"
        existing = next(
            (win for win in mdi.subWindowList() if win.objectName() == name), None
        )
        if existing is not None and existing.widget() is not None:
            mdi.setActiveSubWindow(existing)
            existing.show()
            existing.raise_()
            return

        # Retire only this addon's old dock. Never alter FreeCAD's document views.
        old_dock = main.findChild(QtWidgets.QDockWidget, "SMBS_PressBrake2D_Dock")
        if old_dock is not None:
            old_widget = old_dock.widget()
            if old_widget is not None and hasattr(old_widget, "_stop_timer"):
                old_widget._stop_timer()
            old_dock.hide()
            old_dock.setWidget(None)
            old_dock.deleteLater()
        _DOCK = None

        widget = _PreviewPanel()
        window = QtWidgets.QMdiSubWindow()
        window.setObjectName(name)
        window.setWindowTitle("PressBrakeSim 2.5D — рабочее пространство")
        window.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        window.setWidget(widget)
        mdi.addSubWindow(window)
        window.resize(1000, 720)
        window.showMaximized()
        mdi.setActiveSubWindow(window)

        sel = Gui.Selection.getSelection() if hasattr(Gui, "Selection") else []
        if sel:
            widget.load_part()
        _log("2.5D workspace opened in FreeCAD MDI area")


def _button(widgets, label, callback, enabled=True):
    btn = widgets.QPushButton(label)
    btn.setEnabled(enabled)
    btn.clicked.connect(callback)
    return btn


class _PreviewPanel:
    """Lazy Qt widget factory to keep headless command import possible."""

    def __new__(cls, library_root: Path | str | None = None):
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
                # Machine cycle is the primary simulation; legacy Bend Only remains selectable.
                self.execution_mode = CycleExecutionMode.FULL_CYCLE
                self.press_cycle: PressCycleController | None = None
                self.last_scene_bounds: SceneBounds2D | None = None
                self._locked_25d_scene_rect = None
                # Last valid machine-space image. Never fall back to a flat
                # IDENTITY pose when a later bend cannot be represented.
                self._last_valid_machine_section = None
                self._last_valid_machine_pose = None
                self._last_valid_machine_bend = None
                self._machine_pose_fault = None
                self.motion_trace = None
                self._physical_check_signature = None
                self._trace_frame_dt = None
                self._trace_directory = _LOG_FILE_PATH.parent / "pressbrake_traces"
                self.runtime_git_info = get_runtime_git_info()
                self.last_sequence_search_report = None
                self.last_planar_search_report = None
                self._preview_only_planar_order = None
                self._conditional_bends_status: dict[str, str] = {}
                self._highlighted_end_zone_info: dict | None = None
                _log(
                    "PressBrakeSim startup: module={} | branch={} | commit={}".format(
                        self.runtime_git_info["module_file"],
                        self.runtime_git_info["git_branch"],
                        self.runtime_git_info["git_commit"],
                    )
                )
                self.angle = 0.0
                self.playing = False
                self._last_tick_monotonic: float | None = None
                self.timer = QtCore.QTimer(self)
                self.timer.setInterval(25)  # 40 Hz Qt playback; one timer only
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
                self.calculate = _button(QtWidgets, "Расчёт", self.calculate_sequence, False)
                self.planar_calculate = _button(
                    QtWidgets, "2D варианты", self.calculate_planar_candidates, False
                )
                self.planar_calculate.setToolTip(
                    "Быстрый 2D поиск вариантов для просмотра. Не подтверждает "
                    "физическую возможность гибки и не отменяет 3D-валидацию."
                )
                self.tooling.setToolTip("Открыть библиотеку пользовательской оснастки (PUNCH / DIE)")
                self.calculate.setToolTip(
                    "Автоматический поиск допустимой последовательности гибки с физической проверкой 2.5D + 3D (D6.3.4)"
                )
                for widget in (self.load, self.tooling, self.calculate, self.planar_calculate):
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

                # Development virtual tool length & controlled punch end-zone policy
                self.virtual_tool_len_checkbox = QtWidgets.QCheckBox(
                    "Разработка: длина инструмента равна длине гиба"
                )
                self.virtual_tool_len_checkbox.setChecked(False)
                self.virtual_tool_len_checkbox.setToolTip(
                    "Виртуальная длина пуансона и матрицы автоматически приравнивается к длине активной линии гиба "
                    "с сохранением неизменного 2D-профиля сечения (исходные FCStd не изменяются)."
                )
                self.virtual_tool_len_checkbox.toggled.connect(self._on_virtual_tool_length_toggled)
                t_layout.addWidget(self.virtual_tool_len_checkbox)

                ez_row = QtWidgets.QHBoxLayout()
                ez_row.addWidget(QtWidgets.QLabel("Концевая зона пуансона (мм):"))
                self.end_zone_len_spin = QtWidgets.QDoubleSpinBox()
                self.end_zone_len_spin.setRange(0.0, 200.0)
                self.end_zone_len_spin.setDecimals(1)
                self.end_zone_len_spin.setSingleStep(1.0)
                self.end_zone_len_spin.setValue(10.0)
                self.end_zone_len_spin.valueChanged.connect(self._on_end_zone_length_changed)
                ez_row.addWidget(self.end_zone_len_spin)
                t_layout.addLayout(ez_row)

                self.allow_end_zone_checkbox = QtWidgets.QCheckBox(
                    "Условный допуск концевых зон пуансона (CONDITIONAL)"
                )
                self.allow_end_zone_checkbox.setChecked(False)
                self.allow_end_zone_checkbox.setToolTip(
                    "Разрешить условное продолжение (статус CONDITIONAL, не PASS), если пересечение детали "
                    "с пуансоном находится строго в пределах заданных концевых зон пуансона."
                )
                self.allow_end_zone_checkbox.toggled.connect(self._on_allow_end_zone_toggled)
                t_layout.addWidget(self.allow_end_zone_checkbox)
                self.tooling_setup.end_zone_length_mm = float(self.end_zone_len_spin.value())
                self.tooling_setup.allow_end_zone_collision = bool(self.allow_end_zone_checkbox.isChecked())

                self.tooling_status = QtWidgets.QLabel(self.tooling_setup.status_line())
                self.tooling_status.setWordWrap(True)
                self.tooling_status.setStyleSheet("font-size: 11px; color: #2c3e50;")
                t_layout.addWidget(self.tooling_status)
                # Explicit, on-demand physical check: no expensive BRep
                # booleans in the 25–40 Hz drawing/timer path.
                self.physical_check_btn = _button(
                    QtWidgets, "Проверить текущую позу: 2D + 3D",
                    self.check_current_physical_pose, True,
                )
                self.physical_check_btn.setToolTip(
                    "Проверка текущего кадра по 2D-сечению и, если имеются "
                    "реальные FCStd пуансона и матрицы, по нативной 3D-геометрии. "
                    "Не сертифицирует весь цикл или станок."
                )
                t_layout.addWidget(self.physical_check_btn)
                self.full_physical_check_btn = _button(
                    QtWidgets, "Проверить весь гиб: D6.3.4",
                    self.check_active_bend_physical_trajectory, True,
                )
                self.full_physical_check_btn.setToolTip(
                    "Ресурсоёмкая проверка выборки 3D-состояний текущего гиба. "
                    "Во время выполнения анимация должна быть остановлена. "
                    "Непрерывное движение и прочие компоненты станка не сертифицированы."
                )
                t_layout.addWidget(self.full_physical_check_btn)
                self.physical_check_result = QtWidgets.QLabel(
                    "Проверка коллизий не выполнена."
                )
                self.physical_check_result.setWordWrap(True)
                t_layout.addWidget(self.physical_check_result)
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
                self.cycle_mode_combo.setCurrentIndex(1)
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

                self._auto_select_default_tooling()
                self._refresh_tooling_combos()

                self.view = Canvas()
                self.scene = QtWidgets.QGraphicsScene(self.view)
                self.view.setScene(self.scene)
                root.addWidget(self.view, 1)

                row = QtWidgets.QHBoxLayout()
                # Stable operator controls: Reset, Play All, Pause/Resume, Fit.
                # Angle adjustment is via the slider; bend navigation uses
                # explicit Previous/Next Step and the operation table.
                self.reset_btn = _button(QtWidgets, "↺ Reset", self.reset, False)
                self.play_btn = _button(QtWidgets, "▶ Play All", self.play_all, False)
                self.play_btn.setToolTip(
                    "Запуск автоматической симуляции в выбранном режиме: "
                    "Bend Only или Full Cycle."
                )
                self.pause_btn = _button(
                    QtWidgets, "Ⅱ Pause", self.toggle_pause_resume, False
                )
                self.fit_btn = _button(QtWidgets, "Fit", self.fit, True)
                for widget in (
                    self.reset_btn, self.play_btn, self.pause_btn, self.fit_btn
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
                for widget in (
                    self.prev_step_btn,
                    self.next_step_btn,
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

                self._install_workspace_layout(root, QtCore, QtWidgets)
                self.draw()

            def _install_workspace_layout(self, root, QtCore, QtWidgets):
                """Rearrange existing controls without rebuilding the simulation engine.

                Preserve all initialized widgets, signals, scene and the single QTimer.
                """
                # The existing constructor builds these items in a known order.
                entries = [root.takeAt(0) for _ in range(root.count())]
                if len(entries) != 14:
                    raise RuntimeError(
                        "Unexpected PressBrakeSim layout: {} items".format(len(entries))
                    )

                def insert(layout, item, stretch=0):
                    widget = item.widget()
                    if widget is not None:
                        layout.addWidget(widget, stretch)
                    elif item.layout() is not None:
                        nested = item.layout()
                        # QLayout taken out of the original root still has an
                        # QObject parent in some Qt/PySide versions.
                        nested.setParent(None)
                        layout.addLayout(nested, stretch)

                # Header: title, source selection, and working status.
                for item in entries[:3]:
                    insert(root, item)

                splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal, self)
                views = QtWidgets.QTabWidget(splitter)
                views.setObjectName("SMBS_ViewerTabs")
                views.addTab(self.view, "2.5D · Гибка")
                self._preview_3d = QtWidgets.QGraphicsView()
                self._last_3d_frame_at = -1.0
                self._fixed_3d_view_rect = None
                self._preview_3d.setObjectName("SMBS_3D_ReadOnly")
                self._preview_3d_scene = QtWidgets.QGraphicsScene(self._preview_3d)
                self._preview_3d.setScene(self._preview_3d_scene)
                views.addTab(self._preview_3d, "3D · Просмотр")
                views.currentChanged.connect(self._on_view_changed)

                controls = QtWidgets.QTabWidget(splitter)
                controls.setObjectName("SMBS_ControlTabs")
                simulation = QtWidgets.QWidget()
                sim_layout = QtWidgets.QVBoxLayout(simulation)
                sim_layout.setSpacing(8)
                # Mode selection belongs beside playback, not in diagnostic
                # settings. One Play All operates whichever mode is selected.
                insert(sim_layout, entries[4])  # mode / speed / cycle config
                insert(sim_layout, entries[6])  # Reset / Play All / Pause / Fit
                insert(sim_layout, entries[7])  # angle slider
                insert(sim_layout, entries[8])  # active operation
                insert(sim_layout, entries[9])  # next/previous step
                insert(sim_layout, entries[10])  # move order
                self.operations.setMaximumHeight(16777215)
                insert(sim_layout, entries[11], 1)  # operation table
                controls.addTab(simulation, "Симуляция")

                tooling = QtWidgets.QWidget()
                tool_layout = QtWidgets.QVBoxLayout(tooling)
                insert(tool_layout, entries[3])  # tooling group
                tool_layout.addStretch(1)
                controls.addTab(tooling, "Оснастка")

                settings = QtWidgets.QWidget()
                settings_layout = QtWidgets.QVBoxLayout(settings)
                insert(settings_layout, entries[12])  # diagnostics button
                insert(settings_layout, entries[13])  # diagnostics output

                self.trace_checkbox = QtWidgets.QCheckBox(
                    "Автоматически записывать движения при Play All (JSONL)"
                )
                self.trace_checkbox.setChecked(False)
                self.trace_checkbox.setToolTip(
                    "Создаёт папку при начале записи. После остановки сохраняет "
                    "кадры в *.jsonl и сводный отчёт в *.summary.json."
                )
                settings_layout.addWidget(self.trace_checkbox)
                self.trace_checkbox.toggled.connect(self._on_trace_checkbox_toggled)

                self.trace_status = QtWidgets.QLabel(
                    "Запись отключена. Каталог: {}".format(self._trace_directory)
                )
                self.trace_status.setWordWrap(True)
                self.trace_status.setTextInteractionFlags(
                    QtCore.Qt.TextSelectableByMouse
                )
                settings_layout.addWidget(self.trace_status)

                trace_buttons = QtWidgets.QHBoxLayout()
                self.trace_start_button = QtWidgets.QPushButton("Начать запись JSONL")
                self.trace_start_button.clicked.connect(
                    lambda _checked=False: self._start_motion_trace(manual=True)
                )
                trace_buttons.addWidget(self.trace_start_button)
                self.trace_stop_button = QtWidgets.QPushButton("Завершить и создать JSON")
                self.trace_stop_button.clicked.connect(
                    lambda _checked=False: self._finish_motion_trace("USER_STOP")
                )
                trace_buttons.addWidget(self.trace_stop_button)
                settings_layout.addLayout(trace_buttons)

                self.trace_open_button = QtWidgets.QPushButton("Открыть папку записей")
                self.trace_open_button.clicked.connect(
                    lambda _checked=False: self._open_trace_directory()
                )
                settings_layout.addWidget(self.trace_open_button)
                self.trace_choose_button = QtWidgets.QPushButton(
                    "Выбрать папку для JSONL/JSON"
                )
                self.trace_choose_button.clicked.connect(
                    lambda _checked=False: self._choose_trace_directory()
                )
                settings_layout.addWidget(self.trace_choose_button)
                settings_layout.addStretch(1)
                controls.addTab(settings, "Настройки")

                splitter.addWidget(views)
                splitter.addWidget(controls)
                splitter.setStretchFactor(0, 4)
                splitter.setStretchFactor(1, 2)
                splitter.setSizes([720, 370])
                root.addWidget(splitter, 1)
                self._viewer_tabs = views
                self._control_tabs = controls

            def _on_trace_checkbox_toggled(self, enabled):
                if enabled:
                    if self.session is not None:
                        self._start_motion_trace(manual=True)
                    else:
                        self.trace_status.setText(
                            "Сначала загрузите деталь. Запись начнётся при Play All. "
                            "Каталог: {}".format(self._trace_directory)
                        )
                else:
                    self._finish_motion_trace("RECORDING_DISABLED")

            def _choose_trace_directory(self):
                if self.motion_trace is not None and self.motion_trace.is_open:
                    self.trace_status.setText(
                        "Сначала завершите текущую запись перед сменой папки."
                    )
                    return
                chosen = QtWidgets.QFileDialog.getExistingDirectory(
                    self, "Выберите каталог для записи JSONL и JSON",
                    str(self._trace_directory.parent)
                )
                if not chosen:
                    return
                try:
                    target = Path(chosen).expanduser().resolve()
                    target.mkdir(parents=True, exist_ok=True)
                    self._trace_directory = target
                    self.trace_status.setText(
                        "Каталог записи: {}. Нажмите «Начать запись JSONL».".format(
                            self._trace_directory
                        )
                    )
                except OSError as exc:
                    self.trace_status.setText(
                        "Ошибка доступа к каталогу {}: {}".format(chosen, exc)
                    )

            def _open_trace_directory(self):
                try:
                    self._trace_directory.mkdir(parents=True, exist_ok=True)
                    target = QtCore.QUrl.fromLocalFile(
                        str(self._trace_directory.resolve())
                    )
                    if not QtGui.QDesktopServices.openUrl(target):
                        raise OSError("System file manager refused to open the folder")
                except Exception as exc:
                    message = "Не удалось открыть каталог записи {}: {}".format(
                        self._trace_directory, exc
                    )
                    self.trace_status.setText(message)
                    _warn(message)

            def _start_motion_trace(self, manual=False):
                if self.session is None:
                    if manual:
                        self.trace_status.setText(
                            "Запись невозможна: сначала загрузите деталь."
                        )
                    return
                if not manual and not self.trace_checkbox.isChecked():
                    return
                if self.motion_trace is not None and self.motion_trace.is_open:
                    self.trace_status.setText(
                        "Запись продолжается: {}".format(self.motion_trace.path)
                    )
                    return
                try:
                    self._trace_directory.mkdir(parents=True, exist_ok=True)
                    # The human project version is the one published by
                    # the FreeCAD add-on. The fingerprint identifies this
                    # *exact* GUI source even without a .git directory.
                    from freecad_smbs import __version__ as project_version
                    module_path = Path(__file__).resolve()
                    try:
                        source_digest = hashlib.sha256(
                            module_path.read_bytes()
                        ).hexdigest()
                    except OSError as exc:
                        source_digest = "READ_ERROR: {}".format(exc)
                    trace = MotionTraceRecorder(
                        self._trace_directory,
                        project_version=project_version,
                        project_channel="2D",
                        build_id=source_digest,
                    )
                    filename = trace.start()
                    self.motion_trace = trace
                    if manual and not self.trace_checkbox.isChecked():
                        self.trace_checkbox.blockSignals(True)
                        self.trace_checkbox.setChecked(True)
                        self.trace_checkbox.blockSignals(False)
                    bend_graph_data = None
                    if getattr(self.session, "fold_state", None) is not None:
                        fs = self.session.fold_state
                        bend_graph_data = {
                            bid: {
                                "fixed_panel": getattr(bend, "fixed_panel", None),
                                "moving_panel": getattr(bend, "moving_panel", None),
                                "angle_deg": getattr(bend, "angle_deg", None),
                            }
                            for bid, bend in getattr(fs, "bends", {}).items()
                        }
                    trace.event(
                        "RECORDING_STARTED",
                        active_bend=str(self.session.active_bend_id),
                        order=list(self.session.bend_order),
                        part_label=str(self.session.part_label),
                        mode=self.execution_mode.value,
                        bends_topology=bend_graph_data,
                    )
                    # Runtime evidence: distinguishes actual executed Python
                    # module from a checkout path, without guessing from the
                    # FreeCAD menu or the user-selected git branch.
                    trace.event(
                        "RUNTIME_MODULE",
                        module_path=str(module_path),
                        sha256=source_digest,
                        python_version=sys.version.split()[0],
                        mode=self.execution_mode.value,
                        project_version=project_version,
                        project_channel="2D",
                        build_id=source_digest,
                    )
                    # Requirement 10: Log versions and SHAs of kinematic modules
                    import smbs_core.kinematics2d.press_cycle as _mod_pc
                    import smbs_core.kinematics2d.workpiece_placement_3d as _mod_wp3d
                    import smbs_core.kinematics2d.workpiece_transfer_3d as _mod_wt3d
                    import smbs_core.kinematics2d.machine_section_3d as _mod_ms3d

                    def _module_digest(mod):
                        try:
                            return hashlib.sha256(Path(mod.__file__).resolve().read_bytes()).hexdigest()
                        except Exception as e:
                            return f"ERROR: {e}"

                    trace.event(
                        "KINEMATICS_MODULES_INFO",
                        press_cycle_sha=_module_digest(_mod_pc),
                        workpiece_placement_3d_sha=_module_digest(_mod_wp3d),
                        workpiece_transfer_3d_sha=_module_digest(_mod_wt3d),
                        machine_section_3d_sha=_module_digest(_mod_ms3d),
                        collision_validated=False,
                        support_validated=False,
                    )
                    # Make the directory and its first valid JSONL record
                    # visible immediately, not only after buffered frames.
                    trace.flush()
                    self.trace_status.setText(
                        "Версия: {} (2D) | сборка: {}\n"
                        "Запись: {}\nФайл: {}".format(
                            project_version, source_digest[:12],
                            self._trace_directory.resolve(), filename
                        )
                    )
                    _log("Motion trace START: {}".format(filename))
                except Exception as exc:
                    message = "Не удалось создать JSONL в {}: {}".format(
                        self._trace_directory, exc
                    )
                    _warn(message)
                    self.trace_status.setText(message)
                    self.status.setText(message)
                    self.motion_trace = None

            def _finish_motion_trace(self, reason="MANUAL_STOP"):
                # Reset is a state change, not a request to discard logs.
                # Enforce the recording contract at the finalization boundary,
                # even if another controller/GUI path requests reason=RESET.
                if str(reason) == "RESET":
                    self._trace_event(
                        "RESET_TRACE_RETAINED",
                        caller="".join(traceback.format_stack(limit=5)[-3:-1]),
                    )
                    return
                trace = self.motion_trace
                if trace is None or not trace.is_open:
                    self.trace_status.setText(
                        "Нет активной записи. Каталог: {}".format(
                            self._trace_directory.resolve()
                        )
                    )
                    return
                try:
                    # Record exactly which code path requested finalization.
                    # Essential when Qt signal wiring re-enters an old callback.
                    trace.event(
                        "RECORDING_STOP_REQUEST",
                        reason=str(reason),
                        caller="".join(
                            traceback.format_stack(limit=5)[-3:-1]
                        ),
                    )
                    summary = trace.stop(str(reason))
                    self.trace_status.setText(
                        "Версия: {} ({}), сборка: {}\n"
                        "Запись завершена.\nJSONL: {}\nJSON: {}".format(
                            trace.project_version, trace.project_channel,
                            trace.build_id[:12], trace.path, summary
                        )
                    )
                    _log("Motion trace SUMMARY: {}".format(summary))
                    if reason in ("USER_STOP", "PLAYBACK_COMPLETED",
                                  "WORKSPACE_CLOSED", "RECORDING_DISABLED",
                                  "KINEMATIC_POSE_HALT"):
                        self.trace_checkbox.blockSignals(True)
                        self.trace_checkbox.setChecked(False)
                        self.trace_checkbox.blockSignals(False)
                except Exception as exc:
                    message = "Не удалось создать отчёт JSON: {}".format(exc)
                    _warn(message)
                    self.trace_status.setText(message)
                    self.status.setText(message)

            def _trace_event(self, name, **fields):
                if self.motion_trace is not None and self.motion_trace.is_open:
                    try:
                        self.motion_trace.event(name, **fields)
                    except Exception as exc:
                        _warn("Motion trace event failed: {}".format(exc))

            def _trace_drawn_frame(self, sec, pose, warning, pc):
                trace = self.motion_trace
                if trace is None or not trace.is_open or self.session is None:
                    return
                try:
                    frame = make_frame_record(
                        session=self.session,
                        press_cycle=pc,
                        execution_mode=self.execution_mode,
                        section=sec,
                        pose=pose,
                        pose_warning=warning,
                        tooling=self.tooling_setup,
                        scene_rect=self.scene.sceneRect(),
                        dt_s=self._trace_frame_dt,
                    )
                    trace.record(frame)
                    self._trace_frame_dt = None
                except Exception as exc:
                    _warn("Motion trace frame failed: {}".format(exc))
                    self.trace_status.setText("Ошибка трассы: {}".format(exc))
                    self._finish_motion_trace("TRACE_FRAME_ERROR")

            def _on_view_changed(self, index):
                if index == 1:
                    self._update_3d_preview(force=True)

            def _update_3d_preview(self, force=False):
                """Live whole-workpiece isometric 3D projection, read-only.

                Consumes the same FoldState angles and machine pose as 2.5D.
                No Part.Shape changes, duplicate bend simulation or OCC booleans.
                """
                # Keep the existing single playback timer; cap redraw cost
                # of a complex whole-part mesh to about 12 frames/second.
                now = time.perf_counter()
                if not force and self._last_3d_frame_at >= 0.0:
                    if now - self._last_3d_frame_at < 1.0 / 12.0:
                        return
                self._last_3d_frame_at = now
                scene = self._preview_3d_scene
                scene.clear()
                if self.session is None:
                    scene.addSimpleText("Загрузите листовую деталь в PressBrakeSim")
                    return
                try:
                    # Fixed isometric assembly view: only FoldState bends.
                    # No machine-space pose, negative-bend flip or tooling.
                    pose = WorkpiecePose2D()
                    faces = build_sheet_preview_faces(
                        self.session.fold_state,
                        self.session.panel_vertices_xy,
                        self.session.controller.canonical_order[0],
                        pose,
                        segments=12,
                    )
                except (PreviewMeshError, WorkpiecePoseError, KinematicsError) as exc:
                    _warn("3D whole-sheet preview unavailable: {}".format(exc))
                    scene.addSimpleText("3D: {}".format(str(exc)))
                    return

                # Camera: machine s (horizontal), depth (bend axis), z (vertical).
                # This is a projected 3D surface viewer, not an OCC solid viewport.
                def project(p):
                    s, depth, z = p
                    return QtCore.QPointF(
                        0.87 * s + 0.48 * depth,
                        -0.83 * z + 0.36 * depth - 0.10 * s,
                    )

                palette = {
                    "panel_top": ("#8bb9d4", "#37647e"),
                    "panel_bottom": ("#718b9c", "#37647e"),
                    "panel_edge": ("#587b90", "#37536a"),
                    "zone_inner": ("#eac77b", "#9a7134"),
                    "zone_outer": ("#e0b967", "#9a7134"),
                    "zone_end": ("#bd9c65", "#9a7134"),
                }
                # Stable approximate painter ordering: view-depth, then height.
                ordering = sorted(
                    faces,
                    key=lambda face: (
                        sum(pt[1] for pt in face.vertices)/len(face.vertices),
                        sum(pt[2] for pt in face.vertices)/len(face.vertices),
                    ),
                )
                for face in ordering:
                    poly = QtGui.QPolygonF([project(pt) for pt in face.vertices])
                    if len(poly) < 3:
                        continue
                    fill_hex, edge_hex = palette.get(face.kind, ("#91a5b3", "#4c6474"))
                    pen = QtGui.QPen(QtGui.QColor(edge_hex), 0.65)
                    pen.setCosmetic(True)
                    brush = QtGui.QBrush(QtGui.QColor(fill_hex))
                    item = scene.addPolygon(poly, pen, brush)
                    item.setToolTip("{}: {}".format(face.kind, face.identifier))

                label = scene.addSimpleText(
                    "3D FULL PART | {} | FIXED ISOMETRIC | {} | KINEMATIC_PREVIEW".format(
                        self.session.active_bend_id,
                        self.execution_mode.value,
                    )
                )
                label.setBrush(QtGui.QBrush(QtGui.QColor("#536b7f")))
                sheet_points = [project(v) for f in faces for v in f.vertices]
                min_x = min(p.x() for p in sheet_points)
                min_y = min(p.y() for p in sheet_points)
                max_x = max(p.x() for p in sheet_points)
                max_y = max(p.y() for p in sheet_points)
                label.setPos(min_x, min_y - 25)
                pad = max(10.0, 0.08 * max(max_x-min_x, max_y-min_y))
                if self._fixed_3d_view_rect is None:
                    # Stable viewing frame across positive/negative operations:
                    # the sheet deforms, but the isometric camera does not pan.
                    width = max_x - min_x
                    height = max_y - min_y
                    extra = max(25.0, 0.65 * max(width, height))
                    self._fixed_3d_view_rect = QtCore.QRectF(
                        min_x - extra, min_y - extra - 25,
                        width + 2 * extra, height + 2 * extra + 25,
                    )
                    scene.setSceneRect(self._fixed_3d_view_rect)
                    self._preview_3d.fitInView(
                        self._fixed_3d_view_rect, QtCore.Qt.KeepAspectRatio
                    )
                else:
                    scene.setSceneRect(self._fixed_3d_view_rect)

            def unavailable(self):
                pass  # Disabled controls for Phase 5B/6 features.

            def _auto_select_default_tooling(self):
                """Auto-select verified default tooling (puancon / matrica) if none selected."""
                if self.tooling_setup.punch is not None and self.tooling_setup.die is not None:
                    return
                try:
                    tools = self.tool_library.list_tools()
                    # First priority: verified baseline tools "puancon" and "matrica"
                    for t in tools:
                        if t.validation_status != ValidationStatus.INVALID:
                            if self.tooling_setup.punch is None and t.kind == ToolKind.PUNCH:
                                if t.tool_id.lower() == "puancon":
                                    self.tooling_setup.select_punch(t)
                            if self.tooling_setup.die is None and t.kind == ToolKind.DIE:
                                if t.tool_id.lower() == "matrica":
                                    self.tooling_setup.select_die(t)
                    # Second priority: any other matching punch / die
                    for t in tools:
                        if t.validation_status != ValidationStatus.INVALID:
                            if self.tooling_setup.punch is None and t.kind == ToolKind.PUNCH:
                                if "punch" in t.tool_id.lower():
                                    self.tooling_setup.select_punch(t)
                            if self.tooling_setup.die is None and t.kind == ToolKind.DIE:
                                if "die" in t.tool_id.lower():
                                    self.tooling_setup.select_die(t)
                    if self.tooling_setup.punch is None:
                        for t in tools:
                            if t.kind == ToolKind.PUNCH and t.validation_status != ValidationStatus.INVALID:
                                self.tooling_setup.select_punch(t)
                                break
                    if self.tooling_setup.die is None:
                        for t in tools:
                            if t.kind == ToolKind.DIE and t.validation_status != ValidationStatus.INVALID:
                                self.tooling_setup.select_die(t)
                                break
                    self._update_tooling_status_label()
                except Exception as exc:
                    _warn(f"Failed to auto-select tooling: {exc}")

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

            def _physical_pose_signature(self):
                """Identity of the actual pose and selected tool set, not a GUI frame number."""
                if self.session is None:
                    return None
                fs = self.session.fold_state
                pc = self.press_cycle
                return (
                    id(fs),
                    self.session.active_bend_id,
                    tuple(sorted((str(k), float(v)) for k, v in fs.angles.items())),
                    id(self.tooling_setup.punch),
                    id(self.tooling_setup.die),
                    getattr(self.tooling_setup, "aligned", False),
                    bool(getattr(self.tooling_setup, "match_active_bend_length", False)),
                    round(float(getattr(self.tooling_setup, "active_bend_length_mm", 0.0) or 0.0), 4),
                    bool(getattr(self.tooling_setup, "allow_end_zone_collision", False)),
                    round(float(getattr(self.tooling_setup, "end_zone_length_mm", 0.0) or 0.0), 4),
                    getattr(getattr(self.tooling_setup, "punch_machine_transform", None), "to_matrix_tuple", lambda: ())(),
                    getattr(getattr(self.tooling_setup, "die_machine_transform", None), "to_matrix_tuple", lambda: ())(),
                    (getattr(getattr(pc, "phase", None), "value", getattr(pc, "phase", "")) if pc is not None else ""),
                    (float(getattr(pc, "ram_z_mm", 0.0) or 0.0) if pc is not None and getattr(pc, "ram_z_mm", None) is not None else None),
                    (float(getattr(pc, "transfer_progress", 0.0) or 0.0) if pc is not None and getattr(pc, "transfer_progress", None) is not None else None),
                )

            def _active_bend_length_mm(self) -> float:
                if self.session is None or getattr(self.session, "fold_state", None) is None:
                    return 0.0
                try:
                    bend = self.session.fold_state.bends[self.session.active_bend_id]
                    axis = getattr(bend, "axis_xyz", None)
                    if axis is not None:
                        return float(math.dist(axis[0], axis[1]))
                except Exception:
                    pass
                return 0.0

            def _on_virtual_tool_length_toggled(self, enabled: bool):
                bend_len = self._active_bend_length_mm()
                if hasattr(self.tooling_setup, "set_match_active_bend_length"):
                    self.tooling_setup.set_match_active_bend_length(bool(enabled), bend_len)
                if self.press_cycle is not None and hasattr(self.press_cycle, "set_match_active_bend_length"):
                    self.press_cycle.set_match_active_bend_length(bool(enabled))
                self._update_tooling_status_label()
                self.draw()

            def _on_end_zone_length_changed(self, val: float):
                try:
                    fval = max(0.0, float(val))
                except (TypeError, ValueError):
                    return
                self.tooling_setup.end_zone_length_mm = fval
                self._update_tooling_status_label()

            def _on_allow_end_zone_toggled(self, enabled: bool):
                self.tooling_setup.allow_end_zone_collision = bool(enabled)
                self._update_tooling_status_label()

            def _prompt_end_zone_conditional_dialog(self, bend_id: str, ez_analysis) -> bool:
                """Show Russian confirmation dialog when an END_ZONE_ONLY punch collision is detected.
                Returns True if operator chooses 'Продолжить с предупреждением', False if 'Отменить'.
                """
                side_str = str(getattr(ez_analysis, "affected_side", "UNKNOWN"))
                vol_mm3 = float(getattr(ez_analysis, "total_overlap_volume_mm3", 0.0))
                ez_mm = float(getattr(ez_analysis, "end_zone_length_mm", self.tooling_setup.end_zone_length_mm))

                self._highlighted_end_zone_info = {
                    "bend_id": str(bend_id),
                    "affected_side": side_str,
                    "overlap_volume_mm3": vol_mm3,
                    "end_zone_length_mm": ez_mm,
                }
                self.draw()
                QtWidgets.QApplication.processEvents()

                custom_handler = getattr(self, "_end_zone_dialog_handler", None)
                if callable(custom_handler):
                    return bool(custom_handler(bend_id, ez_analysis))

                msg_box = QtWidgets.QMessageBox(self)
                msg_box.setIcon(QtWidgets.QMessageBox.Warning)
                msg_box.setWindowTitle("Концевое пересечение с пуансоном (CONDITIONAL)")
                msg_box.setText(
                    "Обнаружено пересечение детали с концевой зоной пуансона.\n"
                    "Для этой операции может потребоваться пуансон с вырезом на конце.\n"
                    "Подходящая геометрия такого инструмента ещё не проверена.\n"
                    "Продолжить проверку в условном режиме?"
                )
                msg_box.setInformativeText(
                    "Операция/гиб: {}\n"
                    "Сторона пуансона: {}\n"
                    "Объём пересечения: {:.4f} мм³\n"
                    "Концевая зона пуансона: {:.1f} мм".format(
                        bend_id, side_str, vol_mm3, ez_mm
                    )
                )
                btn_continue = msg_box.addButton(
                    "Продолжить с предупреждением", QtWidgets.QMessageBox.AcceptRole
                )
                msg_box.addButton("Отменить", QtWidgets.QMessageBox.RejectRole)
                msg_box.exec()
                return msg_box.clickedButton() == btn_continue

            def check_current_physical_pose(self):
                """Read-only current-pose check; never modifies FoldState or GUI animation."""
                if self.session is None:
                    self.physical_check_result.setText("UNVERIFIED: деталь не загружена")
                    return
                if self.timer.isActive() or self.session.playback_status == PlaybackStatus.PLAYING:
                    self.physical_check_result.setText(
                        "UNVERIFIED: сначала Pause; проверяется только неподвижный кадр."
                    )
                    return
                self.physical_check_btn.setEnabled(False)
                self._highlighted_end_zone_info = None
                try:
                    from smbs_core.kinematics2d.physical_validation_25d import (
                        PhysicalEvidence,
                        check_current_25d_pose,
                    )
                    self._sync_tooling_to_active_bend()
                    pc = self._ensure_press_cycle_controller()
                    ez_len = float(getattr(self.tooling_setup, "end_zone_length_mm", 0.0) or 0.0)
                    allow_ez = bool(getattr(self.tooling_setup, "allow_end_zone_collision", False))
                    match_len = bool(getattr(self.tooling_setup, "match_active_bend_length", False))
                    report = check_current_25d_pose(
                        self.session,
                        pc,
                        self.tooling_setup,
                        native_3d=True,
                        match_active_bend_length=match_len,
                        allow_end_zone_collision=allow_ez,
                        end_zone_length_mm=ez_len,
                        classify_end_zone_length_mm=ez_len if ez_len > 0.0 else 10.0,
                    )
                    ez = getattr(report, "end_zone_analysis", None)
                    if (
                        report.status is PhysicalEvidence.COLLISION
                        and not allow_ez
                        and ez is not None
                        and getattr(getattr(ez, "classification", None), "value", "") == "END_ZONE_ONLY"
                        and report.section_status != "INTERFERENCE"
                    ):
                        active_id = str(self.session.active_bend_id)
                        if self._prompt_end_zone_conditional_dialog(active_id, ez):
                            eff_ez = ez_len if ez_len > 0.0 else 10.0
                            report = check_current_25d_pose(
                                self.session,
                                pc,
                                self.tooling_setup,
                                native_3d=True,
                                match_active_bend_length=self.tooling_setup.match_active_bend_length,
                                allow_end_zone_collision=True,
                                end_zone_length_mm=eff_ez,
                                classify_end_zone_length_mm=eff_ez,
                            )
                        else:
                            self._highlighted_end_zone_info = None
                            self.draw()

                    if report.status is PhysicalEvidence.CONDITIONAL:
                        active_id = str(self.session.active_bend_id)
                        self._conditional_bends_status[active_id] = (
                            "CONDITIONAL (требуется пуансон с вырезом на конце; геометрия не проверена)"
                        )
                        if ez is not None:
                            self._highlighted_end_zone_info = {
                                "bend_id": active_id,
                                "affected_side": str(getattr(ez, "affected_side", "UNKNOWN")),
                                "overlap_volume_mm3": float(getattr(ez, "total_overlap_volume_mm3", 0.0)),
                                "end_zone_length_mm": float(getattr(ez, "end_zone_length_mm", ez_len)),
                            }
                        self._populate_operations()
                        self.draw()

                    detail = "{} | 2D={} | 3D={} | {} | scope={}".format(
                        report.status.value, report.section_status,
                        report.native_status, report.reason,
                        ",".join(report.checked_scope),
                    )
                    self.physical_check_result.setText(detail)
                    self._physical_check_signature = self._physical_pose_signature()
                    self._trace_event(
                        "PHYSICAL_POSE_CHECK",
                        status=report.status.value,
                        section_status=report.section_status,
                        native_status=report.native_status,
                        reason=report.reason,
                        checked_scope=list(report.checked_scope),
                        fully_qualified=report.is_fully_qualified,
                        is_conditional=bool(getattr(report, "is_conditional", False)),
                        tool_length_info=getattr(report, "tool_length_info", None),
                    )
                    _log("Physical pose check: " + detail)
                except Exception as exc:
                    self.physical_check_result.setText(
                        "UNVERIFIED: ошибка физической проверки: {}".format(exc)
                    )
                    _error("Physical pose check exception: " + traceback.format_exc())
                finally:
                    self.physical_check_btn.setEnabled(True)

            def check_active_bend_physical_trajectory(self):
                """D6.3.4 adaptive evaluation on demand; no timer-side OCC calls."""
                if self.session is None:
                    self.physical_check_result.setText(
                        "UNVERIFIED: деталь не загружена"
                    )
                    return
                if self.timer.isActive() or self.session.playback_status == PlaybackStatus.PLAYING:
                    self.physical_check_result.setText(
                        "UNVERIFIED: Pause перед проверкой всей траектории."
                    )
                    return
                self.full_physical_check_btn.setEnabled(False)
                self.physical_check_btn.setEnabled(False)
                self._highlighted_end_zone_info = None
                self.physical_check_result.setText(
                    "Проверяется нативная 3D-траектория D6.3.4; "
                    "состояние FreeCAD будет сохранено."
                )
                QtWidgets.QApplication.processEvents()
                try:
                    from smbs_core.kinematics2d.physical_validation_25d import (
                        PhysicalEvidence,
                        check_active_25d_bend_trajectory,
                    )
                    self._sync_tooling_to_active_bend()
                    ez_len = float(getattr(self.tooling_setup, "end_zone_length_mm", 0.0) or 0.0)
                    allow_ez = bool(getattr(self.tooling_setup, "allow_end_zone_collision", False))
                    match_len = bool(getattr(self.tooling_setup, "match_active_bend_length", False))
                    report = check_active_25d_bend_trajectory(
                        self.session,
                        self.tooling_setup,
                        match_active_bend_length=match_len,
                        allow_end_zone_collision=allow_ez,
                        end_zone_length_mm=ez_len,
                        classify_end_zone_length_mm=ez_len if ez_len > 0.0 else 10.0,
                    )
                    ez = getattr(report, "end_zone_analysis", None)
                    if (
                        report.status is PhysicalEvidence.COLLISION
                        and not allow_ez
                        and ez is not None
                        and getattr(getattr(ez, "classification", None), "value", "") == "END_ZONE_ONLY"
                    ):
                        active_id = str(self.session.active_bend_id)
                        if self._prompt_end_zone_conditional_dialog(active_id, ez):
                            eff_ez = ez_len if ez_len > 0.0 else 10.0
                            report = check_active_25d_bend_trajectory(
                                self.session,
                                self.tooling_setup,
                                match_active_bend_length=self.tooling_setup.match_active_bend_length,
                                allow_end_zone_collision=True,
                                end_zone_length_mm=eff_ez,
                                classify_end_zone_length_mm=eff_ez,
                            )
                        else:
                            self._highlighted_end_zone_info = None
                            self.draw()

                    if report.status is PhysicalEvidence.CONDITIONAL:
                        active_id = str(self.session.active_bend_id)
                        self._conditional_bends_status[active_id] = (
                            "CONDITIONAL (требуется пуансон с вырезом на конце; геометрия не проверена)"
                        )
                        if ez is not None:
                            self._highlighted_end_zone_info = {
                                "bend_id": active_id,
                                "affected_side": str(getattr(ez, "affected_side", "UNKNOWN")),
                                "overlap_volume_mm3": float(getattr(ez, "total_overlap_volume_mm3", 0.0)),
                                "end_zone_length_mm": float(getattr(ez, "end_zone_length_mm", ez_len)),
                            }
                        self._populate_operations()
                        self.draw()

                    detail = "D6.3.4 {} | {} | {}".format(
                        report.status.value, report.native_status, report.reason,
                    )
                    self.physical_check_result.setText(detail)
                    self._physical_check_signature = self._physical_pose_signature()
                    self._trace_event(
                        "D634_BEND_TRAJECTORY_CHECK",
                        status=report.status.value,
                        native_status=report.native_status,
                        reason=report.reason,
                        checked_scope=list(report.checked_scope),
                        fully_qualified=report.is_fully_qualified,
                        is_conditional=bool(getattr(report, "is_conditional", False)),
                        tool_length_info=getattr(report, "tool_length_info", None),
                    )
                    _log("D6.3.4 trajectory: " + detail)
                except Exception as exc:
                    # A failed FoldState restoration is a hard stop, not a
                    # warning the user can click through while still playing.
                    self._machine_pose_fault = (
                        "D634_VALIDATION_HALTED: " + str(exc)
                    )
                    self._stop_timer()
                    self.physical_check_result.setText(
                        "UNVERIFIED: " + self._machine_pose_fault
                    )
                    _error(traceback.format_exc())
                finally:
                    self.full_physical_check_btn.setEnabled(True)
                    self.physical_check_btn.setEnabled(True)

            def calculate_planar_candidates(self):
                """Show ranked sampled-2D candidate sequences, never mark them physically verified."""
                if self.session is None:
                    self.status.setText("Сначала загрузите деталь SheetMetal.")
                    return
                if self.timer.isActive() or self.session.playback_status == PlaybackStatus.PLAYING:
                    self.status.setText("Остановите воспроизведение перед 2D-поиском.")
                    return
                self.planar_calculate.setEnabled(False)
                self.calculate.setEnabled(False)
                self.status.setText("Выполняется предварительный поиск последовательностей в 2D...")
                QtWidgets.QApplication.processEvents()
                try:
                    from smbs_core.kinematics2d.sequence_search_2d import (
                        search_bend_sequences_2d,
                    )
                    # A 2D preview with virtual axial tool length is permitted
                    # ONLY for exploration, never treated as production safety
                    # evidence. Native multi-sections cannot be virtually extended.
                    has_sections = bool(
                        getattr(self.tooling_setup, "punch_section_offsets_xm", ())
                        or getattr(self.tooling_setup, "die_section_offsets_xm", ())
                    )
                    report = search_bend_sequences_2d(
                        self.session, self.tooling_setup,
                        # Quick GUI mode: find the first complete 2D preview.
                        # Full ranking remains in the standalone search tool.
                        # This does not qualify 3D physical feasibility.
                        max_solutions=1,
                        max_complete_candidates=1,
                        max_search_time_s=30.0,
                        max_nodes=5000,
                        max_evaluations=10000,
                        match_active_bend_length=not has_sections,
                    )
                    self.last_planar_search_report = report
                    self._trace_event(
                        "PLANAR_2D_SEARCH_COMPLETED",
                        status=report.status.value,
                        evaluated_steps=report.evaluated_steps,
                        visited_nodes=report.visited_nodes,
                        candidate_count=len(report.candidates),
                        limit_reason=report.limit_reason,
                    )
                    self._show_planar_candidates_dialog(report)
                except Exception as exc:
                    msg = "2D поиск не выполнен: {}".format(exc)
                    self.status.setText(msg)
                    _error("Planar search exception: " + traceback.format_exc())
                finally:
                    self._sync_playback_buttons()

            def _show_planar_candidates_dialog(self, report):
                """Choose a preview-only bend order; never present it as 3D approved."""
                dlg = QtWidgets.QDialog(self)
                dlg.setWindowTitle("SMBS — предварительные 2D варианты")
                dlg.resize(700, 430)
                layout = QtWidgets.QVBoxLayout(dlg)
                heading = QtWidgets.QLabel(
                    "ТОЛЬКО 2D-ПРОСМОТР. Проверка 3D-коллизий и физическая "
                    "допустимость не подтверждены. Для производственного "
                    "порядка требуется отдельная проверка."
                )
                heading.setWordWrap(True)
                layout.addWidget(heading)
                details = QtWidgets.QLabel(
                    "Статус: {} | вариантов: {} | 2D проверок: {} | причина остановки: {}".format(
                        report.status.value, len(report.candidates),
                        report.evaluated_steps, report.limit_reason or "нет",
                    )
                )
                details.setWordWrap(True)
                layout.addWidget(details)
                depth_info = QtWidgets.QLabel(
                    "Максимальная глубина: {}/{} | путь: {}\n"
                    "Классификация проверок: {}".format(
                        report.max_depth_reached,
                        len(self.session.bend_order) if self.session is not None else "?",
                        " → ".join(report.deepest_prefix) or "(начало)",
                        ", ".join("{}: {}".format(name, count)
                                  for name, count in report.rejection_counts) or "нет",
                    )
                )
                depth_info.setWordWrap(True)
                layout.addWidget(depth_info)
                options = QtWidgets.QListWidget(dlg)
                for index, candidate in enumerate(report.candidates):
                    suffix = (" | стоимость {:.2f}".format(candidate.cost)
                              if candidate.cost_complete else " | стоимость не подтверждена")
                    options.addItem(
                        "{}. {} | {}{}".format(
                            index + 1, " → ".join(candidate.sequence), candidate.evidence, suffix
                        )
                    )
                if report.candidates:
                    options.setCurrentRow(0)
                layout.addWidget(options)
                controls = QtWidgets.QHBoxLayout()
                use = QtWidgets.QPushButton("Показать порядок в симуляции")
                use.setEnabled(bool(report.candidates))
                export_btn = QtWidgets.QPushButton("Сохранить диагностику JSON")
                close = QtWidgets.QPushButton("Закрыть")
                controls.addWidget(use)
                controls.addWidget(export_btn)
                controls.addWidget(close)
                layout.addLayout(controls)

                def on_preview():
                    row = options.currentRow()
                    if row < 0 or row >= len(report.candidates):
                        return
                    candidate = report.candidates[row]
                    try:
                        self._stop_timer()
                        self.session.reset_all_bends()
                        self.session.set_bend_order(list(candidate.sequence))
                        self._preview_only_planar_order = tuple(candidate.sequence)
                        # A previous strict validation does not apply to a
                        # newly selected, independently ranked planar order.
                        self.last_sequence_search_report = None
                        self._conditional_bends_status.clear()
                        self._populate_operations()
                        self._configure_for_active_bend()
                        self._sync_tooling_to_active_bend()
                        self.draw()
                        self.status.setText(
                            "2D-ПРОСМОТР (НЕ ПРОВЕРЕНО В 3D): "
                            + " → ".join(candidate.sequence)
                        )
                        self.physical_check_result.setText(
                            "UNVERIFIED: порядок выбран из 2D-кандидатов. "
                            "Коллизии станка и всего хода не проверены."
                        )
                        self._trace_event(
                            "PLANAR_2D_PREVIEW_ORDER_SELECTED",
                            sequence=list(candidate.sequence),
                            evidence=candidate.evidence,
                            physical_status="UNVERIFIED",
                        )
                        dlg.accept()
                    except Exception as exc:
                        self.status.setText("Нельзя показать 2D порядок: " + str(exc))
                        _error("Planar preview exception: " + traceback.format_exc())

                def on_export():
                    import json
                    destination, _ = QtWidgets.QFileDialog.getSaveFileName(
                        dlg, "Сохранить 2D диагностику",
                        str(Path.home() / "Desktop" / "smbs_2d_search_diagnostics.json"),
                        "JSON (*.json)",
                    )
                    if not destination:
                        return
                    try:
                        Path(destination).write_text(
                            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        self.status.setText("2D диагностика сохранена: " + destination)
                    except Exception as exc:
                        self.status.setText("Ошибка сохранения диагностики: " + str(exc))

                use.clicked.connect(on_preview)
                export_btn.clicked.connect(on_export)
                close.clicked.connect(dlg.reject)
                dlg.exec()

            def calculate_sequence(self):
                """Run automatic 2.5D + D6 physical sequence search with backtracking and forward replay."""
                if self.session is None:
                    self.status.setText("UNVERIFIED: сначала загрузите листовую деталь.")
                    return
                if self.timer.isActive() or self.session.playback_status == PlaybackStatus.PLAYING:
                    self.status.setText("Сначала остановите воспроизведение (Pause / Reset) перед расчётом последовательности.")
                    return
                self.calculate.setEnabled(False)
                self.physical_check_btn.setEnabled(False)
                self.full_physical_check_btn.setEnabled(False)
                self._highlighted_end_zone_info = None
                self.status.setText(
                    "Выполняется поиск последовательности гибки с физической проверкой 2.5D + D6 (backtracking)..."
                )
                self.physical_check_result.setText(
                    "Идёт поиск последовательности (строгий проход → условный при необходимости → прямой прогон с нуля)..."
                )
                QtWidgets.QApplication.processEvents()
                try:
                    from smbs_core.kinematics2d.sequence_search import (
                        SearchStatus,
                        search_bend_sequence_25d,
                    )
                    from smbs_core.kinematics2d.operation_cost import OperationWeights

                    def _search_progress(depth: int, total: int, partial_seq: Tuple[str, ...]) -> bool:
                        seq_str = " -> ".join(partial_seq) if partial_seq else "корень"
                        self.physical_check_result.setText(
                            f"Поиск последовательности (глубина {depth}/{total}): {seq_str}..."
                        )
                        QtWidgets.QApplication.processEvents()
                        return True

                    self._sync_tooling_to_active_bend()
                    pc = self._ensure_press_cycle_controller()
                    search_rep = search_bend_sequence_25d(
                        self.session,
                        pc,
                        self.tooling_setup,
                        match_active_bend_length=self.tooling_setup.match_active_bend_length,
                        allow_end_zone_collision=self.tooling_setup.allow_end_zone_collision,
                        end_zone_length_mm=self.tooling_setup.end_zone_length_mm,
                        weights=OperationWeights(),
                        progress_callback=_search_progress,
                    )
                    self.last_sequence_search_report = search_rep
                    self._preview_only_planar_order = None
                    self._conditional_bends_status.clear()
                    if search_rep.status is SearchStatus.CONDITIONAL_VALIDATED:
                        for bid in search_rep.conditional_bends:
                            self._conditional_bends_status[str(bid)] = (
                                "CONDITIONAL (требуется пуансон с вырезом на конце; геометрия не проверена)"
                            )
                    self._populate_operations()
                    self._configure_for_active_bend()
                    self._sync_tooling_to_active_bend()
                    self.draw()
                    self.status.setText(search_rep.message_ru)
                    cost_info = ""
                    if search_rep.plan_cost:
                        cost_val = search_rep.plan_cost.get("total", 0.0)
                        cost_info = f" | штраф={cost_val:.1f}"
                    self.physical_check_result.setText(
                        "Поиск последовательности: {} | {} | 3D-проверок={} (отсев A={}, B={}) | глубина={}/{}{}".format(
                            search_rep.status.value,
                            search_rep.overall_evidence.value,
                            search_rep.stage_c_native_evaluated,
                            search_rep.stage_a_pruned,
                            search_rep.stage_b_pruned,
                            search_rep.max_depth_reached,
                            search_rep.total_bends,
                            cost_info,
                        )
                    )
                    self._trace_event(
                        "SEQUENCE_SEARCH_COMPLETED",
                        status=search_rep.status.value,
                        overall_evidence=search_rep.overall_evidence.value,
                        found_sequence=list(search_rep.found_sequence) if search_rep.found_sequence else None,
                        conditional_bends=list(search_rep.conditional_bends),
                        states_visited=search_rep.states_visited,
                        step_evaluations=search_rep.step_evaluations,
                        stage_a_pruned=search_rep.stage_a_pruned,
                        stage_b_pruned=search_rep.stage_b_pruned,
                        stage_c_native_evaluated=search_rep.stage_c_native_evaluated,
                        plan_cost=search_rep.plan_cost,
                        elapsed_s=round(search_rep.elapsed_s, 3),
                    )
                    _log("Sequence search result: " + search_rep.message_ru)
                except Exception as exc:
                    msg = "UNVERIFIED: ошибка поиска последовательности: {}".format(exc)
                    self.status.setText(msg)
                    self.physical_check_result.setText(msg)
                    _error("Sequence search exception: " + traceback.format_exc())
                finally:
                    self.full_physical_check_btn.setEnabled(True)
                    self.physical_check_btn.setEnabled(True)
                    self._sync_playback_buttons()

            def _update_tooling_status_label(self):
                self.tooling_status.setText(self.tooling_setup.status_line())
                if (self._physical_check_signature is not None and
                        self._physical_check_signature != self._physical_pose_signature()):
                    self._physical_check_signature = None
                    self.physical_check_result.setText(
                        "УСТАРЕЛО: деталь, положение или оснастка изменились. "
                        "Проверьте текущую позу повторно."
                    )
                self._update_press_cycle_labels()

            def _update_press_cycle_labels(self):
                if self.press_cycle is not None:
                    phase_val = getattr(getattr(self.press_cycle, "phase", None), "value", "IDLE")
                    sim_val = getattr(getattr(self.press_cycle, "simulation_mode", None), "value", "NORMAL")
                    contact_val = getattr(getattr(self.press_cycle, "contact_kind", None), "value", "SURFACE")
                    ram_z = float(getattr(self.press_cycle, "ram_z_mm", 0.0) or 0.0)
                    ram_stroke = float(getattr(self.press_cycle, "ram_stroke_mm", 0.0) or 0.0)
                    self.cycle_phase_label.setText(
                        f"Cycle Phase: {phase_val} ({sim_val} | {contact_val})"
                    )
                    self.ram_position_label.setText(
                        f"Ram Position: {ram_z:+.2f} mm (Stroke: {ram_stroke:.2f} mm)"
                    )
                else:
                    sim_val = getattr(getattr(self.cycle_config, "simulation_mode", None), "value", "NORMAL")
                    ram_z = float(getattr(self.tooling_setup, "ram_z_mm", 0.0) or 0.0)
                    ram_stroke = float(getattr(self.tooling_setup, "ram_stroke_mm", 0.0) or 0.0)
                    self.cycle_phase_label.setText(
                        f"Cycle Phase: IDLE ({sim_val})"
                    )
                    self.ram_position_label.setText(
                        f"Ram Position: {ram_z:+.2f} mm (Stroke: {ram_stroke:.2f} mm)"
                    )

            def _ensure_press_cycle_controller(self) -> PressCycleController | None:
                if self.session is None:
                    self.press_cycle = None
                    return None
                if self.press_cycle is not None:
                    ctrl = getattr(self.session, "controller", None)
                    if ctrl is None or getattr(self.press_cycle, "sequence", None) is ctrl:
                        return self.press_cycle
                ctrl = getattr(self.session, "controller", None)
                if ctrl is None:
                    return self.press_cycle
                self.press_cycle = PressCycleController(
                    sequence_controller=ctrl,
                    tooling_setup=self.tooling_setup,
                    config=self.cycle_config,
                    cross_section_provider=lambda: (
                        self.session.cross_section(segments=16) if self.session is not None and hasattr(self.session, "cross_section") else None
                    ),
                    follow_sheet_contact_preview=True,
                    animate_workpiece_transfer=True,
                )
                self.press_cycle.set_execution_mode(self.execution_mode)
                return self.press_cycle

            def set_cycle_mode(self, mode: CycleExecutionMode | str) -> None:
                resolved = CycleExecutionMode(mode) if isinstance(mode, str) else mode
                if self.timer.isActive() or self.playing:
                    self._stop_timer()
                self.execution_mode = resolved
                self._machine_pose_fault = None
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
                bend_len = self._active_bend_length_mm()
                if hasattr(self.tooling_setup, "set_match_active_bend_length"):
                    self.tooling_setup.set_match_active_bend_length(
                        bool(getattr(self.tooling_setup, "match_active_bend_length", False)), bend_len
                    )
                if self.press_cycle is not None and hasattr(self.press_cycle, "on_tooling_changed"):
                    self.press_cycle.on_tooling_changed()
                elif (getattr(self.tooling_setup, "punch", None) is not None or getattr(self.tooling_setup, "die", None) is not None) and hasattr(self.session, "cross_section"):
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
                    self.calculate.setEnabled(False)
                    self.planar_calculate.setEnabled(False)
                    return
                st = self.session.playback_status
                self.playing = st == PlaybackStatus.PLAYING
                bend_order = getattr(self.session, "bend_order", [])
                self.calculate.setEnabled(not self.playing and len(bend_order) > 1)
                self.planar_calculate.setEnabled(not self.playing and len(bend_order) > 0)
                self.play_btn.setText(
                    "▶ Play All (станок)" if self.execution_mode == CycleExecutionMode.FULL_CYCLE
                    else "▶ Play All (геометрия)"
                )
                if st == PlaybackStatus.PLAYING:
                    self.pause_btn.setText("Ⅱ Pause")
                    self.pause_btn.setEnabled(True)
                elif st == PlaybackStatus.PAUSED:
                    self.pause_btn.setText("▶ Resume")
                    self.pause_btn.setEnabled(True)
                else:
                    self.pause_btn.setText("Ⅱ Pause")
                    self.pause_btn.setEnabled(False)

                can_reorder_fn = getattr(self.session, "can_reorder", None)
                can_reorder_val = can_reorder_fn() if callable(can_reorder_fn) else False
                can_edit_order = can_reorder_val and len(bend_order) > 1
                idx = getattr(self.session, "active_bend_index", 0)
                total = len(bend_order)
                canonical = getattr(self.session, "canonical_order", bend_order)
                self.move_up_btn.setEnabled(can_edit_order and idx > 0)
                self.move_down_btn.setEnabled(can_edit_order and 0 <= idx < total - 1)
                self.restore_order_btn.setEnabled(
                    can_edit_order and bend_order != canonical
                )

            def _set_playback_controls_enabled(self, enabled: bool):
                for w in (
                    self.reset_btn,
                    self.play_btn,
                    self.slider,
                    self.prev_step_btn,
                    self.next_step_btn,
                ):
                    w.setEnabled(enabled)
                self.fit_btn.setEnabled(
                    enabled or self.tooling_setup.punch is not None or self.tooling_setup.die is not None
                )
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

                self._finish_motion_trace("NEW_PART_LOADED")
                self._stop_timer()
                # 3D surface preview uses this session's FoldState directly;
                # never read or mutate the original source document again.
                self._fixed_3d_view_rect = None
                self._locked_25d_scene_rect = None
                self._last_valid_machine_section = None
                self._last_valid_machine_pose = None
                self._last_valid_machine_bend = None
                self._machine_pose_fault = None
                self._last_3d_frame_at = -1.0
                self.session = new_session
                self.last_planar_search_report = None
                self._preview_only_planar_order = None
                self.last_sequence_search_report = None
                self._ensure_press_cycle_controller()
                self._sync_tooling_to_active_bend()
                self._populate_operations()
                self._configure_for_active_bend()
                self._set_playback_controls_enabled(True)
                self._update_tooling_status_label()
                self.status.setText(msg)
                self.check_result.setText(msg)
                self.draw()
                if self._viewer_tabs.currentIndex() == 1:
                    self._update_3d_preview()
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
                if b.bend_id in self._conditional_bends_status:
                    status_str += " [CONDITIONAL]"
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
                self._machine_pose_fault = None
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
                self._machine_pose_fault = None
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

            def reset(self):
                self._machine_pose_fault = None
                self._last_valid_machine_section = None
                self._last_valid_machine_pose = None
                self._last_valid_machine_bend = None
                # Reset is preparation for a new playback, not an explicit
                # request to stop recording. Keep the JSONL stream open so
                # the following Play All and its FRAME events are captured.
                self._trace_event("RESET")
                self._stop_timer()
                self._locked_25d_scene_rect = None
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
                if (self._machine_pose_fault and
                        self.execution_mode == CycleExecutionMode.FULL_CYCLE):
                    self.status.setText(
                        "Full Cycle остановлен из-за геометрии: {}. "
                        "Переключитесь на Bend Only для автоматической "
                        "анимации всех гибов или Reset для повторной проверки.".format(
                            self._machine_pose_fault
                        )
                    )
                    return
                self._last_valid_machine_section = None
                self._last_valid_machine_pose = None
                self._last_valid_machine_bend = None
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
                except (SequenceError, PressCycleError) as exc:
                    # Fail closed: never start an automatic timer after an
                    # invalid sequence or transfer/machine state.
                    if self.execution_mode == CycleExecutionMode.FULL_CYCLE:
                        self._machine_pose_fault = str(exc)
                    if self.press_cycle is not None:
                        self.press_cycle.pause()
                    else:
                        self.session.pause()
                    self._stop_timer()
                    self.status.setText(str(exc))
                    return
                self._configure_for_active_bend()
                self._update_tooling_status_label()
                self._start_motion_trace()
                self._locked_25d_scene_rect = QtCore.QRectF(self.scene.sceneRect())
                # Lock one spatial view for the full playback, including
                # TRANSFER and active-bend changes.
                self.playing = True
                self._last_tick_monotonic = time.monotonic()
                self._sync_playback_buttons()
                if not self.timer.isActive():
                    self.timer.start()
                self.draw()
                if self.session.playback_status == PlaybackStatus.PLAYING:
                    if not self.timer.isActive():
                        self.timer.start()
                    self.status.setText(
                        "AUTO PLAY: {} | гиб {} | таймер {} мс".format(
                            self.execution_mode.value,
                            self.session.active_bend_id,
                            self.timer.interval()
                        )
                    )
                else:
                    self.status.setText(
                        "AUTO PLAY не запущен: статус {} | {}".format(
                            self.session.playback_status.value,
                            self._machine_pose_fault or "проверьте состояние последовательности"
                        )
                    )
                _log("Play All started at bend {}".format(self.session.active_bend_id))

            def pause(self):
                if self.session is None:
                    return
                self._trace_event("USER_PAUSE")
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
                if self._machine_pose_fault and (
                    self.execution_mode == CycleExecutionMode.FULL_CYCLE
                ):
                    self.status.setText(
                        "Невозможно продолжить этот Full Cycle: {}. "
                        "Выберите Bend Only в настройках режима или Reset.".format(
                            self._machine_pose_fault
                        )
                    )
                    return
                self._trace_event("USER_RESUME")
                try:
                    pc = self._ensure_press_cycle_controller()
                    if pc is not None and self.execution_mode == CycleExecutionMode.FULL_CYCLE:
                        pc.resume()
                        self.session.active_bend_index = self.session.controller.active_index
                    else:
                        self.session.resume()
                        if pc is not None:
                            pc.phase = CyclePhase.BENDING
                except (SequenceError, PressCycleError) as exc:
                    # Fail closed: never start an automatic timer after an
                    # invalid sequence or transfer/machine state.
                    if self.execution_mode == CycleExecutionMode.FULL_CYCLE:
                        self._machine_pose_fault = str(exc)
                    if self.press_cycle is not None:
                        self.press_cycle.pause()
                    else:
                        self.session.pause()
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
                self._machine_pose_fault = None
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
                self._machine_pose_fault = None
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
                """One Qt tick drives exactly one sequencer/cycle advance."""
                if self.session is None:
                    self._stop_timer()
                    return
                if self.session.playback_status != PlaybackStatus.PLAYING:
                    self._stop_timer()
                    self.status.setText(
                        "AUTO PLAY остановлен: {}".format(
                            self.session.playback_status.value
                        )
                    )
                    return
                now = time.monotonic()
                dt = (
                    0.035 if self._last_tick_monotonic is None
                    else max(0.005, now - self._last_tick_monotonic)
                )
                self._last_tick_monotonic = now
                self._trace_frame_dt = dt

                try:
                    previous_bend = self.session.active_bend_index
                    pc = self._ensure_press_cycle_controller()
                    if (pc is not None and
                            self.execution_mode == CycleExecutionMode.FULL_CYCLE):
                        pc.advance_time(dt)
                        self.session.active_bend_index = (
                            self.session.controller.active_index
                        )
                    else:
                        # Bend Only is genuine TIMER-driven automatic playback,
                        # even if machine pose is unsupported for nonparallel bends.
                        self.session.advance_time(dt)
                        if (self.session.active_bend_index != previous_bend
                                and self.tooling_setup.aligned):
                            self._sync_tooling_to_active_bend()
                    self._configure_for_active_bend()
                    self._update_tooling_status_label()
                    self.draw()
                    if self._machine_pose_fault:
                        # draw() already paused, finalized the JSONL trace and
                        # displayed the exact geometric blocker.
                        return
                    if (self.execution_mode != CycleExecutionMode.FULL_CYCLE
                            and self.session.active_bend_index != previous_bend):
                        QtCore.QTimer.singleShot(0, self.fit)
                    if self.session.playback_status == PlaybackStatus.COMPLETED:
                        self._stop_timer()
                        self.draw()
                        self._finish_motion_trace("PLAYBACK_COMPLETED")
                        self.status.setText(
                            "AUTO PLAY завершён: {} | {} гибов".format(
                                self.execution_mode.value,
                                len(self.session.bend_order),
                            )
                        )
                        _log("Sequence playback completed")
                    else:
                        phase = (
                            pc.phase.value
                            if pc is not None and
                            self.execution_mode == CycleExecutionMode.FULL_CYCLE
                            else "BEND_ONLY"
                        )
                        self.status.setText(
                            "AUTO PLAY: {} | {} | гиб {} | угол {:+.1f}° | {} мс".format(
                                self.execution_mode.value,
                                phase,
                                self.session.active_bend_id,
                                self.session.current_angle_deg,
                                self.timer.interval(),
                            )
                        )
                except Exception as exc:
                    # A failed controller step must not leave a logically PLAYING
                    # FoldState after stopping the Qt timer. The last valid
                    # angles remain observable; resuming Full Cycle requires
                    # an explicit reset or mode change, never silent replay.
                    reason = "AUTO PLAY ERROR: {}".format(exc)
                    _error(reason + "\n" + traceback.format_exc())
                    if self.execution_mode == CycleExecutionMode.FULL_CYCLE:
                        self._machine_pose_fault = reason
                    try:
                        if self.press_cycle is not None:
                            self.press_cycle.pause()
                        else:
                            self.session.pause()
                    except Exception:
                        _error("Failed to pause controller after automatic playback error")
                    self._stop_timer()
                    self._trace_event("AUTOPLAY_ERROR", reason=reason)
                    self._finish_motion_trace("AUTOPLAY_ERROR")
                    self._sync_playback_buttons()
                    self.status.setText(
                        reason + " | Состояние зафиксировано; "
                        "Reset или смена режима для повторного запуска."
                    )

            def fit(self):
                if self.playing and self.execution_mode == CycleExecutionMode.FULL_CYCLE:
                    # User may pause first; no camera jumps during playback.
                    return
                if hasattr(self, "_viewer_tabs") and self._viewer_tabs.currentIndex() == 1:
                    rect = self._preview_3d_scene.sceneRect()
                    if not rect.isEmpty():
                        self._preview_3d.fitInView(rect, QtCore.Qt.KeepAspectRatio)
                    return
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
                    "unavailable_controls_disabled": (not self.calculate.isEnabled()) if not has_part else True,
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
                self._finish_motion_trace("WORKSPACE_CLOSED")
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
                    if (
                        self._highlighted_end_zone_info is not None
                        and self.session is not None
                        and str(self._highlighted_end_zone_info.get("bend_id")) == str(self.session.active_bend_id)
                    ):
                        punch_pen = QtGui.QPen(QtGui.QColor("#f39c12"), 3.0, QtCore.Qt.DashLine)
                        punch_pen.setCosmetic(True)
                        punch_brush = QtGui.QBrush(QtGui.QColor(243, 156, 18, 175))
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
                        if (
                            self._highlighted_end_zone_info is not None
                            and self.session is not None
                            and str(self._highlighted_end_zone_info.get("bend_id")) == str(self.session.active_bend_id)
                        ):
                            hz = self._highlighted_end_zone_info
                            badge_txt = self.scene.addSimpleText(
                                "КОНЦЕВАЯ ЗОНА ПУАНСОНА ({}) | {:.2f} мм³ | зона {:.1f} мм [CONDITIONAL]".format(
                                    hz.get("affected_side", "?"),
                                    float(hz.get("overlap_volume_mm3", 0.0)),
                                    float(hz.get("end_zone_length_mm", 0.0)),
                                )
                            )
                            badge_txt.setBrush(QtGui.QBrush(QtGui.QColor("#d35400")))
                            badge_txt.setPos(tp.x() - 60.0, tp.y() - 35.0)
                            badge_txt.setZValue(Z_LAYER_SHEET_NEUTRAL_AND_TANGENTS + 5.0)
                            badge_txt.setData(0, "end_zone_highlight")

            def _halt_unsupported_machine_pose(self, reason):
                """Stop unsafe 2.5D preview; retain last known machine picture.

                The FoldState is never rolled back: the display is explicitly
                the LAST VERIFIED FRAME, not a claim that current bend is valid.
                """
                reason = str(reason)
                if self._machine_pose_fault == reason:
                    return
                self._machine_pose_fault = reason
                self._trace_event(
                    "KINEMATIC_POSE_HALT",
                    bend=str(self.session.active_bend_id) if self.session else "",
                    reason=reason,
                )
                _warn("2.5D machine pose HALT: {}".format(reason))
                if self.press_cycle is not None:
                    self.press_cycle.pause()
                elif self.session is not None:
                    self.session.pause()
                self._stop_timer()
                # A diagnostic halt is terminal for this recording. Persist
                # both JSONL and the summary immediately; do not leave a
                # buffered trace open waiting for a separate user action.
                self._finish_motion_trace("KINEMATIC_POSE_HALT")
                self.status.setText(
                    "Симуляция остановлена: {}. Отображён последний корректный "
                    "кадр. Для продолжения автоматической анимации без "
                    "станка выберите Bend Only и нажмите Play All.".format(reason)
                )

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
                        # 2.5D cross-section derived from authoritative FoldState
                        fs = self.session.fold_state
                        verts = self.session.panel_vertices_xy
                        reference_id = self.session.canonical_order[0]
                        ref_bend = fs.bends[reference_id]
                        ref_frame = (
                            ref_bend.bend_zone,
                            1.0 if ref_bend.moving_left else -1.0,
                        )

                        pose = WorkpiecePose2D()
                        pose_warning = ""
                        if self._machine_pose_fault is not None:
                            pose_warning = self._machine_pose_fault
                            if self._last_valid_machine_section is not None:
                                sec = self._last_valid_machine_section
                                pose = self._last_valid_machine_pose
                            else:
                                sec = fs.cross_section_2d(reference_id, verts, segments=32)
                        elif self.execution_mode == CycleExecutionMode.FULL_CYCLE:
                            pc_for_pose = self._ensure_press_cycle_controller()
                            try:
                                if pc_for_pose is None:
                                    raise WorkpiecePoseError("Press cycle is not initialized")

                                # D3: True physical cutting-plane section at X_m = 0 in Machine Space
                                current_placement = pc_for_pose.current_workpiece_placement_3d()
                                sec = compute_machine_cutting_plane_section(
                                    fs,
                                    verts,
                                    current_placement,
                                    active_bend_id=self.session.active_bend_id,
                                    segments=32,
                                )

                                # Log diagnostic event for machine section
                                self._trace_event(
                                    "MACHINE_SECTION_3D",
                                    active_bend=self.session.active_bend_id,
                                    phase=pc_for_pose.phase.value,
                                    cutting_panels=[p[0] for p in sec.get("other_panel_polygons", [])],
                                    translation=list(current_placement.translation),
                                )

                                self._last_valid_machine_section = sec
                                self._last_valid_machine_pose = pose
                                self._last_valid_machine_bend = self.session.active_bend_id
                            except (WorkpiecePoseError, PressCycleError) as exc:
                                pose_warning = "MACHINE_PRESENTATION_UNSUPPORTED: " + str(exc)
                                self._halt_unsupported_machine_pose(pose_warning)
                                if self._last_valid_machine_section is not None:
                                    sec = self._last_valid_machine_section
                                    pose = self._last_valid_machine_pose
                                else:
                                    sec = fs.cross_section_2d(reference_id, verts, segments=32)
                        else:
                            # BEND_ONLY mode: use local active bend section
                            sec = fs.cross_section_2d(self.session.active_bend_id, verts, segments=32)

                        if pose_warning:
                            # Unsupported 2.5D placement must never look like
                            # an accepted machine bend. Show a scene warning,
                            # including when the right-hand task controls
                            # are hidden or the status text is clipped.
                            # Report the actual unsupported geometry reason,
                            # especially NONPARALLEL_BEND_AXES versus sheet-roll
                            # contact limitations. Do not hide the cause.
                            explanation = pose_warning.strip(" |")[:140]
                            warning_item = self.scene.addSimpleText(
                                "СТОП 2.5D: ПОЗА НЕ ПРОВЕРЕНА | " + explanation
                            )
                            warning_item.setBrush(
                                QtGui.QBrush(QtGui.QColor("#e74c3c"))
                            )
                            marker = self.tooling_setup.die_v_center_machine()
                            if marker is not None:
                                warning_item.setPos(
                                    float(marker[0]) - 55.0,
                                    -float(marker[1]) - 40.0,
                                )
                            warning_item.setZValue(Z_LAYER_AUXILIARY)

                        if (pose_warning and
                                self._last_valid_machine_section is None):
                            # A source FoldState section is NOT a valid machine
                            # pose. If none was ever computed, suppress the
                            # workpiece entirely instead of drawing IDENTITY
                            # at a misleading punch/die location.
                            self.step.setText(
                                "СТОП: невозможно расположить заготовку в 2.5D | "
                                + pose_warning
                            )
                            self._update_press_cycle_labels()
                            self.last_scene_bounds = compute_scene_bounds_2d(
                                sheet_pts=[],
                                punch_pts=punch_pts,
                                die_pts=die_pts,
                                focus_sheet_when_tall_tooling=True,
                            )
                            rx, ry, rw, rh = self.last_scene_bounds.view_rect
                            if self._locked_25d_scene_rect is not None:
                                self.scene.setSceneRect(
                                    self._locked_25d_scene_rect
                                )
                            else:
                                self.scene.setSceneRect(rx, ry, rw, rh)
                            return

                        # sec is now in Machine Coordinates; do not apply the
                        # same transform a second time in any renderer path.
                        to_sheet_qpt = to_qpt
                        make_sheet_poly = make_poly
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
                            op_item = self.scene.addPolygon(make_sheet_poly(opoly), other_pen, other_brush)
                            op_item.setZValue(Z_LAYER_SHEET_OTHER)
                            op_item.setData(0, "sheet_other_panel")

                        zone_pen = QtGui.QPen(QtGui.QColor("#b9770e"), 1.2)
                        zone_pen.setCosmetic(True)
                        zone_brush = QtGui.QBrush(QtGui.QColor("#f8c471"))
                        for _, ozpoly in sec.get("other_zone_polygons", ()):
                            oz_item = self.scene.addPolygon(make_sheet_poly(ozpoly), zone_pen, zone_brush)
                            oz_item.setZValue(Z_LAYER_SHEET_OTHER)
                            oz_item.setData(0, "sheet_other_zone")

                        # 3. Fixed rigid panel
                        panel_pen = QtGui.QPen(QtGui.QColor("#1b4f72"), 1.2)
                        panel_pen.setCosmetic(True)
                        fixed_brush = QtGui.QBrush(QtGui.QColor("#85929e"))
                        fixed_item = self.scene.addPolygon(make_sheet_poly(sec["fixed_polygon"]), panel_pen, fixed_brush)
                        fixed_item.setZValue(Z_LAYER_SHEET_PANELS)
                        fixed_item.setData(0, "sheet_fixed")

                        # 4. Moving rigid panel (transformed by FoldState)
                        moving_brush = QtGui.QBrush(QtGui.QColor("#5dade2"))
                        moving_item = self.scene.addPolygon(make_sheet_poly(sec["moving_polygon"]), panel_pen, moving_brush)
                        moving_item.setZValue(Z_LAYER_SHEET_PANELS)
                        moving_item.setData(0, "sheet_moving")

                        # 5. Finite-radius bend zone polygon
                        zone_item = self.scene.addPolygon(make_sheet_poly(sec["zone_polygon"]), zone_pen, zone_brush)
                        zone_item.setZValue(Z_LAYER_SHEET_ZONE)
                        zone_item.setData(0, "sheet_zone")

                        # 6. Both sheet surfaces (inner concave and outer convex)
                        inner_pen = QtGui.QPen(QtGui.QColor("#0e6251"), 2.0)
                        inner_pen.setCosmetic(True)
                        for curve in (sec["inner_surface"],) + tuple(sec.get("other_inner_curves", ())):
                            if not curve:
                                continue
                            inner_path = QtGui.QPainterPath()
                            inner_pts = [to_sheet_qpt(p) for p in curve]
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
                            outer_pts = [to_sheet_qpt(p) for p in curve]
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
                            n_pts = [to_sheet_qpt(p) for p in curve]
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
                            p1, p2 = to_sheet_qpt(t_top), to_sheet_qpt(t_bot)
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
                                status_str + (" | LAST_VALID_FRAME at " +
                                    str(self._last_valid_machine_bend)
                                    if pose_warning else " | " + pose.mode)
                                    + (" | " + pose_warning if pose_warning else ""),
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
                    # Never chase scene bounds while an animation is running.
                    # Re-fitting each active bend made the entire part appear
                    # to jump even when its world-space points were continuous.
                    if self._locked_25d_scene_rect is not None:
                        # Preserve bounds on the last frame after the timer stops.
                        self.scene.setSceneRect(self._locked_25d_scene_rect)
                    else:
                        self.scene.setSceneRect(rx, ry, rw, rh)
                    if self.session is not None:
                        self._trace_drawn_frame(
                            sec, pose, pose_warning,
                            self.press_cycle,
                        )
                    if hasattr(self, "_viewer_tabs") and self._viewer_tabs.currentIndex() == 1:
                        self._update_3d_preview()
                    self.last_draw_time_ms = (time.perf_counter() - t_draw_start) * 1000.0
                except Exception:
                    _error(traceback.format_exc())
                    raise

        return Panel(library_root=library_root)
