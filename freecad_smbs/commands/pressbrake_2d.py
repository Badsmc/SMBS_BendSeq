"""PressBrakeSim manual SheetMetal simulation in Coin3D.

Uses the existing SheetMetal extraction and FoldState geometry:
  sheetmetal_unfold_reader.read_unfold
  -> panel_builder.build_panels
  -> panel_graph.graph_from_panel_build
  -> panel_kinematics.FoldState (with explicit BendZone)
"""
from __future__ import annotations

import hashlib
import math
import os
import sys
import time
import traceback
import weakref
from dataclasses import dataclass, field, replace
from pathlib import Path

from smbs_core.kinematics2d.developer_debug_log import (
    debug_event as _desktop_debug_event,
    desktop_directory as _debug_desktop_directory,
)
from smbs_core.kinematics2d.fold_sequence import (
    BendSequenceController,
    PlaybackStatus,
    SequenceError,
    parse_manual_bend_order,
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
    build_sheet_preview_faces, transform_preview_faces, IDENTITY_PLACEMENT, PreviewMeshError,
)
from smbs_core.kinematics2d.machine_section_3d import compute_machine_cutting_plane_section
from smbs_core.kinematics2d.tooling_assembly import has_native_sections
from smbs_core.kinematics2d.workpiece_placement_3d import PlacementTransform3D, WorkpiecePlacement3D
from smbs_core.kinematics2d.workpiece_pose_2d import WorkpiecePose2D, WorkpiecePoseError
from smbs_core.kinematics2d.tooling_25d import (
    DEFAULT_INITIAL_PUNCH_CLEARANCE_MM,
    NOT_SPECIFIED,
    ToolingError,
    ToolingSetup2D,
    ToolKind,
    ToolLibrary25D,
    ToolProfile,
    ValidationStatus,
    import_tool_from_dxf,
    import_tool_from_freecad_object,
    import_tool_from_step,
)

_SLIDER_SCALE = 10  # 0.1 degree slider resolution
_LOADING_ORIENTATION_COLUMN = 2


class _SMBSFCStdSaveObserver:
    """Watch Ctrl+S on the bound document, without a second save path.

    FreeCAD's slotStartSaveDocument runs before the native FCStd serializer.
    This observer MUST NOT call document.save(), or it would recurse.
    """

    def __init__(self, workspace):
        self._panel_ref = weakref.ref(workspace)

    def slotStartSaveDocument(self, document, filename):
        panel = self._panel_ref()
        if panel is None or panel.session is None:
            return
        if not getattr(panel, "_smbs_allow_auto_embed", False):
            return
        if str(getattr(document, "Name", "")) != str(
            getattr(panel, "_smbs_document_name", "")
        ):
            return
        try:
            panel._write_embedded_smbs_json(document)
        except Exception as exc:
            # Never corrupt or abort the user's CAD document save.
            message = "SMBS JSON не вложен в FCStd: " + str(exc)
            _error(message + "\n" + traceback.format_exc())
            panel.status.setText(message)



def _pressbrake_user_data_directory():
    """Platform-specific writable diagnostics home, not the plugin checkout.

    FreeCAD installations may be under Program Files, a read-only AppImage,
    or a macOS .app bundle. Traces must never depend on repository permissions.
    """
    runtime = sys.modules.get("FreeCAD")
    if runtime is not None:
        try:
            directory = runtime.getUserAppDataDir()
            if directory:
                return Path(directory).expanduser()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            pass
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
    return base / "FreeCAD"


def _qt():
    """Return QtCore, QtGui, QtWidgets for FreeCAD-supported bindings."""
    try:
        # FreeCAD 1.x exposes a PySide compatibility shim. On Qt6 it has
        # QtWidgets separately; on older PySide/Qt4 widgets live in QtGui.
        from PySide import QtCore, QtGui
        try:
            from PySide import QtWidgets
        except ImportError:
            QtWidgets = QtGui
        return QtCore, QtGui, QtWidgets
    except ImportError:
        try:
            from PySide2 import QtCore, QtGui, QtWidgets
            return QtCore, QtGui, QtWidgets
        except ImportError:
            from PySide6 import QtCore, QtGui, QtWidgets
            return QtCore, QtGui, QtWidgets


def _log(message):
    """Write normal diagnostics to the ONE Desktop JSON, not a second .log."""
    _desktop_debug_event("FREECAD_INFO", message=str(message))


def _warn(message):
    """Log warning without FreeCAD's potentially reentrant NotificationArea."""
    _desktop_debug_event("FREECAD_WARNING", message=str(message))


def _error(message):
    """Log traceback or error in the ONE Desktop debug JSON."""
    _desktop_debug_event("FREECAD_ERROR", message=str(message))


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
    # ZIP installs lack .git, therefore git_commit=UNKNOWN is expected.
    # Hash the actual Mod tree and record an unambiguous identity of the
    # source implementation instead of guessing it matches GitHub HEAD.
    try:
        from smbs_core.kinematics2d.runtime_build_identity import (
            runtime_source_fingerprint,
        )
        source_identity = runtime_source_fingerprint(repo_root)
    except Exception as exc:
        source_identity = {
            "schema_version": "smbs.runtime_source_sha256.v1",
            "error_type": type(exc).__name__,
            "required_source_files_present": False,
            "composite_source_sha256": None,
        }
    loaded_modules = {}
    for name in (
        "smbs_core.kinematics2d.manual_physical_validation",
        "smbs_core.kinematics2d.native_collision_3d",
        "smbs_core.kinematics2d.motion_trajectory_3d",
    ):
        module = sys.modules.get(name)
        loaded_modules[name] = (
            str(Path(module.__file__).resolve())
            if module is not None and getattr(module, "__file__", None)
            else None
        )
    return {
        "module_file": str(mod_path),
        "repo_root": str(repo_root),
        "git_branch": branch,
        "git_commit": commit,
        "installed_source_identity": source_identity,
        "loaded_native_module_origins_at_start": loaded_modules,
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
    local_bend_dimensions_mm: dict = field(default_factory=dict)
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
    # Local bend-line distance is independent of the visual machine setup.
    # Flat development does not incorporate thickness or previous fold angles.
    from smbs_core.kinematics2d.gauge_preview import (
        local_bend_dimension_mm,
    )
    outline_xy = getattr(panel_build, "outline_vertices_xy", ())
    local_bend_dimensions_mm = {}
    axes_xy = {
        bend.bend_id: tuple((p[0], p[1]) for p in bend.axis_xyz)
        for bend in fold_state.bends.values()
    }
    for bend in fold_state.bends.values():
        bid = bend.bend_id
        try:
            local_bend_dimensions_mm[bid] = local_bend_dimension_mm(
                axes_xy[bid], outline_xy,
                tuple(axis for other_id, axis in axes_xy.items() if other_id != bid),
            )
        except (ValueError, TypeError, KeyError) as exc:
            _warn("Local bend dimension unavailable for {}: {}".format(
                bid, exc,
            ))
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
        local_bend_dimensions_mm=local_bend_dimensions_mm,
    )


