"""PressBrakeSim 2.5D — Real SheetMetal Geometry Viewer (Phase 3A).

Uses the existing 2.5D pipeline without introducing a second geometry engine:
  sheetmetal_unfold_reader.read_unfold
  -> panel_builder.build_panels
  -> panel_graph.graph_from_panel_build
  -> panel_kinematics.FoldState (with explicit BendZone)
"""
from __future__ import annotations

from freecad_smbs.ui.orbit_camera import orbit_project, clamp_pitch

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


_ICON_ROOT = Path(__file__).resolve().parents[1] / "resources" / "icons"
_CONTROLS_DOCK_NAME = "SMBS_SimulationControls"


def get_workspace_widget():
    """Locate existing viewer; never create a second simulation session."""
    import FreeCADGui as Gui
    _QtCore, _QtGui, QtWidgets = _qt()
    main = Gui.getMainWindow()
    mdi = main.findChild(QtWidgets.QMdiArea)
    if mdi is None:
        return None
    for subwindow in mdi.subWindowList():
        if subwindow.objectName() == "SMBS_PressBrake2D_Workspace":
            return subwindow.widget()
    return None


def _selection_snapshot():
    """Capture active model selection before changing FreeCAD MDI focus."""
    import FreeCAD as App
    import FreeCADGui as Gui
    return getattr(App, "ActiveDocument", None), list(Gui.Selection.getSelection())


def show_workspace_controls(workspace, tab=None):
    """Show durable addon-owned Tasks dock without FreeCAD Control.showDialog.

    This avoids the activeDialog()/closeDialog() lifecycle bug encountered in
    FreeCAD 1.1 while retaining one set of widgets and one playback timer.
    """
    import FreeCADGui as Gui
    QtCore, _QtGui, QtWidgets = _qt()
    if tab is not None:
        workspace._control_tabs.setCurrentIndex(tab)
    dock = getattr(workspace, "_controls_dock", None)
    if dock is not None:
        dock.show()
        dock.raise_()
        workspace.show_task_panel_btn.hide()
        return dock

    main = Gui.getMainWindow()
    dock = QtWidgets.QDockWidget("PressBrakeSim — Task Panel", main)
    dock.setObjectName(_CONTROLS_DOCK_NAME)
    dock.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea)
    dock.setAttribute(QtCore.Qt.WA_DeleteOnClose, False)
    form = QtWidgets.QWidget(dock)
    layout = QtWidgets.QVBoxLayout(form)
    layout.setContentsMargins(4, 4, 4, 4)
    # Each task tab owns a QScrollArea. Keep the tab bar outside the
    # scrolling content so it remains visible even in a short dock.
    layout.addWidget(workspace._control_tabs, 1)
    close_button = QtWidgets.QPushButton("Закрыть панель")
    close_button.setObjectName("SMBS_CloseTaskPanel")
    close_button.clicked.connect(dock.hide)
    layout.addWidget(close_button)
    dock.setWidget(form)
    main.addDockWidget(QtCore.Qt.LeftDockWidgetArea, dock)
    dock.visibilityChanged.connect(
        lambda visible: workspace.show_task_panel_btn.setVisible(not visible)
    )
    workspace._controls_dock = dock
    dock.show()
    dock.raise_()
    workspace.show_task_panel_btn.hide()
    return dock


def close_workspace_controls(workspace):
    """Remove owned dock without deleting controls prematurely."""
    dock = getattr(workspace, "_controls_dock", None)
    if dock is None:
        return
    workspace._control_tabs.setParent(workspace)
    workspace._control_tabs.hide()
    dock.hide()
    dock.setWidget(None)
    dock.deleteLater()
    workspace._controls_dock = None


class PressBrake2DCommand:
    def GetResources(self):
        return {
            "Pixmap": str(_ICON_ROOT / "gui_simulation.svg"),
            "MenuText": "Симуляция",
            "ToolTip": "Показать окно моделирования и панель управления",
        }

    def IsActive(self):
        return True

    def Activated(self):
        import FreeCADGui as Gui
        doc, selected = _selection_snapshot()
        QtCore, _QtGui, QtWidgets = _qt()
        main = Gui.getMainWindow()
        mdi = main.findChild(QtWidgets.QMdiArea)
        if mdi is None:
            raise RuntimeError("FreeCAD MDI area was not found")

        name = "SMBS_PressBrake2D_Workspace"
        existing = next(
            (win for win in mdi.subWindowList() if win.objectName() == name),
            None,
        )
        if existing is not None and existing.widget() is not None:
            mdi.setActiveSubWindow(existing)
            existing.show()
            existing.raise_()
            show_workspace_controls(existing.widget())
            return

        widget = _PreviewPanel()
        window = QtWidgets.QMdiSubWindow()
        window.setObjectName(name)
        window.setWindowTitle("PressBrakeSim 2.5D — симуляция")
        window.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        window.setWidget(widget)
        mdi.addSubWindow(window)
        window.resize(1000, 720)
        window.showMaximized()
        mdi.setActiveSubWindow(window)
        show_workspace_controls(widget)
        if doc is not None and selected:
            widget.load_part(selection=selected, document=doc)
        _log("Experimental PressBrakeSim GUI opened; original search engine retained")


class LoadPartCommand:
    def GetResources(self):
        return {
            "Pixmap": str(_ICON_ROOT / "gui_part.svg"),
            "MenuText": "Деталь",
            "ToolTip": "Загрузить выбранную развёртку SheetMetal",
        }

    def IsActive(self):
        try:
            doc, selection = _selection_snapshot()
            return bool(doc is not None and selection and
                        resolve_selected_unfold(doc, selection))
        except (SelectionWarning, AttributeError, TypeError, ValueError, ImportError):
            return False

    def Activated(self):
        doc, selection = _selection_snapshot()
        if not self.IsActive():
            return
        workspace = get_workspace_widget()
        created = workspace is None
        if created:
            PressBrake2DCommand().Activated()
            workspace = get_workspace_widget()
        if workspace is None:
            raise RuntimeError("PressBrakeSim viewer did not open")
        if not created:
            workspace.load_part(selection=selection, document=doc)
        show_workspace_controls(workspace, 0)


class ToolingTabCommand:
    def GetResources(self):
        return {
            "Pixmap": str(_ICON_ROOT / "gui_tooling.svg"),
            "MenuText": "Оснастка",
            "ToolTip": "Открыть оснастку симулятора",
        }

    def IsActive(self):
        return True

    def Activated(self):
        workspace = get_workspace_widget()
        if workspace is None:
            PressBrake2DCommand().Activated()
            workspace = get_workspace_widget()
        if workspace is not None:
            show_workspace_controls(workspace, 1)


class CalculateCommand:
    def GetResources(self):
        return {
            "Pixmap": str(_ICON_ROOT / "gui_calculate.svg"),
            "MenuText": "Расчёт",
            "ToolTip": "Полный поиск последовательности с физическими проверками",
        }

    def IsActive(self):
        workspace = get_workspace_widget()
        return bool(workspace is not None and workspace.calculate.isEnabled())

    def Activated(self):
        workspace = get_workspace_widget()
        if workspace is not None and workspace.calculate.isEnabled():
            workspace.calculate_sequence()
class BackgaugeCommand:
    """Configure explicit backgauge stop geometry and per-bend targets."""
    def GetResources(self):
        return {
            "Pixmap": str(_ICON_ROOT / "gui_backgauge.svg"),
            "MenuText": "Задний упор",
            "ToolTip": "Целевые позиции заднего упора, веса и проверка 2D коллизий",
        }

    def IsActive(self):
        return True

    def Activated(self):
        workspace = get_workspace_widget()
        if workspace is None:
            PressBrake2DCommand().Activated()
            workspace = get_workspace_widget()
        if workspace is not None:
            workspace.open_backgauge_dialog()


class SequenceCatalogCommand:
    """Open saved sequence collection, without spawning a second simulator."""
    def GetResources(self):
        return {
            "Pixmap": str(_ICON_ROOT / "gui_sequence_catalog.svg"),
            "MenuText": "Последовательности",
            "ToolTip": "JSON автоматических и ручных последовательностей; назначение симуляции",
        }

    def IsActive(self):
        return True

    def Activated(self):
        workspace = get_workspace_widget()
        if workspace is None:
            PressBrake2DCommand().Activated()
            workspace = get_workspace_widget()
        if workspace is not None:
            workspace.open_sequence_catalog_dialog()