_ICON_ROOT = Path(__file__).resolve().parents[1] / "resources" / "icons"
class _SMBSNativeTaskPanel:
    """FreeCAD-native Tasks adapter; NOT a custom QDockWidget.

    The authoritative controls, signal connections and press-cycle timer are
    owned by the MDI workspace. Every native Tasks activation gets a fresh
    disposable form which temporarily hosts the *existing* control widgets.
    When FreeCAD closes or replaces this task, we detach those widgets
    BEFORE it can destroy the form; reopening cannot lose the simulation.
    """

    def __init__(self, workspace, form):
        self.workspace = workspace
        self.form = form
        self._attached = True

    def getStandardButtons(self):
        # A task's standard Cancel closes only the controls, NOT the
        # simulation workspace or the user's FCStd document.
        _QtCore, _QtGui, QtWidgets = _qt()
        # PySide6 exposes a scoped enum which is not directly int()-castable
        # on some FreeCAD/macOS Qt builds. FreeCAD's Tasks API expects a mask.
        cancel = QtWidgets.QDialogButtonBox.StandardButton.Cancel
        return int(getattr(cancel, "value", cancel))

    def modifyStandardButtons(self, button_box):
        _QtCore, _QtGui, QtWidgets = _qt()
        button = button_box.button(QtWidgets.QDialogButtonBox.Cancel)
        if button is not None:
            button.setText("Закрыть")

    def isAllowedAlterDocument(self):
        return True

    def isAllowedAlterSelection(self):
        # SheetMetal's original unfold may be selected while Tasks is open.
        return True

    def isAllowedAlterView(self):
        return True

    def _detach(self):
        if not self._attached:
            return
        _desktop_debug_event("TASKS_CLOSE_DETACH", panel="SMBS_NativeTasksRoot")
        self._attached = False
        workspace = self.workspace
        self.workspace = None
        if workspace is None:
            return
        # Native FreeCAD owns self.form. Detach all *reusable* children
        # before the TaskDialogPython releases that Qt form.
        # Hidden widgets do not auto-show when reparented on Tasks reopen.
        # Explicitly remove them from the disposable FreeCAD form layout.
        for child in (
            workspace._task_stack, workspace.status,
            workspace.task_progress, workspace._task_status_chip,
            workspace._task_progress_caption,
        ):
            old_parent = child.parentWidget()
            old_layout = old_parent.layout() if old_parent is not None else None
            if old_layout is not None:
                old_layout.removeWidget(child)
            child.setParent(workspace)
            child.hide()
        if getattr(workspace, "_native_task_panel", None) is self:
            workspace._native_task_panel = None
            workspace._task_section_label = None
            workspace.show_task_panel_btn.show()

    def closed(self):
        # FreeCAD can close Tasks externally when another edit begins.
        self._detach()

    def accept(self):
        return self.reject()

    def reject(self):
        import FreeCADGui as Gui
        self._detach()
        Gui.Control.closeDialog()
        return True


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
    """Display ALL SMBS control pages inside the standard FreeCAD Tasks view.

    One native Gui.Control.showDialog is active at a time. No independent dock
    or floating addon-owned dock is created. The MDI contains the Coin3D
    preview and the flat pattern used to select bends.
    """
    import FreeCADGui as Gui
    QtCore, _QtGui, QtWidgets = _qt()
    from freecad_smbs.ui.pressbrake_theme import OUTER_MARGIN, SECTION_GAP

    if tab is not None and 0 <= int(tab) < workspace._task_stack.count():
        workspace._task_stack.setCurrentIndex(int(tab))
    section = workspace._task_stack.currentWidget()
    # Explicit Manual orientation must update the same live viewer pose.
    # Never borrow an unverified 2D heuristic for native 3D validation.
    cycle = getattr(workspace, "press_cycle", None)
    if (cycle is not None
            and getattr(workspace, "session", None) is not None
            and hasattr(workspace, "_effective_loading_orientation_map")):
        cycle.loading_orientation_by_bend = (
            workspace._effective_loading_orientation_map()
        )
        if hasattr(workspace, "_rear_frame_for_preview"):
            cycle.rear_panel_vertices_xy = (
                workspace._rear_frame_for_preview()
            )
    caption = workspace._task_page_titles.get(section, "Панель задач")
    header = getattr(workspace, "_task_section_label", None)
    if header is not None:
        header.setText(caption)

    active = bool(Gui.Control.activeDialog())
    panel = getattr(workspace, "_native_task_panel", None)
    if panel is not None and panel._attached and active:
        _desktop_debug_event(
            "TASKS_SWITCH_SECTION", section=str(caption), index=tab,
        )
        # FreeCAD Tasks already owns this form. Never call showDialog
        # twice or create an extra Qt window simply to switch SMBS pages.
        workspace.show_task_panel_btn.hide()
        return panel
    if panel is not None:
        # The old native task disappeared (ESC, other command, Cancel).
        # Drop only its disposable form; preserve the live controls.
        panel.closed()

    if active:
        _desktop_debug_event(
            "TASKS_OPEN_BLOCKED", reason="another_freecad_task_active",
            requested_section=str(caption),
        )
        # Do not close an unrelated FreeCAD Sketcher/PartDesign task and
        # lose its pending edits merely because an SMBS toolbar was clicked.
        message = (
            "Сначала завершите текущую задачу FreeCAD, "
            "затем откройте раздел SMBS снова."
        )
        workspace.status.setText(message)
        workspace.show_task_panel_btn.show()
        _warn(message)
        return None

    form = QtWidgets.QWidget()
    form.setObjectName("SMBS_NativeTasksRoot")
    form.setWindowTitle("SMBS — " + caption)
    layout = QtWidgets.QVBoxLayout(form)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    header = QtWidgets.QLabel(form)
    header.setObjectName("SMBS_TaskSectionTitle")
    header.setText(caption)
    header.setContentsMargins(OUTER_MARGIN, 5, OUTER_MARGIN, 5)
    font = header.font()
    font.setBold(True)
    header.setFont(font)
    layout.addWidget(header, 0)
    workspace._task_section_label = header

    # Reuse the SAME stacked pages and their original handlers (Manual,
    # Simulation, Tooling, Part, Candidates, Settings, specialist dialogs).
    layout.addWidget(workspace._task_stack, 1)

    footer = QtWidgets.QFrame(form)
    footer.setObjectName("SMBS_TaskFooter")
    footer_layout = QtWidgets.QVBoxLayout(footer)
    footer_layout.setContentsMargins(OUTER_MARGIN, 5, OUTER_MARGIN, 5)
    footer_layout.setSpacing(max(1, SECTION_GAP // 2))
    row = QtWidgets.QHBoxLayout()
    row.addWidget(workspace._task_status_chip)
    row.addStretch(1)
    row.addWidget(workspace._task_progress_caption)
    footer_layout.addLayout(row)
    footer_layout.addWidget(workspace.task_progress)
    workspace.status.setObjectName("SMBS_StatusLabel")
    workspace.status.setWordWrap(True)
    workspace.status.setMaximumHeight(38)
    footer_layout.addWidget(workspace.status)
    layout.addWidget(footer)

    # Closing an earlier native task explicitly hid these live widgets.
    # Qt does not reverse that state when they join a new layout.
    for child in (
        workspace._task_stack, workspace.status,
        workspace.task_progress, workspace._task_status_chip,
        workspace._task_progress_caption,
    ):
        child.show()

    panel = _SMBSNativeTaskPanel(workspace, form)
    workspace._native_task_panel = panel
    try:
        # This opens the actual native FreeCAD Tasks tab, not a new dock.
        Gui.Control.showDialog(panel)
    except Exception:
        panel.closed()
        raise
    workspace._on_task_status_change(workspace.status.text())
    workspace.show_task_panel_btn.hide()
    _desktop_debug_event(
        "TASKS_OPENED", section=str(caption), selected_tab=tab,
        stacked_pages=workspace._task_stack.count(),
    )
    return panel


def activate_task_dialog(workspace, key):
    """Select an existing specialist page, never spawn a second window."""
    widgets = getattr(workspace, "_task_dialogs", {})
    widget = widgets.get(key)
    if widget is None:
        return False
    page = getattr(widget, "_smbs_task_page", widget)
    index = workspace._task_stack.indexOf(page)
    if index < 0:
        widgets.pop(key, None)
        return False
    show_workspace_controls(workspace, tab=index)
    return True


def embed_task_dialog(workspace, widget, title, key):
    """Rehost a real QDialog inside the *single* contextual Tasks surface.

    No extra tabs or second simulation panel are created. QDialog signals,
    callbacks and cleanup remain bound to the original controls.
    """
    QtCore, _QtGui, QtWidgets = _qt()
    from freecad_smbs.ui.pressbrake_theme import (
        OUTER_MARGIN, SECTION_GAP, style_action,
    )
    if activate_task_dialog(workspace, key):
        widget.deleteLater()
        return
    stack = workspace._task_stack
    return_page = stack.currentWidget()
    dialogs = getattr(workspace, "_task_dialogs", None)
    if dialogs is None:
        dialogs = {}
        workspace._task_dialogs = dialogs

    widget.setWindowFlags(QtCore.Qt.Widget)
    widget.setMinimumWidth(0)
    widget.setObjectName("SMBS_Task_" + key)
    original_layout = widget.layout()
    if original_layout is not None:
        original_layout.setContentsMargins(
            OUTER_MARGIN, OUTER_MARGIN, OUTER_MARGIN, OUTER_MARGIN
        )
        original_layout.setSpacing(SECTION_GAP)

    page = QtWidgets.QWidget(stack)
    page.setObjectName("SMBS_EmbeddedPage_" + key)
    page_layout = QtWidgets.QVBoxLayout(page)
    page_layout.setContentsMargins(0, 0, 0, 0)
    page_layout.setSpacing(0)
    scroll = QtWidgets.QScrollArea(page)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
    scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
    scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    scroll.setWidget(widget)
    page_layout.addWidget(scroll, 1)

    bottom = QtWidgets.QWidget(page)
    bottom_layout = QtWidgets.QHBoxLayout(bottom)
    bottom_layout.setContentsMargins(
        OUTER_MARGIN, SECTION_GAP // 2, OUTER_MARGIN, SECTION_GAP // 2
    )
    bottom_layout.addStretch(1)
    close_btn = style_action(QtWidgets.QPushButton("Закрыть раздел"))
    bottom_layout.addWidget(close_btn)
    page_layout.addWidget(bottom, 0)

    widget._smbs_task_page = page
    index = stack.addWidget(page)
    workspace._task_page_titles[page] = title
    dialogs[key] = widget

    def on_finished(_result):
        i = stack.indexOf(page)
        if i < 0:
            return
        was_current = (stack.currentWidget() is page)
        stack.removeWidget(page)
        workspace._task_page_titles.pop(page, None)
        if dialogs.get(key) is widget:
            del dialogs[key]
        if was_current:
            fallback = return_page if stack.indexOf(return_page) >= 0 else stack.widget(0)
            if fallback is not None:
                show_workspace_controls(workspace, tab=stack.indexOf(fallback))
        page.deleteLater()

    def on_close():
        widget.close()  # honour QDialog.closeEvent (tool previews cleanup)
        if stack.indexOf(page) >= 0:
            on_finished(0)

    widget.finished.connect(on_finished)
    close_btn.clicked.connect(on_close)
    workspace._install_automatic_debug_ui_signals(QtWidgets, root=widget)
    _desktop_debug_event(
        "SPECIALIST_TASK_OPENED", key=str(key), title=str(title),
    )
    show_workspace_controls(workspace, tab=index)


def close_workspace_controls(workspace):
    """Close only our native Tasks dialog; retain the FCStd and CAD viewer."""
    import FreeCADGui as Gui
    panel = getattr(workspace, "_native_task_panel", None)
    if panel is None:
        return
    # FreeCAD may destroy a TaskPanel form when closing it. Reparent the
    # persistent controls first. The closed() callback is idempotent.
    panel.closed()
    if bool(Gui.Control.activeDialog()):
        Gui.Control.closeDialog()
    _desktop_debug_event("TASKS_CLOSE_REQUESTED")


def _has_loaded_part():
    """Single prerequisite for commands following the Detail action."""
    workspace = get_workspace_widget()
    return bool(workspace is not None and workspace.session is not None)


class PressBrake2DCommand:
    def GetResources(self):
        return {
            "Pixmap": str(_ICON_ROOT / "gui_simulation.svg"),
            "MenuText": "Симуляция",
            "ToolTip": "Открыть симуляцию Coin3D и штатную вкладку Задачи FreeCAD",
        }

    def IsActive(self):
        return _has_loaded_part()

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
            workspace = existing.widget()
            # Simulation is navigation only, not an automatic search. Leave
            # the manual order, fold angles and playback state untouched.
            show_workspace_controls(workspace, tab=2)
            viewer = workspace._preview_3d
            if viewer._native and getattr(viewer, "_native_doc", None):
                workspace._update_3d_preview(force=True)
            else:
                workspace._open_coin_preview()
            return

        widget = _PreviewPanel()
        window = QtWidgets.QMdiSubWindow()
        window.setObjectName(name)
        window.setWindowTitle("PressBrakeSim — Coin3D")
        window.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        window.setWidget(widget)
        mdi.addSubWindow(window)
        window.resize(1000, 720)
        window.show()
        mdi.setActiveSubWindow(window)
        # A valid selected part must be loaded BEFORE handing the control
        # widgets to the native Tasks pane. Previously the empty-state
        # panel could appear first and remain visually blank on macOS.
        if doc is not None and selected:
            widget.load_part(selection=selected, document=doc)
        show_workspace_controls(widget, tab=2)
        widget._open_coin_preview()


class LoadPartCommand:
    def GetResources(self):
        return {
            "Pixmap": str(_ICON_ROOT / "gui_part.svg"),
            "MenuText": "Деталь",
            "ToolTip": "Загрузить выбранную развёртку SheetMetal",
        }

    def IsActive(self):
        # The Detail section must remain accessible before a part is selected.
        return True

    def Activated(self):
        import FreeCAD as App
        import FreeCADGui as Gui
        doc, selection = _selection_snapshot()
        try:
            resolve_selected_unfold(doc, selection)
        except (SelectionWarning, AttributeError, TypeError, ValueError) as exc:
            message = "Выберите развёртку SheetMetal: " + str(exc)
            App.Console.PrintWarning(message + "\\n")
            # Feedback must remain visible with FreeCAD's Report View hidden.
            _QtCore, _QtGui, QtWidgets = _qt()
            QtWidgets.QMessageBox.information(
                Gui.getMainWindow(), "SMBS — Деталь", message
            )
            workspace = get_workspace_widget()
            if workspace is not None:
                workspace.status.setText(message)
            return

        workspace = get_workspace_widget()
        if workspace is None:
            # Bootstrap the viewer only AFTER validating the selection.
            # PressBrake2DCommand loads this same valid selection once.
            PressBrake2DCommand().Activated()
            workspace = get_workspace_widget()
        else:
            workspace.load_part(selection=selection, document=doc)
        if workspace is None or workspace.session is None:
            App.Console.PrintWarning("SMBS: деталь не загружена.\\n")
            return
        show_workspace_controls(workspace, 0)
        Gui.updateGui()


class ToolingTabCommand:
    def GetResources(self):
        return {
            "Pixmap": str(_ICON_ROOT / "gui_tooling.svg"),
            "MenuText": "Оснастка",
            "ToolTip": "Открыть оснастку симулятора",
        }

    def IsActive(self):
        return _has_loaded_part()

    def Activated(self):
        workspace = get_workspace_widget()
        if workspace is None:
            PressBrake2DCommand().Activated()
            workspace = get_workspace_widget()
        if workspace is not None:
            workspace.open_tooling_settings_dialog()


class CalculateCommand:
    def GetResources(self):
        return {
            "Pixmap": str(_ICON_ROOT / "gui_calculate.svg"),
            "MenuText": "Автопоиск последовательности",
            "ToolTip": "Служебный автоматический поиск; для выбора порядка начните с «Поиска 2D вариантов»",
        }

    def IsActive(self):
        return _has_loaded_part()

    def Activated(self):
        workspace = get_workspace_widget()
        if workspace is None:
            PressBrake2DCommand().Activated()
            workspace = get_workspace_widget()
        if workspace is not None:
            show_workspace_controls(workspace, 2)
            if workspace.calculate.isEnabled():
                workspace.calculate_sequence()


class BackgaugeCommand:
    """Configure explicit backgauge stop geometry and per-bend targets."""
    def GetResources(self):
        return {
            "Pixmap": str(_ICON_ROOT / "gui_backgauge.svg"),
            "MenuText": "Задний упор",
            "ToolTip": "Визуальная FCStd-модель упора: позиция по контуру развёртки",
        }

    def IsActive(self):
        return _has_loaded_part()

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
            "ToolTip": "Открыть сохранённые последовательности гибки из JSON",
        }

    def IsActive(self):
        return _has_loaded_part()

    def Activated(self):
        workspace = get_workspace_widget()
        if workspace is None:
            PressBrake2DCommand().Activated()
            workspace = get_workspace_widget()
        if workspace is not None:
            workspace.open_sequence_catalog_dialog()


class ManualCommand:
    """Enable manual bend-line ordering only after SheetMetal loading."""

    def GetResources(self):
        return {
            "Pixmap": str(_ICON_ROOT / "gui_manual.svg"),
            "MenuText": "Порядок и проверка",
            "ToolTip": "Вручную выбрать порядок гибов, запустить симуляцию или проверить коллизии",
        }

    def IsActive(self):
        workspace = get_workspace_widget()
        return bool(workspace is not None and workspace.session is not None)

    def Activated(self):
        workspace = get_workspace_widget()
        if workspace is None or workspace.session is None:
            return
        workspace.activate_manual_mode()


class SettingsCommand:
    """Open all simulation, machine and diagnostic settings."""

    def GetResources(self):
        return {
            "Pixmap": str(_ICON_ROOT / "gui_settings.svg"),
            "MenuText": "Настройки",
            "ToolTip": "Режим гибки, скорость, проверки, допуски и запись",
        }

    def IsActive(self):
        return _has_loaded_part()

    def Activated(self):
        workspace = get_workspace_widget()
        if workspace is None:
            PressBrake2DCommand().Activated()
            workspace = get_workspace_widget()
        if workspace is not None:
            workspace.open_settings_dialog()


class PlanarCandidatesCommand:
    """Preserve experiment's separate fast 2D candidate search."""
    def GetResources(self):
        return {
            "Pixmap": str(_ICON_ROOT / "gui_planar_candidates.svg"),
            "MenuText": "Поиск 2D вариантов",
            "ToolTip": "Найти полные последовательности гибов за ограниченное время; коллизии 3D ещё не проверены",
        }

    def IsActive(self):
        return _has_loaded_part()

    def Activated(self):
        workspace = get_workspace_widget()
        if workspace is None:
            PressBrake2DCommand().Activated()
            workspace = get_workspace_widget()
        if workspace is not None:
            show_workspace_controls(workspace, 3)
            if workspace.planar_calculate.isEnabled():
                workspace.calculate_planar_candidates()


def _button(widgets, label, callback, enabled=True):
    btn = widgets.QPushButton(label)
    btn.setEnabled(enabled)
    btn.clicked.connect(callback)
    return btn


def _playback_button(QtCore, widgets, label, callback, enabled=True):
    """FreeCAD-style compact toolbar control; keep existing signal handlers."""
    btn = widgets.QToolButton()
    btn.setAutoRaise(True)
    btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
    btn.setText(label)
    btn.setEnabled(enabled)
    btn.clicked.connect(callback)
    return btn


class _PreviewPanel:
    """Lazy Qt widget factory to keep headless command import possible."""

    def __new__(cls, library_root: Path | str | None = None):
        QtCore, QtGui, QtWidgets = _qt()

        class Canvas(QtWidgets.QGraphicsView):
            """2D/3D zoomable viewer; optional 3D orbit controlled by parent Panel."""

            def __init__(self, parent=None, bend_click_callback=None):
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
                self._pan_last_pos = None
                self._pan_button = None
                self.setToolTip(
                    "Колесо мыши: масштаб; средняя кнопка или Ctrl+ЛКМ "
                    "и перетаскивание: перемещение сцены по горизонтали "
                    "и вертикали. Fit: восстановить масштаб и центр."
                )

            def reset_camera(self):
                scene = self.scene()
                if scene is None or scene.sceneRect().isEmpty():
                    return
                self._zoom_steps = 0.0
                self.resetTransform()
                self.fitInView(scene.sceneRect(), QtCore.Qt.KeepAspectRatio)
                # Explicit Fit establishes a new camera selected by the user.
                # Never auto-fit it again during bending or a viewport resize.
                self._user_camera_modified = True

            def update_scene_bounds(self, bounds):
                """Keep the operator's camera fixed across every render tick.

                QGraphicsView derives scroll ranges and pixel alignment from
                sceneRect. Calling setSceneRect() followed by centerOn() at
                30-40 Hz causes cumulative camera drift, even when the requested
                center is numerically unchanged. Once the user zooms, pans, or
                presses Fit, never touch sceneRect or camera here again.
                Geometry may move inside the fixed machine-coordinate view.
                Only explicit user navigation is allowed to change the camera.
                """
                scene = self.scene()
                if scene is None:
                    return
                if self._user_camera_modified:
                    return
                # Avoid redundant Qt scroll-range updates for unchanged bounds.
                if scene.sceneRect() != bounds:
                    scene.setSceneRect(bounds)

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
                # Middle-drag or Ctrl+LMB pans without selecting bend lines.
                pan_button = (
                    event.button() == QtCore.Qt.MiddleButton
                    or (
                        event.button() == QtCore.Qt.LeftButton
                        and bool(event.modifiers() & QtCore.Qt.ControlModifier)
                    )
                )
                if pan_button:
                    self._pan_last_pos = event.pos()
                    self._pan_button = event.button()
                    self.setCursor(QtCore.Qt.ClosedHandCursor)
                    event.accept()
                    return
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
                super().mousePressEvent(event)

            def mouseMoveEvent(self, event):
                if self._pan_last_pos is not None:
                    pos = event.pos()
                    delta = pos - self._pan_last_pos
                    self._pan_last_pos = pos
                    if delta.x() or delta.y():
                        tr = self.transform()
                        # Viewer transforms only scale; convert viewport
                        # pixels to scene units to make pan speed invariant
                        # under mouse-wheel zoom, in both axes.
                        sx, sy = float(tr.m11()), float(tr.m22())
                        if abs(sx) > 1e-10 and abs(sy) > 1e-10:
                            self.translate(delta.x() / sx, delta.y() / sy)
                            self._user_camera_modified = True
                    event.accept()
                    return
                super().mouseMoveEvent(event)

            def mouseReleaseEvent(self, event):
                if (self._pan_last_pos is not None
                        and event.button() == self._pan_button):
                    self._pan_last_pos = None
                    self._pan_button = None
                    self.unsetCursor()
                    event.accept()
                    return
                super().mouseReleaseEvent(event)

            def resizeEvent(self, event):
                super().resizeEvent(event)
                if not self._user_camera_modified and self.scene() is not None:
                    if not self.scene().sceneRect().isEmpty():
                        self.fitInView(self.scene().sceneRect(), QtCore.Qt.KeepAspectRatio)

        class TaskStatusLabel(QtWidgets.QLabel):
            """Existing application status, observed without polling the GUI."""

            def __init__(self, workspace, message):
                super().__init__(message, workspace)
                self._workspace = workspace

            def setText(self, message):
                super().setText(str(message))
                self.setToolTip(str(message))
                if hasattr(self, "_workspace"):
                    self._workspace._on_task_status_change(str(message))

        class TaskPhysicalResultLabel(QtWidgets.QLabel):
            """Mirror the actual validator output into the compact simulation card."""

            def __init__(self, workspace, message):
                super().__init__(message, workspace)
                self._workspace = workspace

            def setText(self, message):
                super().setText(str(message))
                self.setToolTip(str(message))
                if hasattr(self, "_workspace"):
                    self._workspace._on_physical_result_change(str(message))

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
                self._machine_pose_fault = None
                self.motion_trace = None
                self._physical_check_signature = None
                self._trace_frame_dt = None
                self._trace_directory = (
                    _pressbrake_user_data_directory() / "pressbrake_traces"
                )
                self.runtime_git_info = get_runtime_git_info()
                self.last_sequence_search_report = None
                self.last_planar_search_report = None
                self._saved_full_sequences = []
                self._preview_only_planar_order = None
                self._partial_preview_limit = None
                self._manual_sequence_prefix: list[str] = []
                self._manual_orientation_order = ()
                self._manual_orientation_by_bend = {}
                self._manual_orientation_overrides: dict[str, bool] = {}
                self._last_manual_sequence_report = None
                self._collision_inspection = None
                self._alternate_retract_inspection = None
                self._last_assisted_lift_inspection = None
                self._last_assisted_lift_report = None
                self._collision_display_active = False
                self._manual_3d_verdicts = {}
                self._manual_retract_verdicts = {}
                self._backgauge_settings = QtCore.QSettings("SMBS", "BendSeq")
                self.backgauge_model = None
                self._backgauge_preview_cache = {}
                self._tooling_sections_preview = None
                self._simulation_layouts = {}
                self._simulation_layout_key = None
                self._machine_gauge_envelope_cache = None
                # Preserve the exact S/Z candidate accepted by the search.
                # Otherwise a GUI redraw would recompute the primary gauge
                # and depict a different setup from the chosen DFS branch.
                self._selected_planar_gauge_order = ()
                self._selected_planar_gauge_positions = {}
                self._selected_planar_gauge_model_id = None
                self._ergonomic_loading_cache_key = None
                self._ergonomic_loading_cache = {}
                self._ergonomic_loading_cache_status = "NO_PART"
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
                self._manual_preview_unverified = False
                self._smbs_document_name = None
                self._smbs_fcstd_observer = None
                self._smbs_allow_auto_embed = False
                self._embedded_diagnostics_history = None
                self._conditional_bends_status: dict[str, str] = {}
                self._highlighted_end_zone_info: dict | None = None
                _log(
                    "PressBrakeSim startup: module={} | branch={} | commit={}".format(
                        self.runtime_git_info["module_file"],
                        self.runtime_git_info["git_branch"],
                        self.runtime_git_info["git_commit"],
                    )
                )
                _desktop_debug_event(
                    "SMBS_RUNTIME_READY",
                    runtime=self.runtime_git_info,
                    cycle_config={
                        "initial_clearance_mm":
                            self.cycle_config.initial_clearance_mm,
                        "mode": self.execution_mode.value,
                    },
                )
                self.angle = 0.0
                self.playing = False
                self._last_tick_monotonic: float | None = None
                self.timer = QtCore.QTimer(self)
                self.timer.setTimerType(QtCore.Qt.PreciseTimer)
                self.timer.setInterval(25)  # 40 Hz Qt playback; one timer only
                self.timer.timeout.connect(self.advance)
                self.setMinimumWidth(0)
                self.setObjectName("SMBS_ViewerRoot")
                # The host FreeCAD style paints the viewport and viewer tabs.
                # No colored borders, background fills or pseudo-native QSS.

                from freecad_smbs.ui.pressbrake_theme import (
                    OUTER_MARGIN, SECTION_GAP,
                )
                root = QtWidgets.QVBoxLayout(self)
                root.setContentsMargins(
                    OUTER_MARGIN, OUTER_MARGIN,
                    OUTER_MARGIN, OUTER_MARGIN,
                )
                root.setSpacing(SECTION_GAP)
                title = QtWidgets.QLabel("PressBrakeSim")
                # Keep its layout slot (used by the original 11-item
                # reparenting contract), but the MDI title already names
                # the simulator. A second large heading wastes CAD space.
                title.hide()
                root.addWidget(title)

                self.status = TaskStatusLabel(
                    self, "Выделите листовую деталь в FreeCAD и нажмите «Деталь»."
                )
                self.status.setWordWrap(True)
                root.addWidget(self.status)

                # Toolbar owns main actions. Retain two original enabled-state
                # proxies because experimental physical/planar search logic
                # relies on them, without duplicating on-screen buttons.
                self.calculate = _button(QtWidgets, "Расчёт", self.calculate_sequence, False)
                self.planar_calculate = _button(
                    QtWidgets, "Поиск 2D вариантов", self.calculate_planar_candidates, False
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
                t_layout.setContentsMargins(16, 16, 16, 16)
                t_layout.setSpacing(16)

                t_row1 = QtWidgets.QHBoxLayout()
                self.import_punch_btn = _button(QtWidgets, "Import Punch", self.import_punch_dialog, True)
                self.import_die_btn = _button(QtWidgets, "Import Die", self.import_die_dialog, True)
                self.tool_library_btn = _button(QtWidgets, "Библиотека оснастки", self.open_full_tooling_library, True)
                for w in (self.import_punch_btn, self.import_die_btn, self.tool_library_btn):
                    t_row1.addWidget(w)
                t_layout.addLayout(t_row1)

                t_row2 = QtWidgets.QHBoxLayout()
                t_row2.addWidget(QtWidgets.QLabel("Punch:"))
                self.select_punch_combo = QtWidgets.QComboBox()
                self.select_punch_combo.currentIndexChanged.connect(self._on_punch_combo_changed)
                t_row2.addWidget(self.select_punch_combo, 1)
                t_layout.addLayout(t_row2)

                t_row3 = QtWidgets.QHBoxLayout()
                t_row3.addWidget(QtWidgets.QLabel("Die:"))
                self.select_die_combo = QtWidgets.QComboBox()
                self.select_die_combo.currentIndexChanged.connect(self._on_die_combo_changed)
                t_row3.addWidget(self.select_die_combo, 1)
                t_layout.addLayout(t_row3)

                # Development virtual tool length & controlled punch end-zone policy
                self.virtual_tool_len_checkbox = QtWidgets.QCheckBox(
                    "Виртуальная длина инструмента\nравна длине гиба"
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
                    "Условный допуск концевых зон\nпуансона (CONDITIONAL)"
                )
                self.allow_end_zone_checkbox.setChecked(False)
                self.allow_end_zone_checkbox.setToolTip(
                    "Условный допуск столкновений в заданных концевых зонах "
                    "пуансона (от каждого торца). Не обрезает 3D-геометрию "
                    "инструмента и НЕ подтверждает наличие физического "
                    "выреза. Если центральное пересечение превышает "
                    "действующий объёмный допуск OCC, гиб блокируется. "
                    "Результат CONDITIONAL — не физический PASS; требуется "
                    "проверка реального пуансона с концевыми вырезами."
                )
                self.allow_end_zone_checkbox.toggled.connect(self._on_allow_end_zone_toggled)
                t_layout.addWidget(self.allow_end_zone_checkbox)
                self.tooling_setup.end_zone_length_mm = float(self.end_zone_len_spin.value())
                self.tooling_setup.allow_end_zone_collision = bool(self.allow_end_zone_checkbox.isChecked())

                self.tooling_status = QtWidgets.QLabel(self.tooling_setup.status_line())
                self.tooling_status.setWordWrap(True)
                t_layout.addWidget(self.tooling_status)
                # Explicit, on-demand physical check: no expensive BRep
                # booleans in the 25–40 Hz drawing/timer path.
                self.physical_check_btn = _button(
                    QtWidgets, "Один кадр: диагностика 2D/3D",
                    self.check_current_physical_pose, True,
                )
                self.physical_check_btn.setToolTip(
                    "Проверка текущего кадра по 2D-сечению и, если имеются "
                    "реальные FCStd пуансона и матрицы, по нативной 3D-геометрии. "
                    "Не сертифицирует весь цикл или станок."
                )
                t_layout.addWidget(self.physical_check_btn)
                self.full_physical_check_btn = _button(
                    QtWidgets, "Один гиб: 3D проверка D6",
                    self.check_active_bend_physical_trajectory, True,
                )
                self.full_physical_check_btn.setToolTip(
                    "Ресурсоёмкая проверка выборки 3D-состояний текущего гиба. "
                    "Во время выполнения анимация должна быть остановлена. "
                    "Непрерывное движение и прочие компоненты станка не сертифицированы."
                )
                t_layout.addWidget(self.full_physical_check_btn)
                self.physical_check_result = TaskPhysicalResultLabel(
                    self, "UNVERIFIED: проверка коллизий не выполнена."
                )
                self.physical_check_result.setWordWrap(True)
                t_layout.addWidget(self.physical_check_result)
                root.addWidget(self.tooling_group)

                # Phase 5B: Press Cycle section (Bend Only / Full Cycle, Ram Position, Cycle Phase, Speed & Config)
                self.press_cycle_group = QtWidgets.QGroupBox("Режим симуляции")
                pc_layout = QtWidgets.QVBoxLayout(self.press_cycle_group)
                pc_layout.setContentsMargins(0, 0, 0, 0)
                pc_layout.setSpacing(16)

                pc_row1 = QtWidgets.QGridLayout()
                pc_row1.setHorizontalSpacing(16)
                pc_row1.setVerticalSpacing(16)
                pc_row1.addWidget(QtWidgets.QLabel("Mode:"), 0, 0)
                self.cycle_mode_combo = QtWidgets.QComboBox()
                self.cycle_mode_combo.addItem("Bend Only", CycleExecutionMode.BEND_ONLY.value)
                self.cycle_mode_combo.addItem("Full Cycle", CycleExecutionMode.FULL_CYCLE.value)
                self.cycle_mode_combo.setCurrentIndex(1)
                self.cycle_mode_combo.currentIndexChanged.connect(self._on_cycle_mode_combo_changed)
                pc_row1.addWidget(self.cycle_mode_combo, 0, 1)
                pc_row1.addWidget(QtWidgets.QLabel("Speed (°/s):"), 1, 0)
                self.cycle_speed_spin = QtWidgets.QDoubleSpinBox()
                self.cycle_speed_spin.setRange(1.0, 720.0)
                self.cycle_speed_spin.setValue(self.cycle_config.bend_rate_deg_s)
                self.cycle_speed_spin.setSingleStep(15.0)
                self.cycle_speed_spin.valueChanged.connect(self._on_cycle_speed_changed)
                pc_row1.addWidget(self.cycle_speed_spin, 1, 1)

                self.cycle_config_btn = _button(QtWidgets, "Cycle Config...", self.open_cycle_config_dialog, True)
                pc_row1.addWidget(self.cycle_config_btn, 2, 1)
                pc_row1.setColumnStretch(0, 0)
                pc_row1.setColumnStretch(1, 1)
                pc_layout.addLayout(pc_row1)

                pc_row2 = QtWidgets.QVBoxLayout()
                self.cycle_phase_label = QtWidgets.QLabel("Cycle Phase: IDLE (KINEMATIC_PREVIEW)")
                self.ram_position_label = QtWidgets.QLabel(
                    "Ram Position: {:+.2f} mm (Stroke: 0.00 mm)".format(self.tooling_setup.ram_z_mm)
                )
                pc_row2.addWidget(self.cycle_phase_label)
                pc_row2.addWidget(self.ram_position_label)
                pc_layout.addLayout(pc_row2)
                self.loading_orientation_status = QtWidgets.QLabel(
                    "Оператор ← | Упор → | Подача: пока не определена"
                )
                self.loading_orientation_status.setWordWrap(True)
                self.cycle_phase_label.setWordWrap(True)
                self.ram_position_label.setWordWrap(True)
                pc_layout.addWidget(self.loading_orientation_status)
                root.addWidget(self.press_cycle_group)

                self._auto_select_default_tooling()
                self._refresh_tooling_combos()

                from freecad_smbs.ui.coin3d_viewer import create_coin_viewer
                self._preview_3d = create_coin_viewer(self)
                _desktop_debug_event(
                    "COIN3D_RENDERER_SELECTED",
                    available=self._preview_3d._available,
                    native_document_view=self._preview_3d._native,
                )
                root.addWidget(self._preview_3d, 1)

                row = QtWidgets.QGridLayout()
                row.setHorizontalSpacing(3)
                row.setVerticalSpacing(3)
                # One playback button owns Play, Pause and Resume, with step
                # navigation immediately on either side.
                self.reset_btn = _playback_button(QtCore, QtWidgets, "Reset", self.reset, False)
                self.prev_step_btn = _playback_button(QtCore, QtWidgets, "|<<", self.previous_step, False)
                self.play_btn = _playback_button(
                    QtCore, QtWidgets, "Play", self._toggle_play_pause, False
                )
                self.play_btn.setToolTip("Play / Pause / Resume для выбранного режима")
                self.next_step_btn = _playback_button(QtCore, QtWidgets, ">>|", self.next_step, False)
                self.fit_btn = _playback_button(QtCore, QtWidgets, "Fit", self.fit, True)
                buttons = (
                    self.reset_btn, self.prev_step_btn, self.play_btn,
                    self.next_step_btn, self.fit_btn,
                )
                captions = ("Reset", "|<<", "Play / Pause", ">>|", "Fit")
                sizes = (48, 34, 64, 34, 36)
                row.setColumnMinimumWidth(0, 0)
                for col, (widget, caption, width) in enumerate(
                    zip(buttons, captions, sizes)
                ):
                    # Native compact controls: one row, no separate labels and
                    # no duplicate Pause button. Text updates in the original
                    # _sync_playback_buttons() callback.
                    widget.setToolTip(caption)
                    widget.setFixedSize(width, 27)
                    row.addWidget(widget, 0, col)
                    row.setColumnStretch(col, 0)
                root.addLayout(row)

                self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
                self.slider.setRange(0, 90 * _SLIDER_SCALE)
                self.slider.setEnabled(False)
                self.slider.valueChanged.connect(self._on_slider_changed)
                root.addWidget(self.slider)


                order_row = QtWidgets.QGridLayout()
                order_row.setHorizontalSpacing(6)
                order_row.setVerticalSpacing(6)
                self.move_up_btn = _button(QtWidgets, "↑ Move Up", self.move_selected_up, False)
                self.move_down_btn = _button(QtWidgets, "↓ Move Down", self.move_selected_down, False)
                self.restore_order_btn = _button(QtWidgets, "↺ Restore Order", self.restore_order, False)
                order_row.addWidget(self.move_up_btn, 0, 0)
                order_row.addWidget(self.move_down_btn, 0, 1)
                order_row.addWidget(self.restore_order_btn, 1, 0, 1, 2)
                order_row.setColumnStretch(0, 0)
                order_row.setColumnStretch(1, 0)
                order_row.setColumnStretch(2, 1)
                root.addLayout(order_row)

                self.operations = QtWidgets.QTreeWidget()
                self.operations.setHeaderLabels([
                    "Step", "Bend", "180°", "Target", "Current", "До гиба, мм",
                    "Упор S, мм", "Status", "RETRACT",
                ])
                self.operations.headerItem().setToolTip(
                    _LOADING_ORIENTATION_COLUMN,
                    "Разворот заготовки вокруг вертикальной оси для этого гиба.",
                )
                self.operations.setRootIsDecorated(False)
                self.operations.setHorizontalScrollBarPolicy(
                    QtCore.Qt.ScrollBarAsNeeded
                )
                self.operations.setObjectName("SMBS_Operations")
                self.operations.setToolTip(
                    "До гиба, мм: локальный размер по развёртке до ближайшей "
                    "соседней линии гиба или края. Упор S, мм: визуальная "
                    "установка от оси гиба до лицевой поверхности упора, "
                    "рассчитанная по уже согнутой детали (с толщиной и радиусом); "
                    "Меняется с положением заготовки. Кнопка «Короткой стороной к упору» "
                    "подбирает положение для выбранного гиба. "
                    "В ручном режиме отображается заданная позиция. "
                    "Без проверки контакта или столкновений."
                )
                self.operations.setAlternatingRowColors(True)
                self.operations.setMinimumHeight(220)
                self.operations.currentItemChanged.connect(self._on_operation_selected)
                self.operations.itemChanged.connect(self._on_operation_orientation_changed)
                self.operations.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
                self.operations.customContextMenuRequested.connect(
                    self._show_operation_orientation_menu
                )
                root.addWidget(self.operations)

                self.check_btn = _button(QtWidgets, "Проверить интерфейс", self.diagnostics)
                root.addWidget(self.check_btn)
                self.check_result = QtWidgets.QLabel("Диагностика ещё не запускалась")
                self.check_result.setWordWrap(True)
                root.addWidget(self.check_result)

                self._install_workspace_layout(root, QtCore, QtWidgets)
                self._install_automatic_debug_ui_signals(QtWidgets)
                self._last_debug_frame_at = 0.0
                self._last_debug_frame_signature = None
                self.draw()

            def _debug_session_context(self):
                """Snapshot actual operator state, without Qt or OCC objects."""
                session = self.session
                tooling = self.tooling_setup
                return {
                    "part": session.part_label if session is not None else None,
                    "document": self._smbs_document_name,
                    "active_bend": session.active_bend_id if session is not None else None,
                    "bend_order": list(session.bend_order) if session is not None else [],
                    "manual_prefix": list(self._manual_sequence_prefix),
                    "fold_angles_deg": dict(session.fold_state.angles)
                    if session is not None else {},
                    "punch": getattr(getattr(tooling, "punch", None), "tool_id", None),
                    "die": getattr(getattr(tooling, "die", None), "tool_id", None),
                    "tool_length_virtual": bool(getattr(
                        tooling, "match_active_bend_length", False,
                    )),
                    "end_zone_allowed": bool(getattr(
                        tooling, "allow_end_zone_collision", False,
                    )),
                    "end_zone_mm": getattr(tooling, "end_zone_length_mm", None),
                    "cycle_mode": self.execution_mode.value,
                    "initial_clearance_mm": self.cycle_config.initial_clearance_mm,
                    "running": bool(self.timer.isActive()),
                    "manual_orientation_by_bend": (
                        self._manual_orientation_selection(session.bend_order)
                        if session is not None else {}
                    ),
                }

            def _debug_ui_action(self, widget, action, value=None):
                try:
                    widget_name = str(widget.objectName() or "")
                    caption = (
                        widget.text() if hasattr(widget, "text") and
                        callable(widget.text) else ""
                    )
                    _desktop_debug_event(
                        "OPERATOR_UI_ACTION",
                        action=str(action),
                        widget=widget_name or type(widget).__name__,
                        widget_class=type(widget).__name__,
                        label=str(caption)[:200],
                        value=value,
                        session=self._debug_session_context(),
                    )
                except Exception:
                    # An unavailable UI log must never block input in FreeCAD.
                    pass

            def _install_automatic_debug_ui_signals(self, QtWidgets, root=None):
                """Capture user actions automatically without duplicating handlers.

                Includes controls inside the standard FreeCAD Tasks stack and
                the MDI; newly embedded specialist settings are wired too.
                No keyboard passwords or arbitrary document text is collected.
                """
                for widget in (root or self).findChildren(QtWidgets.QWidget):
                    if widget.property("_smbs_debug_wired"):
                        continue
                    widget.setProperty("_smbs_debug_wired", True)
                    if isinstance(widget, QtWidgets.QAbstractButton):
                        widget.clicked.connect(
                            lambda checked=False, w=widget:
                                self._debug_ui_action(w, "CLICK", bool(checked))
                        )
                    elif isinstance(widget, QtWidgets.QComboBox):
                        widget.currentIndexChanged.connect(
                            lambda index, w=widget:
                                self._debug_ui_action(
                                    w, "SELECT", {
                                        "index": int(index),
                                        "text": str(w.currentText())[:200],
                                    },
                                )
                        )
                    elif isinstance(widget, QtWidgets.QAbstractSlider):
                        widget.sliderReleased.connect(
                            lambda w=widget:
                                self._debug_ui_action(w, "SLIDER_RELEASE", w.value())
                        )
                    elif isinstance(widget, (
                            QtWidgets.QDoubleSpinBox, QtWidgets.QSpinBox)):
                        widget.valueChanged.connect(
                            lambda val, w=widget:
                                self._debug_ui_action(w, "VALUE", val)
                        )
                    elif isinstance(widget, QtWidgets.QLineEdit):
                        widget.editingFinished.connect(
                            lambda w=widget:
                                self._debug_ui_action(w, "EDIT_FINISHED")
                        )
                    elif isinstance(widget, QtWidgets.QTabWidget):
                        widget.currentChanged.connect(
                            lambda index, w=widget:
                                self._debug_ui_action(w, "VIEW_TAB", index)
                        )

            def _install_workspace_layout(self, root, QtCore, QtWidgets):
                """Rearrange existing controls without rebuilding the simulation engine.

                Preserve all initialized widgets, signals, scene and the single QTimer.
                """
                # The existing constructor builds these items in a known order.
                entries = [root.takeAt(0) for _ in range(root.count())]
                if len(entries) != 11:
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

                # MDI contains only the heading and CAD views.
                # The original status QLabel is reparented into FreeCAD Tasks.
                insert(root, entries[0])
                self._task_progress_busy = False
                self.task_progress = QtWidgets.QProgressBar(self)
                self.task_progress.setObjectName("SMBS_TaskProgress")
                self.task_progress.setTextVisible(False)
                self.task_progress.setFixedHeight(7)
                self.task_progress.setRange(0, 100)
                self.task_progress.setValue(0)
                self._task_status_chip = QtWidgets.QLabel("Ready", self)
                self._task_status_chip.setObjectName("SMBS_StatusChip")
                self._task_status_chip.setProperty("state", "ready")
                self._task_progress_caption = QtWidgets.QLabel("0%", self)
                self._task_progress_caption.setObjectName("SMBS_SectionCaption")
                status_font = self._task_status_chip.font()
                status_font.setBold(True)
                self._task_status_chip.setFont(status_font)

                views = QtWidgets.QTabWidget(self)
                views.setObjectName("SMBS_ViewerTabs")
                views.setDocumentMode(True)
                views.setIconSize(QtCore.QSize(16, 16))
                views.tabBar().setExpanding(False)
                self._last_3d_frame_at = -1.0
                self._last_3d_debug_at = -1.0
                self._last_3d_rendered_bend = None
                self._coin_fold_mesh_key = None
                self._coin_fold_mesh_faces = ()
                self._coin_last_error = None
                if self._preview_3d._native:
                    self._preview_3d.hide()
                else:
                    views.addTab(self._preview_3d, QtGui.QIcon(str(
                        _ICON_ROOT / "gui_part.svg"
                    )), "Coin3D")
                self._flat_view = Canvas(bend_click_callback=self._select_flat_bend)
                self._flat_view.setObjectName("SMBS_FlatPattern_ManualOrder")
                self._flat_scene = QtWidgets.QGraphicsScene(self._flat_view)
                self._flat_view.setScene(self._flat_scene)
                views.addTab(self._flat_view, QtGui.QIcon(str(
                    _ICON_ROOT / "gui_sequence_catalog.svg"
                )), "Развёртка · Порядок")
                views.currentChanged.connect(self._on_view_changed)

                controls = QtWidgets.QStackedWidget()
                controls.setObjectName("SMBS_TaskStack")
                controls.setSizePolicy(QtWidgets.QSizePolicy.Ignored,
                                       QtWidgets.QSizePolicy.Expanding)

                from freecad_smbs.ui.pressbrake_theme import (
                    OUTER_MARGIN, SECTION_GAP, section_card, style_action,
                )

                def card(title):
                    return section_card(QtCore, QtWidgets, title)

                def scroll_task_page(content, name):
                    # Scroll the CONTENT of each task tab (not the tab bar).
                    # A minimum-size layout prevents Qt from compressing the
                    # controls into the viewport and overlapping them.
                    content.setObjectName("SMBS_TaskContent")
                    content.layout().setContentsMargins(
                        OUTER_MARGIN, OUTER_MARGIN,
                        OUTER_MARGIN, OUTER_MARGIN,
                    )
                    content.layout().setSpacing(SECTION_GAP)
                    # The page must be able to shrink with the FreeCAD dock.
                    content.layout().setSizeConstraint(
                        QtWidgets.QLayout.SetNoConstraint
                    )
                    # FreeCAD's native Tasks dock already provides its own
                    # scrolling. A nested QScrollArea creates the unwanted
                    # second scrollbar and steals wheel events from controls.
                    content.setObjectName(name)
                    content.setMinimumSize(0, 0)
                    content.setSizePolicy(
                        QtWidgets.QSizePolicy.Preferred,
                        QtWidgets.QSizePolicy.Minimum
                    )
                    return content

                # Main Task Panel uses the same 20/16px engineering card grid
                # as the preview design. No duplicate playback/controller widgets.
                simulation = QtWidgets.QWidget()
                sim_layout = QtWidgets.QVBoxLayout(simulation)
                part_page = QtWidgets.QWidget()
                part_layout = QtWidgets.QVBoxLayout(part_page)

                source_card, source_body = card("")
                source_grid = QtWidgets.QGridLayout()
                source_grid.setHorizontalSpacing(SECTION_GAP)
                self._source_summary = QtWidgets.QLabel(
                    str(self.session.part_label) if self.session else
                    "Деталь не загружена"
                )
                self._source_summary.setObjectName("SMBS_SectionCaption")
                self._source_summary.setWordWrap(True)
                self._source_summary.setAlignment(
                    QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter
                )
                source_grid.addWidget(self._source_summary, 0, 0)
                self._source_load_btn = style_action(
                    QtWidgets.QPushButton("Загрузить"), compact=True
                )
                self._source_load_btn.setToolTip(
                    "Загрузить выделенную в FreeCAD развёртку SheetMetal"
                )
                self._source_load_btn.clicked.connect(
                    lambda _checked=False: self.load_part()
                )
                source_grid.addWidget(self._source_load_btn, 0, 1)
                source_grid.setColumnStretch(0, 1)
                source_grid.setColumnStretch(1, 0)
                source_body.addLayout(source_grid)
                part_layout.addWidget(source_card)
                part_layout.addStretch(1)
                self._page_part = scroll_task_page(part_page, "SMBS_PartScroll")

                # The native Tasks header already says "Симуляция": no duplicate
                # section title or large diagnostic card above playback.
                sim_body = sim_layout
                for btn in (
                    self.reset_btn, self.prev_step_btn, self.next_step_btn,
                    self.fit_btn,
                ):
                    style_action(btn, compact=True)
                style_action(self.play_btn, primary=True)
                insert(sim_body, entries[5])  # Reset / Prev / Play-Pause / Next / Fit
                self.slider.setToolTip(
                    "Перемещение угла и пуансона: кинематический просмотр, "
                    "не проверка коллизий"
                )
                insert(sim_body, entries[6])  # angle slider
                self._manual_preview_notice = QtWidgets.QLabel(
                    "РУЧНОЙ ПРОСМОТР — коллизии не проверены"
                )
                self._manual_preview_notice.setWordWrap(True)
                self._manual_preview_notice.hide()
                sim_body.addWidget(self._manual_preview_notice)

                self._coin_frame_status = QtWidgets.QLabel()
                self._coin_frame_status.setWordWrap(True)
                sim_body.addWidget(self._coin_frame_status)
                self.machine_preview_checkbox = QtWidgets.QCheckBox("Показывать станок")
                self.machine_preview_checkbox.setChecked(True)
                self.machine_preview_checkbox.toggled.connect(lambda: self.draw())
                sim_body.addWidget(self.machine_preview_checkbox)
                # ManualMode: sequence search is not exposed to the operator.
                self.calculate.hide()
                sim_layout.addStretch(1)
                self._page_simulation = scroll_task_page(
                    simulation, "SMBS_SimulationScroll"
                )
                manual = QtWidgets.QWidget()
                manual_layout = QtWidgets.QVBoxLayout(manual)
                order_card, order_body = card("Ручной порядок гибов и проверка")
                self.manual_order_input = QtWidgets.QLineEdit()
                self.manual_order_input.setObjectName("SMBS_ManualOrderInput")
                self.manual_order_input.setAccessibleName("Последовательность гибов")
                self.manual_order_input.setPlaceholderText("Порядок, например 2-1-3-4")
                self.manual_order_input.setToolTip(
                    "Введите номера линий с развёртки, например 2-1-3-4 "
                    "или B02 B01 B03 B04. Enter применяет введённый порядок."
                )
                self.manual_order_input.returnPressed.connect(self._apply_typed_manual_order)
                order_body.addWidget(self.manual_order_input)
                # Sequence guidance belongs in tooltips, not a permanent
                # multi-line paragraph above operator controls.
                for btn in (
                    self.move_up_btn, self.move_down_btn, self.restore_order_btn,
                ):
                    style_action(btn, compact=True)
                insert(order_body, entries[7])  # move up/down/restore grid

                undo_row = QtWidgets.QVBoxLayout()
                undo_row.setSpacing(SECTION_GAP)
                self.manual_undo_btn = style_action(
                    QtWidgets.QPushButton("Отменить линию")
                )
                self.manual_undo_btn.clicked.connect(self._undo_manual_bend)
                self.manual_clear_btn = style_action(
                    QtWidgets.QPushButton("Очистить")
                )
                self.manual_clear_btn.clicked.connect(self._clear_manual_order)
                undo_row.addWidget(self.manual_undo_btn)
                undo_row.addWidget(self.manual_clear_btn)
                order_body.addLayout(undo_row)
                self.saved_sequences_btn = style_action(
                    QtWidgets.QPushButton("Сохранённые варианты"), compact=True
                )
                self.saved_sequences_btn.setToolTip(
                    "Загрузить ранее сохранённый ручной порядок из JSON "
                    "текущего FCStd. При необходимости проверить его заново."
                )
                self.saved_sequences_btn.clicked.connect(
                    self._select_saved_sequence
                )
                order_body.addWidget(self.saved_sequences_btn)
                info_button = style_action(QtWidgets.QPushButton("Информация"), compact=True)
                info_button.clicked.connect(self._show_bend_information)
                order_body.addWidget(info_button)
                self.auto_orientation_checkbox = QtWidgets.QCheckBox(
                    "Подбирать положение заготовки (0° / 180°)"
                )
                self.auto_orientation_checkbox.setChecked(True)
                self.auto_orientation_checkbox.setToolTip(
                    "Для гибов с автоматически заданным положением OCC может "
                    "проверить второе положение. Галочка в колонке «180°» "
                    "закрепляет выбранное положение конкретного гиба."
                )
                order_body.addWidget(self.auto_orientation_checkbox)
                short_rear_button = style_action(
                    QtWidgets.QPushButton("Короткой стороной к упору"), compact=True,
                )
                short_rear_button.setToolTip("Подобрать положение заготовки для выбранного гиба.")
                short_rear_button.clicked.connect(self._use_short_rear_orientation)
                order_body.addWidget(short_rear_button)

                self.operations.setMinimumHeight(220)
                self.operations.setMaximumHeight(16777215)
                self.operations.setSizePolicy(
                    QtWidgets.QSizePolicy.Expanding,
                    QtWidgets.QSizePolicy.Expanding,
                )
                insert(order_body, entries[8], 1)  # actual sequence table
                self.manual_simulate_btn = style_action(
                    QtWidgets.QPushButton("Симулировать без проверки"), compact=True
                )
                self.manual_simulate_btn.setToolTip(
                    "Просмотреть ручной порядок. Если есть актуальный 3D-результат, "
                    "симуляция использует положения из этой проверки."
                )
                self.manual_simulate_btn.clicked.connect(
                    lambda: self.simulate_manual_order_unverified()
                )
                # Orientation evidence remains in the status and tooltips.
                # Restore the quick Stage A/B action from the
                # integration/gui-2d-search-screening branch. Keep rigorous
                # native OCC as a DISTINCT opt-in action, not a silent upgrade
                # of the original fast "Проверить" button.
                self.manual_validate_btn = style_action(
                    QtWidgets.QPushButton("Быстрая проверка OCC (5 углов)"), compact=True
                )
                self.manual_validate_btn.setObjectName(
                    "SMBS_ManualCoarseOCCCheck"
                )
                self.manual_validate_btn.setToolTip(
                    "Реальные FCStd инструменты и OCC: 0, 25, 50, 75, 100% "
                    "угла каждого гиба, а также выборка APPROACH/RETRACT. "
                    "Точное проникновение отклоняет порядок сразу. Если его "
                    "не найдено, непросмотренные положения остаются UNVERIFIED."
                )
                self.manual_validate_btn.clicked.connect(
                    lambda: self._validate_export_manual_order(coarse_occ=True)
                )
                self.manual_validate_3d_btn = style_action(
                    QtWidgets.QPushButton("Проверить и показать"), primary=True
                )
                self.manual_validate_3d_btn.setObjectName(
                    "SMBS_ManualNative3DCheck"
                )
                self.manual_validate_3d_btn.setToolTip(
                    "Проверить выбранные гибы с реальными FCStd пуансоном и матрицей. "
                    "Показать 3D-позу и результат проверки. Проверка подведения, "
                    "гибки и отвода пуансона может занять несколько минут."
                )
                self.manual_validate_3d_btn.clicked.connect(
                    lambda: self._validate_export_manual_order(show_preview=True)
                )
                self.manual_lift_probe_btn = style_action(
                    QtWidgets.QPushButton("Проверить подъём OCC и показать движение"),
                    compact=True,
                )
                self.manual_lift_probe_btn.setObjectName("SMBS_ManualAssistedRetractProbe")
                self.manual_lift_probe_btn.setToolTip(
                    "Доступно после подтверждённой коллизии с пуансоном при его "
                    "подъёме (RETRACT). Проверяет альтернативные OCC-позы и "
                    "включает синхронный подъём в симуляции только при отсутствии "
                    "нового блокера. Исходный COLLISION остаётся."
                )
                self.manual_lift_probe_btn.clicked.connect(self._inspect_assisted_retract)
                self.manual_lift_probe_btn.setEnabled(False)
                self.manual_lift_preview_btn = style_action(
                    QtWidgets.QPushButton("Показать совместный подъём в симуляции"),
                    compact=True,
                )
                self.manual_lift_preview_btn.setObjectName("SMBS_ManualAssistedRetractPreview")
                self.manual_lift_preview_btn.setToolTip(
                    "Запустить весь выбранный порядок в режиме Full Cycle; "
                    "на отмеченном гибе показать предполагаемый подъём детали "
                    "вместе с пуансоном. Исходная коллизия остаётся."
                )
                self.manual_lift_preview_btn.clicked.connect(self._play_assisted_retract_preview)
                self.manual_lift_preview_btn.setEnabled(False)
                self.manual_release_probe_btn = style_action(
                    QtWidgets.QPushButton("Проверить боковой вывод детали OCC"),
                    compact=True,
                )
                self.manual_release_probe_btn.setObjectName("SMBS_ManualAssistedReleaseProbe")
                self.manual_release_probe_btn.setToolTip(
                    "После проверки подъёма исследовать вывод детали в обе "
                    "стороны по машинной Y. Промежутки и удержание не доказаны."
                )
                self.manual_release_probe_btn.clicked.connect(self._inspect_assisted_release)
                self.manual_release_probe_btn.setEnabled(False)
                verify_row = QtWidgets.QVBoxLayout()
                order_body.insertWidget(1, self.manual_validate_3d_btn)
                verify_row.addWidget(self.manual_validate_btn)
                verify_row.addWidget(self.manual_lift_probe_btn)
                verify_row.addWidget(self.manual_lift_preview_btn)
                verify_row.addWidget(self.manual_release_probe_btn)
                order_body.addLayout(verify_row)
                self.manual_lift_probe_hint = QtWidgets.QLabel(
                    "Подъём детали: сначала найдите коллизию пуансона на RETRACT. "
                    "Проверка остаётся экспериментальной."
                )
                self.manual_lift_probe_hint.setWordWrap(True)
                self.manual_lift_probe_hint.hide()
                # Verification must have a visible result in the Manual
                # workspace, not a file-save dialog as its only feedback.
                self.manual_check_summary = QtWidgets.QLabel(
                    "Выберите гибы и нажмите «Проверить и показать»."
                )
                self.manual_check_summary.setWordWrap(True)
                self.manual_check_summary.setObjectName("SMBS_ManualCheckSummary")
                order_body.insertWidget(2, self.manual_check_summary)
                order_body.insertWidget(3, self.manual_simulate_btn)
                self.manual_check_details = QtWidgets.QPlainTextEdit()
                self.manual_check_details.setObjectName("SMBS_ManualCheckDetails")
                self.manual_check_details.setReadOnly(True)
                self.manual_check_details.setMaximumHeight(160)
                self.manual_check_details.setPlaceholderText(
                    "Здесь появятся результаты по каждому проверенному гибу."
                )
                self.manual_check_details.hide()
                order_body.addWidget(self.manual_check_details)
                self.manual_show_collision_btn = style_action(
                    QtWidgets.QPushButton("Показать коллизию в 3D")
                )
                self.manual_show_collision_btn.setObjectName(
                    "SMBS_ShowNativeCollision"
                )
                self.manual_show_collision_btn.setToolTip(
                    "Открыть 3D-вьювер с фактическим положением детали "
                    "в момент найденного пересечения. Красные границы "
                    "соответствуют объёму OCC Common."
                )
                self.manual_show_collision_btn.clicked.connect(
                    self._show_first_collision_in_3d
                )
                self.manual_show_collision_btn.setEnabled(False)
                self.manual_show_collision_btn.hide()
                self.manual_exact_occ_btn = style_action(
                    QtWidgets.QPushButton("Открыть точное пересечение OCC"),
                    compact=True,
                )
                self.manual_exact_occ_btn.setObjectName(
                    "SMBS_OpenExactNativeOCCCollision"
                )
                self.manual_exact_occ_btn.setToolTip(
                    "Открыть отдельный несохранённый документ FreeCAD с "
                    "реальными FCStd-пуансоном и матрицей, деталью "
                    "в момент столкновения и точным красным OCC Common. "
                    "В дереве видимости можно скрывать любые объекты."
                )
                self.manual_exact_occ_btn.clicked.connect(
                    self._open_exact_collision_inspector
                )
                self.manual_exact_occ_btn.setEnabled(False)
                self.manual_exact_occ_btn.hide()
                order_body.addWidget(self.manual_show_collision_btn)
                order_body.addWidget(self.manual_exact_occ_btn)
                self._manual_preview_help = QtWidgets.QLabel(
                    "Симуляция не проверяет коллизии. Быстрая OCC проверка "
                    "ищет точное 3D пересечение в пяти углах. Полная 3D проверка "
                    "идёт по всем выбранным гибам, но проверяет выборку поз, "
                    "а не непрерывное движение. Результат выводится здесь и "
                    "сохраняется в FCStd."
                )
                self._manual_preview_help.setWordWrap(True)
                self._manual_preview_help.hide()
                manual_layout.addWidget(order_card, 1)
                self._page_manual = scroll_task_page(
                    manual, "SMBS_ManualScroll"
                )

                # RETRACT warning belongs to Settings, not Simulation.
                self.retract_warning_checkbox = QtWidgets.QCheckBox(
                    "RETRACT: продолжать 2D-поиск\nс предупреждением"
                )
                self.retract_warning_checkbox.setChecked(False)
                self.retract_warning_checkbox.setToolTip(
                    "Диагностическое продолжение, а не физический допуск. "
                    "Пересечения APPROACH, BENDING, BOTTOM и упора блокируют поиск."
                )

                tooling = QtWidgets.QWidget()
                td_layout = QtWidgets.QVBoxLayout(tooling)
                tooling_card, tooling_body = card("Оснастка и станок")
                # One tooling context from the native toolbar, with exactly
                # two internal pages requested by the operator.
                tool_main = QtWidgets.QWidget()
                tool_main_layout = QtWidgets.QFormLayout(tool_main)
                tool_main_layout.setContentsMargins(
                    SECTION_GAP, SECTION_GAP, SECTION_GAP, SECTION_GAP
                )
                tool_main_layout.setSpacing(SECTION_GAP)
                tool_main_layout.setFieldGrowthPolicy(
                    QtWidgets.QFormLayout.AllNonFixedFieldsGrow
                )
                # Keep labels in a fixed left column, dropdowns on the
                # right even when the Tasks dock is relatively narrow.
                tool_main_layout.setRowWrapPolicy(
                    QtWidgets.QFormLayout.DontWrapRows
                )
                tool_main_layout.setLabelAlignment(
                    QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter
                )
                self.select_punch_combo.setMinimumWidth(0)
                self.select_die_combo.setMinimumWidth(0)
                self.select_punch_combo.setSizePolicy(
                    QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed
                )
                self.select_die_combo.setSizePolicy(
                    QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed
                )
                tool_main_layout.addRow("Punch", self.select_punch_combo)
                tool_main_layout.addRow("Die", self.select_die_combo)
                # Keep diagnostics in the shared task status area, not
                # as an extra paragraph below the two selectors.
                self.tooling_status.setParent(self)
                self.tooling_status.hide()
                tooling_body.addWidget(tool_main)
                for button in (self.import_punch_btn, self.import_die_btn):
                    tooling_body.addWidget(style_action(button))
                self.tool_library_btn.hide()
                td_layout.addWidget(tooling_card)
                td_layout.addStretch(1)
                self._page_tooling = scroll_task_page(
                    tooling, "SMBS_ToolingScroll"
                )
                # All active tooling widgets have been reparented.
                self.tooling_group.deleteLater()

                settings = QtWidgets.QWidget()
                settings_layout = QtWidgets.QVBoxLayout(settings)
                advanced = QtWidgets.QWidget(settings)
                advanced_layout = QtWidgets.QVBoxLayout(advanced)
                advanced_layout.setContentsMargins(0, 0, 0, 0)
                advanced_layout.setSpacing(SECTION_GAP)

                cycle_card, cycle_body = card("Режим симуляции")
                self.press_cycle_group.setTitle("")
                self.press_cycle_group.setFlat(True)
                style_action(self.cycle_config_btn)
                insert(cycle_body, entries[3])  # Mode / Full Cycle / Speed
                # Only used by automatic search: deliberately hidden in ManualMode.
                self.retract_warning_checkbox.hide()
                settings_layout.addWidget(cycle_card)

                tool_opts_card, tool_opts_layout = card("Оснастка — параметры")
                tool_opts_layout.addWidget(self.virtual_tool_len_checkbox)
                zone_row = QtWidgets.QGridLayout()
                zone_row.setHorizontalSpacing(SECTION_GAP)
                end_zone_caption = QtWidgets.QLabel(
                    "Концевая зона пуансона (мм):"
                )
                end_zone_caption.setWordWrap(True)
                zone_row.addWidget(end_zone_caption, 0, 0)
                zone_row.addWidget(self.end_zone_len_spin, 0, 1)
                zone_row.setColumnStretch(0, 1)
                zone_row.setColumnStretch(1, 0)
                tool_opts_layout.addLayout(zone_row)
                tool_opts_layout.addWidget(self.allow_end_zone_checkbox)
                advanced_layout.addWidget(tool_opts_card)

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
                weights_card, weights_body = card("Веса операций / 2D поиск")
                weights_box = QtWidgets.QWidget()
                weights_form = QtWidgets.QFormLayout(weights_box)
                weights_form.setContentsMargins(0, 0, 0, 0)
                weights_form.setSpacing(SECTION_GAP)
                weights_form.setRowWrapPolicy(
                    QtWidgets.QFormLayout.WrapLongRows
                )
                weights_form.setFieldGrowthPolicy(
                    QtWidgets.QFormLayout.AllNonFixedFieldsGrow
                )
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
                weights_note.hide()
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
                style_action(reset_weights_btn)
                weights_form.addRow(reset_weights_btn)
                weights_body.addWidget(weights_box)
                # Keep a Qt parent for the hidden search-weight editors. Their
                # values remain referenced by FCStd save/restore and diagnostics.
                # Without addWidget(), Python releases this unparented card and
                # its QDoubleSpinBoxes, causing "Internal C++ object already deleted".
                # ManualMode must not show search-only controls to the operator.
                advanced_layout.addWidget(weights_card)
                weights_card.hide()

                diagnostics_card, diagnostics_body = card(
                    "Результаты / Диагностика"
                )
                self._simulation_diagnostics_summary = QtWidgets.QLabel(
                    "Unverified · физическая проверка ещё не выполнена"
                )
                self._simulation_diagnostics_summary.setObjectName(
                    "SMBS_SectionCaption"
                )
                self._simulation_diagnostics_summary.setWordWrap(True)
                diagnostics_body.addWidget(self._simulation_diagnostics_summary)
                self._on_physical_result_change(
                    self.physical_check_result.text()
                )
                for check in (
                    self.physical_check_btn, self.full_physical_check_btn,
                ):
                    style_action(check)
                    diagnostics_body.addWidget(check)
                diagnostics_body.addWidget(self.physical_check_result)
                style_action(self.check_btn)
                insert(diagnostics_body, entries[9])  # diagnostic button
                insert(diagnostics_body, entries[10])  # diagnostic result
                advanced_layout.addWidget(diagnostics_card)

                trace_card, trace_body = card("Запись и файлы диагностики")
                self.trace_checkbox = QtWidgets.QCheckBox(
                    "Автоматическая запись движений\nпри Play All (JSONL)"
                )
                self.trace_checkbox.setChecked(False)
                self.trace_checkbox.setToolTip(
                    "Создаёт папку при начале записи. После остановки сохраняет "
                    "кадры в *.jsonl и сводный отчёт в *.summary.json."
                )
                trace_body.addWidget(self.trace_checkbox)
                self.trace_checkbox.toggled.connect(self._on_trace_checkbox_toggled)

                self.trace_status = QtWidgets.QLabel(
                    "Запись отключена. Каталог: {}".format(self._trace_directory)
                )
                self.trace_status.setWordWrap(True)
                self.trace_status.setTextInteractionFlags(
                    QtCore.Qt.TextSelectableByMouse
                )
                trace_body.addWidget(self.trace_status)
                # Developer journal is ALWAYS on, independent of the
                # optional legacy JSONL motion recording below.
                self.desktop_debug_log_note = QtWidgets.QLabel(
                    "Автоматический единый журнал разработки:\n" +
                    str(_debug_desktop_directory() / "SMBS_debug_log.json") +
                    "\nЗаписывается без включения кнопок. Может содержать "
                    "названия деталей и локальные пути."
                )
                self.desktop_debug_log_note.setObjectName(
                    "SMBS_DesktopDebugLogPath"
                )
                self.desktop_debug_log_note.setTextInteractionFlags(
                    QtCore.Qt.TextSelectableByMouse
                )
                self.desktop_debug_log_note.setWordWrap(True)
                self.desktop_debug_log_note.hide()

                trace_buttons = QtWidgets.QVBoxLayout()
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
                trace_body.addLayout(trace_buttons)

                self.trace_open_button = QtWidgets.QPushButton("Открыть папку записей")
                self.trace_open_button.clicked.connect(
                    lambda _checked=False: self._open_trace_directory()
                )
                trace_body.addWidget(self.trace_open_button)
                self.trace_choose_button = QtWidgets.QPushButton(
                    "Выбрать папку для JSONL/JSON"
                )
                self.trace_choose_button.clicked.connect(
                    lambda _checked=False: self._choose_trace_directory()
                )
                trace_body.addWidget(self.trace_choose_button)
                for btn in (
                    self.trace_start_button, self.trace_stop_button,
                    self.trace_open_button, self.trace_choose_button,
                ):
                    style_action(btn)
                advanced_layout.addWidget(trace_card)

                # JSON is a genuine FCStd attachment, not a plaintext string
                # masquerading as a file and not a second external archive.
                fcstd_card, fcstd_body = card("Данные SMBS в документе FCStd")
                self.fcstd_save_btn = style_action(
                    QtWidgets.QPushButton("Сохранить в FCStd"), compact=True
                )
                self.fcstd_save_btn.setToolTip(
                    "Вложить JSON с порядком гибов, настройками и историей "
                    "проверок в этот документ и сохранить FCStd"
                )
                self.fcstd_save_btn.clicked.connect(
                    self._save_embedded_smbs_json_clicked
                )
                self.fcstd_load_btn = style_action(
                    QtWidgets.QPushButton("Восстановить из FCStd"), compact=True
                )
                self.fcstd_load_btn.setToolTip(
                    "Восстановить сохранённый порядок и настройки. "
                    "Исторические результаты не считаются проверенными."
                )
                self.fcstd_load_btn.clicked.connect(
                    self._restore_embedded_smbs_json_clicked
                )
                fcstd_buttons = QtWidgets.QHBoxLayout()
                fcstd_buttons.addWidget(self.fcstd_save_btn)
                fcstd_buttons.addWidget(self.fcstd_load_btn)
                fcstd_buttons.addStretch(1)
                fcstd_body.addLayout(fcstd_buttons)
                self.fcstd_note = QtWidgets.QLabel(
                    "JSON автоматически вкладывается при Ctrl+S после загрузки "
                    "детали. История проверки — только справочная."
                )
                self.fcstd_note.setWordWrap(True)
                # Detailed save/restore feedback is already shown in the
                # persistent task footer status; avoid a second paragraph.
                self.fcstd_note.hide()
                settings_layout.addWidget(fcstd_card)
                advanced_toggle = style_action(
                    QtWidgets.QPushButton("Дополнительно…"), compact=True
                )
                advanced_toggle.setCheckable(True)
                advanced_toggle.setChecked(False)
                advanced_toggle.setToolTip(
                    "Параметры оснастки, дополнительные OCC проверки "
                    "и диагностические записи"
                )
                advanced_toggle.toggled.connect(advanced.setVisible)
                settings_layout.addWidget(advanced_toggle)
                settings_layout.addWidget(advanced)
                advanced.hide()
                settings_layout.addStretch(1)
                self._page_settings = scroll_task_page(
                    settings, "SMBS_SettingsScroll"
                )

                # Five manual-only contextual pages match operator toolbar actions.
                # Only the selected page is shown. Specialist QDialogs may be
                # temporarily added and removed without creating tabs.
                page_specs = (
                    ("Деталь", self._page_part),
                    ("Оснастка", self._page_tooling),
                    ("Симуляция", self._page_simulation),
                    ("Порядок и проверка", self._page_manual),
                    ("Настройки", self._page_settings),
                )
                self._task_page_titles = {}
                for name, page in page_specs:
                    controls.addWidget(page)
                    self._task_page_titles[page] = name
                root.addWidget(views, 1)
                self._viewer_tabs = views
                self._task_stack = controls
                self._native_task_panel = None
                self.show_task_panel_btn = QtWidgets.QPushButton("Показать панель управления")
                self.show_task_panel_btn.setObjectName("SMBS_ShowTaskPanel")
                self.show_task_panel_btn.clicked.connect(
                    lambda: show_workspace_controls(self)
                )
                root.addWidget(self.show_task_panel_btn)
                self.show_task_panel_btn.hide()
                self._apply_svg_button_icons()
                self._task_stack.installEventFilter(self)
                self._apply_context_width(360)

            def _apply_context_width(self, width):
                """Keep operator information useful at narrow Tasks widths."""
                compact = int(width) < 590
                # Keep orientation and verdicts visible in the narrow Tasks pane.
                for col in (3, 4, 5, 6):
                    self.operations.setColumnHidden(col, compact)
                self.operations.header().setSectionResizeMode(
                    QtWidgets.QHeaderView.Stretch
                )
                self.operations.header().setSectionResizeMode(
                    _LOADING_ORIENTATION_COLUMN, QtWidgets.QHeaderView.ResizeToContents
                )

            def eventFilter(self, watched, event):
                if (getattr(self, "_task_stack", None) is watched
                        and event.type() == QtCore.QEvent.Resize):
                    self._apply_context_width(event.size().width())
                return super().eventFilter(watched, event)

            def _on_task_status_change(self, message):
                """Refresh native Tasks severity when handlers call status.setText."""
                if not hasattr(self, "_task_status_chip"):
                    return
                from freecad_smbs.ui.pressbrake_theme import classify_status
                state, caption = classify_status(message)
                if getattr(self, "_task_progress_busy", False):
                    state, caption = "running", "Running"
                badge = self._task_status_chip
                if badge.property("state") != state:
                    badge.setProperty("state", state)
                    badge.style().unpolish(badge)
                    badge.style().polish(badge)
                badge.setText(caption)

            def _on_physical_result_change(self, message):
                if hasattr(self, "_simulation_diagnostics_summary"):
                    self._simulation_diagnostics_summary.setText(str(message))
                    self._simulation_diagnostics_summary.setToolTip(str(message))
                # Status-bar reports the last actual validation outcome,
                # never a synthetic success badge.
                if hasattr(self, "status") and not getattr(
                    self, "_task_progress_busy", False
                ):
                    self.status.setText(str(message))

            def _set_task_busy(self, busy):
                """Use indeterminate progress for unbounded DFS/OCC searches."""
                self._task_progress_busy = bool(busy)
                if busy:
                    self.task_progress.setRange(0, 0)
                    self._task_progress_caption.setText("Расчёт…")
                else:
                    self.task_progress.setRange(0, 100)
                    self._update_task_progress()
                self._on_task_status_change(self.status.text())

            def _update_task_progress(self):
                """Known geometric bend progress, never DFS search percentage."""
                if getattr(self, "_task_progress_busy", False):
                    return
                session = self.session
                if session is None or not getattr(session, "bend_order", None):
                    self.task_progress.setValue(0)
                    self._task_progress_caption.setText("0%")
                    return
                n = len(session.bend_order)
                idx = min(n, max(0, int(session.active_bend_index)))
                try:
                    angle = float(session.current_angle_deg)
                    target = abs(float(session.target_angle_deg))
                    fraction = (
                        min(1.0, abs(angle) / target) if target > 1e-8 else 0.0
                    )
                except (ValueError, TypeError, AttributeError, ZeroDivisionError):
                    fraction = 0.0
                if session.playback_status == PlaybackStatus.COMPLETED:
                    idx, fraction = n, 0.0
                value = min(100, max(0, int(
                    round(100.0 * (idx + fraction) / n)
                )))
                if self.task_progress.value() != value:
                    self.task_progress.setValue(value)
                caption = "Гибы · {}%".format(value)
                if self._task_progress_caption.text() != caption:
                    self._task_progress_caption.setText(caption)

            def open_tooling_settings_dialog(self):
                """Show the single tooling page, without nested tabs."""
                show_workspace_controls(self, tab=1)

            def open_settings_dialog(self):
                """Show all configuration controls on one contextual page."""
                show_workspace_controls(self, tab=4)

            def _apply_svg_button_icons(self):
                """Use the same industrial SVG family in task controls.

                Only the visual presentation is modified. The existing
                QPushButton instances, signals and enabled states are retained.
                QtSvg renders the assets in FreeCAD's bundled Qt runtime.
                """
                icon_pairs = (
                    # Playback buttons are intentionally text-sized. Only
                    # Play/Pause retains a state-changing icon alongside text.
                    (self.play_btn, "control_play.svg"),
                    (self.restore_order_btn, "control_reset.svg"),
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
                # Always-on Desktop JSON, independent of optional JSONL.
                _desktop_debug_event(str(name), **fields)
                if self.motion_trace is not None and self.motion_trace.is_open:
                    try:
                        self.motion_trace.event(name, **fields)
                    except Exception as exc:
                        _warn("Motion trace event failed: {}".format(exc))

            def _trace_drawn_frame(self, placement, ram_z, gauge_stations=(), gauge_contact=None):
                """Sample diagnostics from the same placement sent to Coin3D."""
                trace = self.motion_trace
                tracing = trace is not None and trace.is_open
                now = time.monotonic()
                pc = self.press_cycle
                bend_id = self.session.active_bend_id
                signature = (bend_id, getattr(pc, "phase", None))
                interval = 1.0 if (self.timer.isActive()
                                   and getattr(self.session.unfold_data, "dxf_manual", False)) else 0.2
                full_debug = (
                    signature != self._last_debug_frame_signature
                    or now - self._last_debug_frame_at >= interval
                )
                if not tracing and not full_debug:
                    return
                try:
                    section = compute_machine_cutting_plane_section(
                        self.session.fold_state, self.session.panel_vertices_xy,
                        placement, active_bend_id=bend_id, segments=32,
                    )
                    frame = make_frame_record(
                        session=self.session, press_cycle=pc, execution_mode=self.execution_mode,
                        section=section, pose=WorkpiecePose2D(), tooling=self.tooling_setup,
                        dt_s=self._trace_frame_dt, view="Coin3D",
                    )
                    frame["rendered_machine_rotation"] = placement.rotation
                    frame["rendered_machine_translation_mm"] = placement.translation
                    frame["rendered_ram_z_mm"] = ram_z
                    if gauge_contact is not None:
                        frame["rendered_backgauge"] = {
                            "stations_xm_mm": gauge_stations, "stop_face_sz_mm": gauge_contact,
                        }
                    if full_debug:
                        self._last_debug_frame_at = now
                        self._last_debug_frame_signature = signature
                        _desktop_debug_event("PREVIEW_GEOMETRY_FRAME", frame=frame)
                    if tracing:
                        trace.record(frame)
                        self._trace_frame_dt = None
                except Exception as exc:
                    _desktop_debug_event("PREVIEW_FRAME_ERROR", error=str(exc), bend_id=bend_id)
                    if tracing:
                        self.trace_status.setText("Ошибка трассы: " + str(exc))
                        self._finish_motion_trace("TRACE_FRAME_ERROR")

            def _on_view_changed(self, index):
                selected = self._viewer_tabs.widget(index)
                if selected is self._preview_3d:
                    self.draw()
                elif selected is self._flat_view:
                    self._draw_flat_pattern()

            def _open_coin_preview(self):
                if not self._preview_3d._native:
                    self._viewer_tabs.setCurrentWidget(self._preview_3d)
                rendered = self._update_3d_preview(force=True)
                if rendered and self.session is not None:
                    self._preview_3d.show_view()
                return rendered

            def _update_3d_preview(self, force=False):
                """Send the shared FoldState, tooling and OCC pose to Coin3D."""
                viewer = self._preview_3d
                if self.session is None:
                    viewer.clear_preview()
                    self._coin_frame_status.setText("Загрузите развёртку SheetMetal.")
                    return True
                if not viewer._available:
                    message = "Coin3D недоступен. Проверьте pivy.coin в Python FreeCAD."
                    if self.timer.isActive():
                        self.pause()
                    self._coin_frame_status.setText(message)
                    self.status.setText(message)
                    return False
                if (not force and not viewer._native
                        and self._viewer_tabs.currentWidget() is not viewer):
                    return True

                now = time.perf_counter()
                bend_id = self.session.active_bend_id
                if (not force and self.timer.isActive()
                        and not self._collision_display_active
                        and bend_id == self._last_3d_rendered_bend
                        and now - self._last_3d_frame_at < 0.015):
                    return True
                proposal = self._tooling_sections_preview
                report = getattr(self, "_task_dialogs", {}).get("tool_assembly")
                show_proposal = (
                    proposal is not None and proposal[0] == bend_id and report is not None
                    and self._task_stack.currentWidget() is report._smbs_task_page
                    and not self.timer.isActive()
                )
                inspection = (self._collision_inspection
                              if self._collision_display_active and not show_proposal else None)
                try:
                    viewer.attach_document(self._smbs_document_name)
                    highlighted = ()
                    bounds = []
                    inspected_length = None
                    if inspection:
                        from smbs_core.kinematics2d.native_collision_attribution import collision_preview_boxes
                        from smbs_core.kinematics2d.workpiece_placement_validation_3d import (
                            compare_native_placement_matrices,
                        )
                        blocker = inspection["first_blocker"]
                        actual = blocker["sampled_machine_pose"]
                        bend_id = inspection["bend_id"]
                        if actual.get("bend_id") != bend_id:
                            raise PreviewMeshError("missing_native_machine_frame_for_collision")
                        ram_z = float(actual["ram_z_mm"])
                        fold_state = FoldState(self.session.fold_state.graph, self.session.fold_state.fixed_panel)
                        for previous, angle in actual["completed_angles"].items():
                            fold_state.set_angle(previous, float(angle))
                        fold_state.set_angle(bend_id, float(actual["signed_angle_deg"]))
                        expected = WorkpiecePlacement3D.compute_placement(
                            fold_state, bend_id,
                            support_plane_zm=float(actual["support_plane_zm"]),
                            align_bisector=True, align_bend_arc_center=True,
                            reverse_axis=self._effective_loading_orientation_map()[bend_id],
                        )
                        comparison = compare_native_placement_matrices(expected, actual)
                        _desktop_debug_event(
                            "MANUAL_NATIVE_PREVIEW_POSE_COMPARISON",
                            bend_id=bend_id, phase=actual["phase"], evidence=comparison,
                        )
                        if comparison["status"] != "MATCH":
                            raise PreviewMeshError("NATIVE_PREVIEW_POSE_MISMATCH:" + str(comparison))
                        placement = PlacementTransform3D(
                            rotation=tuple(tuple(row) for row in actual["rotation"]),
                            translation=tuple(actual["translation"]),
                        )
                        inspected_length = math.dist(*fold_state.bends[bend_id].axis_xyz)
                        attribution = blocker.get("component_attribution") or {}
                        highlighted = tuple(
                            hit["component_id"].split(":", 1)[1]
                            for hit in attribution.get("hits", ())
                            if hit.get("component_id", "").startswith("panel:")
                        )
                        bounds = [entry["bbox"] for entry in collision_preview_boxes(blocker)]
                        message = f"{bend_id} · {actual['phase']} · Z={ram_z:+.2f} мм · коллизия OCC"
                    else:
                        fold_state = self.session.fold_state
                        placement = self._current_preview_machine_placement()
                        ram_z = float(self.tooling_setup.ram_z_mm)
                        current_verdict = self._manual_3d_verdicts.get(bend_id)
                        message = (
                            "OCC: {} (SAMPLED)".format(current_verdict) if current_verdict
                            else "Симуляция без подтверждённого результата OCC"
                        )

                    fold_signature = (id(self.session), bend_id, tuple(fold_state.angles.items()))
                    reused_fold_mesh = fold_signature == self._coin_fold_mesh_key
                    if not reused_fold_mesh:
                        self._coin_fold_mesh_faces = build_sheet_preview_faces(
                            fold_state, self.session.panel_vertices_xy, bend_id,
                            WorkpiecePose2D(), machine_placement=IDENTITY_PLACEMENT,
                            panel_meshes=getattr(self.session.unfold_data, "panel_meshes", None),
                        )
                        self._coin_fold_mesh_key = fold_signature
                    faces = transform_preview_faces(self._coin_fold_mesh_faces, placement)
                    viewer.set_sheet_faces(faces, highlighted=highlighted)
                    tooling = self.tooling_setup
                    stations, contact, gauge_note = self._coin_backgauge_positions(
                        self.session.bend_order.index(bend_id), at_collision=bool(inspection),
                    )
                    messages = viewer.update_machine(
                        self.tool_library.library_root, tooling, ram_z,
                        frame_visible=self.machine_preview_checkbox.isChecked(),
                        gauge_stations=stations, gauge_contact=contact,
                    )
                    section_preview = proposal[1] if show_proposal else None
                    if not show_proposal and not inspection and tooling.tool_assembly is None and not current_verdict:
                        section_preview = self._simulation_tool_sections(bend_id)
                        if section_preview:
                            message = f"{bend_id} · Раскладка сегментов для симуляции"
                    if show_proposal:
                        message = f"{bend_id} · Предпросмотр раскладки сегментов"
                        if not tooling.visible:
                            tooling = replace(tooling, visible=True)
                    viewer.update_tools(
                        tooling, ram_z, active_bend_length_mm=inspected_length,
                        section_preview=section_preview,
                    )
                    viewer.set_collision_boxes(bounds)
                    status = " · ".join(filter(None, (message, *messages, gauge_note)))
                    if self._coin_frame_status.text() != status:
                        self._coin_frame_status.setText(status)
                    previous = self._last_3d_frame_at
                    self._last_3d_frame_at = now
                    self._last_3d_rendered_bend = bend_id
                    self._coin_last_error = None
                    if not inspection and not show_proposal:
                        self._trace_drawn_frame(placement, ram_z, stations, contact)
                    if self.timer.isActive() and now - self._last_3d_debug_at >= 0.5:
                        self._last_3d_debug_at = now
                        _desktop_debug_event(
                            "COIN3D_FRAME", bend_id=bend_id, face_count=len(faces),
                            fold_mesh_reused=reused_fold_mesh,
                            frame_interval_ms=round((now - previous) * 1000, 2),
                            render_ms=round((time.perf_counter() - now) * 1000, 2),
                            renderer="FreeCAD Coin3D" if viewer._native else "Coin3D/Quarter",
                        )
                    return True
                except (ValueError, KeyError, RuntimeError, TypeError, AttributeError, IndexError) as exc:
                    message = "Coin3D: " + str(exc)
                    if message != self._coin_last_error:
                        _desktop_debug_event(
                            "COIN3D_PREVIEW_ERROR", bend_id=bend_id, message=message,
                            traceback=traceback.format_exc(),
                        )
                        self._coin_last_error = message
                    if self.timer.isActive():
                        self._halt_unsupported_machine_pose(message)
                    self._coin_frame_status.setText(message)
                    self.status.setText(message)
                    return False

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
                    self.tooling_setup.punch_section_spans_xm,
                    self.tooling_setup.die_section_spans_xm,
                    bool(getattr(self.tooling_setup, "allow_end_zone_collision", False)),
                    round(float(getattr(self.tooling_setup, "end_zone_length_mm", 0.0) or 0.0), 4),
                    getattr(getattr(self.tooling_setup, "punch_machine_transform", None), "to_matrix_tuple", lambda: ())(),
                    getattr(getattr(self.tooling_setup, "die_machine_transform", None), "to_matrix_tuple", lambda: ())(),
                    (getattr(getattr(pc, "phase", None), "value", getattr(pc, "phase", "")) if pc is not None else ""),
                    (float(getattr(pc, "ram_z_mm", 0.0) or 0.0) if pc is not None and getattr(pc, "ram_z_mm", None) is not None else None),
                    (float(getattr(pc, "transfer_progress", 0.0) or 0.0) if pc is not None and getattr(pc, "transfer_progress", None) is not None else None),
                    tuple(sorted(self._effective_loading_orientation_map().items())),
                )

            def _show_bend_information(self):
                if self.session is None:
                    self.status.setText("Сначала загрузите развёртку.")
                    return
                if not self._apply_typed_manual_order():
                    return
                from freecad_smbs.ui.bend_information import show_bend_information

                show_bend_information(self, embed_task_dialog)

            def _tool_assembly_inputs(self, *, require_pair=True):
                from smbs_core.geometry_backend.io.clamp_validator import load_clamp_config
                from smbs_core.kinematics2d.segment_assembly_planner import read_inventory

                punch, die = self.tooling_setup.punch, self.tooling_setup.die
                if require_pair and (punch is None or die is None):
                    raise ValueError("Сначала выберите пуансон и матрицу.")

                def stock(tool):
                    if tool is None:
                        return ()
                    path = Path(tool.source_file)
                    if path.suffix.lower() != ".fcstd" or not path.is_file():
                        return ()
                    return read_inventory(path)

                p_inventory, d_inventory = stock(punch), stock(die)
                if require_pair and (not p_inventory or not d_inventory):
                    raise ValueError("Задайте запасы пуансона и матрицы через «Сегменты и количество».")
                clamp_config = load_clamp_config(self.tool_library.library_root)
                return p_inventory, d_inventory, clamp_config

            def _tool_assembly_order(self):
                return tuple(self._manual_sequence_prefix) or self.session.bend_order

            def _calculate_common_tool_assembly(self, order):
                from smbs_core.kinematics2d.segment_assembly_planner import plan_pair_for_bend
                from smbs_core.kinematics2d.tooling_assembly import ToolAssembly

                p_inventory, d_inventory, cfg = self._tool_assembly_inputs()
                if not cfg.centres_mm:
                    raise ValueError("На вкладке «Лапки» задайте центры зажимов или их количество.")
                bends = self.session.fold_state.bends
                required = max(math.dist(*bends[bend_id].axis_xyz) for bend_id in order)
                plan = plan_pair_for_bend(p_inventory, d_inventory, required, cfg)
                return ToolAssembly.from_plan(plan)

            def _verify_tool_assembly_geometry(self, assembly):
                from smbs_core.kinematics2d.verified_tool_spans import verified_fixed_tool_spans

                candidate = replace(
                    self.tooling_setup, tool_assembly=assembly,
                    match_active_bend_length=False,
                    punch_section_offsets_xm=(), die_section_offsets_xm=(),
                )
                verified_fixed_tool_spans(candidate)

            def _close_tool_assembly_report(self):
                report = getattr(self, "_task_dialogs", {}).get("tool_assembly")
                if report is not None:
                    report.reject()

            def _apply_tool_assembly(self, assembly):
                if self.tooling_setup.tool_assembly == assembly:
                    return
                if self.timer.isActive() or self.playing:
                    self.pause()
                if assembly is not None:
                    self.tooling_setup.set_match_active_bend_length(False)
                    blocked = self.virtual_tool_len_checkbox.blockSignals(True)
                    self.virtual_tool_len_checkbox.setChecked(False)
                    self.virtual_tool_len_checkbox.blockSignals(blocked)
                self.tooling_setup.set_tool_assembly(assembly)
                self._machine_pose_fault = None
                self._physical_check_signature = None
                self._last_manual_sequence_report = None
                self._manual_3d_verdicts.clear()
                self._manual_retract_verdicts.clear()
                self._conditional_bends_status.clear()
                self._clear_manual_check_feedback()
                prefix_count = len(self._manual_sequence_prefix)
                self._partial_preview_limit = (
                    prefix_count if 0 < prefix_count < len(self.session.bend_order) else None
                )
                self._highlighted_end_zone_info = None
                self.physical_check_result.setText("Оснастка изменилась. Текущий набор ещё не проверен.")
                self._manual_preview_notice.setText("РУЧНОЙ ПРОСМОТР — коллизии не проверены")
                self._sync_tooling_to_active_bend()
                self._update_operations_visual_state()
                self.draw()

            def _simulation_tool_sections(self, bend_id):
                from smbs_core.geometry_backend.io.clamp_validator import ClampValidatorError
                from smbs_core.kinematics2d.segment_assembly_planner import plan_pair_for_bend

                tooling = self.tooling_setup
                key = (id(self.session), id(tooling.punch), id(tooling.die),
                       self.tool_library.library_root)
                if self._simulation_layout_key != key:
                    self._simulation_layout_key = key
                    self._simulation_layouts.clear()
                length = math.dist(*self.session.fold_state.bends[bend_id].axis_xyz)
                if length not in self._simulation_layouts:
                    sections = {}
                    try:
                        punch, die, config = self._tool_assembly_inputs(require_pair=False)
                        machine = getattr(self._preview_3d, "_machine", None)
                        if not config.centres_mm and machine is not None:
                            config = replace(config, positions_mm=tuple(
                                (left + right) / 2 for left, right in machine.clamp_spans
                            ))
                        plan = plan_pair_for_bend(punch, die, length, config)
                        punch_plan = plan.get("punch_preview", plan)
                        if not punch_plan.get("clamps", {}).get("proposed_reposition"):
                            for kind, source in (("punch", punch_plan), ("die", plan)):
                                spans = tuple((item["start_x_mm"], item["end_x_mm"])
                                              for item in source.get(kind + "_segments", ()))
                                if spans:
                                    sections[kind] = spans
                    except (OSError, ValueError, ClampValidatorError) as exc:
                        _warn(f"Раскладка для симуляции: {exc}")
                    self._simulation_layouts[length] = sections or None
                return self._simulation_layouts[length]

            def _on_tool_inventory_changed(self, kind=None, tool_id=None):
                self._simulation_layout_key = None
                if kind is not None:
                    key = str(getattr(kind, "value", kind)).lower()
                    selected = getattr(self.tooling_setup, key, None)
                    if selected is None or selected.tool_id != tool_id:
                        return
                self._close_tool_assembly_report()
                if self.tooling_setup.tool_assembly is not None:
                    self._apply_tool_assembly(None)
                    self.status.setText("Склад или зажимы изменились. Подберите набор заново.")
                elif kind is None:
                    self.draw()

            def _show_manual_tooling_assembly(self):
                """Show per-bend proposals and an explicit common layout for native OCC."""
                from collections import Counter
                from smbs_core.geometry_backend.io.clamp_validator import ClampValidatorError
                from smbs_core.kinematics2d.segment_assembly_planner import plan_pair_for_bend
                from smbs_core.kinematics2d.tooling_assembly import (
                    clamp_spans_for_centres, punch_segment_supports,
                )

                if self.session is None:
                    self.status.setText("Загрузите деталь перед подбором набора.")
                    return
                if self._task_progress_busy or self.timer.isActive() or self.playing:
                    self.status.setText("Остановите симуляцию или дождитесь окончания проверки.")
                    return
                if not self._apply_typed_manual_order():
                    return
                try:
                    p_inventory, d_inventory, cfg = self._tool_assembly_inputs(require_pair=False)
                    order = self._tool_assembly_order()
                    machine = getattr(self._preview_3d, "_machine", None)
                    nominal_clamps = not cfg.centres_mm
                    if nominal_clamps and machine is not None:
                        cfg = replace(cfg, positions_mm=tuple(
                            (left + right) / 2 for left, right in machine.clamp_spans
                        ))
                    bends = self.session.fold_state.bends
                    reports = []
                    by_length = {}
                    for bend_id in order:
                        length = math.dist(*bends[bend_id].axis_xyz)
                        if length not in by_length:
                            by_length[length] = plan_pair_for_bend(
                                p_inventory, d_inventory, length, cfg,
                            )
                        reports.append((bend_id, by_length[length]))
                except (OSError, ValueError, KeyError, ClampValidatorError) as exc:
                    self.status.setText(str(exc))
                    return

                def describe_sections(label, spans, clamp_spans=()):
                    lengths = [round(hi - lo, 6) for lo, hi in spans]
                    counts = Counter(lengths)
                    stock = ", ".join(f"{length:g} мм × {count}" for length, count in counts.items())
                    lines = [f"  {label} {sum(lengths):.3f} мм · {stock}"]
                    if clamp_spans:
                        center = (spans[0][0] + spans[-1][1]) / 2.0
                        lines.append(f"  Центр пуансона по X {center:+.3f} мм")
                    support = punch_segment_supports(spans, clamp_spans)
                    for index, ((lo, hi), length, contacts) in enumerate(zip(spans, lengths, support), 1):
                        entry = f"    {index}. {length:g} мм · X {lo:+.3f}…{hi:+.3f}"
                        if contacts:
                            overlaps = ", ".join(f"№{number} ({overlap:.3g} мм)"
                                                 for number, overlap in contacts)
                            entry += f" · опора на лапки {overlaps}"
                        lines.append(entry)
                    return lines

                def describe_shortfall(required, punch_length):
                    missing = required - punch_length
                    if missing > 1e-6:
                        return [f"  Пуансон короче линии гиба на {missing:.3f} мм. OCC требует полного перекрытия."]
                    return []

                def spans_for(plan, kind):
                    return tuple((item["start_x_mm"], item["end_x_mm"])
                                 for item in plan.get(kind + "_segments", ()))

                def describe(plan):
                    punch_plan = plan.get("punch_preview", plan)
                    lines = []
                    if "punch_preview" in plan:
                        if not d_inventory:
                            lines.append("  Задайте склад выбранной матрицы для расчёта её секций.")
                        else:
                            lines.append("  Из склада матрицы не найден подходящий набор.")
                    status = punch_plan["status"]
                    if status == "NO_INVENTORY_COMBINATION":
                        reason = ("Из склада пуансона не собрать нужную длину."
                                  if p_inventory else "Задайте склад выбранного пуансона.")
                        return lines + [f"  {reason}"]
                    if status == "ASSEMBLY_SEARCH_LIMIT":
                        return lines + ["  Достигнут предел поиска секций пуансона."]
                    if status != "ASSEMBLY_CANDIDATE_UNVERIFIED":
                        return lines + ["  Не найден пуансон с опорой на заданные зажимы."]
                    clamps = punch_plan["clamps"]
                    zones = clamp_spans_for_centres(clamps["centers_mm"], clamps["width_mm"])
                    lines.extend(describe_sections("Пуансон", spans_for(punch_plan, "punch"), zones))
                    if spans_for(plan, "die"):
                        lines.extend(describe_sections("Матрица", spans_for(plan, "die")))
                    lines.extend(describe_shortfall(plan["required_mm"], punch_plan["punch_total_mm"]))
                    centres = ", ".join(f"{x:+.3f}" for x in clamps["centers_mm"])
                    lines.append(f"  Центры зажимов по X, мм · {centres}")
                    if clamps["proposed_reposition"]:
                        lines.append("  Предложенные позиции зажимов. Сверьте их со станком.")
                    return lines

                punch, die = self.tooling_setup.punch, self.tooling_setup.die
                titles = [
                    f"Пуансон {punch.tool_id if punch else '—'}",
                    f"Матрица {die.tool_id if die else '—'}",
                    f"Зажимы {cfg.width_mm:g} мм вдоль X, зазор от {cfg.gap_between_mm:g} мм.",
                    "X = 0 в середине станка. Сегменты перечислены от меньшего X к большему.",
                ]
                if nominal_clamps:
                    titles.append("Для расчёта показан ряд по CAD-траверсе. Перед применением задайте лапки станка.")
                lines = ["Раскладка по гибам", ""]
                for bend_id, plan in reports:
                    lines.append(f"{bend_id} · линия гиба {plan['required_mm']:.3f} мм")
                    lines.extend(describe(plan))
                    lines.append("")
                per_bend = "\n".join(lines)

                self._close_tool_assembly_report()
                dialog = QtWidgets.QDialog(self)
                layout = QtWidgets.QVBoxLayout(dialog)
                bend_picker = QtWidgets.QComboBox()
                bend_picker.setAccessibleName("Гиб для предпросмотра секций")
                for bend_id, plan in reports:
                    bend_picker.addItem(f"{bend_id} · {plan['required_mm']:.2f} мм", bend_id)
                current = bend_picker.findData(self.session.active_bend_id)
                bend_picker.setCurrentIndex(max(0, current))
                layout.addWidget(bend_picker)
                edit = QtWidgets.QPlainTextEdit(dialog)
                edit.setObjectName("SMBS_ToolAssemblyReport")
                edit.setReadOnly(True)
                layout.addWidget(edit, 1)
                feedback = QtWidgets.QLabel()
                feedback.setWordWrap(True)
                layout.addWidget(feedback)
                apply_btn = QtWidgets.QPushButton("Применить общий набор")
                apply_btn.setObjectName("SMBS_ApplyCommonToolAssembly")
                apply_btn.setToolTip("Один комплект на выбранный порядок по длине самого длинного гиба.")
                layout.addWidget(apply_btn)
                clear_btn = QtWidgets.QPushButton("Снять общий набор")
                layout.addWidget(clear_btn)

                plans_by_bend = dict(reports)
                _desktop_debug_event(
                    "TOOL_ASSEMBLY_CALCULATED", bend_order=order,
                    punch_stock=p_inventory, die_stock=d_inventory,
                    plans=plans_by_bend,
                )

                def preview_bend():
                    bend_id = bend_picker.currentData()
                    plan = plans_by_bend[bend_id]
                    punch_plan = plan.get("punch_preview", plan)
                    sections = {"punch": spans_for(punch_plan, "punch"),
                                "die": spans_for(plan, "die")}
                    if punch_plan.get("clamps", {}).get("proposed_reposition"):
                        sections = {}
                        feedback.setText("Для предпросмотра задайте позиции или число лапок на станке.")
                    elif sections["punch"]:
                        lengths = " + ".join(f"{hi - lo:g}" for lo, hi in sections["punch"])
                        feedback.setText(f"{bend_id} · Пуансон {lengths} мм в Coin3D. "
                                         "Для OCC примените общий набор.")
                    else:
                        feedback.setText(f"{bend_id} · Подходящих секций для предпросмотра нет.")
                    self._tooling_sections_preview = (bend_id, sections) if any(sections.values()) else None
                    if self._tooling_sections_preview is not None:
                        row = self.session.bend_order.index(bend_id)
                        self.operations.setCurrentItem(self.operations.topLevelItem(row))
                        self._open_coin_preview()
                    else:
                        self.draw()

                def end_preview():
                    if self._tooling_sections_preview is not None:
                        self._tooling_sections_preview = None
                        self.draw()

                def update_report():
                    assembly = self.tooling_setup.tool_assembly
                    clear_btn.setEnabled(assembly is not None)
                    header = list(titles)
                    if assembly is not None:
                        header.extend(["", f"Общий набор активен в Coin3D и OCC · {assembly.bend_length_mm:.3f} мм"])
                        header.extend(describe_sections(
                            "Пуансон", assembly.punch_spans_xm, assembly.clamp_spans_xm,
                        ))
                        header.extend(describe_sections("Матрица", assembly.die_spans_xm))
                        header.extend(describe_shortfall(
                            assembly.bend_length_mm,
                            sum(hi - lo for lo, hi in assembly.punch_spans_xm),
                        ))
                        centres = ", ".join(f"{(lo + hi) / 2:+.3f}" for lo, hi in assembly.clamp_spans_xm)
                        header.append(f"  Центры зажимов по X, мм · {centres}")
                    edit.setPlainText("\n".join(header) + "\n\n" + per_bend)

                def apply_common():
                    if self._task_progress_busy or self.timer.isActive() or self.playing:
                        feedback.setText("Остановите симуляцию или дождитесь окончания проверки.")
                        return
                    try:
                        order = self._tool_assembly_order()
                        assembly = self._calculate_common_tool_assembly(order)
                        self._verify_tool_assembly_geometry(assembly)
                    except Exception as exc:
                        feedback.setText(str(exc))
                        return
                    end_preview()
                    self._apply_tool_assembly(assembly)
                    _desktop_debug_event(
                        "TOOL_ASSEMBLY_APPLIED", bend_order=order,
                        punch_sections=assembly.punch_spans_xm, die_sections=assembly.die_spans_xm,
                        clamp_zones=assembly.clamp_spans_xm,
                    )
                    feedback.setText("Общий набор применён. Коллизии проверяются кнопками OCC.")
                    update_report()

                def clear_common():
                    end_preview()
                    self._apply_tool_assembly(None)
                    feedback.setText("Возвращена исходная оснастка.")
                    update_report()

                update_report()
                apply_btn.clicked.connect(apply_common)
                clear_btn.clicked.connect(clear_common)
                embed_task_dialog(self, dialog, "Раскладка оснастки", "tool_assembly")
                dialog.finished.connect(end_preview)
                bend_picker.currentIndexChanged.connect(preview_bend)
                preview_bend()

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
                if enabled and self.tooling_setup.tool_assembly is not None:
                    self._close_tool_assembly_report()
                    self._apply_tool_assembly(None)
                self._manual_3d_verdicts.clear()
                self._manual_retract_verdicts.clear()
                self._clear_manual_check_feedback()
                bend_len = self._active_bend_length_mm()
                if hasattr(self.tooling_setup, "set_match_active_bend_length"):
                    self.tooling_setup.set_match_active_bend_length(bool(enabled), bend_len)
                if self.press_cycle is not None and hasattr(self.press_cycle, "set_match_active_bend_length"):
                    self.press_cycle.set_match_active_bend_length(bool(enabled))
                self._update_tooling_status_label()
                self.draw()

            def _on_end_zone_length_changed(self, val: float):
                self._manual_3d_verdicts.clear()
                self._manual_retract_verdicts.clear()
                self._clear_manual_check_feedback()
                try:
                    fval = max(0.0, float(val))
                except (TypeError, ValueError):
                    return
                self.tooling_setup.end_zone_length_mm = fval
                self._update_tooling_status_label()

            def _on_allow_end_zone_toggled(self, enabled: bool):
                self._manual_3d_verdicts.clear()
                self._manual_retract_verdicts.clear()
                self._clear_manual_check_feedback()
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
                if self.session.active_bend_id not in self._effective_loading_orientation_map():
                    self.physical_check_result.setText(
                        "UNVERIFIED: автоматическая ориентация текущего гиба "
                        "не определена."
                    )
                    return
                if self.timer.isActive() or self.session.playback_status == PlaybackStatus.PLAYING:
                    self.physical_check_result.setText(
                        "UNVERIFIED: сначала Pause; проверяется только неподвижный кадр."
                    )
                    return
                self.physical_check_btn.setEnabled(False)
                self._highlighted_end_zone_info = None
                self._set_task_busy(True)
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
                    self._set_task_busy(False)
                    self.physical_check_btn.setEnabled(True)

            def check_active_bend_physical_trajectory(self):
                """D6.3.4 adaptive evaluation on demand; no timer-side OCC calls."""
                if self.session is None:
                    self.physical_check_result.setText(
                        "UNVERIFIED: деталь не загружена"
                    )
                    return
                orientation = self._effective_loading_orientation_map()
                if self.session.active_bend_id not in orientation:
                    self.physical_check_result.setText(
                        "UNVERIFIED: автоматическая ориентация текущего гиба "
                        "не определена."
                    )
                    return
                active_reverse = bool(orientation[self.session.active_bend_id])
                if self.timer.isActive() or self.session.playback_status == PlaybackStatus.PLAYING:
                    self.physical_check_result.setText(
                        "UNVERIFIED: Pause перед проверкой всей траектории."
                    )
                    return
                self.full_physical_check_btn.setEnabled(False)
                self.physical_check_btn.setEnabled(False)
                self._highlighted_end_zone_info = None
                self._set_task_busy(True)
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
                        reverse_axis=active_reverse,
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
                                reverse_axis=active_reverse,
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
                    self._set_task_busy(False)
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
                self._manual_preview_unverified = False
                self._manual_preview_notice.hide()
                self._set_task_busy(True)
                # Rear gauge is visualization-only; never spend OCC search
                # budget on collision, clearance, contact or CAD penetration.
                search_budget_s = 30.0
                self.status.setText(
                    "Быстрый поиск (до {:.0f} с): 2D-сечения. Возможны 3D-коллизии; "
                    "полная проверка отключена.".format(search_budget_s)
                )
                QtWidgets.QApplication.processEvents()
                try:
                    from smbs_core.kinematics2d.sequence_search_2d import (
                        search_bend_sequences_2d,
                    )
                    # A 2D preview with virtual axial tool length is permitted
                    # ONLY for exploration, never treated as production safety
                    # evidence. Native multi-sections cannot be virtually extended.
                    has_sections = has_native_sections(self.tooling_setup)
                    # No CAD backgauge contact trace in visual-only mode.
                    self._last_backgauge_cad_trace_path = None
                    report = search_bend_sequences_2d(
                        self.session, self.tooling_setup,
                        # Collect multiple complete candidates for genuine
                        # weight-based comparison, subject to the search budget.
                        # The best found is not proof of global optimality.
                        max_solutions=12,
                        max_complete_candidates=12,
                        weights=self._selected_operation_weights(),
                        backgauge_model=None,
                        backgauge_cad_trace_path=None,
                        # This workflow offers complete sequences only.
                        min_partial_bends=0,
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
                            1.8 if len(self.session.bend_order) >= 10
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
                    # Store validated complete candidates with their preview
                    # orientation, not merely the last solver status.
                    for candidate in self._complete_planar_candidates(report):
                        directions = {
                            step.bend_id: step.orientation_variant.startswith(
                                "REVERSE_MACHINE_Z_180"
                            ) for step in candidate.steps
                        }
                        if len(directions) == len(candidate.sequence):
                            entry = {
                                "bend_ids": list(candidate.sequence),
                                "orientation_by_bend": directions,
                                "origin": "2D_SEARCH",
                                "evidence": str(candidate.evidence),
                            }
                            if entry not in self._saved_full_sequences:
                                self._saved_full_sequences.append(entry)
                    self._saved_full_sequences = self._saved_full_sequences[-32:]
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
                    )
                    complete = self._complete_planar_candidates(report)
                    if complete:
                        self._show_planar_candidates_dialog(report)
                    else:
                        limit = (
                            " Достигнут предел поиска ({}). Это не доказывает, "
                            "что решения нет; повторный запуск начинает поиск "
                            "заново.".format(report.limit_reason)
                            if report.limit_reason else ""
                        )
                        message = (
                            "За {:.0f} с полных 2D вариантов не найдено."
                            .format(search_budget_s) + limit
                        )
                        self.status.setText(message)
                        QtWidgets.QMessageBox.information(
                            self, "Поиск 2D вариантов", message,
                        )
                except Exception as exc:
                    msg = "2D поиск не выполнен: {}".format(exc)
                    self.status.setText(msg)
                    _error("Planar search exception: " + traceback.format_exc())
                finally:
                    self._set_task_busy(False)
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
                has_sections = has_native_sections(self.tooling_setup)
                valid, reason = validate_partial_planar_prefix(
                    self.session, self.tooling_setup, sequence,
                    match_active_bend_length=not has_sections,
                    allow_retract_warning=self.retract_warning_checkbox.isChecked(),
                    backgauge_model=None,
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
                self._partial_preview_limit = len(sequence) if is_partial else None
                self._preview_only_planar_order = sequence
                self._selected_planar_gauge_order = ()
                self._selected_planar_gauge_positions = {}
                self._selected_planar_gauge_model_id = None
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

            def _select_saved_sequence(self):
                """Use an archived order as UNVERIFIED, never as a collision pass."""
                if self.session is None:
                    self.status.setText("Сначала загрузите деталь.")
                    return
                saved = tuple(self._saved_full_sequences)
                if not saved:
                    self.status.setText(
                        "Сохранённых полных 2D вариантов пока нет."
                    )
                    return
                labels = [
                    "{}. {}".format(i + 1, " → ".join(item["bend_ids"]))
                    for i, item in enumerate(saved)
                ]
                label, accepted = QtWidgets.QInputDialog.getItem(
                    self, "Сохранённые варианты", "Последовательность:",
                    labels, 0, False
                )
                if not accepted or label not in labels:
                    return
                item = saved[labels.index(label)]
                order = tuple(item["bend_ids"])
                if (len(order) != len(self.session.canonical_order)
                        or set(order) != set(self.session.canonical_order)):
                    self.status.setText(
                        "Вариант не соответствует текущей детали."
                    )
                    return
                self._stop_timer()
                self.session.reset_all_bends()
                self.session.set_bend_order(order)
                self._manual_orientation_overrides.clear()
                orientations = item.get("orientation_by_bend", {})
                if set(orientations) == set(order):
                    self._manual_orientation_order = order
                    self._manual_orientation_by_bend = dict(orientations)
                else:
                    self._manual_orientation_order = ()
                    self._manual_orientation_by_bend = {}
                self._manual_sequence_prefix = list(order)
                self._sync_manual_order_input()
                self._manual_preview_unverified = True
                self._manual_preview_notice.show()
                self._manual_3d_verdicts.clear()
                self._manual_retract_verdicts.clear()
                self._clear_manual_check_feedback()
                self._last_manual_sequence_report = None
                self._ensure_press_cycle_controller()
                self._sync_tooling_to_active_bend()
                self._populate_operations()
                self._configure_for_active_bend()
                self.draw()
                self._draw_flat_pattern(reset_camera=False)
                self.physical_check_result.setText(
                    "UNVERIFIED: исторический вариант из FCStd. "
                    "Повторите проверку коллизий."
                )
                self.status.setText(
                    "Сохранённый порядок выбран: {} | UNVERIFIED"
                    .format(" → ".join(order))
                )

            def _complete_planar_candidates(self, report):
                """Only full, unique orders can enter the Manual workflow."""
                total = len(self.session.bend_order)
                expected = set(self.session.bend_order)
                seen = set()
                complete = []
                for candidate in report.candidates:
                    sequence = tuple(candidate.sequence)
                    if (len(sequence) != total or set(sequence) != expected
                            or sequence in seen):
                        continue
                    seen.add(sequence)
                    complete.append(candidate)
                return complete

            def _show_planar_candidates_dialog(self, report):
                """Choose a complete 2D order for Manual verification."""
                # A new search may replace a previous result; close the old tab
                # explicitly rather than presenting a stale list.
                previous = getattr(self, "_task_dialogs", {}).get("planar_candidates")
                if previous is not None:
                    previous.reject()
                dlg = QtWidgets.QDialog(self)
                dlg.setWindowTitle("SMBS — найденные полные порядки")
                dlg.resize(700, 430)
                layout = QtWidgets.QVBoxLayout(dlg)
                heading = QtWidgets.QLabel(
                    "Шаг 2: выберите один полный порядок и откройте раздел "
                    "«Порядок и проверка». Номера появятся на развёртке. "
                    "Затем отдельно нажмите «Быстрая проверка OCC (5 углов)» или "
                    "«Полная проверка (3D)». Пока 3D коллизии не проверены."
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
                        report.status.value, len(self._complete_planar_candidates(report)),
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
                options.setMinimumHeight(145)
                visible_candidates = self._complete_planar_candidates(report)
                for index, candidate in enumerate(visible_candidates):
                    suffix = (" | штраф {:.2f}".format(candidate.cost)
                              if candidate.cost_complete else " | штраф не подтверждён")
                    evidence_label = {
                        "SAMPLED_2D_CLEAR_OR_TOUCH":
                            "в проверенных 2D сечениях препятствий не обнаружено",
                        "CONDITIONAL_RETRACT_WARNING_NOT_3D_VALIDATED":
                            "предупреждение при отводе пуансона (RETRACT)",
                    }.get(candidate.evidence, "есть неопределённые 2D участки")
                    reversed_bends = [
                        step.bend_id for step in candidate.steps
                        if step.orientation_variant.startswith("REVERSE_MACHINE_Z_180")
                    ]
                    options.addItem(
                        "{}. {} | {}{} | разворот 180°: {}".format(
                            index + 1, " → ".join(candidate.sequence),
                            evidence_label, suffix,
                            ", ".join(reversed_bends) or "нет",
                        )
                    )
                if visible_candidates:
                    options.setCurrentRow(0)
                # Put the decision first. Long DFS diagnostics remain below.
                layout.insertWidget(1, options)
                controls = QtWidgets.QHBoxLayout()
                use = QtWidgets.QPushButton("Выбрать порядок → к проверке")
                use.setEnabled(bool(visible_candidates))
                export_btn = QtWidgets.QPushButton("Сохранить диагностику JSON")
                close = QtWidgets.QPushButton("Закрыть")
                controls.addWidget(use)
                controls.addWidget(export_btn)
                controls.addWidget(close)
                layout.insertLayout(2, controls)

                def on_preview():
                    row = options.currentRow()
                    if row < 0 or row >= len(visible_candidates):
                        return
                    candidate = visible_candidates[row]
                    try:
                        if candidate not in self._complete_planar_candidates(report):
                            raise ValueError("Выбранный порядок не является полным")
                        self._stop_timer()
                        self.session.reset_all_bends()
                        dlg.accept()
                        self.activate_manual_mode()
                        if not self._apply_manual_order(candidate.sequence):
                            return
                        orientations = {
                            step.bend_id: step.orientation_variant.startswith(
                                "REVERSE_MACHINE_Z_180"
                            ) for step in candidate.steps
                        }
                        if set(orientations) != set(candidate.sequence):
                            raise ValueError("Ориентация 2D неполная; вариант не назначен")
                        self._manual_orientation_order = tuple(candidate.sequence)
                        self._manual_orientation_by_bend = orientations
                        self._populate_operations()
                        self._ensure_press_cycle_controller()
                        if self.press_cycle is not None:
                            self.press_cycle.loading_orientation_by_bend = (
                                self._manual_orientation_selection(candidate.sequence)
                            )
                        self.draw()
                        self._viewer_tabs.setCurrentWidget(self._flat_view)
                        self._draw_flat_pattern(reset_camera=True)
                        self.status.setText(
                            "Порядок выбран (3D НЕ ПРОВЕРЕН): "
                            + " → ".join(candidate.sequence)
                        )
                        self.physical_check_result.setText(
                            "UNVERIFIED: выбран полный 2D порядок. "
                            "Запустите «Быстрая проверка OCC (5 углов)» или "
                            "«Проверить и показать». "
                            "Возможны коллизии."
                        )
                        self._trace_event(
                            "PLANAR_2D_MANUAL_ORDER_SELECTED",
                            sequence=list(candidate.sequence),
                            evidence=candidate.evidence,
                            mixed_orientation=mixed_orientation,
                            physical_status="UNVERIFIED",
                        )
                        if mixed_orientation:
                            self.status.setText(
                                "Ориентация каждого гиба перенесена из 2D; "
                                "3D-проверка использует те же установки."
                            )
                    except Exception as exc:
                        self.status.setText("Нельзя назначить 2D порядок: " + str(exc))
                        _error("Planar manual selection exception: " + traceback.format_exc())

                def on_export():
                    import json
                    destination, _ = QtWidgets.QFileDialog.getSaveFileName(
                        dlg, "Сохранить 2D диагностику",
                        str(_debug_desktop_directory() / "smbs_2d_search_diagnostics.json"),
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
                embed_task_dialog(self, dlg, "Выбор порядка", "planar_candidates")

            def calculate_sequence(self):
                """Run automatic 2.5D + D6 physical sequence search with backtracking and forward replay."""
                self._trace_event(
                    "SEQUENCE_SEARCH_MODE_SELECTED",
                    mode="FULL_NATIVE_D6_SAMPLED",
                    continuous_motion_certified=False,
                )
                if self.session is None:
                    self.status.setText("UNVERIFIED: сначала загрузите листовую деталь.")
                    return
                if self.timer.isActive() or self.session.playback_status == PlaybackStatus.PLAYING:
                    self.status.setText("Сначала остановите воспроизведение (Pause / Reset) перед расчётом последовательности.")
                    return
                self.calculate.setEnabled(False)
                self.physical_check_btn.setEnabled(False)
                self.full_physical_check_btn.setEnabled(False)
                self._manual_preview_unverified = False
                self._manual_preview_notice.hide()
                self._highlighted_end_zone_info = None
                self._set_task_busy(True)
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
                        self._task_progress_caption.setText(
                            f"Глубина {depth}/{total} · не %"
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
                    self._partial_preview_limit = None
                    self._selected_planar_gauge_order = ()
                    self._selected_planar_gauge_positions = {}
                    self._selected_planar_gauge_model_id = None
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
                    self._set_task_busy(False)
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
                    lift = self.press_cycle.assisted_retract_lift_mm()
                    self.cycle_phase_label.setText(
                        f"Cycle Phase: {phase_val} ({sim_val} | {contact_val})"
                        + (f" | предполагаемый подъём +{lift:.2f} мм, UNVERIFIED"
                           if lift > 0.0 else "")
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

            def _rear_frame_for_preview(self):
                # The per-bend orientation map owns the machine-facing side.
                # Passing vertices here would apply a second independent flip.
                return None

            def _on_operation_orientation_changed(self, item, column):
                if column != _LOADING_ORIENTATION_COLUMN:
                    return
                self._set_manual_loading_override(
                    item, item.checkState(column) == QtCore.Qt.Checked
                )

            def _show_operation_orientation_menu(self, pos):
                if self.operations.columnAt(pos.x()) != _LOADING_ORIENTATION_COLUMN:
                    return
                item = self.operations.itemAt(pos)
                if item is None:
                    return
                bend_id = item.data(0, QtCore.Qt.UserRole)
                if bend_id not in self._manual_orientation_overrides:
                    return
                menu = QtWidgets.QMenu(self.operations)
                automatic = menu.addAction("Подбирать положение автоматически")
                run_menu = getattr(menu, "exec", None) or menu.exec_
                if run_menu(self.operations.viewport().mapToGlobal(pos)) == automatic:
                    self._set_manual_loading_override(item, None)

            def _set_manual_loading_override(self, item, reverse_axis):
                """Set 0°/180° for one bend, or return it to automatic selection."""
                bend_id = item.data(0, QtCore.Qt.UserRole)
                if self.session is None or bend_id not in self.session.bend_order:
                    return
                if (self._task_progress_busy or self.timer.isActive()
                        or self.session.playback_status == PlaybackStatus.PLAYING):
                    self._update_operations_visual_state()
                    self.status.setText(
                        "Остановите симуляцию или дождитесь окончания проверки."
                    )
                    return
                order = tuple(self.session.bend_order)
                if reverse_axis is None:
                    self._manual_orientation_overrides.pop(bend_id, None)
                else:
                    self._manual_orientation_overrides[bend_id] = reverse_axis

                self._machine_pose_fault = None
                self._physical_check_signature = None
                self._highlighted_end_zone_info = None
                self._manual_3d_verdicts.clear()
                self._manual_retract_verdicts.clear()
                self._conditional_bends_status.clear()
                self._last_manual_sequence_report = None
                self._backgauge_preview_cache.clear()
                prefix_size = len(self._manual_sequence_prefix)
                self._partial_preview_limit = prefix_size if 0 < prefix_size < len(order) else None
                self._clear_manual_check_feedback()
                self._manual_preview_notice.setText("РУЧНОЙ ПРОСМОТР — коллизии не проверены")
                self.physical_check_result.setText(
                    "UNVERIFIED: положение заготовки изменилось. Проверьте коллизии повторно."
                )
                if self.session.active_bend_id != bend_id:
                    # The native checkbox indicator does not select its row.
                    self.operations.setCurrentItem(item, _LOADING_ORIENTATION_COLUMN)
                else:
                    cycle = self._ensure_press_cycle_controller()
                    if cycle is not None and cycle.phase == CyclePhase.TRANSFER:
                        # Discard paused transfer endpoints from the previous loading map.
                        cycle.select_operation(self.session.active_bend_index)
                    self._update_operations_visual_state()
                    self._update_tooling_status_label()
                    self.draw()
                choices = self._effective_loading_orientation_map()
                _desktop_debug_event(
                    "MANUAL_LOADING_180_CHANGED",
                    bend_id=bend_id,
                    operator_side="-s_LEFT",
                    machine_rear_side="+s_RIGHT",
                    reverse_axis=choices[bend_id],
                    manual_override=reverse_axis is not None,
                    selected_order=list(order),
                    orientation_by_bend=choices,
                    physical_status="UNVERIFIED",
                )
                pose = "180°" if choices[bend_id] else "0°"
                source = "подбор положения" if reverse_axis is None else "вручную"
                self.status.setText(
                    f"{bend_id}: положение {pose} ({source}). "
                    "Коллизии для нового положения ещё не проверены."
                )

            def _manual_orientation_selection(self, bend_ids):
                """Select from the single map used by both previews and OCC."""
                choices = self._effective_loading_orientation_map()
                return {bid: bool(choices[bid]) for bid in bend_ids if bid in choices}

            def _effective_loading_orientation_map(self):
                """Use the same per-bend placement for Coin3D and validation."""
                if self.session is None:
                    self._ergonomic_loading_status = "NO_PART"
                    return {}
                order = tuple(self.session.bend_order)
                if (self._manual_orientation_order == order
                        and set(self._manual_orientation_by_bend) == set(order)):
                    self._ergonomic_loading_status = (
                        "MANUAL_180_OVERRIDE_UNVERIFIED"
                    )
                    choices = self._manual_orientation_by_bend
                else:
                    choices = self._default_loading_orientation_map(order)
                return {
                    bend_id: self._manual_orientation_overrides.get(bend_id, reverse_axis)
                    for bend_id, reverse_axis in choices.items()
                }

            def _use_short_rear_orientation(self):
                if self.session is None:
                    return
                item = self.operations.topLevelItem(self.session.active_bend_index)
                if item is not None:
                    order = tuple(self.session.bend_order)
                    orientations = self._default_loading_orientation_map(order)
                    self._set_manual_loading_override(item, orientations[self.session.active_bend_id])

            def _default_loading_orientation_map(self, order):
                """Prefer the shorter reach towards the rear gauge."""
                model = self.backgauge_model
                stations = self._backgauge_stations() if (
                    model is not None and model.automatic and model.cad_geometry_enabled
                ) else ()
                key = (id(self.session), id(self.session.fold_state), order, stations)
                if key == self._ergonomic_loading_cache_key:
                    self._ergonomic_loading_status = self._ergonomic_loading_cache_status
                    return self._ergonomic_loading_cache
                from smbs_core.kinematics2d.loading_orientation_preview import (
                    plan_preview_loading_orientations,
                )
                try:
                    suggestions = plan_preview_loading_orientations(
                        self.session.fold_state,
                        self.session.panel_vertices_xy,
                        order,
                        deadband_mm=0.0,
                        stations_xm_mm=stations,
                    )
                    choices = {
                        item.bend_id: item.reverse_axis
                        for item in suggestions
                    }
                    unavailable = [
                        item.bend_id for item in suggestions
                        if item.status != "ERGONOMIC_ONLY_NOT_CAD_VALIDATED"
                    ]
                    self._ergonomic_loading_status = (
                        "AUTO_SHORT_REAR_UNVERIFIED"
                        if not unavailable else
                        "AUTO_SHORT_REAR_INCOMPLETE:" + ",".join(unavailable)
                    )
                except (ValueError, TypeError, KeyError, AttributeError) as exc:
                    _warn(
                        "Operator-side loading preview unavailable: "
                        + type(exc).__name__ + ":" + str(exc)
                    )
                    self._ergonomic_loading_status = (
                        "AUTO_SHORT_REAR_UNAVAILABLE:" + type(exc).__name__
                    )
                    choices = {}
                # Native placement can still evaluate canonical 0° and the
                # opposite pose when the ergonomic preference is unavailable.
                for bend_id in order:
                    choices.setdefault(bend_id, False)
                self._ergonomic_loading_cache_key = key
                self._ergonomic_loading_cache = choices
                self._ergonomic_loading_cache_status = self._ergonomic_loading_status
                return choices

            def _current_preview_machine_placement(self):
                """Return common machine-space sheet pose for 2D and 3D views."""
                if self.session is None:
                    raise WorkpiecePoseError("No part loaded")
                if self.session.active_bend_id not in self._effective_loading_orientation_map():
                    raise WorkpiecePoseError(
                        "AUTO_SHORT_REAR_ORIENTATION_UNVERIFIED:"
                        + str(self.session.active_bend_id)
                    )
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
                # Bend Only also uses the real coupled stroke. The previous
                # constant outer_radius placement rotated the sheet in place
                # while only the punch descended: visually impossible.
                if pc is None:
                    raise WorkpiecePoseError(
                        "BEND_ONLY_REQUIRES_PRESS_CYCLE"
                    )
                try:
                    if getattr(self.session.unfold_data, "dxf_manual", False) and pc.coupled_kinematics is None:
                        sheet_support_z = zone.outer_radius_mm
                    else:
                        sheet_support_z = pc.preview_workpiece_support_zm()
                except (PressCycleError, ValueError, TypeError) as exc:
                    raise WorkpiecePoseError(
                        "BEND_ONLY_COUPLED_SHEET_PLACEMENT_FAILED:" + str(exc)
                    ) from exc
                orientations = self._effective_loading_orientation_map()
                return WorkpiecePlacement3D.compute_placement(
                    fs, bid,
                    support_plane_zm=float(sheet_support_z),
                    align_bisector=True,
                    align_bend_arc_center=True,
                    reverse_axis=bool(orientations.get(bid, False)),
                    rear_panel_vertices_xy=self._rear_frame_for_preview(),
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
                if bend_id in self._manual_orientation_overrides:
                    evidence = "положение задано вручную"
                elif source.startswith("SELECTED_2D"):
                    evidence = "выбрано из 2D-поиска; 3D не проверено"
                elif source == "AUTO_SHORT_REAR_UNVERIFIED":
                    evidence = "короткая сторона к упору; КОНТАКТ НЕ ПРОВЕРЕН"
                else:
                    evidence = source
                self.loading_orientation_status.setText(
                    "Оператор ← (-S)  |  Упор → (+S)  |  {}: {}  |  {}"
                    .format(
                        bend_id,
                        ("НЕ ОПРЕДЕЛЕНА" if bend_id not in choices else
                         "РАЗВОРОТ 180°" if reversed_pose else "обычно 0°"),
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
                            self._rear_frame_for_preview()
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
                    self._rear_frame_for_preview()
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
                if activate_task_dialog(self, "cycle_config"):
                    return
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
                embed_task_dialog(self, dlg, "Параметры цикла", "cycle_config")

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
                self._manual_3d_verdicts.clear()
                self._manual_retract_verdicts.clear()
                self._clear_manual_check_feedback()
                try:
                    self.tooling_setup.select_punch(punch)
                except ToolingError as exc:
                    self.status.setText(str(exc))
                    return
                self._close_tool_assembly_report()
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
                # Camera stays fixed; use the explicit Fit control to reframe.

            def select_die(self, die: ToolProfile | None):
                self._manual_3d_verdicts.clear()
                self._manual_retract_verdicts.clear()
                self._clear_manual_check_feedback()
                try:
                    self.tooling_setup.select_die(die)
                except ToolingError as exc:
                    self.status.setText(str(exc))
                    return
                self._close_tool_assembly_report()
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
                # Camera stays fixed; use the explicit Fit control to reframe.

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
                # Camera stays fixed; use the explicit Fit control to reframe.

            def reset_tooling_position(self):
                self.tooling_setup.reset_position()
                self._update_tooling_status_label()
                self.draw()
                # Camera stays fixed; use the explicit Fit control to reframe.

            def toggle_tooling_visibility(self):
                self.tooling_setup.visible = not self.tooling_setup.visible
                self._update_tooling_status_label()
                self.draw()
                # Camera stays fixed; use the explicit Fit control to reframe.

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

            def open_full_tooling_library(self):
                """Open the single canonical library with segment inventory."""
                if activate_task_dialog(self, "full_tooling_library"):
                    return
                from smbs_core.geometry_backend.io.tooling_library import (
                    ToolingLibrary,
                )
                from freecad_smbs.ui.tooling_panel import ToolingPanel
                try:
                    library = ToolingLibrary(self.tool_library.library_root)
                    panel = ToolingPanel(library, parent=self)
                    embed_task_dialog(
                        self, panel, "Библиотека", "full_tooling_library"
                    )
                except Exception as exc:
                    QtWidgets.QMessageBox.warning(
                        self, "Библиотека оснастки", str(exc)
                    )

            def _stop_timer(self):
                self.timer.stop()
                self.playing = False
                self._last_tick_monotonic = None
                self._sync_playback_buttons()

            def _sync_playback_buttons(self):
                if self.session is None:
                    self._update_task_progress()
                    self.play_btn.setText("Play")
                    self.play_btn.setIcon(QtGui.QIcon(str(_ICON_ROOT / "control_play.svg")))
                    self.move_up_btn.setEnabled(False)
                    self.move_down_btn.setEnabled(False)
                    self.restore_order_btn.setEnabled(False)
                    self.calculate.setEnabled(False)
                    self.planar_calculate.setEnabled(False)
                    return
                st = self.session.playback_status
                self._update_task_progress()
                self.playing = st == PlaybackStatus.PLAYING
                bend_order = getattr(self.session, "bend_order", [])
                self.calculate.setEnabled(not self.playing and len(bend_order) > 1)
                self.planar_calculate.setEnabled(not self.playing and len(bend_order) > 0)
                if st == PlaybackStatus.PLAYING:
                    self.play_btn.setText("Pause")
                    icon_name = "control_pause.svg"
                elif st == PlaybackStatus.PAUSED:
                    self.play_btn.setText("Resume")
                    icon_name = "control_play.svg"
                else:
                    self.play_btn.setText("Play")
                    icon_name = "control_play.svg"
                self.play_btn.setIcon(QtGui.QIcon(str(_ICON_ROOT / icon_name)))

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

            def load_part(self, selection=None, document=None, *, prepared_session=None):
                """Load selected SheetMetal object captured before viewer focus."""
                import FreeCAD as App
                import FreeCADGui as Gui

                doc = document if document is not None else getattr(App, "ActiveDocument", None)
                if selection is None:
                    selection = Gui.Selection.getSelection() if hasattr(Gui, "Selection") else []

                # Early precondition check BEFORE touching timer, session, controls, or scene
                _desktop_debug_event(
                    "PART_LOAD_REQUEST",
                    document_name=getattr(doc, "Name", None),
                    document_path=getattr(doc, "FileName", None),
                    selection=[
                        {"name": getattr(item, "Name", None),
                         "label": getattr(item, "Label", None),
                         "type": getattr(item, "TypeId", None)}
                        for item in selection
                    ],
                )
                if prepared_session is None:
                    outcome, new_session, msg = execute_load_part(self.session, doc, selection)
                else:
                    outcome, new_session, msg = "loaded", prepared_session, "Развёртка DXF рассчитана."
                _desktop_debug_event(
                    "PART_LOAD_OUTCOME", outcome=outcome, message=msg,
                )
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
                self._machine_pose_fault = None
                self._last_3d_frame_at = -1.0
                self.tooling_setup.set_tool_assembly(None)
                self._close_tool_assembly_report()
                self.session = new_session
                self._preview_3d.clear_preview()
                self._coin_fold_mesh_key = None
                self._coin_fold_mesh_faces = ()
                _desktop_debug_event(
                    "PART_GEOMETRY_LOADED",
                    document_name=str(doc.Name),
                    document_path=str(getattr(doc, "FileName", "") or ""),
                    part_label=new_session.part_label,
                    fixed_panel=new_session.fixed_panel,
                    panel_vertices=new_session.panel_vertices_xy,
                    bends=[
                        {
                            "id": bend.bend_id,
                            "angle_deg": bend.angle_deg,
                            "fixed_panel": bend.fixed_panel,
                            "moving_panel": bend.moving_panel,
                            "axis_xyz": bend.axis_xyz,
                            "thickness_mm": bend.bend_zone.thickness_mm
                            if bend.bend_zone else None,
                            "inner_radius_mm": bend.bend_zone.inner_radius_mm
                            if bend.bend_zone else None,
                            "bend_allowance_mm": bend.bend_zone.bend_allowance_mm
                            if bend.bend_zone else None,
                        }
                        for bend in new_session.ordered_bends
                    ],
                )
                self._smbs_document_name = str(doc.Name)
                self._smbs_allow_auto_embed = False
                self.last_planar_search_report = None
                self._saved_full_sequences = []
                self._last_manual_sequence_report = None
                self._manual_3d_verdicts.clear()
                self._manual_retract_verdicts.clear()
                self._clear_manual_check_feedback()
                self._preview_only_planar_order = None
                self._partial_preview_limit = None
                self._selected_planar_gauge_order = ()
                self._selected_planar_gauge_positions = {}
                self._selected_planar_gauge_model_id = None
                self._backgauge_preview_cache.clear()
                self._manual_sequence_prefix = []
                self._manual_orientation_order = ()
                self._manual_orientation_by_bend = {}
                self._manual_orientation_overrides.clear()
                self._manual_preview_unverified = False
                self._manual_selection_enabled = False
                self._manual_preview_notice.hide()
                self.last_sequence_search_report = None
                self._ensure_press_cycle_controller()
                self._sync_tooling_to_active_bend()
                self._populate_operations()
                self._configure_for_active_bend()
                self._set_playback_controls_enabled(True)
                self._update_tooling_status_label()
                restore_message = ""
                try:
                    restore_message = self._restore_embedded_smbs_json(doc)
                except Exception as exc:
                    # Changed geometry, wrong schema or damaged JSON must
                    # never overwrite the successfully loaded live session.
                    _warn("Embedded SMBS state not restored: " + str(exc))
                    restore_message = (
                        "SMBS JSON не восстановлен: " + str(exc) +
                        " | Автозапись JSON отключена до явного сохранения,"
                        " чтобы не уничтожить старые данные."
                    )
                    self._smbs_allow_auto_embed = False
                    self.fcstd_note.setText(restore_message)
                self._sync_manual_order_input()
                try:
                    self._register_embedded_smbs_observer()
                except Exception as exc:
                    _warn("SMBS save observer not registered: " + str(exc))
                    restore_message += (
                        " | Автосохранение JSON недоступно: " + str(exc)
                    )
                self.status.setText(
                    msg + (" | " + restore_message if restore_message else "")
                )
                self.check_result.setText(msg)
                if hasattr(self, "_source_summary"):
                    self._source_summary.setText(
                        str(self.session.part_label or "SheetMetal")
                    )
                self._update_task_progress()
                # Refresh native FreeCAD command availability once a real
                # SheetMetal part is committed, not on mere selection.
                Gui.updateGui()
                self.draw()
                self._draw_flat_pattern(reset_camera=True)

            def _bound_smbs_document(self):
                """Never write SMBS data into a different active FCStd tab."""
                import FreeCAD as App

                if self.session is None or not self._smbs_document_name:
                    raise RuntimeError("Сначала загрузите SheetMetal деталь")
                document = App.getDocument(self._smbs_document_name)
                if document is None:
                    raise RuntimeError("Документ выбранной детали закрыт")
                return document

            def _register_embedded_smbs_observer(self):
                import FreeCAD as App

                if self._smbs_fcstd_observer is not None:
                    return
                observer = _SMBSFCStdSaveObserver(self)
                App.addDocumentObserver(observer)
                self._smbs_fcstd_observer = observer

            def _clear_manual_check_feedback(self):
                """Invalidate LIVE validation display; never present stale PASS."""
                if not hasattr(self, "manual_check_summary"):
                    return
                self.manual_simulate_btn.setText("Симулировать без проверки")
                self.manual_check_summary.setText(
                    "Текущий порядок ещё не проверен. Нажмите «Проверить и показать»."
                )
                self.manual_check_details.clear()
                self.manual_check_details.hide()
                self._collision_inspection = None
                self._alternate_retract_inspection = None
                self._last_assisted_lift_inspection = None
                self._last_assisted_lift_report = None
                if self.press_cycle is not None:
                    self.press_cycle.assisted_retract_preview_bends.clear()
                if hasattr(self, "manual_lift_preview_btn"):
                    self.manual_lift_preview_btn.setEnabled(False)
                if hasattr(self, "manual_release_probe_btn"):
                    self.manual_release_probe_btn.setEnabled(False)
                self._collision_display_active = False
                if hasattr(self, "manual_show_collision_btn"):
                    self.manual_show_collision_btn.setEnabled(False)
                    self.manual_show_collision_btn.hide()
                if hasattr(self, "manual_exact_occ_btn"):
                    self.manual_exact_occ_btn.setEnabled(False)
                    self.manual_exact_occ_btn.hide()
                if hasattr(self, "manual_lift_probe_btn"):
                    self.manual_lift_probe_btn.setEnabled(False)
                    self.manual_lift_probe_hint.setText(
                        "Подъём детали: сначала найдите коллизию пуансона на "
                        "RETRACT. Проверка остаётся экспериментальной."
                    )

            def _show_manual_check_feedback(self, status, summary, steps=()):
                """Show native check result in Manual, including the failed bend."""
                if not hasattr(self, "manual_check_summary"):
                    return
                self.manual_check_summary.setText(str(summary))
                lines = []
                for item in steps or ():
                    bend_id = str(item.get("bend_id", "?"))
                    index = item.get("step_number", "?")
                    verdict = str(item.get("status", "UNVERIFIED"))
                    phase_evidence = str(item.get("reason", ""))
                    tool_pair = item.get("punch_die_pair")
                    retract = item.get("retract") or {}
                    blocker = item.get("first_blocker") or {}
                    orientation = "180°" if item.get("reverse_axis") else "0°"
                    try:
                        angle_text = "{:+.2f}".format(float(item.get("angle_deg", 0.0)))
                    except (TypeError, ValueError):
                        angle_text = "?"
                    lines.append(
                        "{}. {} | {} | положение {} | угол гиба {}°".format(
                            index, bend_id, verdict, orientation, angle_text,
                        )
                    )
                    for trial in item.get("orientation_trials", ()):
                        location = " / ".join(
                            value for value in (trial.get("phase"), trial.get("tool"))
                            if value not in (None, "NONE", "UNKNOWN")
                        )
                        lines.append("  {}: {}{}".format(
                            "180°" if trial["reverse_axis"] else "0°",
                            trial["status"], f" ({location})" if location else "",
                        ))
                    lengths = item.get("tool_length_info") or {}
                    if item.get("tool_setup_reason"):
                        lines.append("  ОСНАСТКА: " + item["tool_setup_reason"])
                    if lengths.get("punch_effective_length_mm") is not None:
                        lines.append(
                            "  ДЛИНА ПУАНСОНА: источник={} мм, OCC={} мм; "
                            "режим={}".format(
                                lengths.get("punch_source_length_mm"),
                                lengths.get("punch_effective_length_mm"),
                                "ВИРТУАЛЬНАЯ" if lengths.get(
                                    "is_virtual_tool_geometry"
                                ) else "РЕАЛЬНАЯ",
                            )
                        )
                    if blocker and blocker.get("phase") not in (
                            None, "NONE", "UNKNOWN"):
                        from smbs_core.kinematics2d.native_assisted_retract import (
                            classify_punch_intersection_span,
                        )
                        length_diagnosis = classify_punch_intersection_span(
                            blocker, lengths,
                            float(self.tooling_setup.end_zone_length_mm),
                        )
                        if length_diagnosis["status"] == "CENTRAL_PUNCH_INTERFERENCE":
                            lines.append(
                                "  ЦЕНТРАЛЬНОЕ ПЕРЕСЕЧЕНИЕ: {}. "
                                "Уменьшение допустимых торцевых зон его "
                                "не устраняет.".format(
                                    ", ".join(length_diagnosis["central_hits"])
                                )
                            )
                        elif length_diagnosis["status"] == "END_ZONE_ONLY_BBOX_UNVERIFIED":
                            lines.append(
                                "  ПЕРЕСЕЧЕНИЕ У ТОРЦА (BBox): {}; "
                                "реальный вырез инструмента НЕ ПРОВЕРЕН."
                                .format(", ".join(
                                    length_diagnosis["end_zone_hits"]
                                ))
                            )
                        tool_label = {
                            "PUNCH": "пуансон",
                            "DIE": "матрица",
                            "PUNCH_AND_DIE": "пуансон и матрица",
                        }.get(blocker.get("tool"), "источник не установлен")
                        lines.append(
                            "  ПРОБЛЕМА: фаза {} | {} | Z={} мм | "
                            "пуансон={:.6f} мм³, матрица={:.6f} мм³".format(
                                blocker.get("phase", "?"), tool_label,
                                blocker.get("ram_z_mm")
                                if blocker.get("ram_z_mm") is not None else "—",
                                float(
                                    blocker.get("first_sample_punch_overlap_mm3")
                                    if blocker.get("first_sample_punch_overlap_mm3")
                                    is not None else
                                    blocker.get("punch_overlap_mm3") or 0.0
                                ),
                                float(
                                    blocker.get("first_sample_die_overlap_mm3")
                                    if blocker.get("first_sample_die_overlap_mm3")
                                    is not None else
                                    blocker.get("die_overlap_mm3") or 0.0
                                ),
                            )
                        )
                        lines.append(
                            "  МАКС. ПРОНИКНОВЕНИЕ за проверенные положения: "
                            "пуансон={:.6f} мм³, матрица={:.6f} мм³".format(
                                float(blocker.get("punch_overlap_mm3") or 0),
                                float(blocker.get("die_overlap_mm3") or 0),
                            )
                        )
                        bbox_dict = blocker.get("intersection_bbox_xyz_mm") or {}
                        for kind, coords in bbox_dict.items():
                            if isinstance(coords, dict):
                                lines.append(
                                    "  ОБЛАСТЬ {} (машинные XYZ, мм): "
                                    "X [{:.3f}, {:.3f}], "
                                    "Y [{:.3f}, {:.3f}], "
                                    "Z [{:.3f}, {:.3f}]".format(
                                        kind.upper(),
                                        coords["xmin"], coords["xmax"],
                                        coords["ymin"], coords["ymax"],
                                        coords["zmin"], coords["zmax"],
                                    )
                                )
                        bracket = blocker.get("sampled_onset_bracket") or {}
                        if bracket:
                            lines.append(
                                "  НАЧАЛО СТОЛКНОВЕНИЯ (выборочно): "
                                "{}° без блокирующей коллизии → "
                                "{}° с коллизией, {} доп. проверок. "
                                "Непроверенные углы не исключены.".format(
                                    bracket.get(
                                        "last_sampled_nonblocking_angle_deg"
                                    ),
                                    bracket.get(
                                        "first_sampled_blocking_angle_deg"
                                    ),
                                    bracket.get("refinement_samples", 0),
                                )
                            )
                        attribution = blocker.get("component_attribution") or {}
                        if attribution:
                            names = attribution.get(
                                "hits", attribution.get("positive_overlap_components", [])
                            )
                            if names:
                                lines.append(
                                    "  ПЕРЕСЕКШИЕСЯ ЭЛЕМЕНТЫ (native OCC): " +
                                    ", ".join(
                                        "{} {:.3f} мм³".format(
                                            hit.get("component_id", "?"),
                                            float(hit.get("overlap_volume_mm3", 0.0)),
                                        )
                                        for hit in names[:8]
                                    )
                                )
                            else:
                                lines.append(
                                    "  Конкретная панель не подтверждена: " +
                                    str(attribution.get("status", "UNVERIFIED"))
                                )
                        lines.append(
                            "  Код: {} | {}".format(
                                blocker.get("phase_status", "UNVERIFIED"),
                                str(blocker.get("reason", ""))[:250],
                            )
                        )
                    elif phase_evidence:
                        # Do not show the first 650 bytes of timings from
                        # APPROACH; they obscure later BENDING/BOTTOM failure.
                        if phase_evidence.startswith("native_d634_rejected:"):
                            lines.append(
                                "  D6 отклонил траекторию — этап и причина "
                                "не определены; подробности в FCStd."
                            )
                        else:
                            lines.append("  " + phase_evidence[:240])
                    if isinstance(tool_pair, dict):
                        lines.append(
                            "  Пуансон/матрица: {} | {} позиций{}".format(
                                tool_pair.get("status", "UNVERIFIED"),
                                tool_pair.get("checked_ram_positions", 0),
                                (" | " + str(tool_pair.get("reason", "")))
                                if tool_pair.get("status") != "SAMPLED_CLEAR" else "",
                            )
                        )
                        if tool_pair.get("status") == "COLLISION":
                            lines.append(
                                "  ПУАНСОН–МАТРИЦА: фазы {} | Z={} мм | {} мм³".format(
                                    ", ".join(tool_pair.get("phases", ())),
                                    tool_pair.get("ram_z_mm", "—"),
                                    tool_pair.get("overlap_volume_mm3", "—"),
                                )
                            )
                    lines.append(
                        "  RETRACT: {} | нач. Z={} мм → конеч. Z={} мм | "
                        "выборок={} | {}".format(
                            retract.get("status", "NOT_EVALUATED"),
                            (retract.get("start_z_mm") if
                             retract.get("start_z_mm") is not None else "—"),
                            (retract.get("end_z_mm") if
                             retract.get("end_z_mm") is not None else "—"),
                            retract.get("sample_count", 0),
                            str(retract.get("reason", ""))[:180],
                        )
                    )
                if not lines:
                    lines = [
                        "Ни один гиб не подтверждён. Проверьте выбранные "
                        "FCStd-инструменты, режим длины и журнал ошибок."
                    ]
                self.manual_check_details.setPlainText("\n".join(lines))
                self.manual_check_details.show()

            def _show_first_collision_in_3d(self):
                """Visualize true OCC hit bounding box in the native machine frame.

                A *separate* isolated FoldState recreates the native D6
                collision pose. Do not project machine coordinates onto the
                unverified live placement: its reverse axis may differ.
                """
                if not self._collision_inspection:
                    self.status.setText("Нет сохранённой 3D-коллизии для показа.")
                    return
                if self.session.playback_status == PlaybackStatus.PLAYING:
                    self.pause()
                self._stop_timer()
                self._collision_display_active = not self._collision_display_active
                if hasattr(self, "manual_show_collision_btn"):
                    self.manual_show_collision_btn.setText(
                        "Вернуться к обычному 3D"
                        if self._collision_display_active else
                        "Показать коллизию в 3D"
                    )
                if not self._open_coin_preview():
                    return False
                self._trace_event(
                    "MANUAL_COLLISION_3D_INSPECTION",
                    bend_id=self._collision_inspection.get("bend_id"),
                    blocker=self._collision_inspection.get("first_blocker"),
                )
                self.status.setText(
                    "Показано место столкновения в машинных XYZ. "
                    "Геометрия обзора приближённая, красная область — "
                    "границы точного 3D-пересечения OCC."
                    if self._collision_display_active else
                    "Возврат к обычному 3D-просмотру; коллизия остаётся "
                    "в диагностике Manual."
                )

            def _current_native_tooling_identity(self):
                """Capture the exact current inputs for on-demand OCC replay."""
                return {
                    "punch_id": getattr(
                        getattr(self.tooling_setup, "punch", None),
                        "tool_id", None,
                    ),
                    "die_id": getattr(
                        getattr(self.tooling_setup, "die", None),
                        "tool_id", None,
                    ),
                    "match_active_bend_length": bool(
                        self.tooling_setup.match_active_bend_length
                    ),
                    "punch_sections": tuple(
                        self.tooling_setup.punch_section_offsets_xm or ()
                    ),
                    "die_sections": tuple(
                        self.tooling_setup.die_section_offsets_xm or ()
                    ),
                    "punch_section_spans_xm": getattr(self.tooling_setup, "punch_section_spans_xm", ()),
                    "die_section_spans_xm": getattr(self.tooling_setup, "die_section_spans_xm", ()),
                    "end_zone_allow": bool(
                        self.tooling_setup.allow_end_zone_collision
                    ),
                    "end_zone_mm": float(
                        self.tooling_setup.end_zone_length_mm
                    ),
                    "initial_clearance_mm": float(
                        self.cycle_config.initial_clearance_mm
                    ),
                    "orientation_by_bend": tuple(sorted(
                        self._manual_orientation_selection(self.session.bend_order).items()
                    )),
                    "bend_order": self.session.bend_order,
                    "session_identity": id(self.session),
                }

            def _matching_manual_occ_report(self, order):
                """Return only LIVE native evidence for the identical setup.

                Embedded historic FCStd reports are deliberately excluded:
                they are NOT a fresh runtime clearance certificate.
                """
                report = self._last_manual_sequence_report
                if not isinstance(report, dict) or self.session is None:
                    return None
                physical = report.get("manual_3d_validation") or {}
                identity = report.get("native_tooling_identity")
                if not identity or tuple(physical.get("requested_bends", ())) != tuple(order):
                    return None
                if identity != self._current_native_tooling_identity():
                    return None
                return report

            def _native_collision_for_playback(self):
                """Return the first proven native blocker for current inputs."""
                if self.session is None:
                    return None
                selected = tuple(self._manual_sequence_prefix) or self.session.bend_order
                report = self._matching_manual_occ_report(selected)
                if report is None or report.get("status") != "COLLISION":
                    return None
                inspection = self._collision_inspection
                if not inspection or not inspection.get("first_blocker"):
                    return None
                # The already-captured source identity is the safety boundary.
                if inspection.get("tooling_identity") != self._current_native_tooling_identity():
                    return None
                pose = (inspection["first_blocker"].get("sampled_machine_pose")
                        or {})
                if (pose.get("bend_id") != inspection.get("bend_id")
                        or pose.get("phase") !=
                        inspection["first_blocker"].get("phase")):
                    return None
                return inspection

            def _native_collision_identity_matches(self, inspection):
                """Refuse a stale native pose after any tooling/orientation edit."""
                if self.session is None or inspection is None:
                    return False
                expected = inspection.get("tooling_identity") or {}
                current = self._current_native_tooling_identity()
                if not expected or current != expected:
                    self.status.setText(
                        "Оснастка изменилась после 3D-проверки. "
                        "Нажмите «Проверить и показать» повторно."
                    )
                    _desktop_debug_event(
                        "MANUAL_EXACT_OCC_INSPECTION_STALE_TOOLING",
                        bend_id=inspection.get("bend_id"),
                        expected=expected, current=current,
                    )
                    return False
                return True

            def _inspect_assisted_retract(self):
                """Probe primary or preserved alternative native RETRACT."""
                from smbs_core.kinematics2d.native_assisted_retract import (
                    select_assisted_retract_inspection,
                )
                inspection = select_assisted_retract_inspection(
                    self._collision_inspection,
                    self._alternate_retract_inspection,
                )
                if not self._native_collision_identity_matches(inspection):
                    self.status.setText(
                        "Оснастка или положение изменились. Повторите 3D-проверку."
                    )
                    return False
                self._stop_timer()
                self.status.setText("OCC: проверяется совместный подъём детали и пуансона...")
                QtWidgets.QApplication.processEvents()
                from smbs_core.kinematics2d.native_assisted_retract import (
                    inspect_punch_assisted_retract, compare_preview_lift_bounds,
                )
                report = inspect_punch_assisted_retract(
                    self.session, self.tooling_setup, inspection["bend_id"],
                    inspection["first_blocker"], inspection["retract"],
                )
                sampled_lift = report["status"] in (
                    "SAMPLED_LIFT_CONDITIONAL_UNVERIFIED",
                    "SAMPLED_LIFT_WITHOUT_PENETRATION_UNVERIFIED",
                )
                # A 180-degree alternative is NOT the operator's currently
                # selected orientation. The probe may inspect it but cannot
                # silently animate the original 0-degree Full Cycle as though
                # the native evidence matched.
                active_orientation = self._manual_orientation_selection(
                    self.session.bend_order
                )
                orientation_matches = (
                    not inspection.get("alternative_only", False)
                    and bool(active_orientation.get(inspection["bend_id"], False))
                    == bool(inspection.get("reverse_axis",
                        active_orientation.get(inspection["bend_id"], False)))
                )
                can_preview = sampled_lift and orientation_matches
                if sampled_lift:
                    from smbs_core.kinematics2d.coupled_kinematics_2d import (
                        CoupledAirBendingKinematics,
                    )
                    bend_id = inspection["bend_id"]
                    bend = self.session.fold_state.bends[bend_id]
                    coupled = CoupledAirBendingKinematics.from_tooling_and_fold_state(
                        self.tooling_setup, self.session.fold_state, bend_id,
                    )
                    alignment = compare_preview_lift_bounds(
                        report, coupled, bend.angle_deg,
                        self.cycle_config.initial_clearance_mm,
                    )
                    report["preview_alignment"] = alignment
                    can_preview = can_preview and alignment["matched"]
                pc = self._ensure_press_cycle_controller()
                if pc is not None:
                    pc.assisted_retract_preview_bends.discard(inspection["bend_id"])
                    if can_preview:
                        pc.assisted_retract_preview_bends.add(inspection["bend_id"])
                self.manual_lift_preview_btn.setEnabled(
                    can_preview and pc is not None
                )
                self._last_assisted_lift_report = (
                    report if sampled_lift else None
                )
                self._last_assisted_lift_inspection = (
                    inspection if sampled_lift else None
                )
                self.manual_release_probe_btn.setEnabled(sampled_lift)
                _desktop_debug_event(
                    "MANUAL_ASSISTED_RETRACT_EXPERIMENT", result=report,
                    historical_replay=bool(inspection.get("historical", False)),
                    alternative_orientation=bool(
                        inspection.get("alternative_only", False)
                    ),
                    native_reverse_axis=bool(inspection.get(
                        "reverse_axis", False
                    )),
                    preview_enabled=can_preview,
                )
                self.status.setText(
                    "Подъём: {}. Проверено поз: {}. {} Исходная коллизия "
                    "сохраняется; зацепление и вывод детали не проверены.".format(
                        report["status"], len(report["samples"]),
                        "В полной симуляции показано совместное движение."
                        if can_preview else
                        "Экспериментальная поза отдельно от текущего порядка; "
                        "автосимуляция не включена."
                        if inspection.get("alternative_only", False)
                        else "Совместное движение отключено."
                    )
                )
                self.manual_lift_probe_hint.setText(
                    "{}{} Показ условный: это предполагаемое сцепление, не "
                    "подтверждение безопасности и не замена исходного COLLISION."
                    .format("Симуляция подъёма включена." if can_preview
                            else "Симуляция подъёма отключена.",
                            " Нижняя точка 2D и OCC не совпадает."
                            if report.get("preview_alignment", {}).get("matched") is False
                            else "")
                )
                self.manual_check_details.appendPlainText(
                    "\n{} — эксперимент подъёма: {}, {} поз; {}".format(
                        inspection["bend_id"], report["status"],
                        len(report["samples"]), report["reason"]
                    )
                )
                self.manual_check_details.show()
                return report

            def _inspect_assisted_release(self):
                """Explore sampled lateral exit without promoting Manual COLLISION."""
                inspection = self._last_assisted_lift_inspection
                lift = self._last_assisted_lift_report
                if not self._native_collision_identity_matches(inspection) or not lift:
                    self._clear_manual_check_feedback()
                    return False
                self._stop_timer()
                self.status.setText("OCC: проверяется боковой вывод поднятой детали...")
                QtWidgets.QApplication.processEvents()
                from smbs_core.kinematics2d.native_assisted_retract import (
                    inspect_lateral_release,
                )
                report = inspect_lateral_release(
                    self.session, self.tooling_setup, inspection["bend_id"],
                    inspection["first_blocker"], lift,
                )
                _desktop_debug_event(
                    "MANUAL_ASSISTED_RELEASE_EXPERIMENT", result=report,
                )
                directions = report.get("candidate_directions_y") or []
                message = (
                    "Вывод OCC: {}. Проверенные направления Y: {}. "
                    "Исходный COLLISION сохраняется; непрерывный путь, "
                    "удержание детали и станок не подтверждены."
                    .format(report["status"], directions or "нет")
                )
                self.status.setText(message)
                self.manual_check_details.appendPlainText(
                    "\n{} — {}\n{}".format(
                        inspection["bend_id"], message,
                        "; ".join(
                            "Y{}: {}, {:.1f} мм, {} поз".format(
                                p["direction_y"], p["status"], p["travel_mm"],
                                len(p["samples"]),
                            ) for p in report.get("paths", ())
                        ) or report.get("reason", ""),
                    )
                )
                self.manual_check_details.show()
                return report

            def _play_assisted_retract_preview(self):
                """Replay the entire selected order with the sampled lift assumption."""
                inspection = self._collision_inspection
                if not self._native_collision_identity_matches(inspection):
                    self._clear_manual_check_feedback()
                    return
                pc = self._ensure_press_cycle_controller()
                bend_id = inspection["bend_id"]
                if pc is None or bend_id not in pc.assisted_retract_preview_bends:
                    self.status.setText("Сначала проверьте подъём в OCC.")
                    return
                self._stop_timer()
                self._machine_pose_fault = None
                try:
                    self.set_cycle_mode(CycleExecutionMode.FULL_CYCLE)
                    pc.reset()
                    self.session.active_bend_index = pc.sequence.active_index
                    self._configure_for_active_bend()
                    self._collision_display_active = False
                    self.manual_show_collision_btn.setText("Показать коллизию в 3D")
                    self.play_all()
                except (PressCycleError, SequenceError, ValueError) as exc:
                    self.status.setText("Симуляция подъёма недоступна: " + str(exc))
                    return
                _desktop_debug_event(
                    "MANUAL_ASSISTED_RETRACT_PREVIEW_STARTED",
                    bend_id=bend_id, existing_verdict="COLLISION",
                    physical_certification="UNVERIFIED",
                )
                self.status.setText(
                    "Симуляция {}: предполагаемый совместный подъём на RETRACT; "
                    "исходный COLLISION сохраняется, вывод детали не проверен."
                    .format(bend_id)
                )

            def _open_exact_collision_inspector(self):
                """Open a separate native FreeCAD scene with actual OCC solids."""
                inspection = self._collision_inspection
                if not self._native_collision_identity_matches(inspection):
                    self.status.setText(
                        "Нет актуального native 3D-пересечения для осмотра."
                    )
                    return False
                self._stop_timer()
                self.status.setText(
                    "Создание точной OCC-сцены. Исходный FCStd не изменяется..."
                )
                QtWidgets.QApplication.processEvents()
                try:
                    from smbs_core.kinematics2d.native_collision_inspector import (
                        open_exact_collision_document,
                    )
                    report = open_exact_collision_document(
                        self.session, self.tooling_setup,
                        inspection["bend_id"],
                        inspection["first_blocker"],
                    )
                except Exception as exc:
                    message = "{}: {}".format(type(exc).__name__, exc)
                    self.status.setText(
                        "Не удалось воспроизвести OCC-пересечение: "
                        + message + ". Геометрические допуски не менялись."
                    )
                    _desktop_debug_event(
                        "MANUAL_EXACT_OCC_INSPECTION_FAILED",
                        bend_id=inspection.get("bend_id"), error=message,
                    )
                    _error(
                        "Native OCC collision inspector failed: "
                        + traceback.format_exc()
                    )
                    return False
                _desktop_debug_event(
                    "MANUAL_EXACT_OCC_INSPECTION_OPENED",
                    result=report,
                )
                self.status.setText(
                    "Открыт документ {}. Красные тела — реальные OCC Common; "
                    "пуансон, матрицу, деталь и отдельные зоны можно "
                    "скрывать через дерево FreeCAD. "
                    "Непрерывная безопасность не сертифицирована."
                    .format(report["document_name"])
                )
                return True

            def _record_manual_precheck_failure(self, selected, reason):
                """Fail visibly and save the UNVERIFIED reason in FCStd as well."""
                message = "UNVERIFIED: " + str(reason)
                _desktop_debug_event(
                    "MANUAL_PRECHECK_REJECTED",
                    selected_order=list(selected), reason=str(reason),
                    session=self._debug_session_context(),
                )
                self._last_manual_sequence_report = {
                    "schema_version": "smbs.manual_2d_and_native_3d.v2",
                    "status": "UNVERIFIED",
                    "planar_status": "NOT_RUN",
                    "physical_certification": "NOT_CHECKED",
                    "manual_3d_validation": {
                        "status": "UNVERIFIED",
                        "reason": str(reason),
                        "requested_bends": list(selected),
                        "checked_bends": 0,
                        "steps": [],
                    },
                }
                self._show_manual_check_feedback("UNVERIFIED", reason)
                self.physical_check_result.setText(message)
                try:
                    note = self._persist_manual_check_to_fcstd()
                except Exception as exc:
                    note = "НЕ СОХРАНЕНО В FCStd: " + str(exc)
                    _error(
                        "Manual validation precheck could not be embedded: "
                        + traceback.format_exc()
                    )
                self.manual_check_summary.setText(message + "\n" + note)
                self.status.setText(message)

            def _persist_manual_check_to_fcstd(self):
                """Embed manual results, saving the SAME FCStd, never JSON.

                No export dialog is allowed here. For an unsaved FreeCAD
                document, stage the included attachment and rely on native
                File → Save to choose the first FCStd filename.
                """
                document = self._bound_smbs_document()
                snapshot = self._write_embedded_smbs_json(document)
                self._smbs_allow_auto_embed = True
                self._embedded_diagnostics_history = snapshot["diagnostics"]
                _desktop_debug_event(
                    "FCSTD_MANUAL_DIAGNOSTICS_STAGED",
                    document_name=getattr(document, "Name", None),
                    file_name=getattr(document, "FileName", None),
                    manual_status=(
                        self._last_manual_sequence_report or {}
                    ).get("status"),
                    stored_sections=list(snapshot.get("diagnostics", {}).keys()),
                )
                filename = str(getattr(document, "FileName", "") or "")
                if filename:
                    # FreeCAD itself writes PropertyFileIncluded into the
                    # native FCStd archive, preserving all other objects.
                    document.save()
                    _desktop_debug_event(
                        "FCSTD_MANUAL_DIAGNOSTICS_SAVED",
                        document_name=getattr(document, "Name", None),
                        path=filename,
                    )
                    return "Результат сохранён внутри FCStd: " + filename
                _desktop_debug_event(
                    "FCSTD_MANUAL_DIAGNOSTICS_PENDING_FIRST_SAVE",
                    document_name=getattr(document, "Name", None),
                )
                return (
                    "Результат вложен в открытый документ. "
                    "Для записи на диск сохраните его как FCStd через Ctrl+S."
                )

            def _write_embedded_smbs_json(self, document):
                """Stage a native included attachment without calling save()."""
                from freecad_smbs.integration.fcstd_session_store import (
                    build_snapshot, write_included_json,
                )

                if document is None or (
                    str(getattr(document, "Name", ""))
                    != str(self._smbs_document_name)
                ):
                    raise RuntimeError("Неверный документ для данных SMBS")
                weights = self._selected_operation_weights()
                snapshot = build_snapshot(
                    session=self.session,
                    document=document,
                    tooling=self.tooling_setup,
                    cycle_config=self.cycle_config,
                    execution_mode=self.execution_mode,
                    manual_prefix=tuple(self._manual_sequence_prefix),
                    orientation_by_bend=self._manual_orientation_selection(
                        self.session.bend_order
                    ),
                    manual_orientation_overrides=self._manual_orientation_overrides,
                    orientation_source=(
                        "SELECTED_2D_CANDIDATE"
                        if self._manual_orientation_order == tuple(self.session.bend_order)
                        and set(self._manual_orientation_by_bend)
                        == set(self.session.bend_order)
                        else "AUTO_SHORT_REAR"
                    ),
                    manual_report=(
                        self._last_manual_sequence_report
                        if self._last_manual_sequence_report is not None
                        else (self._embedded_diagnostics_history or {}).get("manual")
                    ),
                    search_report=self.last_sequence_search_report,
                    planar_report=self.last_planar_search_report,
                    operation_weights=weights.to_dict(),
                    retract_warning=self.retract_warning_checkbox.isChecked(),
                    saved_sequences=self._saved_full_sequences,
                )
                write_included_json(document, snapshot)
                return snapshot

            def _save_embedded_smbs_json_clicked(self):
                try:
                    document = self._bound_smbs_document()
                    self._write_embedded_smbs_json(document)
                    self._smbs_allow_auto_embed = True
                    # An unsaved document has no target archive yet. Avoid
                    # silently writing to a made-up default filename.
                    if str(getattr(document, "FileName", "")):
                        document.save()
                    else:
                        proposed = str(
                            Path.home() /
                            (str(document.Name) + ".FCStd")
                        )
                        target, _ = QtWidgets.QFileDialog.getSaveFileName(
                            self, "Сохранить деталь с JSON SMBS",
                            proposed, "FreeCAD (*.FCStd)",
                        )
                        if not target:
                            self.status.setText(
                                "JSON подготовлен в документе; "
                                "FCStd не сохранён (отмена)."
                            )
                            return
                        document.saveAs(str(target))
                    self.fcstd_note.setText(
                        "JSON вложен: SMBS_Data / SessionJSON. "
                        "Сохранён в " + str(document.FileName)
                    )
                    self.status.setText(
                        "SMBS JSON сохранён внутри FCStd; "
                        "результаты проверок — исторические."
                    )
                except Exception as exc:
                    _error("FCStd SMBS save failed: " + traceback.format_exc())
                    self.status.setText("Ошибка вложения JSON: " + str(exc))

            def _restore_embedded_smbs_json_clicked(self):
                try:
                    document = self._bound_smbs_document()
                    message = self._restore_embedded_smbs_json(document)
                    self.status.setText(message)
                except Exception as exc:
                    _error("FCStd SMBS load failed: " + traceback.format_exc())
                    self.status.setText("Ошибка чтения JSON: " + str(exc))

            def _restore_embedded_smbs_json(self, document):
                """Only apply validated geometry/order; saved passes are history."""
                from freecad_smbs.integration.fcstd_session_store import (
                    EmbeddedSessionError, read_included_json,
                    validate_restore, tool_reference,
                )

                payload = read_included_json(document)
                if payload is None:
                    self._smbs_allow_auto_embed = True
                    self._embedded_diagnostics_history = None
                    self.fcstd_note.setText(
                        "В этом документе пока нет вложенного SMBS JSON."
                    )
                    return "В FCStd нет сохранённой сессии SMBS."
                order, prefix = validate_restore(
                    payload, self.session, document
                )
                self._smbs_allow_auto_embed = True
                if self.timer.isActive() or self.playing:
                    self._stop_timer()
                # A loaded FoldState must be clean before reordering; neither
                # recorded angles nor stale collision passes are replayed.
                self.session.reset_all_bends()
                self.session.set_bend_order(order)
                self._manual_sequence_prefix = list(prefix)
                saved_orientations = payload["order"].get("orientation_by_bend", {})
                # Old snapshots could contain an arbitrary checkbox override.
                # Recompute those. Preserve only a complete selected 2D pose.
                selected_pose = (
                    payload["order"].get("orientation_source")
                    == "SELECTED_2D_CANDIDATE"
                    and set(saved_orientations) == set(order)
                )
                self._manual_orientation_order = tuple(order) if selected_pose else ()
                self._manual_orientation_by_bend = (
                    {bid: saved_orientations[bid] for bid in order}
                    if selected_pose else {}
                )
                self._manual_orientation_overrides = dict(
                    payload["order"].get("manual_orientation_overrides", {})
                )
                self._manual_preview_unverified = False
                self._manual_selection_enabled = False
                self._manual_preview_notice.hide()
                self._partial_preview_limit = None
                self._preview_only_planar_order = None
                self.last_sequence_search_report = None
                self.last_planar_search_report = None
                self._last_manual_sequence_report = None
                self._manual_3d_verdicts.clear()
                self._manual_retract_verdicts.clear()
                self._clear_manual_check_feedback()
                self._physical_check_signature = None
                self._conditional_bends_status.clear()
                self._embedded_diagnostics_history = payload["diagnostics"]
                self._saved_full_sequences = list(
                    payload["order"].get("found_sequences", [])
                )

                warnings = []
                for key, kind in (
                    ("punch", ToolKind.PUNCH), ("die", ToolKind.DIE)
                ):
                    ref = payload["tooling"].get(key)
                    if ref is None:
                        # The explicit saved selection of None is meaningful.
                        profile = None
                    else:
                        try:
                            profile = self.tool_library.load_tool(
                                ref["id"], kind, use_cache=False
                            )
                            if (tool_reference(profile) != ref
                                    and tool_reference(profile, legacy_metrics=True) != ref):
                                raise EmbeddedSessionError(
                                    "Tool library profile changed: " + ref["id"]
                                )
                        except (ToolingError, EmbeddedSessionError,
                                OSError, KeyError, TypeError, ValueError) as exc:
                            # Never leave an automatically selected *different*
                            # tool looking as if the saved tool was restored.
                            if key == "punch":
                                self.tooling_setup.select_punch(None)
                            else:
                                self.tooling_setup.select_die(None)
                            warnings.append(
                                "{} не восстановлен, выбор сброшен: {}"
                                .format(key, exc)
                            )
                            continue
                    try:
                        if key == "punch":
                            self.tooling_setup.select_punch(profile)
                        else:
                            self.tooling_setup.select_die(profile)
                    except ToolingError as exc:
                        warnings.append(
                            "{} не восстановлен: {}".format(key, exc)
                        )

                config_dict = dict(
                    payload["simulation"].get("cycle_config", {})
                )
                try:
                    config_dict["simulation_mode"] = CycleSimulationMode(
                        config_dict["simulation_mode"]
                    )
                    restored_config = CycleConfig(**config_dict)
                    self.cycle_speed_spin.blockSignals(True)
                    self.cycle_speed_spin.setValue(
                        restored_config.bend_rate_deg_s
                    )
                    self.cycle_speed_spin.blockSignals(False)
                    self.set_cycle_config(restored_config)
                    self.set_cycle_mode(
                        CycleExecutionMode(
                            payload["simulation"]["execution_mode"]
                        )
                    )
                except (KeyError, TypeError, ValueError, PressCycleError) as exc:
                    warnings.append("Параметры цикла: " + str(exc))

                opts = payload["tooling"]
                try:
                    self.tooling_setup.set_match_active_bend_length(
                        bool(opts["match_active_bend_length"]),
                        self._active_bend_length_mm(),
                    )
                    self.virtual_tool_len_checkbox.setChecked(
                        bool(opts["match_active_bend_length"])
                    )
                    self.end_zone_len_spin.setValue(
                        float(opts["end_zone_length_mm"])
                    )
                    self.allow_end_zone_checkbox.setChecked(
                        bool(opts["allow_end_zone_collision"])
                    )
                    self.retract_warning_checkbox.setChecked(
                        bool(payload["simulation"]["retract_warning"])
                    )
                except (KeyError, TypeError, ValueError, ToolingError) as exc:
                    warnings.append("Допуски оснастки: " + str(exc))

                if opts.get("segment_assembly_active"):
                    try:
                        assembly = self._calculate_common_tool_assembly(prefix or order)
                        self._verify_tool_assembly_geometry(assembly)
                        self.tooling_setup.set_tool_assembly(assembly)
                    except Exception as exc:
                        warnings.append("Секционный набор не восстановлен: " + str(exc))

                for key, value in payload["simulation"].get(
                    "operation_weights", {}
                ).items():
                    editor = self._weight_editors.get(key)
                    if editor is not None:
                        try:
                            number = float(value)
                            if math.isfinite(number) and 0.0 <= number <= 100000.0:
                                editor.setValue(number)
                        except (TypeError, ValueError):
                            warnings.append("Вес операции " + str(key))

                self._refresh_tooling_combos()
                self._ensure_press_cycle_controller()
                try:
                    self._sync_tooling_to_active_bend()
                except (ToolingError, PressCycleError, ValueError) as exc:
                    warnings.append("Выравнивание оснастки: " + str(exc))
                self._populate_operations()
                self._configure_for_active_bend()
                self._draw_flat_pattern(reset_camera=False)
                self.draw()
                self.physical_check_result.setText(
                    "ИСТОРИЯ SMBS ИЗ FCStd: проверки НЕ ПОДТВЕРЖДЕНЫ "
                    "для новой сессии. Нужна повторная проверка."
                )
                historic_manual = payload["diagnostics"].get("manual")
                if isinstance(historic_manual, dict):
                    historic_3d = historic_manual.get("manual_3d_validation")
                    historic_steps = (
                        historic_3d.get("steps", ())
                        if isinstance(historic_3d, dict) else ()
                    )
                    self._show_manual_check_feedback(
                        "HISTORICAL / UNVERIFIED",
                        "В FCStd сохранён прошлый результат {}. "
                        "Для текущего сеанса запустите нужную проверку заново."
                        .format(historic_manual.get("status", "UNVERIFIED")),
                        historic_steps,
                    )
                    from smbs_core.kinematics2d.native_assisted_retract import (
                        historic_lift_candidate,
                    )
                    historic_hit = historic_lift_candidate(
                        historic_3d,
                        order=self.session.bend_order,
                        punch_id=getattr(self.tooling_setup.punch, "tool_id", ""),
                        die_id=getattr(self.tooling_setup.die, "tool_id", ""),
                        virtual_length=bool(self.tooling_setup.match_active_bend_length),
                        end_zone_allow=bool(self.tooling_setup.allow_end_zone_collision),
                        end_zone_mm=float(self.tooling_setup.end_zone_length_mm),
                        orientation_by_bend=self._manual_orientation_selection(
                            self.session.bend_order
                        ),
                    )
                    if historic_hit is not None:
                        self._collision_inspection = {
                            "bend_id": historic_hit["bend_id"],
                            "first_blocker": historic_hit["first_blocker"],
                            "retract": historic_hit["retract"],
                            "tooling_identity": self._current_native_tooling_identity(),
                            "historical": True,
                        }
                        self.manual_lift_probe_btn.setEnabled(True)
                        self.manual_lift_probe_hint.setText(
                            "Исторический {} из FCStd: можно проверить только "
                            "альтернативный подъём. Перед расчётом OCC повторно "
                            "сверит точный объём исходной коллизии; вся "
                            "последовательность остаётся UNVERIFIED."
                            .format(historic_hit["bend_id"])
                        )
                        _desktop_debug_event(
                            "MANUAL_ASSISTED_RETRACT_HISTORIC_REPLAY_READY",
                            bend_id=historic_hit["bend_id"],
                            source="EMBEDDED_FCSTD_UNVERIFIED_UNTIL_EXACT_OCC_REPLAY",
                        )
                else:
                    self._clear_manual_check_feedback()
                note = (
                    "Восстановлены порядок и настройки из FCStd. "
                    "Проверки: ИСТОРИЧЕСКИЕ, UNVERIFIED."
                )
                if warnings:
                    note += " Ограничения: " + "; ".join(warnings)
                self.fcstd_note.setText(note)
                return note

            def _validate_export_manual_order(self, coarse_occ=False, *, show_preview=False):
                """Validate selected manual order: Stage A/B plus actual FCStd 3D.

                The backgauge stays visual-only. Simulation/slider retain
                their separate UNVERIFIED and nonblocking preview paths.
                """
                if self.session is None:
                    self.status.setText("Сначала загрузите деталь.")
                    return
                if self._task_progress_busy or not self._apply_typed_manual_order():
                    return
                prefix = tuple(self._manual_sequence_prefix)
                order = tuple(self.session.bend_order)
                if prefix and order[:len(prefix)] != prefix:
                    self.status.setText(
                        "Ручной порядок изменился: перенумеруйте линии."
                    )
                    return
                selected = prefix or order  # Move Up/Down is a valid full order.
                if not selected:
                    self.status.setText("Нет выбранных гибов.")
                    return
                if (self.timer.isActive()
                        or self.session.playback_status == PlaybackStatus.PLAYING):
                    self.status.setText(
                        "Остановите симуляцию перед проверкой ручного порядка."
                    )
                    return
                open_collision = False
                prepare_preview = False
                self._stop_timer()
                _desktop_debug_event(
                    "MANUAL_VERIFY_REQUESTED",
                    requested_bends=list(selected),
                    all_bends=list(order),
                    session=self._debug_session_context(),
                )
                # Qt event pumping keeps progress legible, but the operator
                # must not change tool identity, options or order mid-check.
                locked_controls = (
                    self.manual_validate_btn, self.manual_validate_3d_btn,
                    self.manual_simulate_btn, self.manual_order_input,
                    self.saved_sequences_btn, self._source_load_btn,
                    self.select_punch_combo, self.select_die_combo,
                    self.virtual_tool_len_checkbox,
                    self.allow_end_zone_checkbox, self.end_zone_len_spin,
                    self.import_punch_btn, self.import_die_btn,
                    self.tool_library_btn, self.move_up_btn,
                    self.move_down_btn, self.restore_order_btn,
                    self.manual_undo_btn, self.manual_clear_btn,
                    self.operations, self.auto_orientation_checkbox,
                )
                old_control_states = tuple(
                    (control, control.isEnabled())
                    for control in locked_controls
                )
                for control, _enabled in old_control_states:
                    control.setEnabled(False)
                self._set_task_busy(True)
                self._manual_3d_verdicts.clear()
                self._manual_retract_verdicts.clear()
                self._conditional_bends_status.clear()
                self._clear_manual_check_feedback()
                self._last_manual_sequence_report = None
                self.status.setText(
                    "{}: {}/{} гибов...".format(
                        "Быстрая OCC проверка (5 углов)" if coarse_occ
                        else "Полная адаптивная 3D проверка",
                        len(selected), len(order),
                    )
                )
                QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)
                try:
                    from smbs_core.kinematics2d.manual_sequence_replay import (
                        evaluate_manual_planar_order,
                    )
                    from smbs_core.kinematics2d.manual_physical_validation import (
                        check_manual_3d_sequence,
                    )
                    from smbs_core.kinematics2d.tooling_25d import ValidationStatus

                    selected_punch = self.tooling_setup.punch
                    selected_die = self.tooling_setup.die
                    if (selected_punch is None or selected_die is None
                            or selected_punch.validation_status
                               != ValidationStatus.GEOMETRY_VERIFIED
                            or selected_die.validation_status
                               != ValidationStatus.GEOMETRY_VERIFIED):
                        self._record_manual_precheck_failure(
                            selected,
                            "Выберите действительные пуансон и матрицу "
                            "с проверенными геометрическими базами.",
                        )
                        return

                    self._sync_tooling_to_active_bend()
                    if not self.tooling_setup.aligned:
                        self._record_manual_precheck_failure(
                            selected,
                            "Выбранная оснастка не выровнена "
                            "относительно текущей линии гиба.",
                        )
                        return
                    match_length = bool(
                        self.tooling_setup.match_active_bend_length
                    )
                    allow_end_zone = bool(
                        self.tooling_setup.allow_end_zone_collision
                    )
                    end_zone_mm = float(
                        self.tooling_setup.end_zone_length_mm
                    )
                    has_sections = has_native_sections(self.tooling_setup)
                    if has_sections and match_length:
                        self._record_manual_precheck_failure(
                            selected,
                            "Виртуальная длина несовместима "
                            "с реальными наборными секциями инструмента.",
                        )
                        return

                    initial_orientation = self._manual_orientation_selection(selected)
                    _desktop_debug_event(
                        "MANUAL_AUTO_ORIENTATION_SELECTED",
                        source=self._ergonomic_loading_status,
                        orientation_by_bend=initial_orientation,
                    )

                    def update_progress(label):
                        self.status.setText(label)
                        self.manual_check_summary.setText(label)
                        QtWidgets.QApplication.processEvents(
                            QtCore.QEventLoop.ExcludeUserInputEvents
                        )

                    def on_progress(step_no, count, bend_id):
                        update_progress(
                            f"3D-проверка: гиб {step_no}/{count} ({bend_id})"
                        )

                    def on_orientation_retry(bend_id, reverse_axis):
                        degrees = 180 if reverse_axis else 0
                        update_progress(
                            f"{bend_id}: проверяется второе положение ({degrees}°)"
                        )

                    from smbs_core.kinematics2d.motion_trajectory_3d import (
                        AdaptiveSamplingConfig,
                    )

                    physical = check_manual_3d_sequence(
                        self.session, self.tooling_setup, selected,
                        match_active_bend_length=match_length,
                        allow_end_zone_collision=allow_end_zone,
                        end_zone_length_mm=end_zone_mm,
                        config=AdaptiveSamplingConfig(
                            coarse_occ_screen=coarse_occ,
                            initial_clearance_mm=float(self.cycle_config.initial_clearance_mm),
                            max_phase_time_s=300.0,
                            max_evaluated_states_per_phase=64,
                        ),
                        progress_callback=on_progress,
                        reverse_axis_by_bend=initial_orientation,
                        auto_orientation=self.auto_orientation_checkbox.isChecked(),
                        locked_orientation_bends=self._manual_orientation_overrides.keys(),
                        orientation_retry_callback=on_orientation_retry,
                    )
                    reverse_selection = physical["reverse_axis_by_bend"]
                    orientation_attempts = physical["orientation_attempts"]
                    alternative_collision_reports = physical["alternative_orientation_blockers"]
                    changed_orientation_bends = [
                        step["bend_id"] for step in physical["steps"]
                        if initial_orientation[step["bend_id"]] != step["reverse_axis"]
                    ]
                    physical["simulation_orientation_before_occ"] = initial_orientation
                    physical["orientation_after_occ"] = dict(reverse_selection)
                    physical["simulation_occ_orientation_mismatch_bends"] = (
                        changed_orientation_bends
                    )
                    if physical["steps"]:
                        current_orientation = self._effective_loading_orientation_map()
                        current_orientation.update(reverse_selection)
                        self._manual_orientation_order = tuple(self.session.bend_order)
                        self._manual_orientation_by_bend = current_orientation
                        self._ensure_press_cycle_controller()
                    # 2D diagnostics must use the final same pose map as OCC.
                    if coarse_occ or not physical["steps"]:
                        planar = {
                            "status": "NOT_RUN",
                            "steps": [], "first_blocker": None,
                        }
                    else:
                        checked_order = selected[:physical["checked_bends"]]
                        try:
                            planar = evaluate_manual_planar_order(
                                self.session,
                                self.tooling_setup,
                                checked_order,
                                match_active_bend_length=match_length,
                                allow_retract_warning=self.retract_warning_checkbox.isChecked(),
                                weights=self._selected_operation_weights(),
                                backgauge_model=None,
                                reverse_axis_by_bend=reverse_selection,
                            )
                        except Exception as planar_exc:
                            # 2D diagnostics are advisory. Failure to build an
                            # approximated section must not prevent independent
                            # native 3D from checking the chosen tools.
                            planar = {
                                "schema_version": "smbs.manual_planar_replay.v1",
                                "status": "UNVERIFIED_2D",
                                "steps": [],
                                "first_blocker": {
                                    "reason": "2d_exception:{}:{}".format(
                                        type(planar_exc).__name__, planar_exc,
                                    ),
                                },
                                "plan_cost": None,
                            }
                        _desktop_debug_event(
                            "MANUAL_STAGE_AB_PLANAR_RESULT",
                            selected_order=list(checked_order),
                            entire_2d_report=planar,
                        )

                    self._collision_inspection = None
                    self._alternate_retract_inspection = None
                    self._last_assisted_lift_inspection = None
                    self._last_assisted_lift_report = None
                    self._collision_display_active = False
                    self.manual_lift_probe_btn.setEnabled(False)
                    self.manual_lift_probe_hint.setText(
                        "Идёт проверка; режим подъёма станет доступен, если "
                        "подтвердится столкновение с пуансоном на RETRACT."
                    )
                    self.manual_show_collision_btn.setText(
                        "Показать коллизию в 3D"
                    )
                    native_tooling_identity = self._current_native_tooling_identity()
                    for step in physical["steps"]:
                        if (self._collision_inspection is None
                                and step.get("status") == "COLLISION"
                                and (step.get("first_blocker") or {})
                                    .get("sampled_machine_pose")):
                            self._collision_inspection = {
                                "bend_id": step["bend_id"],
                                "first_blocker": step["first_blocker"],
                                "retract": step.get("retract") or {},
                                "tooling_identity": native_tooling_identity,
                            }
                        self._manual_3d_verdicts[step["bend_id"]] = (
                            step["status"]
                        )
                        if step["status"] == "CONDITIONAL":
                            self._conditional_bends_status[step["bend_id"]] = (
                                "CONDITIONAL: допуск концевой зоны, "
                                "требуется физическое подтверждение выреза"
                            )
                        self._manual_retract_verdicts[step["bend_id"]] = (
                            (step.get("retract") or {}).get(
                                "status", "NOT_EVALUATED"
                            )
                        )
                    # Keep a second OCC snapshot for a true 180-degree
                    # RETRACT blocker. It is diagnostic only and never
                    # replaces the current orientation or Manual COLLISION.
                    for alt_step in alternative_collision_reports:
                        blocker = alt_step.get("first_blocker") or {}
                        if not blocker.get("sampled_machine_pose"):
                            continue
                        self._alternate_retract_inspection = {
                            "bend_id": alt_step["bend_id"],
                            "reverse_axis": alt_step["reverse_axis"],
                            "first_blocker": blocker,
                            "retract": alt_step.get("retract") or {},
                            "tooling_identity": native_tooling_identity,
                            "alternative_only": True,
                        }
                        _desktop_debug_event(
                            "MANUAL_ALTERNATIVE_COLLISION_RETAINED",
                            bend_id=alt_step["bend_id"],
                            reverse_axis=alt_step["reverse_axis"],
                            phase=blocker.get("phase"),
                            tool=blocker.get("tool"),
                        )
                    if self._collision_inspection:
                        self.manual_show_collision_btn.setEnabled(True)
                        self.manual_show_collision_btn.show()
                        self.manual_exact_occ_btn.setEnabled(True)
                        self.manual_exact_occ_btn.show()
                        lift_hit = self._collision_inspection["first_blocker"]
                        from smbs_core.kinematics2d.native_assisted_retract import (
                            select_assisted_retract_inspection,
                        )
                        lift_candidate = select_assisted_retract_inspection(
                            self._collision_inspection,
                            self._alternate_retract_inspection,
                        )
                        can_lift = lift_candidate is not None
                        is_alternate = bool(
                            lift_candidate and lift_candidate.get(
                                "alternative_only", False
                            )
                        )
                        self.manual_lift_probe_btn.setEnabled(can_lift)
                        self.manual_lift_probe_hint.setText(
                            (
                                "{}: {}RETRACT заблокирован пуансоном. "
                                "Проверить предполагаемый совместный подъём "
                                "и вывод детали. Исходная коллизия останется."
                            ).format(
                                lift_candidate["bend_id"],
                                ("альтернативное положение {}°; ".format(
                                    180 if lift_candidate.get(
                                        "reverse_axis", False
                                    ) else 0
                                ) if is_alternate else ""),
                            ) if can_lift else
                            "Проверка совместного подъёма доступна при "
                            "пересечении заготовки с пуансоном на фазе RETRACT."
                        )
                        _desktop_debug_event(
                            "MANUAL_ASSISTED_RETRACT_AVAILABILITY",
                            bend_id=self._collision_inspection["bend_id"],
                            phase=lift_hit.get("phase"), tool=lift_hit.get("tool"),
                            enabled=can_lift,
                        )
                    else:
                        self.manual_show_collision_btn.setEnabled(False)
                        self.manual_show_collision_btn.hide()
                        self.manual_exact_occ_btn.setEnabled(False)
                        self.manual_exact_occ_btn.hide()
                        self.manual_lift_probe_btn.setEnabled(False)
                        self.manual_lift_probe_hint.setText(
                            "Подъём детали: подтверждённой коллизии с пуансоном "
                            "на RETRACT пока нет."
                        )
                    self._populate_operations()

                    # Keep two independent geometric evidence streams and
                    # explicitly record contradictions such as B03 at 180°:
                    # 2D P09 interference while sampled native OCC is clear.
                    from smbs_core.kinematics2d.manual_sequence_replay import (
                        crosscheck_manual_planar_native_evidence,
                    )
                    crosscheck = (
                        [] if coarse_occ else
                        crosscheck_manual_planar_native_evidence(planar, physical)
                    )
                    physical["planar_native_discrepancies"] = crosscheck
                    if crosscheck:
                        _desktop_debug_event(
                            "MANUAL_2D_NATIVE_EVIDENCE_DISAGREEMENT",
                            evidence=crosscheck,
                        )
                    physical_status = physical["status"]
                    disagreement = planar["status"] != "NOT_RUN" and (
                        planar.get("first_blocker") is not None
                        or planar.get("status") != "SAMPLED_2D_PREFIX_CLEAR_OR_TOUCH"
                    )
                    _desktop_debug_event(
                        "MANUAL_VERIFY_FINAL_RESULT",
                        verdict=physical_status,
                        entire_native_report=physical,
                        planar_status=planar.get("status"),
                        disagreement=disagreement,
                        discrepancies=crosscheck,
                    )
                    complete_steps = physical["checked_bends"]
                    result = dict(planar)
                    result["schema_version"] = "smbs.manual_2d_and_native_3d.v2"
                    result["planar_status"] = planar["status"]
                    result["manual_3d_validation"] = physical
                    result["planar_native_discrepancies"] = crosscheck
                    result["status"] = physical_status
                    # Identical input identity is required before native
                    # evidence may be shown during a later manual replay.
                    result["native_tooling_identity"] = dict(
                        native_tooling_identity
                    )
                    result["physical_certification"] = "NOT_CONTINUOUSLY_CERTIFIED"
                    result["tooling"] = {
                        "punch": selected_punch.name,
                        "punch_id": selected_punch.tool_id,
                        "die": selected_die.name,
                        "die_id": selected_die.tool_id,
                        "match_active_bend_length": match_length,
                        "allow_end_zone_collision_requested": allow_end_zone,
                        "end_zone_length_mm": end_zone_mm,
                        "end_zone_exemption_applied": any(
                            x.get("is_conditional", False)
                            for x in physical["steps"]
                        ),
                    }
                    result["model"] = {
                        "part_label": self.session.part_label,
                        "unfold_object": getattr(self.session.unfold_data, "unfold_object", ""),
                        "source_object": getattr(self.session.unfold_data, "source_object", ""),
                        "source_face": getattr(self.session.unfold_data, "source_face", ""),
                        "thickness_mm": getattr(self.session.unfold_data, "thickness_mm", None),
                        "fixed_panel": self.session.fixed_panel,
                    }
                    result["runtime"] = dict(self.runtime_git_info)
                    result["bend_geometry"] = [
                        {
                            "bend_id": b.bend_id,
                            "angle_deg": float(b.angle_deg),
                            "fixed_panel": b.fixed_panel,
                            "moving_panel": b.moving_panel,
                            "axis_xy": [
                                [float(v) for v in pt]
                                for pt in next(
                                    conn.axis_xy for conn
                                    in self.session.panel_build.connections
                                    if conn.bend_id == b.bend_id
                                )
                            ],
                        }
                        for b in self.session.ordered_bends
                    ]
                    self._last_manual_sequence_report = dict(result)
                    if physical["steps"]:
                        self.manual_simulate_btn.setText("Симулировать результат")
                    reason = physical["reason"]
                    last_step = physical["steps"][-1] if physical["steps"] else {}
                    blocker = last_step.get("first_blocker") or {}
                    last_bend = last_step.get("bend_id", "")
                    tool_setup_issue = {
                        "INSUFFICIENT_PUNCH_COVERAGE": "пуансон не перекрывает линию гиба",
                        "INSUFFICIENT_DIE_COVERAGE": "матрица не перекрывает линию гиба",
                        "TOOL_ASSEMBLY_GAP": "разрыв между секциями оснастки на линии гиба",
                        "MISSING_TOOLING_EVIDENCE": "не удалось подтвердить размеры оснастки",
                    }.get(last_step.get("tool_setup_status"))
                    if physical_status == "COLLISION":
                        pair = last_step.get("punch_die_pair") or {}
                        if pair.get("status") == "COLLISION":
                            message = f"{last_bend}: пуансон сталкивается с матрицей"
                        else:
                            tool = {
                                "PUNCH": "пуансон", "DIE": "матрица",
                                "PUNCH_AND_DIE": "пуансон и матрица",
                            }.get(blocker.get("tool"), "оснастка")
                            phase = blocker.get("phase", "")
                            message = f"{last_bend}: коллизия заготовки ({tool}, {phase})"
                    elif physical_status == "SAMPLED_3D_CLEAR_NOT_CONTINUOUS":
                        message = "В проверенных 3D-положениях коллизий нет"
                    elif physical_status == "CONDITIONAL":
                        message = "Условный результат: пересечение в торцевой зоне пуансона"
                    elif reason == "coarse_OCC_samples_only_uninspected_intervals_and_tool_pair":
                        message = "Быстрая проверка: в выбранных точках коллизий не найдено"
                    elif reason == "selected_manual_prefix_only_remaining_bends_not_checked":
                        message = "В выбранной части порядка блокирующих коллизий не найдено"
                    elif tool_setup_issue:
                        message = f"{last_bend}: {tool_setup_issue}"
                    elif last_bend:
                        message = f"{last_bend}: 3D-проверка не завершена"
                    else:
                        message = "3D-проверка недоступна. Проверьте файлы оснастки"

                    summary_lines = [
                        f"{message}. Обработано гибов: {complete_steps}/{len(order)}.",
                    ]
                    if changed_orientation_bends:
                        summary_lines.append("Положение изменилось на 180° для " +
                                             ", ".join(changed_orientation_bends) + ".")
                    both_blocked = [
                        item["bend_id"] for item in orientation_attempts
                        if item["result"] == "BOTH_ORIENTATIONS_COLLIDE"
                    ]
                    if both_blocked:
                        summary_lines.append("Коллизия в обеих ориентациях: " +
                                             ", ".join(both_blocked) + ".")
                    if crosscheck:
                        summary_lines.append(
                            "Расхождение 2D и 3D: "
                            + ", ".join(item["bend_id"] for item in crosscheck) + "."
                        )
                    elif disagreement:
                        summary_lines.append("2D-диагностика неполная.")
                    summary_lines.append(
                        "3D-проверка охватывает выборочные положения с пуансоном и матрицей. "
                        "Упор, станина, зажимы и перенос между гибами не проверяются."
                    )
                    summary = "\n".join(summary_lines)
                    self.physical_check_result.setText(summary)
                    self.status.setText(summary)
                    self._trace_event(
                        "MANUAL_3D_CHECK_COMPLETED",
                        status=physical_status,
                        mode=physical["validation_mode"],
                        checked_bends=complete_steps,
                        selected_bends=list(selected),
                        punch_id=selected_punch.tool_id,
                        die_id=selected_die.tool_id,
                        virtual_length=match_length,
                        end_zone_conditional=allow_end_zone,
                    )
                    self._show_manual_check_feedback(
                        physical_status, summary, physical["steps"],
                    )
                    try:
                        saved_note = self._persist_manual_check_to_fcstd()
                    except Exception as storage_exc:
                        saved_note = (
                            "НЕ СОХРАНЕНО В FCStd: " + str(storage_exc)
                        )
                        _error(
                            "Manual 3D report could not be embedded: "
                            + traceback.format_exc()
                        )
                    self.manual_check_summary.setText(
                        "{}\n{}".format(summary, saved_note)
                    )
                    self.status.setText(summary)
                    open_collision = (physical_status == "COLLISION"
                                      and self._collision_inspection is not None)
                    prepare_preview = show_preview and bool(physical["steps"])
                except Exception as exc:
                    _error(
                        "Manual 3D tooling validation exception: "
                        + traceback.format_exc()
                    )
                    self._record_manual_precheck_failure(
                        selected,
                        "Ошибка проверки ручного порядка: " + str(exc),
                    )
                finally:
                    self._set_task_busy(False)
                    for control, enabled in old_control_states:
                        control.setEnabled(enabled)

                if prepare_preview:
                    self.simulate_manual_order_unverified(autoplay=False, show_controls=False)
                if open_collision:
                    # Show the exact sampled native pose in the integrated 3D
                    # viewer first. The separate FreeCAD OCC document remains
                    # available explicitly from its inspection button.
                    self._show_first_collision_in_3d()

            def simulate_manual_order_unverified(self, *, autoplay=True, show_controls=True):
                """Prepare playback of the manual selection with live OCC evidence if available."""
                if self.session is None:
                    self.status.setText("Сначала загрузите развёртку детали.")
                    return
                if self._task_progress_busy or not self._apply_typed_manual_order():
                    return
                order = tuple(self.session.bend_order)
                if not order:
                    self.status.setText("Нет гибов для симуляции.")
                    return
                prefix = tuple(self._manual_sequence_prefix)
                if prefix and order[:len(prefix)] != prefix:
                    self.status.setText(
                        "Ручной порядок изменился. Проверьте выбранные линии."
                    )
                    return
                self._stop_timer()
                self._manual_selection_enabled = False
                self._manual_preview_unverified = True
                selected = prefix or order
                preview_count = len(selected)
                self._preview_only_planar_order = None
                self.last_planar_search_report = None
                self.last_sequence_search_report = None
                # A replay must NOT discard actual OCC evidence. Retain it
                # only while the explicit order and selected native tooling
                # configuration still match the check that produced it.
                matched_occ_report = self._matching_manual_occ_report(selected)
                if matched_occ_report is None:
                    self._last_manual_sequence_report = None
                    self._manual_3d_verdicts.clear()
                    self._manual_retract_verdicts.clear()
                    self._clear_manual_check_feedback()
                    self._physical_check_signature = None
                    self._highlighted_end_zone_info = None
                    self._conditional_bends_status.clear()
                else:
                    steps = matched_occ_report["manual_3d_validation"]["steps"]
                    preview_count = len(steps)
                    if steps:
                        last_status = steps[-1]["status"]
                        if (last_status in ("UNVERIFIED", "SECTION_CLEAR_3D_UNVERIFIED")
                                or (last_status == "COLLISION"
                                    and self._native_collision_for_playback() is None)):
                            preview_count -= 1
                self._partial_preview_limit = preview_count if preview_count < len(order) else None
                self._machine_pose_fault = None
                # The last inspection still exists for OCC replay, but the
                # normal animation must remain LIVE until the failing phase.
                self._collision_display_active = False
                if hasattr(self, "manual_show_collision_btn"):
                    self.manual_show_collision_btn.setText(
                        "Показать коллизию в 3D"
                    )
                if show_controls:
                    show_workspace_controls(self, tab=2)
                try:
                    # Only the physical Full Cycle uses the same APPROACH,
                    # BENDING, BOTTOM and RETRACT path as native OCC. Keep the
                    # legacy Bend Only fallback for never-validated previews.
                    self.set_cycle_mode(
                        CycleExecutionMode.FULL_CYCLE
                        if matched_occ_report is not None
                        else CycleExecutionMode.BEND_ONLY
                    )
                    pc = self._ensure_press_cycle_controller()
                    if pc is not None:
                        pc.reset()
                        self.session.active_bend_index = (
                            self.session.controller.active_index
                        )
                    else:
                        self.session.reset_all_bends()
                    if tuple(self.session.bend_order) != order:
                        raise RuntimeError("Сброс изменил выбранный порядок гибов")
                except Exception as exc:
                    self._manual_preview_unverified = False
                    self.status.setText(
                        "Не удалось подготовить ручную симуляцию: " + str(exc)
                    )
                    return
                self._configure_for_active_bend()
                self._update_tooling_status_label()
                self._manual_preview_notice.setText(
                    self.manual_check_summary.text().splitlines()[0]
                    if matched_occ_report is not None else
                    "РУЧНОЙ ПРОСМОТР — коллизии не проверены"
                )
                self._manual_preview_notice.show()
                if matched_occ_report is None:
                    self.physical_check_result.setText(
                        "UNVERIFIED: ручная кинематическая симуляция; "
                        "коллизии с пуансоном, матрицей, упором и станком не проверены."
                    )
                self._open_coin_preview()
                if not autoplay:
                    return
                # Camera stays fixed; use the explicit Fit control to reframe.
                self.play_all()
                if self.session.playback_status == PlaybackStatus.PLAYING:
                    scope = (
                        "первые {}/{} гибов".format(
                            preview_count, len(order)
                        ) if preview_count < len(order) else
                        "все {} гибов".format(len(order))
                    )
                    self.status.setText(
                        "РУЧНАЯ СИМУЛЯЦИЯ: {} · {}".format(
                            scope,
                            "сохранённые выборочные OCC-результаты"
                            if matched_occ_report is not None
                            else "БЕЗ ПРОВЕРКИ КОЛЛИЗИЙ",
                        )
                    )
                    self._trace_event(
                        "MANUAL_UNVERIFIED_PREVIEW_STARTED",
                        bend_order=list(order),
                        explicit_prefix=list(prefix),
                        preview_count=preview_count,
                        collision_validated=False,
                        physical_status="UNVERIFIED",
                    )

            def activate_manual_mode(self):
                """Focus the flat pattern, including from a native Coin document."""
                window = self.parentWidget()
                if isinstance(window, QtWidgets.QMdiSubWindow) and window.mdiArea() is not None:
                    window.mdiArea().setActiveSubWindow(window)
                if self.session is None:
                    self.status.setText("Сначала загрузите развёртку SheetMetal.")
                    return
                if not self._manual_selection_enabled:
                    self._toggle_manual_order(True)
                else:
                    self._viewer_tabs.setCurrentWidget(self._flat_view)
                    self._draw_flat_pattern(reset_camera=False)
                show_workspace_controls(self, tab=3)

            def _toggle_manual_order(self, enabled):
                if enabled and self.session is None:
                    self._manual_selection_enabled = False
                    self.status.setText("Сначала загрузите развёртку SheetMetal.")
                    return
                self._manual_selection_enabled = bool(enabled)
                if enabled:
                    self._manual_preview_unverified = False
                    self._manual_preview_notice.hide()
                    self._stop_timer()
                    self.session.reset_all_bends()
                    self._viewer_tabs.setCurrentWidget(self._flat_view)
                    self._draw_flat_pattern(reset_camera=True)
                self._update_manual_hint()

            def _update_manual_hint(self):
                """Compact status instead of an instructional text panel."""
                if self._manual_selection_enabled:
                    prefix = self._manual_sequence_prefix
                    self.status.setText(
                        "Порядок: {}/{} | {} | коллизии не проверены".format(
                            len(prefix),
                            len(self.session.bend_order) if self.session else 0,
                            " → ".join(prefix) or "порядок пуст",
                        )
                    )

            def _sync_manual_order_input(self):
                selected = tuple(self._manual_sequence_prefix) or self.session.bend_order
                self.manual_order_input.setText(" ".join(selected))
                self.manual_order_input.setModified(False)

            def _apply_typed_manual_order(self):
                if not self.manual_order_input.isModified():
                    return True
                if self.session is None:
                    self.status.setText("Сначала загрузите развёртку SheetMetal.")
                    return False
                try:
                    selected = parse_manual_bend_order(
                        self.manual_order_input.text(), self.session.canonical_order,
                    )
                except SequenceError as exc:
                    self.status.setText(str(exc))
                    return False
                if selected == tuple(self._manual_sequence_prefix):
                    self._sync_manual_order_input()
                    return True
                self._stop_timer()
                self.session.reset_all_bends()
                return self._apply_manual_order(selected)

            def _select_flat_bend(self, bend_id):
                if (self.session is None or not self._manual_selection_enabled
                        or self._task_progress_busy):
                    return
                _desktop_debug_event(
                    "MANUAL_FLAT_BEND_CLICK",
                    bend_id=str(bend_id),
                    accepted_before=list(self._manual_sequence_prefix),
                    current_order=list(self.session.bend_order),
                )
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
                prefix = (*self._manual_sequence_prefix, bend_id)
                if not self._apply_manual_order(prefix):
                    return
                _desktop_debug_event(
                    "MANUAL_FLAT_BEND_ACCEPTED",
                    bend_id=str(bend_id),
                    sequence=list(prefix),
                )

            def _apply_manual_order(self, prefix):
                if self.session is None:
                    return False
                prefix = tuple(prefix)
                remainder = tuple(
                    bend_id for bend_id in self.session.canonical_order
                    if bend_id not in prefix
                )
                try:
                    self.session.set_bend_order(prefix + remainder)
                except SequenceError as exc:
                    self.status.setText(str(exc))
                    return False
                self._manual_sequence_prefix = list(prefix)
                self._manual_3d_verdicts.clear()
                self._manual_retract_verdicts.clear()
                self._last_manual_sequence_report = None
                self._clear_manual_check_feedback()
                if self._manual_orientation_order != tuple(self.session.bend_order):
                    self._manual_orientation_order = ()
                    self._manual_orientation_by_bend = {}
                    self._ergonomic_loading_cache_key = None
                if prefix:
                    self.session.select_bend_index(len(prefix) - 1)
                self._preview_only_planar_order = None
                self._manual_preview_unverified = False
                self._manual_preview_notice.hide()
                self._partial_preview_limit = None
                self._selected_planar_gauge_order = ()
                self._selected_planar_gauge_positions = {}
                self._selected_planar_gauge_model_id = None
                self._backgauge_preview_cache.clear()
                self.last_sequence_search_report = None
                self._conditional_bends_status.clear()
                self._sync_manual_order_input()
                self._populate_operations()
                self._configure_for_active_bend()
                self._draw_flat_pattern()
                self._update_manual_hint()
                self.status.setText(
                    "РУЧНОЙ ПОРЯДОК {}/{} (НЕ ПРОВЕРЕН): {}".format(
                        len(prefix), len(self.session.bend_order),
                        " → ".join(prefix or self.session.bend_order)
                    )
                )
                return True

            def _undo_manual_bend(self):
                if self.session is None or not self.session.can_reorder():
                    return
                if self._manual_sequence_prefix:
                    self._apply_manual_order(self._manual_sequence_prefix[:-1])

            def _clear_manual_order(self):
                if self.session is None:
                    return
                self._stop_timer()
                self.session.reset_all_bends()
                self._manual_orientation_order = ()
                self._manual_orientation_by_bend = {}
                self._apply_manual_order(())

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
                meshes = getattr(self.session.unfold_data, "panel_meshes", {})
                for panel_id, vertices in self.session.panel_vertices_xy.items():
                    if panel_id in meshes:
                        path = QtGui.QPainterPath()
                        path.setFillRule(QtCore.Qt.OddEvenFill)
                        for ring in meshes[panel_id][1]:
                            path.addPolygon(QtGui.QPolygonF([
                                QtCore.QPointF(x, -y) for x, y in ring
                            ]))
                            path.closeSubpath()
                        scene.addPath(path, polygon_pen, fill).setZValue(0)
                        continue
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
                self._flat_view.update_scene_bounds(bounds)
                if reset_camera and not self._flat_view._user_camera_modified:
                    self._flat_view.reset_camera()

            def _format_operation_row(self, idx: int, b) -> tuple[str, ...]:
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
                if b.bend_id in self._manual_3d_verdicts:
                    status_str += " [3D:{}]".format(
                        self._manual_3d_verdicts[b.bend_id]
                    )
                retract_status = self._manual_retract_verdicts.get(b.bend_id)
                retract_label = {
                    "SAMPLED_CLEAR": "ПРОЙДЕН",
                    "CONDITIONAL": "УСЛОВНО",
                    "COLLISION": "КОЛЛИЗИЯ",
                    "NOT_EVALUATED": "НЕ ВЫПОЛНЕН",
                    "UNVERIFIED": "НЕ ПРОВЕРЕН",
                }.get(retract_status, "—")
                flat_mm = self.session.local_bend_dimensions_mm.get(b.bend_id)
                gauge = self._backgauge_preview_position(idx - 1)
                face_s = gauge[0].s_mm if gauge is not None and gauge[0] is not None else None
                return (
                    str(idx),
                    "{} ({} → {})".format(b.bend_id, b.fixed_panel, b.moving_panel),
                    "",  # 180° uses the native Qt check indicator.
                    "{:+.1f}°".format(b.angle_deg),
                    "{:+.1f}°".format(cur),
                    "{:.2f}".format(flat_mm) if flat_mm is not None else "—",
                    "{:.2f}".format(face_s) if face_s is not None else "—",
                    status_str,
                    retract_label,
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
                self._partial_preview_limit = None
                self._sync_manual_order_input()
                self._manual_3d_verdicts.clear()
                self._manual_retract_verdicts.clear()
                self._last_manual_sequence_report = None
                self._clear_manual_check_feedback()
                self._manual_preview_unverified = False
                self._manual_preview_notice.hide()
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
                signals_were_blocked = self.operations.blockSignals(True)
                try:
                    self.operations.clear()
                    if self.session is not None:
                        for _bend in self.session.ordered_bends:
                            self.operations.addTopLevelItem(QtWidgets.QTreeWidgetItem())
                        self._update_operations_visual_state()
                finally:
                    self.operations.blockSignals(signals_were_blocked)

            def _update_operations_visual_state(self):
                if self.session is None:
                    return
                orientations = self._effective_loading_orientation_map()
                signals_were_blocked = self.operations.blockSignals(True)
                try:
                    count = self.operations.topLevelItemCount()
                    for idx, bend in enumerate(self.session.ordered_bends[:count], 1):
                        item = self.operations.topLevelItem(idx - 1)
                        bend_id = bend.bend_id
                        item.setData(0, QtCore.Qt.UserRole, bend_id)
                        cols = self._format_operation_row(idx, bend)
                        for col, label in enumerate(cols):
                            item.setText(col, label)
                        item.setCheckState(
                            _LOADING_ORIENTATION_COLUMN,
                            QtCore.Qt.Checked if orientations[bend_id] else QtCore.Qt.Unchecked,
                        )
                        tooltip = "Галочка — разворот заготовки на 180°. Снята — 0°."
                        if bend_id in self._manual_orientation_overrides:
                            tooltip += (
                                "\nПоложение задано вручную. Правый клик возвращает автоподбор."
                            )
                        item.setToolTip(
                            _LOADING_ORIENTATION_COLUMN, tooltip,
                        )
                        item.setToolTip(
                            1, " | ".join(
                                f"{self.operations.headerItem().text(col)}: {label}"
                                for col, label in enumerate(cols) if label
                            ),
                        )
                    if 0 <= self.session.active_bend_index < count:
                        active = self.operations.topLevelItem(self.session.active_bend_index)
                        if self.operations.currentItem() is not active:
                            self.operations.setCurrentItem(active)
                finally:
                    self.operations.blockSignals(signals_were_blocked)

            def _configure_for_active_bend(self, *, angle_only=False):
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
                if angle_only:
                    row = self.operations.topLevelItem(self.session.active_bend_index)
                    if row is not None:
                        row.setText(4, f"{self.angle:+.1f}°")
                else:
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
                # Camera stays fixed; use the explicit Fit control to reframe.

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
                        pc.set_manual_preview_angle(float(signed_angle_deg))
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
                # Reset is preparation for a new playback, not an explicit
                # request to stop recording. Keep the JSONL stream open so
                # the following Play All and its FRAME events are captured.
                self._trace_event("RESET")
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
                if self._limited_preview_complete():
                    self._show_preview_limit()
                    return
                self._collision_display_active = False
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
                self.playing = True
                self._last_tick_monotonic = time.monotonic()
                self._sync_playback_buttons()
                if not self.timer.isActive():
                    self.timer.start()
                if not self._open_coin_preview():
                    return
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
                if self._open_coin_preview():
                    _log("Playback resumed at bend {}".format(self.session.active_bend_id))

            def _toggle_play_pause(self):
                """Single task-panel control for start, pause and resume."""
                if self.session is None:
                    return
                st = self.session.playback_status
                if st == PlaybackStatus.PLAYING:
                    self.pause()
                elif st == PlaybackStatus.PAUSED:
                    self.resume()
                else:
                    self.play_all()

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
                if self._limited_preview_complete():
                    self._show_preview_limit()
                    return
                native_hit = self._native_collision_for_playback()
                if native_hit is not None:
                    valid, next_index = self.session.controller.is_valid_sequential_prefix()
                    hit_index = self.session.bend_order.index(native_hit["bend_id"])
                    if valid and next_index >= hit_index:
                        if not self._collision_display_active:
                            self._show_first_collision_in_3d()
                        return
                self._collision_display_active = False
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
                # Camera stays fixed; use the explicit Fit control to reframe.

            def previous_step(self):
                if self.session is None:
                    return
                self._stop_timer()
                self._collision_display_active = False
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
                # Camera stays fixed; use the explicit Fit control to reframe.

            def _limited_preview_complete(self):
                limit = self._partial_preview_limit
                return limit is not None and all(
                    self.session.controller.is_bend_completed(bid)
                    for bid in self.session.bend_order[:limit]
                )

            def _show_preview_limit(self):
                self.status.setText(
                    "Просмотр дошёл до границы проверки: {}/{} гибов."
                    .format(self._partial_preview_limit, len(self.session.bend_order))
                )

            def advance(self):
                """One Qt tick drives exactly one sequencer/cycle advance."""
                if self.session is None:
                    self._stop_timer()
                    return
                # A partial candidate must finish retracting the final
                # punch before stopping. Otherwise the last sampled geometry
                # freezes at BOTTOM/BENDING and looks like an illegal setup.
                partial_safe_to_stop = (
                    self.execution_mode != CycleExecutionMode.FULL_CYCLE
                    or self.press_cycle is None
                    or self.press_cycle.phase in (
                        CyclePhase.STEP_COMPLETE, CyclePhase.COMPLETED,
                        CyclePhase.IDLE,
                    )
                )
                if partial_safe_to_stop and self._limited_preview_complete():
                    self.session.pause()
                    if self.press_cycle is not None:
                        self.press_cycle.pause()
                    self._stop_timer()
                    self._finish_motion_trace("PARTIAL_PREVIEW_FINISHED")
                    self._show_preview_limit()
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
                    phase_before = pc.phase.value if pc is not None else None
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
                        elif pc is not None:
                            # Exactly the same coupled stroke model as the
                            # manual slider. Do not leave the punch parked
                            # while the sheet is bending in Bend Only mode.
                            pc.preview_punch_at_current_angle()
                    angle_only = (
                        getattr(self.session.unfold_data, "dxf_manual", False)
                        and self.session.active_bend_index == previous_bend
                        and self.session.playback_status == PlaybackStatus.PLAYING
                    )
                    self._configure_for_active_bend(angle_only=angle_only)
                    if not angle_only:
                        self._update_tooling_status_label()
                    # A real OCC collision was recorded at this exact bend
                    # and phase. Do not animate through it as if it passed:
                    # stop playback and display the native sampled XYZ pose,
                    # including all previously formed panels.
                    native_hit = self._native_collision_for_playback()
                    native_pose = (
                        (native_hit["first_blocker"].get("sampled_machine_pose")
                         or {}) if native_hit is not None else {}
                    )
                    phase_now = (
                        pc.phase.value if pc is not None
                        and self.execution_mode == CycleExecutionMode.FULL_CYCLE
                        else "BEND_ONLY"
                    )
                    hit_phase = native_pose.get("phase")
                    if (native_hit is not None
                            and ((self.session.active_bend_id == native_hit["bend_id"]
                                  and phase_now == hit_phase)
                                 or (self.session.bend_order[previous_bend] == native_hit["bend_id"]
                                     and phase_before == hit_phase))):
                        if pc is not None:
                            pc.pause()
                        else:
                            self.session.pause()
                        self._stop_timer()
                        self._collision_display_active = True
                        if hasattr(self, "manual_show_collision_btn"):
                            self.manual_show_collision_btn.setText(
                                "Вернуться к обычному 3D"
                            )
                        rendered = self._open_coin_preview()
                        reason = (
                            "OCC COLLISION: {} | {} | угол {:+.2f}° | "
                            "ram Z={:.3f} мм. {}"
                        ).format(
                            native_hit["bend_id"], hit_phase,
                            float(native_pose["signed_angle_deg"]),
                            float(native_pose["ram_z_mm"]),
                            "Сохранённый OCC-кадр показан в Coin3D."
                            if rendered else self._coin_frame_status.text(),
                        )
                        self.physical_check_result.setText(reason)
                        self.status.setText(reason)
                        self._trace_event(
                            "MANUAL_PLAYBACK_HALTED_AT_NATIVE_COLLISION",
                            bend_id=native_hit["bend_id"],
                            sampled_machine_pose=native_pose,
                            phase=hit_phase,
                        )
                        self._finish_motion_trace("NATIVE_COLLISION")
                        return
                    if not self.draw():
                        return
                    if self.session.playback_status == PlaybackStatus.COMPLETED:
                        self._stop_timer()
                        self._last_3d_frame_at = -1.0
                        self._update_3d_preview(force=True)
                        self._finish_motion_trace("PLAYBACK_COMPLETED")
                        self.status.setText(
                            "AUTO PLAY завершён: {} | {} гибов{}".format(
                                self.execution_mode.value,
                                len(self.session.bend_order),
                                " | РУЧНОЙ ПРОСМОТР БЕЗ ПРОВЕРКИ КОЛЛИЗИЙ"
                                if self._manual_preview_unverified else "",
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
                            "AUTO PLAY: {} | {} | гиб {} | угол {:+.1f}° | {} мс{}".format(
                                self.execution_mode.value,
                                phase,
                                self.session.active_bend_id,
                                self.session.current_angle_deg,
                                self.timer.interval(),
                                (" | OCC:" + str(
                                    self._manual_3d_verdicts.get(
                                        self.session.active_bend_id,
                                        "UNVERIFIED",
                                    )
                                ) + " (выборочная проверка)"
                                 if self._matching_manual_occ_report(
                                     tuple(self.session.bend_order)
                                 ) is not None else
                                 " | UNVERIFIED"),
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
                if not self._preview_3d._native and self._viewer_tabs.currentWidget() is self._flat_view:
                    self._flat_view.reset_camera()
                else:
                    self._preview_3d.reset_camera(whole_scene=self.machine_preview_checkbox.isChecked())

            def diagnostics(self):
                has_part = self.session is not None
                checks = {
                    "coin3d_available": self._preview_3d._available,
                    "coin3d_mesh": not has_part or getattr(self._preview_3d, "_last_sheet_face_count", 0) > 0,
                    "flat_pattern_attached": self._flat_view.scene() is self._flat_scene,
                    "part_loaded": has_part,
                    "timer_consistency": self.timer.isActive() == self.playing,
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
                # A user may close the simulator first and press Ctrl+S in
                # FreeCAD afterwards. Stage the final live JSON before losing
                # this workspace's observer; the CAD file still requires a
                # native document save. Never overwrite a stale attachment.
                if self.session is not None and self._smbs_allow_auto_embed:
                    try:
                        self._write_embedded_smbs_json(
                            self._bound_smbs_document()
                        )
                    except Exception as exc:
                        _warn("FCStd SMBS stage on viewer close failed: " + str(exc))
                if self._smbs_fcstd_observer is not None:
                    try:
                        import FreeCAD as App
                        App.removeDocumentObserver(self._smbs_fcstd_observer)
                    except Exception as exc:
                        _warn("SMBS observer removal failed: " + str(exc))
                    self._smbs_fcstd_observer = None
                self._finish_motion_trace("WORKSPACE_CLOSED")
                self.timer.stop()
                self.playing = False
                close_workspace_controls(self)
                self._preview_3d.release()
                super().closeEvent(event)

            def _halt_unsupported_machine_pose(self, reason):
                """Pause Coin3D playback at the last rendered machine pose.

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
                _warn("Coin3D machine pose HALT: {}".format(reason))
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
                    "Симуляция остановлена. " + str(reason)
                )

            def _backgauge_stations(self):
                model = self.backgauge_model
                stations = (model.cad_station_xm_mm,)
                if model.cad_second_station_xm_mm is not None:
                    stations += (model.cad_second_station_xm_mm,)
                return stations

            def _sequenced_gauge_envelopes(self, orientations):
                """Cached projected geometry from signed preceding bends."""
                model, session = self.backgauge_model, self.session
                if (model is None or session is None or
                        not model.cad_geometry_enabled):
                    return {}
                stations = self._backgauge_stations()
                key = (id(session), tuple(session.bend_order), stations,
                       tuple(sorted(orientations.items())))
                cached = self._machine_gauge_envelope_cache
                if cached is not None and cached[0] == key:
                    return cached[1]
                from smbs_core.kinematics2d.gauge_preview import (
                    sequenced_gauge_envelopes,
                )
                values, errors = sequenced_gauge_envelopes(
                    session, stations_xm_mm=stations,
                    reverse_axis_by_bend=orientations,
                )
                self._machine_gauge_envelope_cache = (key, values)
                for bend_id, reason in errors.items():
                    _warn("Visual setup of CAD gauge for {} unavailable: {}"
                          .format(bend_id, reason))
                return values

            def _backgauge_preview_position(self, index):
                """Shared setup position of the CAD stop face for the table and viewer."""
                model, session = self.backgauge_model, self.session
                if (model is None or session is None or
                        not model.cad_geometry_enabled):
                    return None
                order = tuple(session.bend_order)
                if index < 0 or index >= len(order):
                    return None
                if not model.automatic and model.part_label and model.part_label != session.part_label:
                    return None
                orientations = self._effective_loading_orientation_map()
                key = (
                    id(session), order, id(model), index,
                    tuple(sorted(orientations.items())),
                )
                if key in self._backgauge_preview_cache:
                    return self._backgauge_preview_cache[key]
                bend_id = order[index]
                try:
                    from smbs_core.kinematics2d.gauge_preview import (
                        visual_gauge_target,
                    )
                    envelopes = (
                        self._sequenced_gauge_envelopes(orientations).get(bend_id, ())
                        if model.automatic else ()
                    )
                    result = visual_gauge_target(model, bend_id, envelopes)
                except (ValueError, TypeError, KeyError) as exc:
                    _warn("Visual gauge target unavailable for {}: {}".format(
                        bend_id, exc,
                    ))
                    result = None
                self._backgauge_preview_cache[key] = result
                return result

            def _coin_backgauge_positions(self, index, *, at_collision=False):
                """Read the existing machine setup and feed the Coin CAD fingers."""
                model, session = self.backgauge_model, self.session
                if (model is None or session is None or
                        not model.cad_geometry_enabled):
                    return (), None, ""
                start = self._backgauge_preview_position(index)
                if start is None:
                    return (), None, ""
                pos, note = start
                if pos is None:
                    return (), None, note
                s, z = pos.s_mm, pos.z_mm
                controller = self.press_cycle
                phase = getattr(getattr(controller, "phase", None), "value", "")
                if (not at_collision and phase == "TRANSFER"
                        and self.execution_mode == CycleExecutionMode.FULL_CYCLE
                        and index + 1 < len(session.bend_order)):
                    target = self._backgauge_preview_position(index + 1)
                    if target is not None and target[0] is not None:
                        t = max(0.0, min(1.0, float(controller.transfer_progress)))
                        s += (target[0].s_mm - s) * t
                        z += (target[0].z_mm - z) * t
                        note = ""
                return self._backgauge_stations(), (s, z), note

            def draw(self):
                self._update_task_progress()
                self._update_loading_orientation_label()
                return self._update_3d_preview()

        return Panel(library_root=library_root)