class PlanarCandidatesCommand:
    """Preserve experiment's separate fast 2D candidate search."""
    def GetResources(self):
        return {
            "Pixmap": str(_ICON_ROOT / "gui_planar_candidates.svg"),
            "MenuText": "2D варианты",
            "ToolTip": "Предварительные 2D варианты; не являются 3D-допуском",
        }

    def IsActive(self):
        workspace = get_workspace_widget()
        return bool(workspace is not None and workspace.planar_calculate.isEnabled())

    def Activated(self):
        workspace = get_workspace_widget()
        if workspace is not None and workspace.planar_calculate.isEnabled():
            workspace.calculate_planar_candidates()


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
            """2D/3D zoomable viewer; optional 3D orbit controlled by parent Panel."""

            def __init__(self, parent=None, orbit_callback=None, bend_click_callback=None):
                super().__init__(parent)
                self._bend_click_callback = bend_click_callback
                self.setRenderHint(QtGui.QPainter.Antialiasing, True)
                self.setDragMode(QtWidgets.QGraphicsView.ScrollHandDrag)
                self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
                self.setResizeAnchor(QtWidgets.QGraphicsView.AnchorViewCenter)
                self.setMinimumHeight(240)
                self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
                self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
                self._user_camera_modified = False
                self._zoom_steps = 0.0
                self._orbit_callback = orbit_callback
                self._last_drag_pos = None

            def reset_camera(self):
                scene = self.scene()
                if scene is None or scene.sceneRect().isEmpty():
                    return
                self._user_camera_modified = False
                self._zoom_steps = 0.0
                self.resetTransform()
                self.fitInView(scene.sceneRect(), QtCore.Qt.KeepAspectRatio)

            def wheelEvent(self, event):
                delta = event.angleDelta().y()
                if delta == 0:
                    event.ignore()
                    return
                steps = delta / 120.0
                next_steps = self._zoom_steps + steps
                if not -35.0 <= next_steps <= 35.0:
                    event.accept()
                    return
                self.scale(1.15 ** steps, 1.15 ** steps)
                self._zoom_steps = next_steps
                self._user_camera_modified = True
                event.accept()

            def mousePressEvent(self, event):
                if (self._bend_click_callback is not None
                        and event.button() == QtCore.Qt.LeftButton):
                    item = self.itemAt(event.pos())
                    while item is not None:
                        bend_id = item.data(0)
                        if isinstance(bend_id, str) and bend_id.startswith("B"):
                            self._bend_click_callback(bend_id)
                            event.accept()
                            return
                        item = item.parentItem()
                if self._orbit_callback is not None and event.button() == QtCore.Qt.LeftButton:
                    self._last_drag_pos = event.pos()
                    event.accept()
                    return
                super().mousePressEvent(event)

            def mouseMoveEvent(self, event):
                if self._last_drag_pos is not None and self._orbit_callback is not None:
                    pos = event.pos()
                    delta = pos - self._last_drag_pos
                    self._last_drag_pos = pos
                    roll = bool(event.modifiers() & QtCore.Qt.ShiftModifier)
                    if delta.x() or delta.y():
                        self._orbit_callback(delta.x(), delta.y(), roll)
                    event.accept()
                    return
                super().mouseMoveEvent(event)

            def mouseReleaseEvent(self, event):
                if self._last_drag_pos is not None and event.button() == QtCore.Qt.LeftButton:
                    self._last_drag_pos = None
                    event.accept()
                    return
                super().mouseReleaseEvent(event)

            def resizeEvent(self, event):
                super().resizeEvent(event)
                if not self._user_camera_modified and self.scene() is not None:
                    if not self.scene().sceneRect().isEmpty():
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
                self._partial_preview_limit = 0
                self._manual_sequence_prefix: list[str] = []
                self._last_manual_sequence_report = None
                self._backgauge_settings = QtCore.QSettings("SMBS", "BendSeq")
                self.backgauge_model = None
                self._backgauge_preview_cache = {}
                # Preserve the exact S/Z candidate accepted by the search.
                # Otherwise a GUI redraw would recompute the primary gauge
                # and depict a different setup from the chosen DFS branch.
                self._selected_planar_gauge_order = ()
                self._selected_planar_gauge_positions = {}
                self._selected_planar_gauge_model_id = None
                self._selected_planar_loading_orientation = {}
                self._ergonomic_loading_cache_key = None
                self._ergonomic_loading_cache = {}
                self._ergonomic_loading_status = "NO_REAR_GAUGE"
                if self._backgauge_settings.value(
                    "backgauge/enabled", False, type=bool
                ):
                    from smbs_core.kinematics2d.backgauge_2d import BackgaugeModel2D
                    import json
                    raw_config = self._backgauge_settings.value(
                        "backgauge/config_json", "", type=str)
                    if raw_config:
                        try:
                            self.backgauge_model = BackgaugeModel2D.from_dict(
                                json.loads(raw_config)
                            )
                        except (ValueError, TypeError, KeyError) as exc:
                            _error("Backgauge configuration rejected: " + str(exc))
                self._manual_selection_enabled = False
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

                # Toolbar owns main actions. Retain two original enabled-state
                # proxies because experimental physical/planar search logic
                # relies on them, without duplicating on-screen buttons.
                self.calculate = _button(QtWidgets, "Расчёт", self.calculate_sequence, False)
                self.planar_calculate = _button(
                    QtWidgets, "2D варианты", self.calculate_planar_candidates, False
                )
                self.calculate.setParent(self)
                self.planar_calculate.setParent(self)
                self.calculate.hide()
                self.planar_calculate.hide()
                self.planar_calculate.setToolTip(
                    "Быстрый 2D поиск вариантов для просмотра. Не подтверждает "
                    "физическую возможность гибки и не отменяет 3D-валидацию."
                )
                self.calculate.setToolTip(
                    "Автоматический поиск допустимой последовательности гибки с физической проверкой 2.5D + 3D (D6.3.4)"
                )

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
                self.loading_orientation_status = QtWidgets.QLabel(
                    "Оператор ← | Упор → | Подача: пока не определена"
                )
                self.loading_orientation_status.setWordWrap(True)
                self.loading_orientation_status.setStyleSheet(
                    "font-size: 11px; color: #815a2a;"
                )
                pc_layout.addWidget(self.loading_orientation_status)
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
                if len(entries) != 13:
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

                # Header and status remain in the MDI viewer.
                for item in entries[:2]:
                    insert(root, item)

                views = QtWidgets.QTabWidget(self)
                views.setObjectName("SMBS_ViewerTabs")
                views.addTab(self.view, QtGui.QIcon(str(
                    _ICON_ROOT / "gui_simulation.svg"
                )), "2.5D · Гибка")
                self._preview_3d = Canvas(orbit_callback=self._orbit_3d)
                self._orbit_yaw = 35.0
                self._orbit_pitch = 25.0
                self._orbit_roll = 0.0
                self._last_3d_frame_at = -1.0
                self._fixed_3d_view_rect = None
                self._preview_3d.setObjectName("SMBS_3D_ReadOnly")
                self._preview_3d_scene = QtWidgets.QGraphicsScene(self._preview_3d)
                self._preview_3d.setScene(self._preview_3d_scene)
                views.addTab(self._preview_3d, QtGui.QIcon(str(
                    _ICON_ROOT / "gui_part.svg"
                )), "3D · Просмотр")
                self._flat_view = Canvas(bend_click_callback=self._select_flat_bend)
                self._flat_view.setObjectName("SMBS_FlatPattern_ManualOrder")
                self._flat_scene = QtWidgets.QGraphicsScene(self._flat_view)
                self._flat_view.setScene(self._flat_scene)
                views.addTab(self._flat_view, QtGui.QIcon(str(
                    _ICON_ROOT / "gui_sequence_catalog.svg"
                )), "Развёртка · Порядок")
                views.currentChanged.connect(self._on_view_changed)

                controls = QtWidgets.QTabWidget()
                controls.setObjectName("SMBS_ControlTabs")
                controls.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                                       QtWidgets.QSizePolicy.Expanding)

                def scroll_task_page(content, name):
                    # Scroll the CONTENT of each task tab (not the tab bar).
                    # A minimum-size layout prevents Qt from compressing the
                    # controls into the viewport and overlapping them.
                    content.layout().setSizeConstraint(
                        QtWidgets.QLayout.SetMinimumSize
                    )
                    page = QtWidgets.QScrollArea()
                    page.setObjectName(name)
                    page.setWidgetResizable(True)
                    page.setFrameShape(QtWidgets.QFrame.NoFrame)
                    page.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOn)
                    page.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
                    page.setMinimumSize(0, 0)
                    page.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                                       QtWidgets.QSizePolicy.Expanding)
                    page.setWidget(content)
                    return page

                simulation = QtWidgets.QWidget()
                sim_layout = QtWidgets.QVBoxLayout(simulation)
                sim_layout.setSpacing(8)
                # Mode selection belongs beside playback, not in diagnostic
                # settings. One Play All operates whichever mode is selected.
                insert(sim_layout, entries[3])  # mode / speed / cycle config
                insert(sim_layout, entries[5])  # Reset / Play All / Pause / Fit
                insert(sim_layout, entries[6])  # angle slider
                insert(sim_layout, entries[7])  # active operation
                insert(sim_layout, entries[8])  # next/previous step
                insert(sim_layout, entries[9])  # move order
                # Vertical controls avoid button collisions in narrow docks.
                manual_row = QtWidgets.QVBoxLayout()
                self.manual_mode_btn = QtWidgets.QPushButton("Ручной порядок: ВЫКЛ")
                self.manual_mode_btn.setCheckable(True)
                self.manual_mode_btn.toggled.connect(self._toggle_manual_order)
                manual_row.addWidget(self.manual_mode_btn)
                self.manual_undo_btn = QtWidgets.QPushButton("↶ Отменить линию")
                self.manual_undo_btn.clicked.connect(self._undo_manual_bend)
                manual_row.addWidget(self.manual_undo_btn)
                self.manual_clear_btn = QtWidgets.QPushButton("Очистить номера")
                self.manual_clear_btn.clicked.connect(self._clear_manual_order)
                manual_row.addWidget(self.manual_clear_btn)
                sim_layout.addLayout(manual_row)
                self.retract_warning_checkbox = QtWidgets.QCheckBox(
                    "RETRACT: продолжать 2D-поиск с предупреждением"
                )
                self.retract_warning_checkbox.setChecked(False)
                self.retract_warning_checkbox.setToolTip(
                    "Только диагностический режим: пересечение пуансона с "
                    "деталью во время RETRACT сохраняется как предупреждение, "
                    "а не как доказанный безопасный контакт. APPROACH, BENDING, "
                    "BOTTOM, матрица и упор по-прежнему блокируют 2D-поиск."
                )
                sim_layout.addWidget(self.retract_warning_checkbox)
                self.manual_validate_btn = QtWidgets.QPushButton(
                    "Проверить ручной порядок и сохранить JSON"
                )
                self.manual_validate_btn.setToolTip(
                    "Последовательно проверить выбранные линии через Stage A/B "
                    "и сохранить полный диагностический JSON для анализа. "
                    "2D-проверка не доказывает отсутствие реальных 3D-коллизий."
                )
                self.manual_validate_btn.clicked.connect(self._validate_export_manual_order)
                sim_layout.addWidget(self.manual_validate_btn)
                self.manual_hint = QtWidgets.QLabel(
                    "Выберите «Развёртка · Порядок», включите ручной режим и нажимайте линии "
                    "в нужной последовательности. Это план без физической сертификации."
                )
                self.manual_hint.setWordWrap(True)
                sim_layout.addWidget(self.manual_hint)
                self.operations.setMinimumHeight(160)
                self.operations.setMaximumHeight(280)
                insert(sim_layout, entries[10], 1)  # operation table
                controls.addTab(
                    scroll_task_page(simulation, "SMBS_SimulationScroll"),
                    QtGui.QIcon(str(_ICON_ROOT / "gui_simulation.svg")),
                    "Симуляция"
                )

                tooling = QtWidgets.QWidget()
                tool_layout = QtWidgets.QVBoxLayout(tooling)
                insert(tool_layout, entries[2])  # tooling group
                tool_layout.addStretch(1)
                controls.addTab(
                    scroll_task_page(tooling, "SMBS_ToolingScroll"),
                    QtGui.QIcon(str(_ICON_ROOT / "gui_tooling.svg")),
                    "Оснастка"
                )

                settings = QtWidgets.QWidget()
                settings_layout = QtWidgets.QVBoxLayout(settings)

                # Cost weights affect ranking only, never collision decisions.
                from smbs_core.kinematics2d.operation_cost import OperationWeights
                weight_defaults = OperationWeights().to_dict()
                weight_names = (
                    ("turn_fixed", "Поворот: базовый штраф"),
                    ("turn_per_90_deg", "Поворот: за 90°"),
                    ("flip", "Переворот листа"),
                    ("shift_fixed", "Сдвиг: базовый штраф"),
                    ("shift_per_100_mm", "Сдвиг: за 100 мм"),
                    ("length_change_fixed", "Смена длины: базовый штраф"),
                    ("length_delta_per_100_mm", "Линия гиба: за 100 мм"),
                    ("punch_length_change_fixed", "Пуансон: смена длины"),
                    ("punch_length_delta_per_100_mm", "Пуансон: за 100 мм"),
                    ("die_length_change_fixed", "Матрица: смена длины"),
                    ("die_length_delta_per_100_mm", "Матрица: за 100 мм"),
                    ("backgauge_move_fixed", "Задний упор: перемещение"),
                    ("backgauge_move_per_100_mm", "Задний упор: за 100 мм"),
                )
                weights_box = QtWidgets.QGroupBox("Веса операций · 2D поиск")
                weights_form = QtWidgets.QFormLayout(weights_box)
                self._weight_editors = {}
                self._weight_preferences = QtCore.QSettings("SMBS", "BendSeq")
                for weight_key, caption in weight_names:
                    editor = QtWidgets.QDoubleSpinBox(weights_box)
                    editor.setRange(0.0, 100000.0)
                    editor.setDecimals(2)
                    editor.setSingleStep(0.5)
                    editor.setValue(float(self._weight_preferences.value(
                        "operation_weights/" + weight_key,
                        weight_defaults[weight_key], type=float,
                    )))
                    editor.setToolTip(
                        "Штрафные баллы для сортировки полных кандидатов. "
                        "Нулевой вес не отключает проверку коллизий."
                    )
                    editor.valueChanged.connect(
                        lambda value, key=weight_key:
                        self._weight_preferences.setValue(
                            "operation_weights/" + key, float(value)
                        )
                    )
                    weights_form.addRow(caption, editor)
                    self._weight_editors[weight_key] = editor
                weights_note = QtWidgets.QLabel(
                    "Меньший штраф = предпочтительнее. Первый гиб не штрафуется. "
                    "При виртуальной подгонке штраф длины пуансона/матрицы — "
                    "только оценка планируемой переналадки. При фиксированной "
                    "оснастке длины не меняются. Это не проверка безопасности."
                )
                weights_note.setWordWrap(True)
                weights_form.addRow(weights_note)
                reset_weights_btn = QtWidgets.QPushButton("Стандартные веса")
                reset_weights_btn.setIcon(QtGui.QIcon(str(
                    _ICON_ROOT / "control_reset.svg"
                )))
                reset_weights_btn.clicked.connect(
                    lambda _checked=False: [
                        self._weight_editors[k].setValue(weight_defaults[k])
                        for k in self._weight_editors
                    ]
                )
                weights_form.addRow(reset_weights_btn)
                settings_layout.addWidget(weights_box)

                gauge_settings_btn = QtWidgets.QPushButton(
                    "Задний упор — геометрия и позиции гибов…")
                gauge_settings_btn.setIcon(QtGui.QIcon(str(
                    _ICON_ROOT / "gui_backgauge.svg"
                )))
                gauge_settings_btn.clicked.connect(self.open_backgauge_dialog)
                settings_layout.addWidget(gauge_settings_btn)
                insert(settings_layout, entries[11])  # diagnostics button
                insert(settings_layout, entries[12])  # diagnostics output

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
                controls.addTab(
                    scroll_task_page(settings, "SMBS_SettingsScroll"),
                    QtGui.QIcon(str(_ICON_ROOT / "gui_backgauge.svg")),
                    "Настройки"
                )

                root.addWidget(views, 1)
                self._viewer_tabs = views
                self._control_tabs = controls
                self._controls_dock = None
                self.show_task_panel_btn = QtWidgets.QPushButton("Показать панель управления")
                self.show_task_panel_btn.setObjectName("SMBS_ShowTaskPanel")
                self.show_task_panel_btn.clicked.connect(
                    lambda: show_workspace_controls(self)
                )
                root.addWidget(self.show_task_panel_btn)
                self.show_task_panel_btn.hide()
                self._apply_svg_button_icons()

            def _apply_svg_button_icons(self):
                """Use the same industrial SVG family in task controls.

                Only the visual presentation is modified. The existing
                QPushButton instances, signals and enabled states are retained.
                QtSvg renders the assets in FreeCAD's bundled Qt runtime.
                """
                icon_pairs = (
                    (self.reset_btn, "control_reset.svg"),
                    (self.play_btn, "control_play.svg"),
                    (self.pause_btn, "control_pause.svg"),
                    (self.fit_btn, "control_fit.svg"),
                    (self.prev_step_btn, "control_prev.svg"),
                    (self.next_step_btn, "control_next.svg"),
                    (self.restore_order_btn, "control_reset.svg"),
                    (self.manual_mode_btn, "control_manual.svg"),
                    (self.manual_undo_btn, "control_prev.svg"),
                    (self.manual_clear_btn, "control_reset.svg"),
                    (self.manual_validate_btn, "control_validate.svg"),
                    (self.trace_start_button, "control_record.svg"),
                    (self.trace_stop_button, "control_stop.svg"),
                    (self.trace_open_button, "control_folder.svg"),
                    (self.trace_choose_button, "control_folder.svg"),
                    (self.show_task_panel_btn, "gui_simulation.svg"),
                    (self.tool_library_btn, "tooling.svg"),
                    (self.import_punch_btn, "gui_tooling.svg"),
                    (self.import_die_btn, "gui_tooling.svg"),
                    (self.select_punch_btn, "gui_tooling.svg"),
                    (self.select_die_btn, "gui_tooling.svg"),
                    (self.align_tooling_btn, "gui_tooling.svg"),
                    (self.reset_tooling_btn, "control_reset.svg"),
                    (self.toggle_tooling_btn, "gui_tooling.svg"),
                    (self.physical_check_btn, "control_validate.svg"),
                    (self.full_physical_check_btn, "control_validate.svg"),
                    (self.cycle_config_btn, "gui_backgauge.svg"),
                    (self.check_btn, "control_validate.svg"),
                )
                for widget, icon_name in icon_pairs:
                    asset = _ICON_ROOT / icon_name
                    if not asset.is_file():
                        raise FileNotFoundError(
                            "SMBS GUI icon is missing: " + str(asset)
                        )
                    widget.setIcon(QtGui.QIcon(str(asset)))
                    widget.setIconSize(QtCore.QSize(19, 19))

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
                        loading_orientation_by_bend=(
                            self._effective_loading_orientation_map()
                        ),
                        loading_orientation_status=(
                            self._ergonomic_loading_status
                        ),
                        selected_planar_prefix=list(
                            self._selected_planar_gauge_order
                        ),
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
                elif index == 2:
                    self._draw_flat_pattern()

            def _orbit_3d(self, dx, dy, roll=False):
                if roll:
                    self._orbit_roll = (self._orbit_roll + dx * 0.45) % 360.0
                else:
                    self._orbit_yaw = (self._orbit_yaw + dx * 0.45) % 360.0
                    self._orbit_pitch = clamp_pitch(self._orbit_pitch + dy * 0.45)
                self._update_3d_preview(force=True)

            def reset_3d_view(self):
                self._orbit_yaw, self._orbit_pitch, self._orbit_roll = 35.0, 25.0, 0.0
                self._update_3d_preview(force=True)
                self._preview_3d.reset_camera()

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
                    # The whole-sheet 3D tab MUST use the same rigid
                    # machine-space pose as the 2.5D sheet. Model-space
                    # rendering hid the actual operator/rear orientation.
                    current_placement = self._current_preview_machine_placement()
                    pose = WorkpiecePose2D()
                    faces = build_sheet_preview_faces(
                        self.session.fold_state,
                        self.session.panel_vertices_xy,
                        self.session.active_bend_id,
                        pose,
                        segments=12,
                        machine_placement=current_placement,
                    )
                except (PreviewMeshError, WorkpiecePoseError, KinematicsError) as exc:
                    _warn("3D whole-sheet preview unavailable: {}".format(exc))
                    scene.addSimpleText("3D: {}".format(str(exc)))
                    return

                # Camera orbit is anchored at the PHYSICAL DIE ORIGIN.
                # Recentering on the mesh centroid each frame hid long
                # operator/rear feeds and produced false visual "jumps".
                # Orbit the camera, never translate the machine or sheet.
                all_vertices = [v for f in faces for v in f.vertices]
                if not all_vertices:
                    scene.addSimpleText("3D: no visible faces")
                    return
                center = (0.0, 0.0, 0.0)
                radius = max(
                    math.sqrt(sum((p[i] - center[i]) ** 2 for i in range(3)))
                    for p in all_vertices
                )
                radius = max(radius, 1.0)

                def camera_point(p):
                    return orbit_project(
                        tuple(p[i] - center[i] for i in range(3)),
                        self._orbit_yaw, self._orbit_pitch, self._orbit_roll,
                    )

                def project(p):
                    x, y, _depth = camera_point(p)
                    return QtCore.QPointF(x, y)

                palette = {
                    "panel_top": ("#8bb9d4", "#37647e"),
                    "panel_bottom": ("#718b9c", "#37647e"),
                    "panel_edge": ("#587b90", "#37536a"),
                    "zone_inner": ("#eac77b", "#9a7134"),
                    "zone_outer": ("#e0b967", "#9a7134"),
                    "zone_end": ("#bd9c65", "#9a7134"),
                }
                # Painter order must follow the currently rotated camera depth.
                ordering = sorted(
                    faces,
                    key=lambda face: sum(camera_point(pt)[2] for pt in face.vertices)
                    / len(face.vertices),
                    reverse=True,
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
                    "3D MACHINE FRAME | {} | {} | {} | NOT 3D CERTIFIED".format(
                        self.session.active_bend_id,
                        self.execution_mode.value,
                        self._ergonomic_loading_status,
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
                # Fixed viewport in camera-centered model coordinates prevents
                # jump/auto-fit when switching bends or dragging the orbit camera.
                if self._fixed_3d_view_rect is None:
                    extent = radius * 1.8 + 25.0
                    self._fixed_3d_view_rect = QtCore.QRectF(
                        -extent, -extent - 25.0, 2 * extent, 2 * extent + 25.0,
                    )
                    scene.setSceneRect(self._fixed_3d_view_rect)
                    self._preview_3d.reset_camera()
                else:
                    scene.setSceneRect(self._fixed_3d_view_rect)

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
                if (self.session is not None
                        and self._selected_planar_loading_orientation.get(
                            str(self.session.active_bend_id), False
                        )):
                    self.physical_check_result.setText(
                        "UNVERIFIED: нативный D6.3.4 проверяет только обычную "
                        "ориентацию; обратная загрузка из 2D-поиска ещё не "
                        "поддержана независимой 3D-проверкой траектории."
                    )
                    return
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

            def open_backgauge_dialog(self):
                from freecad_smbs.commands.backgauge_dialog import (
                    show_backgauge_settings,
                )
                show_backgauge_settings(self)

            def _selected_operation_weights(self):
                from smbs_core.kinematics2d.operation_cost import OperationWeights
                values = OperationWeights().to_dict()
                for name, editor in self._weight_editors.items():
                    values[name] = float(editor.value())
                return OperationWeights(**values)

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
                # Real Part/OCC CAD gauge tests now cost ~0.8-1.6 s per
                # evaluated bend on the 10-bend fixture. The previous 30 s
                # limit expired inside Stage B at the sixth operation, not
                # because a collision was proven. Longer search is only for
                # explicit real CAD mode on complex parts.
                cad_extended_search = bool(
                    len(self.session.bend_order) >= 10
                    and self.backgauge_model is not None
                    and self.backgauge_model.cad_geometry_enabled
                )
                search_budget_s = 90.0 if cad_extended_search else 30.0
                self.status.setText(
                    "Выполняется предварительный поиск в 2D "
                    "(лимит {:.0f} с; 3D безопасность не подтверждена)..."
                    .format(search_budget_s)
                )
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
                    # User-writable, uniquely named diagnostic trace for the
                    # real OCC contact oracle. Created automatically only
                    # when a CAD backgauge is actually selected.
                    self._last_backgauge_cad_trace_path = None
                    if (self.backgauge_model is not None
                            and self.backgauge_model.cad_geometry_enabled):
                        from smbs_core.kinematics2d.backgauge_cad_trace import (
                            fresh_trace_path,
                        )
                        self._last_backgauge_cad_trace_path = str(
                            fresh_trace_path(
                                _pressbrake_user_data_directory() / "diagnostics"
                            )
                        )
                    report = search_bend_sequences_2d(
                        self.session, self.tooling_setup,
                        # Collect multiple complete candidates for genuine
                        # weight-based comparison, subject to the search budget.
                        # The best found is not proof of global optimality.
                        max_solutions=12,
                        max_complete_candidates=12,
                        weights=self._selected_operation_weights(),
                        backgauge_model=self.backgauge_model,
                        backgauge_cad_trace_path=(
                            self._last_backgauge_cad_trace_path
                        ),
                        # Do not lose a six-step sampled prefix when the
                        # 90s OCC budget expires during the seventh bend.
                        # The solver itself now excludes unfinished/unverified
                        # steps from any previewable partial sequence.
                        min_partial_bends=(
                            6 if cad_extended_search else
                            7 if len(self.session.bend_order) >= 10 else 0
                        ),
                        # Alternative machine-Z loading trials are advisory:
                        # the live 2.5D cycle still uses its own active pose.
                        alternate_axis_bends=(
                            tuple(b for b in ("B09", "B10") if b in self.session.bend_order)
                            if len(self.session.bend_order) >= 10 else ()
                        ),
                        allow_end_zone_collision=bool(self.tooling_setup.allow_end_zone_collision),
                        end_zone_length_mm=float(self.tooling_setup.end_zone_length_mm),
                        allow_retract_warning=self.retract_warning_checkbox.isChecked(),
                        max_search_time_s=search_budget_s,
                        max_nodes=5000,
                        max_evaluations=10000,
                        # For 10+ bends, distribute the first search pass
                        # across different starting bends. Otherwise a large
                        # doomed subtree under B10 may consume the 30s budget.
                        root_probe_nodes=(65 if len(self.session.bend_order) >= 10 else 0),
                        # Each root gets <=1.8 s, even when memoization
                        # prevents reaching its node-count allowance.
                        root_probe_seconds=(
                            0.9 if cad_extended_search
                            else 1.8 if len(self.session.bend_order) >= 10
                            else 0.0
                        ),
                        # Long box-like parts can require bends late in CAD
                        # construction order to be formed first. For >=6 bends
                        # explore reverse construction order before considering
                        # the legacy group-first order. This only affects
                        # scheduling; all steps still use the exact same
                        # contact oracle and detailed replay.
                        preferred_bend_order=(
                            tuple(reversed(self.session.bend_order))
                            if len(self.session.bend_order) >= 6
                            else tuple(self.session.bend_order)
                        ),
                        group_first=len(self.session.bend_order) < 6,
                        # Preliminary fast screen; every returned full order is
                        # replayed by the solver at its original 17/9 samples.
                        exploration_fractions=(0.0, 0.25, 0.5, 0.75, 1.0),
                        exploration_stroke_fractions=(0.0, 0.5, 1.0),
                        match_active_bend_length=not has_sections,
                    )
                    self.last_planar_search_report = report
                    if self._last_backgauge_cad_trace_path:
                        _log(
                            "BACKGAUGE_CAD_TRACE_PATH: "
                            + self._last_backgauge_cad_trace_path
                        )
                    self._last_planar_weights = self._selected_operation_weights().to_dict()
                    self._trace_event(
                        "PLANAR_2D_SEARCH_COMPLETED",
                        status=report.status.value,
                        evaluated_steps=report.evaluated_steps,
                        visited_nodes=report.visited_nodes,
                        candidate_count=len(report.candidates),
                        limit_reason=report.limit_reason,
                        budget_seconds=search_budget_s,
                        cad_extended_search=cad_extended_search,
                    )
                    self._show_planar_candidates_dialog(report)
                except Exception as exc:
                    msg = "2D поиск не выполнен: {}".format(exc)
                    self.status.setText(msg)
                    _error("Planar search exception: " + traceback.format_exc())
                finally:
                    self._sync_playback_buttons()

            def open_sequence_catalog_dialog(self):
                from freecad_smbs.commands.sequence_catalog_dialog import show_sequence_catalog
                show_sequence_catalog(self)

            def assign_catalog_sequence(self, entry):
                """Replay an imported order and bind it to the existing simulator."""
                from smbs_core.kinematics2d.sequence_search_2d import (
                    validate_partial_planar_prefix,
                )
                if self.session is None:
                    raise ValueError("Сначала загрузите деталь SheetMetal")
                if self.timer.isActive() or self.session.playback_status == PlaybackStatus.PLAYING:
                    raise ValueError("Остановите воспроизведение перед сменой порядка")
                sequence = tuple(entry.sequence)
                current = tuple(self.session.canonical_order)
                if (not sequence or len(sequence) > len(current)
                        or len(set(sequence)) != len(sequence)
                        or any(b not in current for b in sequence)):
                    raise ValueError("Гибы JSON не соответствуют текущей детали")
                if (entry.origin == "MANUAL" and entry.model_label
                        and entry.model_label != self.session.part_label):
                    raise ValueError(
                        "Ручной JSON относится к другой детали: " + entry.model_label
                    )
                has_sections = bool(
                    getattr(self.tooling_setup, "punch_section_offsets_xm", ())
                    or getattr(self.tooling_setup, "die_section_offsets_xm", ())
                )
                valid, reason = validate_partial_planar_prefix(
                    self.session, self.tooling_setup, sequence,
                    match_active_bend_length=not has_sections,
                    allow_retract_warning=self.retract_warning_checkbox.isChecked(),
                    backgauge_model=self.backgauge_model,
                )
                if not valid:
                    raise ValueError(
                        "Повторная 2D-проверка не пройдена: " + reason
                    )
                self._stop_timer()
                self.session.reset_all_bends()
                remainder = [bend for bend in current if bend not in sequence]
                self.session.set_bend_order(list(sequence) + remainder)
                is_partial = len(sequence) < len(current)
                self._partial_preview_limit = len(sequence) if is_partial else 0
                self._preview_only_planar_order = sequence
                self._selected_planar_gauge_order = ()
                self._selected_planar_gauge_positions = {}
                self._selected_planar_gauge_model_id = None
                self._selected_planar_loading_orientation = {}
                self._backgauge_preview_cache.clear()
                self.last_sequence_search_report = None
                self._conditional_bends_status.clear()
                self._populate_operations()
                self._configure_for_active_bend()
                self._sync_tooling_to_active_bend()
                self.draw()
                self.status.setText(
                    "Каталог → симуляция: {}/{} | {} | {}"
                    .format(len(sequence), len(current), entry.origin, reason)
                )
                self.physical_check_result.setText(
                    "UNVERIFIED: повторная 2D-проверка не является 3D-допуском. "
                    + reason
                )
                self._trace_event(
                    "CATALOG_SEQUENCE_SELECTED",
                    sequence=list(sequence), source=entry.source,
                    evidence=entry.evidence, recheck=reason,
                    physical_status="UNVERIFIED",
                )

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
                weights_caption = QtWidgets.QLabel(
                    "Веса: поворот {:.2f} + {:.2f}/90°, переворот {:.2f}, "
                    "сдвиг {:.2f} + {:.2f}/100 мм, "
                    "линия гиба {:.2f} + {:.2f}/100 мм, "
                    "пуансон {:.2f} + {:.2f}/100 мм, "
                    "матрица {:.2f} + {:.2f}/100 мм, "
                    "задний упор {:.2f} + {:.2f}/100 мм. "
                    "Лучший из найденных планов не обязательно глобально оптимален.".format(
                        *[self._last_planar_weights[k] for k in (
                            "turn_fixed", "turn_per_90_deg", "flip",
                            "shift_fixed", "shift_per_100_mm",
                            "length_change_fixed", "length_delta_per_100_mm",
                            "punch_length_change_fixed", "punch_length_delta_per_100_mm",
                            "die_length_change_fixed", "die_length_delta_per_100_mm",
                            "backgauge_move_fixed", "backgauge_move_per_100_mm",
                        )]
                    )
                )
                weights_caption.setWordWrap(True)
                layout.addWidget(weights_caption)
                details = QtWidgets.QLabel(
                    "Статус: {} | вариантов: {} | 2D проверок: {} | причина остановки: {}".format(
                        report.status.value, len(report.candidates),
                        report.evaluated_steps,
                        ("цель достигнута: найдено заданное количество вариантов"
                         if report.limit_reason == "MAX_COMPLETE_CANDIDATES_REACHED"
                         else report.limit_reason or "нет"),
                    )
                )
                details.setWordWrap(True)
                layout.addWidget(details)
                cad_trace_path = getattr(
                    self, "_last_backgauge_cad_trace_path", None
                )
                if cad_trace_path:
                    from pathlib import Path
                    log_file = Path(cad_trace_path)
                    trace_exists = log_file.is_file()
                    trace_label = QtWidgets.QLabel(
                        ("CAD-лог контакта: " if trace_exists else
                         "CAD-лог не создан — проверьте исходную модель: ")
                        + str(log_file)
                    )
                    trace_label.setWordWrap(True)
                    trace_label.setTextInteractionFlags(
                        QtCore.Qt.TextSelectableByMouse
                    )
                    layout.addWidget(trace_label)
                    if trace_exists:
                        open_trace_folder = QtWidgets.QPushButton(
                            "Открыть папку CAD-лога"
                        )
                        open_trace_folder.clicked.connect(
                            lambda: QtGui.QDesktopServices.openUrl(
                                QtCore.QUrl.fromLocalFile(
                                    str(log_file.parent)
                                )
                            )
                        )
                        layout.addWidget(open_trace_folder)
                if report.partial_candidate is not None and not report.candidates:
                    notice = QtWidgets.QLabel(
                        "ЧАСТИЧНЫЙ РЕЗУЛЬТАТ: {} из {} гибов прошли "
                        "предварительную проверку 2D. "
                        "Это префикс без полного контрольного прогона; "
                        "следующий незавершённый по тайм-ауту гиб не включён. "
                        "Остальные гибы не будут анимироваться. "
                        "Реальная безопасность станка не подтверждена.".format(
                            len(report.partial_candidate.sequence),
                            len(self.session.bend_order),
                        )
                    )
                    notice.setWordWrap(True)
                    layout.addWidget(notice)
                if report.alternative_setup_trials:
                    tested = len(report.alternative_setup_trials)
                    clear = sum(
                        trial[3] == "SAMPLED_2D_CLEAR_OR_TOUCH"
                        for trial in report.alternative_setup_trials
                    )
                    alternate_notice = QtWidgets.QLabel(
                        "Альтернативная установка (разворот на 180°): "
                        "проверено {}, без 2D-пересечений {}. "
                        "Результат пока диагностический, анимация такой установки "
                        "и путь перекладки отдельно не подтверждены.".format(
                            tested, clear
                        )
                    )
                    alternate_notice.setWordWrap(True)
                    layout.addWidget(alternate_notice)
                if report.conditional_end_zone_requested:
                    conditional_notice = QtWidgets.QLabel(
                        "CONDITIONAL запрошен. В быстром 2D-сечении нельзя "
                        "подтвердить столкновение именно с концом пуансона; "
                        "такие пересечения не разрешаются без 3D-классификации."
                    )
                    conditional_notice.setWordWrap(True)
                    layout.addWidget(conditional_notice)
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
                cache_info = QtWidgets.QLabel(
                    "DFS: тупиковых состояний {} | повторов отсечено {} | "
                    "отложенных ветвей {}".format(
                        report.dead_states_proven,
                        report.dead_state_cache_hits,
                        report.deferred_root_probes,
                    )
                )
                cache_info.setWordWrap(True)
                layout.addWidget(cache_info)
                pair_lines = [
                    "{} ↔ {}: оба порядка первых двух гибов отклонены в 2D".format(
                        left, right
                    )
                    for left, right, _, _ in report.paired_first_step_obstructions
                ]
                if pair_lines:
                    pair_info = QtWidgets.QLabel(
                        "Диагностика пар (не доказательство невозможности гибки):\n"
                        + "\n".join(pair_lines[:6])
                    )
                    pair_info.setWordWrap(True)
                    layout.addWidget(pair_info)
                options = QtWidgets.QListWidget(dlg)
                visible_candidates = list(report.candidates)
                if not visible_candidates and report.partial_candidate is not None:
                    visible_candidates.append(report.partial_candidate)
                for index, candidate in enumerate(visible_candidates):
                    suffix = (" | штраф {:.2f}".format(candidate.cost)
                              if candidate.cost_complete else " | штраф не подтверждён")
                    options.addItem(
                        "{}. {} | {}{}".format(
                            index + 1, " → ".join(candidate.sequence), candidate.evidence, suffix
                        )
                    )
                if visible_candidates:
                    options.setCurrentRow(0)
                layout.addWidget(options)
                controls = QtWidgets.QHBoxLayout()
                use = QtWidgets.QPushButton("Показать порядок в симуляции")
                use.setEnabled(bool(visible_candidates))
                export_btn = QtWidgets.QPushButton("Сохранить диагностику JSON")
                close = QtWidgets.QPushButton("Закрыть")
                controls.addWidget(use)
                controls.addWidget(export_btn)
                controls.addWidget(close)
                layout.addLayout(controls)

                def on_preview():
                    row = options.currentRow()
                    if row < 0 or row >= len(visible_candidates):
                        return
                    candidate = visible_candidates[row]
                    try:
                        if (self.backgauge_model is not None
                                or len(candidate.sequence) < len(self.session.bend_order)):
                            from smbs_core.kinematics2d.sequence_search_2d import (
                                validate_partial_planar_prefix,
                            )
                            has_sections = bool(
                                getattr(self.tooling_setup, "punch_section_offsets_xm", ())
                                or getattr(self.tooling_setup, "die_section_offsets_xm", ())
                            )
                            valid, reason = validate_partial_planar_prefix(
                                self.session, self.tooling_setup,
                                candidate.sequence,
                                match_active_bend_length=not has_sections,
                                allow_retract_warning=self.retract_warning_checkbox.isChecked(),
                                backgauge_model=self.backgauge_model,
                                backgauge_variant_indices=tuple(
                                    step.backgauge_variant_index
                                    for step in candidate.steps
                                ),
                                loading_orientation_variants=tuple(
                                    step.orientation_variant
                                    for step in candidate.steps
                                ),
                            )
                            if not valid:
                                self.status.setText(
                                    "Частичный порядок не прошёл полную 2D-проверку: "
                                    + reason
                                )
                                QtWidgets.QMessageBox.warning(
                                    dlg, "2D-просмотр отклонён",
                                    "Полная повторная 2D-проверка обнаружила "
                                    "препятствие: " + reason,
                                )
                                return
                        self._stop_timer()
                        self.session.reset_all_bends()
                        is_partial = len(candidate.sequence) < len(self.session.bend_order)
                        remainder = [bid for bid in self.session.bend_order
                                     if bid not in candidate.sequence]
                        # Sequencer must keep ALL bends: only execute the
                        # demonstrated prefix, leaving the remainder flat.
                        self.session.set_bend_order(list(candidate.sequence) + remainder)
                        self._partial_preview_limit = (
                            len(candidate.sequence) if is_partial else 0
                        )
                        self._preview_only_planar_order = tuple(candidate.sequence)
                        self._physical_check_signature = None
                        self._selected_planar_gauge_order = tuple(candidate.sequence)
                        self._selected_planar_gauge_model_id = id(self.backgauge_model)
                        self._selected_planar_loading_orientation = {
                            str(step.bend_id): step.orientation_variant.startswith(
                                "REVERSE_MACHINE_Z_180"
                            )
                            for step in candidate.steps
                        }
                        self._selected_planar_gauge_positions = {
                            i: (float(step.setup.backgauge_s_mm),
                                float(step.setup.backgauge_z_mm))
                            for i, step in enumerate(candidate.steps)
                            if (step.setup is not None
                                and step.setup.backgauge_s_mm is not None
                                and step.setup.backgauge_z_mm is not None)
                        }
                        self._backgauge_preview_cache.clear()
                        self._ensure_press_cycle_controller()
                        if self.press_cycle is not None:
                            self.press_cycle.loading_orientation_by_bend = dict(
                                self._selected_planar_loading_orientation
                            )
                        # A previous strict validation does not apply to a
                        # newly selected, independently ranked planar order.
                        self.last_sequence_search_report = None
                        self._conditional_bends_status.clear()
                        self._populate_operations()
                        self._configure_for_active_bend()
                        self._sync_tooling_to_active_bend()
                        self.draw()
                        self.status.setText(
                            ("ЧАСТИЧНЫЙ 2D-ПРОСМОТР {}/{} (остальные не проверены): ".format(
                                len(candidate.sequence), len(self.session.bend_order)
                            ) if is_partial else "2D-ПРОСМОТР (НЕ ПРОВЕРЕНО В 3D): ")
                            + " → ".join(candidate.sequence)
                        )
                        self.physical_check_result.setText(
                            "UNVERIFIED: {} "
                            "Полная траектория, 3D-коллизии и незавершённые "
                            "гибы не проверены.".format(
                                "частичный 2D-префикс." if is_partial
                                else "предварительный полный 2D-порядок."
                            )
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
                    self._partial_preview_limit = 0
                    self._selected_planar_gauge_order = ()
                    self._selected_planar_gauge_positions = {}
                    self._selected_planar_gauge_model_id = None
                    self._selected_planar_loading_orientation = {}
                    self._backgauge_preview_cache.clear()
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

            def _effective_loading_orientation_map(self):
                """One source of machine-facing orientation for every viewer.

                Search-selected orientation always takes precedence. For
                manual/canonical playback with an enabled backgauge, use a
                deterministic ergonomic preview, never label it CAD verified.
                The FoldState of the running simulation is not modified.
                """
                if self.session is None:
                    self._ergonomic_loading_status = "NO_PART"
                    return {}
                order = tuple(self.session.bend_order)
                checked_prefix = tuple(self._selected_planar_gauge_order)
                if (checked_prefix and order[:len(checked_prefix)] == checked_prefix
                        and self._selected_planar_gauge_model_id
                        == id(self.backgauge_model)):
                    self._ergonomic_loading_status = (
                        "SELECTED_2D_CAD_STATIC_NOT_FULL_3D_CERTIFIED"
                    )
                    return dict(self._selected_planar_loading_orientation)
                if self.backgauge_model is None:
                    self._ergonomic_loading_status = "NO_REAR_GAUGE"
                    return {}
                key = (
                    id(self.session), order, id(self.backgauge_model),
                )
                if key == self._ergonomic_loading_cache_key:
                    return dict(self._ergonomic_loading_cache)
                from smbs_core.kinematics2d.loading_orientation_preview import (
                    plan_preview_loading_orientations,
                )
                try:
                    suggestions = plan_preview_loading_orientations(
                        self.session.fold_state,
                        self.session.panel_vertices_xy,
                        order,
                    )
                    choices = {
                        item.bend_id: item.reverse_axis
                        for item in suggestions
                        if item.status == "ERGONOMIC_ONLY_NOT_CAD_VALIDATED"
                    }
                    unavailable = [
                        item.bend_id for item in suggestions
                        if item.status != "ERGONOMIC_ONLY_NOT_CAD_VALIDATED"
                    ]
                    self._ergonomic_loading_status = (
                        "ERGONOMIC_PREVIEW_UNVERIFIED"
                        if not unavailable else
                        "ERGONOMIC_PREVIEW_INCOMPLETE:" + ",".join(unavailable)
                    )
                except (ValueError, TypeError, KeyError, AttributeError) as exc:
                    _warn(
                        "Operator-side loading preview unavailable: "
                        + type(exc).__name__ + ":" + str(exc)
                    )
                    self._ergonomic_loading_status = (
                        "ERGONOMIC_PREVIEW_UNAVAILABLE:" + type(exc).__name__
                    )
                    choices = {}
                self._ergonomic_loading_cache_key = key
                self._ergonomic_loading_cache = dict(choices)
                return choices

            def _current_preview_machine_placement(self):
                """Return common machine-space sheet pose for 2D and 3D views."""
                if self.session is None:
                    raise WorkpiecePoseError("No part loaded")
                pc = self._ensure_press_cycle_controller()
                if (pc is not None
                        and self.execution_mode == CycleExecutionMode.FULL_CYCLE):
                    return pc.current_workpiece_placement_3d()
                from smbs_core.kinematics2d.workpiece_placement_3d import (
                    WorkpiecePlacement3D,
                )
                fs = self.session.fold_state
                bid = self.session.active_bend_id
                zone = fs.bends[bid].bend_zone
                if zone is None:
                    raise WorkpiecePoseError(
                        "BEND_ONLY_MACHINE_POSE_REQUIRES_BEND_ZONE:" + bid
                    )
                orientations = self._effective_loading_orientation_map()
                return WorkpiecePlacement3D.compute_placement(
                    fs, bid,
                    support_plane_zm=float(zone.outer_radius_mm),
                    align_bisector=True,
                    align_bend_arc_center=True,
                    reverse_axis=bool(orientations.get(bid, False)),
                    rear_panel_vertices_xy=(
                        self.session.panel_vertices_xy
                        if self.backgauge_model is not None else None
                    ),
                )

            def _update_loading_orientation_label(self):
                """Expose the actual machine direction, never CAD-certify a hint."""
                if self.session is None:
                    self.loading_orientation_status.setText(
                        "Оператор ← | Упор → | Деталь не загружена"
                    )
                    return
                choices = self._effective_loading_orientation_map()
                bend_id = str(self.session.active_bend_id)
                reversed_pose = bool(choices.get(bend_id, False))
                source = self._ergonomic_loading_status
                if source.startswith("SELECTED_2D"):
                    evidence = "выбрано из 2D-поиска; 3D не проверено"
                elif source.startswith("ERGONOMIC_PREVIEW"):
                    evidence = "по длинам сторон; КОНТАКТ НЕ ПРОВЕРЕН"
                else:
                    evidence = source
                self.loading_orientation_status.setText(
                    "Оператор ← (-S)  |  Упор → (+S)  |  {}: {}  |  {}"
                    .format(
                        bend_id,
                        "РАЗВОРОТ 180°" if reversed_pose else "обычно 0°",
                        evidence,
                    )
                )

            def _ensure_press_cycle_controller(self) -> PressCycleController | None:
                if self.session is None:
                    self.press_cycle = None
                    return None
                if self.press_cycle is not None:
                    ctrl = getattr(self.session, "controller", None)
                    if ctrl is None or getattr(self.press_cycle, "sequence", None) is ctrl:
                        self.press_cycle.rear_panel_vertices_xy = (
                            self.session.panel_vertices_xy
                            if self.backgauge_model is not None else None
                        )
                        self.press_cycle.loading_orientation_by_bend = (
                            self._effective_loading_orientation_map()
                        )
                        self.press_cycle.initial_feed_preview_mm = (
                            self.backgauge_model.initial_feed_preview_mm
                            if self.backgauge_model is not None else 0.0
                        )
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
                self.press_cycle.rear_panel_vertices_xy = (
                    self.session.panel_vertices_xy
                    if self.backgauge_model is not None else None
                )
                self.press_cycle.loading_orientation_by_bend = (
                    self._effective_loading_orientation_map()
                )
                self.press_cycle.initial_feed_preview_mm = (
                    self.backgauge_model.initial_feed_preview_mm
                    if self.backgauge_model is not None else 0.0
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
                    self.pause_btn.setIcon(QtGui.QIcon(str(
                        _ICON_ROOT / "control_pause.svg"
                    )))
                    self.pause_btn.setText("Ⅱ Pause")
                    self.pause_btn.setEnabled(False)
                    self.move_up_btn.setEnabled(False)
                    self.move_down_btn.setEnabled(False)
                    self.restore_order_btn.setEnabled(False)
                    self.calculate.setEnabled(False)
                    self.planar_calculate.setEnabled(False)
                    return
                st = self.session.playback_status
                self.pause_btn.setIcon(QtGui.QIcon(str(
                    _ICON_ROOT / ("control_play.svg"
                                  if st == PlaybackStatus.PAUSED
                                  else "control_pause.svg")
                )))
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

            def load_part(self, selection=None, document=None):
                """Load selected SheetMetal object captured before viewer focus."""
                import FreeCAD as App
                import FreeCADGui as Gui

                doc = document if document is not None else getattr(App, "ActiveDocument", None)
                if selection is None:
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
                self._last_manual_sequence_report = None
                self._preview_only_planar_order = None
                self._partial_preview_limit = 0
                self._selected_planar_gauge_order = ()
                self._selected_planar_gauge_positions = {}
                self._selected_planar_gauge_model_id = None
                self._selected_planar_loading_orientation = {}
                self._backgauge_preview_cache.clear()
                self._manual_sequence_prefix = []
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
                self._draw_flat_pattern(reset_camera=True)
                if self._viewer_tabs.currentIndex() == 1:
                    self._update_3d_preview()
                QtCore.QTimer.singleShot(0, self.fit)

            def _validate_export_manual_order(self):
                if self.session is None:
                    self.status.setText("Сначала загрузите деталь.")
                    return
                prefix = tuple(self._manual_sequence_prefix)
                if not prefix:
                    self.status.setText(
                        "Сначала пронумеруйте линии на вкладке «Развёртка · Порядок»."
                    )
                    return
                if self.timer.isActive() or self.session.playback_status == PlaybackStatus.PLAYING:
                    self.status.setText("Остановите симуляцию перед проверкой порядка.")
                    return
                self._stop_timer()
                self.manual_validate_btn.setEnabled(False)
                self.status.setText(
                    "Stage A/B: проверка ручного порядка {} гибов...".format(len(prefix))
                )
                QtWidgets.QApplication.processEvents()
                try:
                    import json
                    from smbs_core.kinematics2d.manual_sequence_replay import (
                        evaluate_manual_planar_order,
                    )
                    has_sections = bool(
                        getattr(self.tooling_setup, "punch_section_offsets_xm", ())
                        or getattr(self.tooling_setup, "die_section_offsets_xm", ())
                    )
                    report = evaluate_manual_planar_order(
                        self.session, self.tooling_setup, prefix,
                        match_active_bend_length=not has_sections,
                        allow_retract_warning=self.retract_warning_checkbox.isChecked(),
                        weights=self._selected_operation_weights(),
                        backgauge_model=self.backgauge_model,
                    )
                    # Identify the exact source geometry used for this run.
                    report["model"] = {
                        "part_label": self.session.part_label,
                        "unfold_object": getattr(self.session.unfold_data, "unfold_object", ""),
                        "source_object": getattr(self.session.unfold_data, "source_object", ""),
                        "source_face": getattr(self.session.unfold_data, "source_face", ""),
                        "thickness_mm": getattr(self.session.unfold_data, "thickness_mm", None),
                        "fixed_panel": self.session.fixed_panel,
                    }
                    report["bend_geometry"] = [
                        {
                            "bend_id": b.bend_id,
                            "angle_deg": float(b.angle_deg),
                            "fixed_panel": b.fixed_panel,
                            "moving_panel": b.moving_panel,
                            "axis_xy": [[float(v) for v in pt]
                                        for pt in next(
                                            conn.axis_xy for conn in
                                            self.session.panel_build.connections
                                            if conn.bend_id == b.bend_id
                                        )],
                        }
                        for b in self.session.ordered_bends
                    ]
                    report["tooling"] = {
                        "punch": (getattr(self.tooling_setup.punch, "name", None)
                                  if self.tooling_setup.punch is not None else None),
                        "die": (getattr(self.tooling_setup.die, "name", None)
                                if self.tooling_setup.die is not None else None),
                        "allow_end_zone_collision_requested": bool(
                            self.tooling_setup.allow_end_zone_collision),
                        "end_zone_length_mm": float(self.tooling_setup.end_zone_length_mm),
                        "end_zone_exemption_applied": False,
                    }
                    report["runtime"] = dict(self.runtime_git_info)
                    self._last_manual_sequence_report = dict(report)
                    score_info = report["plan_cost"]
                    cost_label = (
                        "Штраф принятого порядка {:.2f} балла ({}/{} выбранных гибов). "
                        "Расчёт: {}.".format(
                            score_info["total"],
                            len(report["accepted_prefix"]),
                            len(prefix),
                            "полный выбранный порядок"
                            if report["plan_cost_selected_order_complete"]
                            else "только принятые гибы",
                        )
                        if score_info["is_complete"]
                        else "Штраф не подтверждён: нет полной геометрии установки."
                    )
                    target, _ = QtWidgets.QFileDialog.getSaveFileName(
                        self, "Сохранить проверку ручной последовательности",
                        str(Path.home() / "smbs_manual_sequence_diagnostics.json"),
                        "JSON (*.json)",
                    )
                    if not target:
                        self.status.setText(
                            "Проверка выполнена: {}. {} Сохранение отменено.".format(
                                report["status"], cost_label)
                        )
                        return
                    Path(target).write_text(
                        json.dumps(report, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8",
                    )
                    verdict = (
                        "Условная 2D-проверка: предупреждений RETRACT {}, "
                        "не проверено в 3D".format(len(report["retract_warnings"]))
                        if report["first_blocker"] is None and report["retract_warnings"]
                        else "Нет 2D-препятствий в проверенных сечениях (НЕ 3D-допуск)"
                        if report["first_blocker"] is None else
                        "Проверка остановлена: гиб {bend_id}, шаг {step_number}, "
                        "{evidence}: {reason}".format(**report["first_blocker"])
                    )
                    self.status.setText(
                        "JSON: {} | {} | {}".format(target, verdict, cost_label)
                    )
                    self.manual_hint.setText(
                        "Диагностика сохранена: {}. {}. {} "
                        "Передайте JSON для разбора.".format(target, verdict, cost_label)
                    )
                except Exception as exc:
                    self.status.setText("Ошибка проверки ручного порядка: " + str(exc))
                    _error("Manual-order diagnostic exception: " + traceback.format_exc())
                finally:
                    self.manual_validate_btn.setEnabled(True)

            def _toggle_manual_order(self, enabled):
                self._manual_selection_enabled = bool(enabled)
                self.manual_mode_btn.setText(
                    "Ручной порядок: ВКЛ" if enabled else "Ручной порядок: ВЫКЛ"
                )
                if enabled:
                    if self.session is None:
                        self.manual_mode_btn.setChecked(False)
                        self.status.setText("Сначала загрузите развёртку SheetMetal.")
                        return
                    self._stop_timer()
                    self.session.reset_all_bends()
                    self._viewer_tabs.setCurrentWidget(self._flat_view)
                    self._draw_flat_pattern(reset_camera=True)
                self._update_manual_hint()

            def _update_manual_hint(self):
                if not hasattr(self, "manual_hint"):
                    return
                prefix = self._manual_sequence_prefix
                self.manual_hint.setText(
                    "Ручная последовательность {}/{}: {}. "
                    "Нажмите на ненумерованную линию развёртки; номера ставятся по порядку. "
                    "Порядок ещё НЕ проверен физически.".format(
                        len(prefix),
                        len(self.session.bend_order) if self.session else 0,
                        " → ".join(prefix) or "не назначена"
                    )
                )

            def _select_flat_bend(self, bend_id):
                if self.session is None or not self._manual_selection_enabled:
                    return
                if bend_id in self._manual_sequence_prefix:
                    self.status.setText(
                        "Линия {} уже имеет номер {}.".format(
                            bend_id, self._manual_sequence_prefix.index(bend_id) + 1)
                    )
                    return
                if bend_id not in self.session.fold_state.bends:
                    self.status.setText("Неизвестная линия: " + str(bend_id))
                    return
                if not self.session.can_reorder():
                    self.status.setText(
                        "Изменение порядка недоступно: остановите симуляцию и сделайте Reset."
                    )
                    return
                self._manual_sequence_prefix.append(bend_id)
                self._apply_manual_order()

            def _apply_manual_order(self):
                if self.session is None:
                    return
                prefix = tuple(self._manual_sequence_prefix)
                remainder = tuple(
                    bend_id for bend_id in self.session.canonical_order
                    if bend_id not in prefix
                )
                try:
                    self.session.set_bend_order(prefix + remainder)
                except SequenceError as exc:
                    self.status.setText(str(exc))
                    if prefix:
                        self._manual_sequence_prefix.pop()
                    return
                if prefix:
                    self.session.select_bend_index(len(prefix) - 1)
                self._preview_only_planar_order = None
                self._partial_preview_limit = 0
                self._selected_planar_gauge_order = ()
                self._selected_planar_gauge_positions = {}
                self._selected_planar_gauge_model_id = None
                self._selected_planar_loading_orientation = {}
                self._backgauge_preview_cache.clear()
                self.last_sequence_search_report = None
                self._conditional_bends_status.clear()
                self._populate_operations()
                self._configure_for_active_bend()
                self._draw_flat_pattern()
                self._update_manual_hint()
                self.status.setText(
                    "РУЧНОЙ ПОРЯДОК {}/{} (НЕ ПРОВЕРЕН): {}".format(
                        len(prefix), len(self.session.bend_order),
                        " → ".join(prefix)
                    )
                )

            def _undo_manual_bend(self):
                if self.session is None or not self.session.can_reorder():
                    return
                if self._manual_sequence_prefix:
                    self._manual_sequence_prefix.pop()
                    self._apply_manual_order()

            def _clear_manual_order(self):
                if self.session is None:
                    return
                self._stop_timer()
                self.session.reset_all_bends()
                self._manual_sequence_prefix = []
                self._apply_manual_order()

            def _draw_flat_pattern(self, reset_camera=False):
                if not hasattr(self, "_flat_scene"):
                    return
                scene = self._flat_scene
                scene.clear()
                if self.session is None:
                    scene.addSimpleText("Развёртка не загружена")
                    return
                polygon_pen = QtGui.QPen(QtGui.QColor("#738194"))
                polygon_pen.setWidthF(0.35)
                fill = QtGui.QBrush(QtGui.QColor("#e7edf2"))
                for vertices in self.session.panel_vertices_xy.values():
                    if len(vertices) < 3:
                        continue
                    poly = QtGui.QPolygonF([
                        QtCore.QPointF(float(x), -float(y))
                        for x, y in vertices
                    ])
                    scene.addPolygon(poly, polygon_pen, fill).setZValue(0)
                selected = set(self._manual_sequence_prefix)
                for conn in self.session.panel_build.connections:
                    bend_id = conn.bend_id
                    (x1, y1), (x2, y2) = conn.axis_xy
                    chosen = bend_id in selected
                    active = (chosen and self._manual_sequence_prefix[-1] == bend_id)
                    color = "#e67e22" if active else ("#219653" if chosen else "#2864ac")
                    pen = QtGui.QPen(QtGui.QColor(color))
                    pen.setWidthF(2.3 if active else 1.4)
                    pen.setCosmetic(True)
                    item = scene.addLine(float(x1), -float(y1),
                                         float(x2), -float(y2), pen)
                    item.setData(0, bend_id)
                    item.setZValue(10)
                    item.setToolTip("{} {:+.1f}°".format(bend_id, conn.angle_deg))
                    hit_pen = QtGui.QPen(QtGui.QColor(0, 0, 0, 0))
                    hit_pen.setWidth(13)
                    hit_pen.setCosmetic(True)
                    hit = scene.addLine(float(x1), -float(y1),
                                        float(x2), -float(y2), hit_pen)
                    hit.setZValue(12)
                    hit.setData(0, bend_id)
                    hit.setToolTip("{} {:+.1f}°".format(bend_id, conn.angle_deg))
                    mx, my = (float(x1)+float(x2))/2, -(float(y1)+float(y2))/2
                    number = (str(self._manual_sequence_prefix.index(bend_id)+1)
                              if chosen else "{} {:+.0f}°".format(bend_id, conn.angle_deg))
                    label = scene.addSimpleText(number)
                    label.setBrush(QtGui.QBrush(QtGui.QColor(color)))
                    label.setData(0, bend_id)
                    label.setZValue(15)
                    label.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations)
                    label.setPos(mx, my)
                bounds = scene.itemsBoundingRect().adjusted(-10, -10, 10, 10)
                scene.setSceneRect(bounds)
                if reset_camera:
                    self._flat_view.reset_camera()

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
                # Move Up/Down and Restore replace the manually numbered prefix.
                self._manual_sequence_prefix = []
                self._draw_flat_pattern()
                self._update_manual_hint()
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
                if (self._partial_preview_limit and all(
                    self.session.controller.is_bend_completed(bid)
                    for bid in self.session.bend_order[:self._partial_preview_limit]
                )):
                    self.status.setText(
                        "Частичный 2D-просмотр завершён: следующие гибы не проверены."
                    )
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
                # A partial candidate must finish retracting the final
                # punch before stopping. Otherwise the last sampled geometry
                # freezes at BOTTOM/BENDING and looks like an illegal setup.
                partial_prefix_done = bool(
                    self._partial_preview_limit and all(
                        self.session.controller.is_bend_completed(bid)
                        for bid in self.session.bend_order[
                            :self._partial_preview_limit
                        ]
                    )
                )
                partial_safe_to_stop = (
                    self.execution_mode != CycleExecutionMode.FULL_CYCLE
                    or self.press_cycle is None
                    or self.press_cycle.phase in (
                        CyclePhase.STEP_COMPLETE, CyclePhase.COMPLETED,
                        CyclePhase.IDLE,
                    )
                )
                if partial_prefix_done and partial_safe_to_stop:
                    self.session.pause()
                    if self.press_cycle is not None:
                        self.press_cycle.pause()
                    self._stop_timer()
                    self._finish_motion_trace("PARTIAL_2D_PREVIEW_FINISHED")
                    self.status.setText(
                        "ЧАСТИЧНЫЙ ПРОСМОТР ЗАВЕРШЁН: {}/{} гибов; "
                        "остальные не проверены и остаются плоскими.".format(
                            self._partial_preview_limit, len(self.session.bend_order)
                        )
                    )
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
                    self._preview_3d.reset_camera()
                    return
                self.view.reset_camera()

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
                close_workspace_controls(self)
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

            def _backgauge_preview_position(self, index):
                """Compute a planning target on isolated FoldState, cache by order.

                Never mutate the live FoldState in the playback timer.
                An unresolvable target stays UNKNOWN, not a fabricated origin.
                """
                model = self.backgauge_model
                session = self.session
                if model is None or session is None:
                    return None
                order = tuple(session.bend_order)
                if index < 0 or index >= len(order):
                    return None
                if (model.part_label and
                        model.part_label != session.part_label):
                    return None
                # Read the exact S/Z confirmed for the selected candidate.
                # These are plotted as preview targets; no continuous
                # transfer or 3D manufacturing certification is implied.
                selected_order = self._selected_planar_gauge_order
                if (selected_order
                        and order[:len(selected_order)] == selected_order
                        and self._selected_planar_gauge_model_id == id(model)
                        and index in self._selected_planar_gauge_positions):
                    from smbs_core.kinematics2d.backgauge_2d import (
                        BackgaugePosition2D,
                    )
                    s, z = self._selected_planar_gauge_positions[index]
                    return (
                        BackgaugePosition2D(s, z),
                        s - model.width_s_mm / 2,
                        "SELECTED_DFS_CONTACT_SAMPLED_NOT_3D_CERTIFIED",
                    )
                key = (id(session), order, id(model), index)
                if key in self._backgauge_preview_cache:
                    return self._backgauge_preview_cache[key]
                try:
                    from smbs_core.kinematics2d.panel_kinematics import FoldState
                    model_state = FoldState(
                        session.fold_state.graph, session.fold_state.fixed_panel
                    )
                    for bid in order[:index]:
                        model_state.set_angle(
                            bid, float(model_state.bends[bid].angle_deg)
                        )
                    if model.cad_geometry_enabled:
                        from smbs_core.kinematics2d.backgauge_cad_3d import (
                            BackgaugeCAD3D,
                        )
                        stations_key = (
                            model.cad_station_xm_mm,
                            model.cad_second_station_xm_mm,
                        )
                        cached = getattr(
                            self, "_backgauge_cad_draw_cache", None
                        )
                        if cached is None or cached[0] != stations_key:
                            adapter = BackgaugeCAD3D(
                                station_xm_mm=model.cad_station_xm_mm,
                                second_station_xm_mm=(
                                    model.cad_second_station_xm_mm
                                ),
                            )
                            self._backgauge_cad_draw_cache = (
                                stations_key, adapter,
                            )
                        else:
                            adapter = cached[1]
                        point, depth, evidence, solution = (
                            adapter.resolve_contact_for_bend(
                                model_state, session.panel_vertices_xy,
                                order[index], model,
                                reverse_axis=bool(
                                    self._effective_loading_orientation_map().get(
                                        order[index], False
                                    )
                                ),
                            )
                        )
                        evidence += "|" + solution["note"]
                    else:
                        point, depth, evidence = model.resolve_for_state(
                            model_state, session.panel_vertices_xy,
                            order[index],
                        )
                    result = (point, depth, evidence)
                except Exception as exc:
                    _warn("Backgauge preview target unknown for {}: {}".format(
                        order[index], exc
                    ))
                    result = None
                self._backgauge_preview_cache[key] = result
                return result

            def _draw_backgauge_preview(self, QtCore, QtGui, make_poly):
                """Render gauge position; interpolate its target during TRANSFER.

                This preview does not check collision along the interpolated path.
                """
                model, session = self.backgauge_model, self.session
                if model is None or session is None:
                    return
                idx = session.active_bend_index
                start = self._backgauge_preview_position(idx)
                if start is None:
                    if model.cad_geometry_enabled:
                        warning = self.scene.addSimpleText(
                            "CAD-УПОР: контакт с опорной панелью НЕ подтверждён"
                        )
                        warning.setBrush(
                            QtGui.QBrush(QtGui.QColor("#d06440"))
                        )
                        warning.setPos(20.0, -65.0)
                        warning.setZValue(Z_LAYER_AUXILIARY)
                        warning.setData(0, "cad_backgauge_contact_unknown")
                    return
                pos = start[0]
                controller = self.press_cycle
                phase = getattr(getattr(controller, "phase", None), "value", "")
                if (phase == "TRANSFER" and
                        self.execution_mode == CycleExecutionMode.FULL_CYCLE
                        and idx + 1 < len(session.bend_order)):
                    target = self._backgauge_preview_position(idx + 1)
                    if target is not None:
                        t = max(0.0, min(1.0, float(controller.transfer_progress)))
                        from smbs_core.kinematics2d.backgauge_2d import BackgaugePosition2D
                        pos = BackgaugePosition2D(
                            start[0].s_mm + (target[0].s_mm-start[0].s_mm)*t,
                            start[0].z_mm + (target[0].z_mm-start[0].z_mm)*t,
                        )
                pen = QtGui.QPen(QtGui.QColor("#b45524"), 1.8)
                pen.setCosmetic(True)
                if model.cad_geometry_enabled:
                    # Draw the actual FCStd BRep cutting section at the
                    # explicitly mounted X_m station. NEVER show a pretend
                    # 10x10 rectangle when CAD loading fails.
                    try:
                        from smbs_core.kinematics2d.backgauge_cad_3d import (
                            BackgaugeCAD3D,
                        )
                        cached = getattr(self, "_backgauge_cad_draw_cache", None)
                        stations_key = (
                            model.cad_station_xm_mm,
                            model.cad_second_station_xm_mm,
                        )
                        if cached is None or cached[0] != stations_key:
                            adapter = BackgaugeCAD3D(
                                station_xm_mm=model.cad_station_xm_mm,
                                second_station_xm_mm=(
                                    model.cad_second_station_xm_mm
                                ),
                            )
                            self._backgauge_cad_draw_cache = (
                                stations_key, adapter,
                            )
                        else:
                            adapter = cached[1]
                        # Source Face3 is positioned on the contact edge.
                        # In auto mode the old 2D centre is depth+halfwidth,
                        # but the real CAD stop FACE must be at 'depth'.
                        contact_s = (
                            pos.s_mm - model.width_s_mm / 2
                        )
                        shapes = adapter.positioned_shapes(
                            contact_s, pos.z_mm
                        )
                        from FreeCAD import Vector
                        stations = (
                            [model.cad_station_xm_mm] +
                            ([model.cad_second_station_xm_mm]
                             if model.cad_second_station_xm_mm is not None
                             else [])
                        )
                        path = QtGui.QPainterPath()
                        for station_xm, shape in zip(stations, shapes):
                            wires = shape.slice(
                                Vector(1, 0, 0),
                                float(station_xm),
                            )
                            if not wires:
                                raise ValueError(
                                    "CAD has no section at station X_m={} mm"
                                    .format(station_xm)
                                )
                            for wire in wires:
                                points = []
                                for edge in wire.Edges:
                                    pts = edge.discretize(Number=24)
                                    for pt in pts:
                                        qpt = QtCore.QPointF(
                                            -float(pt.y), -float(pt.z)
                                        )
                                        if not points or points[-1] != qpt:
                                            points.append(qpt)
                                if len(points) < 2:
                                    continue
                                path.moveTo(points[0])
                                for qpt in points[1:]:
                                    path.lineTo(qpt)
                                if wire.isClosed():
                                    path.closeSubpath()
                        if path.isEmpty():
                            raise ValueError("CAD cross-section has no drawable edges")
                        item = self.scene.addPath(
                            path, pen,
                            QtGui.QBrush(QtGui.QColor(190, 106, 51, 80))
                        )
                        item.setData(0, "backgauge_real_fcstd_cad_section")
                        label_text = (
                            "Backgauge.FCStd | CAD-сечение {} упоров | "
                            "путь НЕ сертифицирован"
                        ).format(len(shapes))
                    except Exception as exc:
                        item = None
                        label_text = "ОШИБКА CAD УПОРА: " + str(exc)[:100]
                        _warn(label_text)
                else:
                    polygon = make_poly(model.polygon_at(pos))
                    item = self.scene.addPolygon(
                        polygon, pen,
                        QtGui.QBrush(QtGui.QColor(190, 106, 51, 140))
                    )
                    item.setData(0, "backgauge_2d_unverified")
                    label_text = "Упор 2D · путь НЕ проверен"
                if item is not None:
                    item.setZValue(Z_LAYER_AUXILIARY)
                label = self.scene.addSimpleText(label_text)
                if phase == "LOAD_TO_GAUGE":
                    feed_note = self.scene.addSimpleText(
                        "ПОДВОД ЗАГОТОВКИ К УПОРУ · ПУТЬ НЕ ПРОВЕРЕН"
                    )
                    feed_note.setBrush(QtGui.QBrush(QtGui.QColor("#b45524")))
                    feed_note.setPos(
                        float(pos.s_mm)-100.0, -float(pos.z_mm)-25.0
                    )
                    feed_note.setZValue(Z_LAYER_AUXILIARY)
                label.setBrush(QtGui.QBrush(QtGui.QColor("#b45524")))
                label.setPos(float(pos.s_mm), -float(pos.z_mm)-12.0)
                label.setZValue(Z_LAYER_AUXILIARY)

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
                                current_placement = self._current_preview_machine_placement()
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
                                    reverse_axis=bool(
                                        self._effective_loading_orientation_map().get(
                                            str(self.session.active_bend_id), False
                                        )
                                    ),
                                    orientation_status=self._ergonomic_loading_status,
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
                            # Bend Only is a machine-side preview too. Using
                            # source-local cross_section_2d silently ignored
                            # the search-selected 180-degree loading pose.
                            try:
                                current_placement = (
                                    self._current_preview_machine_placement()
                                )
                                sec = compute_machine_cutting_plane_section(
                                    fs, verts, current_placement,
                                    active_bend_id=self.session.active_bend_id,
                                    segments=32,
                                )
                                self._last_valid_machine_section = sec
                                self._last_valid_machine_pose = pose
                                self._last_valid_machine_bend = (
                                    self.session.active_bend_id
                                )
                            except (WorkpiecePoseError, PressCycleError,
                                    ValueError, KeyError) as exc:
                                pose_warning = (
                                    "BEND_ONLY_MACHINE_POSE_UNVERIFIED:" + str(exc)
                                )
                                if self._last_valid_machine_section is not None:
                                    sec = self._last_valid_machine_section
                                    pose = self._last_valid_machine_pose
                                else:
                                    sec = fs.cross_section_2d(
                                        reference_id, verts, segments=32
                                    )

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

                        # Gauge movement is a visual planning preview; Stage B
                        # validates targets only, not its entire transfer path.
                        self._draw_backgauge_preview(QtCore, QtGui, make_sheet_poly)

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
                        self._update_loading_orientation_label()
                    else:
                        self.step.setText("Активный гиб: — | Угол: — / —")
                        self._update_press_cycle_labels()
                        self._update_loading_orientation_label()

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
