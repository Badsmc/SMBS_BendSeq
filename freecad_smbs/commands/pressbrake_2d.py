Warning: truncated output (original token count: 136423)
Total output lines: 10231

"""PressBrakeSim manual SheetMetal simulation in Coin3D.

Uses the existing SheetMetal extraction and FoldState geometry:
  sheetmetal_unfold_reader.read_unfold
  -> panel_builder.build_panels
  -> panel_graph.graph_from_panel_build
  -> panel_kinematics.FoldState (with explicit BendZone)
"""
from __future__ import annotations

import hashlib
import importlib
import math
import os
import sys
import time
import traceback
import weakref
from dataclasses import dataclass, field, replace
from pathlib import Path

from freecad_smbs.ui.localization import tr

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
from smbs_core.kinematics2d.staged_bends import StagedBendSequenceController
from smbs_core.kinematics2d.staged_orientation import (
    expanded_operation_orientations, reverse_axis_for_operation,
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
from smbs_core.kinematics2d.machine_pose_contract import compute_machine_pose
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


def _qt_validity_check(qt_core):
    """Resolve the binding from the Qt runtime, including FreeCAD's PySide alias."""
    qt_major = qt_core.qVersion().partition(".")[0]
    module = {"5": "shiboken2", "6": "shiboken6"}.get(qt_major)
    if module:
        try:
            return importlib.import_module(module).isValid
        except (ImportError, AttributeError):
            pass

    def is_valid(obj):
        if obj is None:
            return False
        try:
            obj.signalsBlocked()
        except RuntimeError:
            return False
        return True

    return is_valid


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
        self.controller = StagedBendSequenceController(self.fold_state)
        if self.active_bend_index != 0:
            self.controller.select_operation(self.active_bend_index)

    @property
    def ordered_bends(self):
        # Operations may visit one physical bend twice, but FoldState uses
        # the original physical bend ID for both geometric stages.
        return tuple(
            replace(self.controller.bend_for(op.bend_id), angle_deg=op.target_angle_deg)
            for op in self.controller.operation_steps
        )

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
        QtCore, _QtGui, _QtWidgets = _qt()
        panel = self

        class LifetimeGuard(QtCore.QObject):
            def eventFilter(self, watched, event):
                if event.type() in (QtCore.QEvent.DeferredDelete, QtCore.QEvent.Hide):
                    panel._detach()
                return False

        # FreeCAD may replace Tasks without calling reject()/closed().
        # Rescue reusable controls before their disposable parent is deleted.
        self._lifetime_guard = LifetimeGuard(workspace)
        form.installEventFilter(self._lifetime_guard)

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
            workspace._task_progress_caption, workspace._task_result_icons,
        ):
            if not workspace._qt_is_valid(child):
                continue
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


def _workspace_is_live(workspace):
    """A closed Qt workspace may still exist inside QMdiArea's window list.

    Qt keeps QMdiSubWindow instances when WA_DeleteOnClose is disabled.  A
    previously closed workspace has released its Coin3D viewer and timer, so
    it must never be reused even if its Python wrapper is still accessible.
    """
    if workspace is None or getattr(workspace, "_closing", True):
        return False
    valid = getattr(workspace, "_qt_is_valid", None)
    if not callable(valid):
        return False
    try:
        return bool(valid(workspace))
    except (RuntimeError, ReferenceError):
        return False


def get_workspace_widget():
    """Return an open, valid viewer; ignore Qt MDI windows already closing."""
    import FreeCADGui as Gui
    _QtCore, _QtGui, QtWidgets = _qt()
    main = Gui.getMainWindow()
    mdi = main.findChild(QtWidgets.QMdiArea)
    if mdi is None:
        return None
    for subwindow in mdi.subWindowList():
        if subwindow.objectName() == "SMBS_PressBrake2D_Workspace":
            try:
                workspace = subwindow.widget()
            except (RuntimeError, ReferenceError):
                continue
            if _workspace_is_live(workspace):
                return workspace
    return None


def _selection_snapshot():
    """Capture active model selection before changing FreeCAD MDI focus."""
    import FreeCAD as App
    import FreeCADGui as Gui
    return getattr(App, "ActiveDocument", None), list(Gui.Selection.getSelection())


def saved_session_unfold_selection(document):
    """Find a saved SheetMetal unfold without guessing from other open files.

    Only a valid embedded SMBS snapshot may trigger automatic reloading.
    The usual geometry fingerprint is checked again during load_part().
    """
    if document is None or document.getObject("SMBS_Data") is None:
        return ()
    from freecad_smbs.integration.fcstd_session_store import read_included_json

    snapshot = read_included_json(document)
    if snapshot is None:
        return ()
    name = snapshot["geometry"]["data"].get("unfold_object")
    unfold = document.getObject(name) if isinstance(name, str) else None
    if (unfold is None or not getattr(unfold, "Name", "").endswith("_Unfold")
            or not hasattr(unfold, "baseObject")):
        return ()
    return (unfold,)


def show_workspace_controls(workspace, tab=None):
    """Display ALL SMBS control pages inside the standard FreeCAD Tasks view.

    One native Gui.Control.showDialog is active at a time. No independent dock
    or floating addon-owned dock is created. The MDI contains the Coin3D
    preview and the flat pattern used to select bends.
    """
    import FreeCADGui as Gui
    QtCore, _QtGui, QtWidgets = _qt()
    from freecad_smbs.ui.pressbrake_theme import OUTER_MARGIN, SECTION_GAP

    if not _workspace_is_live(workspace):
        _desktop_debug_event("TASKS_OPEN_SKIPPED", reason="workspace_closed")
        return None

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
            workspace._effective_operation_orientation_map()
        )
        if hasattr(workspace, "_rear_frame_for_preview"):
            cycle.loading_rear_frame_for_step = workspace._rear_frame_for_preview
    caption = workspace._task_page_titles.get(section, tr("Панель задач"))
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
    row.addWidget(workspace._task_result_icons)
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
        workspace._task_progress_caption, workspace._task_result_icons,
    ):
        child.show()

    panel = _SMBSNativeTaskPanel(workspace, form)
    valid = workspace._qt_is_valid

    def owner_destroyed(*_args):
        # Native Tasks can outlive the MDI owner. Never leave callable
        # controls pointing at its deleted timer, scenes and hidden buttons.
        panel._attached = False
        panel.workspace = None
        if valid(form):
            form.setEnabled(False)
            form.hide()

    workspace.destroyed.connect(owner_destroyed)
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
    close_btn = style_action(QtWidgets.QPushButton(tr("Закрыть раздел")))
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


def _hide_closed_workspace(workspace):
    """Hide the retained MDI shell after native document activation callbacks.

    Closing the animation document may activate another FreeCAD view while Qt
    is still dispatching Close, leaving our auxiliary MDI shell visible.
    Keep it alive for safe teardown; the next command replaces retired owners.
    """
    window = getattr(workspace, "_mdi_window", None)
    if window is not None and workspace._qt_is_valid(window):
        window.hide()
    workspace.hide()


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
            "MenuText": tr("Симуляция"),
            "ToolTip": "Открыть симуляцию Coin3D и штатную вкладку Задачи FreeCAD",
        }

    def IsActive(self):
        return True

    def Activated(self, *, document=None, selection=None):
        import FreeCADGui as Gui
        if document is None:
            doc, selected = _selection_snapshot()
        else:
            doc = document
            selected = list(selection) if selection is not None else []
        if doc is not None and not selected:
            try:
                selected = list(saved_session_unfold_selection(doc))
            except Exception as exc:
                # Corrupted / unrelated attachments must never silently bind
                # to a different CAD object.
                _warn("SMBS saved selection rejected: " + str(exc))
        QtCore, _QtGui, QtWidgets = _qt()
        main = Gui.getMainWindow()
        from freecad_smbs.ui.lifecycle_debug import install
        install(QtCore, QtWidgets)
        mdi = main.findChild(QtWidgets.QMdiArea)
        if mdi is None:
            raise RuntimeError("FreeCAD MDI area was not found")

        name = "SMBS_PressBrake2D_Workspace"
        existing = next(
            (win for win in mdi.subWindowList() if win.objectName() == name),
            None,
        )
        if existing is not None:
            try:
                workspace = existing.widget()
            except (RuntimeError, ReferenceError):
                workspace = None
            if not _workspace_is_live(workspace):
                # Closing the MDI window can leave a valid but retired widget
                # behind (WA_DeleteOnClose=False). Never reuse its stopped
                # timer, detached Tasks or released Coin scene.
                _desktop_debug_event(
                    "WORKSPACE_STALE_REPLACED",
                    closing=getattr(workspace, "_closing", None),
                )
                mdi.removeSubWindow(existing)
                existing.hide()
                existing.deleteLater()
                existing = None
        if existing is not None:
            # A hidden-but-live MDI workspace can be brought back without
            # discarding the operator's unsaved order and per-operation poses.
            if not existing.isVisible():
                existing.show()
            mdi.setActiveSubWindow(existing)
            if doc is not None and selected and not workspace._task_progress_busy:
                try:
                    unfold = resolve_selected_unfold(doc, selected)
                except (SelectionWarning, AttributeError, TypeError, ValueError):
                    pass
                else:
                    source = (doc.Name, unfold)
                    if getattr(workspace, "_simulation_source", None) != source:
                        workspace.load_part(selection=selected, document=doc)
            show_workspace_controls(workspace, tab=1)
            viewer = workspace._preview_3d
            if viewer._native and getattr(viewer, "_native_doc", None):
                workspace._update_3d_preview(force=True)
            else:
                workspace._open_coin_preview()
            return

        widget = _PreviewPanel()
        class WorkspaceSubWindow(QtWidgets.QMdiSubWindow):
            def closeEvent(self, event):
                # QMdiSubWindow dispatches Close to its child first. Native
                # FreeCAD activation during that callback can re-show the shell.
                # Finalise visibility AFTER the MDI base handler returns.
                super().closeEvent(event)
                if widget._closing:
                    self.hide()
                    event.accept()
                    if not getattr(self, "_retirement_scheduled", False):
                        self._retirement_scheduled = True
                        # Hidden subwindows remain tabs in Qt's tabbed MDI.
                        # Remove the shell after the Close dispatch unwinds;
                        # deleting it inside the native handler is unsafe.
                        QtCore.QTimer.singleShot(0, self._retire)
                    _desktop_debug_event("WORKSPACE_MDI_CLOSE_COMPLETED",
                                         visible=self.isVisible())

            def _retire(self):
                mdi.removeSubWindow(self)
                self.hide()
                self.deleteLater()
                _desktop_debug_event("WORKSPACE_MDI_REMOVED")

        window = WorkspaceSubWindow()
        window.setObjectName(name)
        window.setWindowTitle("PressBrakeSim — Coin3D")
        # This is an auxiliary MDI view, not a FreeCAD document view.
        # Native view activation must not dispose its live simulation owner.
        window.setAttribute(QtCore.Qt.WA_DeleteOnClose, False)
        class WorkspaceLifetimeGuard(QtCore.QObject):
            def eventFilter(self, watched, event):
                if event.type() == QtCore.QEvent.DeferredDelete and not widget._closing:
                    _desktop_debug_event(
                        "WORKSPACE_UNEXPECTED_DELETE_BLOCKED",
                        object_name=watched.objectName(),
                        stack=traceback.format_stack(limit=24),
                    )
                    return True
                return False
        widget._mdi_lifetime_guard = WorkspaceLifetimeGuard(main)
        window.installEventFilter(widget._mdi_lifetime_guard)
        widget.installEventFilter(widget._mdi_lifetime_guard)
        widget._mdi_window = window
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
        show_workspace_controls(widget, tab=1)
        widget._open_coin_preview()


class LoadPartCommand:
    def GetResources(self):
        return {
            "Pixmap": str(_ICON_ROOT / "gui_part.svg"),
            "MenuText": tr("Деталь"),
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
            PressBrake2DCommand().Activated(document=doc, selection=selection)
            workspace = get_workspace_widget()
        else:
            workspace.load_part(selection=selection, document=doc)
        if workspace is None or workspace.session is None:
            App.Console.PrintWarning("SMBS: деталь не загружена.\\n")
            return
        show_workspace_controls(workspace, tab=1)
        Gui.updateGui()


class ToolingTabCommand:
    def GetResources(self):
        return {
            "Pixmap": str(_ICON_ROOT / "gui_tooling.svg"),
            "MenuText": tr("Оснастка"),
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
            workspace.open_tooling_settings_dialog()


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


class _WorkspaceActionCommand:
    """Expose an existing simulation action in the native FreeCAD toolbar."""

    label = ""
    method = ""
    icon = "control_validate.svg"

    def GetResources(self):
        return {"Pixmap": str(_ICON_ROOT / self.icon), "MenuText": tr(self.label),
                "ToolTip": tr(self.label)}

    def IsActive(self):
        return _has_loaded_part()

    def Activated(self):
        workspace = get_workspace_widget()
        if workspace is None or workspace.session is None:
            return
        getattr(workspace, self.method)()


class FastSearchCommand(_WorkspaceActionCommand):
    label = "FAST · найти последовательности (до 30 с)"
    method = "_run_fast_sequence_search"
    icon = "gui_simulation.svg"


class BendInformationCommand(_WorkspaceActionCommand):
    label = "Информация"
    method = "_show_bend_information"
    icon = "gui_part.svg"


class MeshCheckCommand(_WorkspaceActionCommand):
    label = "Mesh 3D (60 с)"
    method = "_run_mesh_sequence_check"


class OccCheckCommand(_WorkspaceActionCommand):
    label = "Быстрая проверка OCC (5 углов)"
    method = "_run_occ_check"


class SettingsCommand:
    """Open all simulation, machine and diagnostic settings."""

    def GetResources(self):
        return {
            "Pixmap": str(_ICON_ROOT / "gui_settings.svg"),
            "MenuText": tr("Настройки"),
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
                self._saved_full_sequences = []
                self._preview_only_planar_order = None
                self._partial_preview_limit = None
                self._manual_sequence_prefix: list[str] = []
                self._manual_orientation_order = ()
                self._manual_orientation_by_bend = {}
                self._manual_orientation_overrides: dict[str, bool] = {}
                # The staging timeline has separate 0°/180° choices for B03.1,
                # B03.2, etc. Never key these by the shared physical bend ID.
                self._manual_operation_orientation_overrides: dict[str, bool] = {}
                # Orientations proposed by FAST are shown in simulation,
                # but must NOT become immutable user pins on the next search.
                self._fast_advisory_orientation_keys: set[str] = set()
                self._last_manual_sequence_report = None
                self._mesh_collision_hits = ()
                self._mesh_collision_index = None
                self._mesh_collision_identity = None
                self._collision_inspection = None
                self._alternate_retract_inspection = None
                self._last_assisted_lift_inspection = None
                self._last_assisted_lift_report = None
                self._collision_display_active = False
                self._manual_3d_verdicts = {}
                self._manual_retract_verdicts = {}
                self._backgauge_settings = QtCore.QSettings("SMBS", "BendSeq")
                self.backgauge_model = None
                # FCStd-specific clamp settings override the shared tooling
                # library only for this loaded document; never mutate it on open.
                self._project_clamp_config = None
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
                self._staged_dnd_pending = False
                self._staged_dnd_in_progress = False
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
                    qt_version=QtCore.qVersion(),
                    qt_binding=QtCore.__name__,
                    cycle_config={
                        "initial_clearance_mm":
                            self.cycle_config.initial_clearance_mm,
                        "mode": self.execution_mode.value,
                    },
                )
                self.angle = 0.0
                self.playing = False
                self._last_tick_monotonic: float | None = None
                self._closing = False
                self._qt_is_valid = _qt_validity_check(QtCore)
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
                self.add_part_btn = _playback_button(QtCore, QtWidgets, "+", LoadPartCommand().Activated, True)
                self.add_part_btn.setObjectName("SMBS_AddPart")
                self.add_part_btn.setIcon(QtGui.QIcon(str(_ICON_ROOT / "control_add.svg")))
                self.add_part_btn.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
                buttons = (
                    self.add_part_btn, self.reset_btn, self.prev_step_btn, self.play_btn,
                    self.next_step_btn, self.fit_btn,
                )
                captions = ("Добавить выбранную развёртку SheetMetal", "Reset", "|<<", "Play / Pause", ">>|", "Fit")
                sizes = (27, 48, 34, 64, 34, 36)
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
                self._playback_button_grid = row

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

                class StagedOperationsTree(QtWidgets.QTreeWidget):
                    def dropEvent(tree, event):
                        owner = getattr(tree, "stage_drop_owner", None)
                        if (owner is None or owner._closing
                                or owner.session is None
                                or owner._staged_dnd_pending
                                or owner._staged_dnd_in_progress
                                or owner.timer.isActive()
                                or owner.playing
                                or not owner.session.can_reorder()):
                            event.ignore()
                            return
                        # Ignore selection/redraw events while Qt owns the
                        # native move. Rebuilding QTreeWidget/QToolButton
                        # children inside dropEvent can invalidate C++ items.
                        owner._staged_dnd_in_progress = True
                        _desktop_debug_event(
                            "STAGED_DROP_BEGIN",
                            current_order=[
                                step.operation_id
                                for step in owner.session.controller.operation_steps
                            ],
                        )
                        try:
                            super().dropEvent(event)
                            accepted = event.isAccepted()
                        finally:
                            owner._staged_dnd_in_progress = False
                        if accepted:
                            owner._on_staged_operations_dropped()

                self.operations = StagedOperationsTree()
                self.operations.stage_drop_owner = self
                self.operations.setHeaderLabels([
                    "Step", "Bend", "180°", "Target", "Current", "До гиба, мм",
                    "Упор S, мм", "Status", "RETRACT", "Предгиб",
                ])
                self.operations.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
                self.operations.setDefaultDropAction(QtCore.Qt.MoveAction)
                self.operations.setDropIndicatorShown(True)
                self.operations.setDragEnabled(False)
                # Process a successful drop exactly once, *after* Qt exits
                # its native dropEvent. rowsMoved can arrive mid-drop when
                # embedded row widgets are still owned by Qt.
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
                self.operations.setToolTip(
                    self.operations.toolTip() + "\nПеретащите строки после добавления предгиба. "
                    "Предгиб всегда должен стоять раньше догиба."
                )
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
                self._task_status_chip = QtWidgets.QLabel(tr("Готово"), self)
                self._task_status_chip.setObjectName("SMBS_StatusChip")
                self._task_status_chip.setProperty("state", "ready")
                self._task_progress_caption = QtWidgets.QLabel("0%", self)
                self._task_progress_caption.setObjectName("SMBS_SectionCaption")
                self._task_result_icons = QtWidgets.QWidget(self)
                icons_layout = QtWidgets.QHBoxLayout(self._task_result_icons)
                icons_layout.setContentsMargins(0, 0, 0, 0)
                icons_layout.setSpacing(3)
                self._task_result_buttons = {}
                self._task_results = {}
                self._mesh_task_result_identity = None
                for key in ("FAST", "Mesh"):
                    button = QtWidgets.QToolButton(self._task_result_icons)
                    button.setObjectName("SMBS_Result" + key)
                    button.setAutoRaise(True)
                    button.setIconSize(QtCore.QSize(16, 16))
                    button.clicked.connect(
                        lambda _checked=False, name=key: self._show_task_result(name)
                    )
                    icons_layout.addWidget(button)
                    self._task_result_buttons[key] = button
                    self._set_task_result(key, "idle", tr("Проверка ещё не запускалась.")
                                          if key == "Mesh" else tr("Поиск ещё не запускался."))
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
                self._create_flat_scene()
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
                # The native Tasks header already says "Симуляция": no duplicate
                # section title or large diagnostic card above playback.
                playback = QtWidgets.QWidget(simulation)
                playback.setObjectName("SMBS_FixedPlayback")
                playback_layout = QtWidgets.QVBoxLayout(playback)
                playback_layout.setContentsMargins(0, 0, 0, 0)
                sim_layout.addWidget(playback)
                body = QtWidgets.QWidget()
                sim_body = QtWidgets.QVBoxLayout(body)
                sim_body.setContentsMargins(0, 0, 0, 0)
                scroll = QtWidgets.QScrollArea(simulation)
                scroll.setObjectName("SMBS_SimulationContentScroll")
                scroll.setWidgetResizable(True)
                scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
                scroll.setWidget(body)
                sim_layout.addWidget(scroll, 1)
                for btn in (
                    self.reset_btn, self.prev_step_btn, self.next_step_btn,
                    self.fit_btn,
                ):
                    style_action(btn, compact=True)
                style_action(self.play_btn, primary=True)
                insert(playback_layout, entries[5])  # Reset / Prev / Play-Pause / Next / Fit
                self.slider.setToolTip(
                    "Ручной просмотр активной операции: двигайте ползунок "
                    "вперёд или назад от начального до целевого угла."
                )
                insert(playback_layout, entries[6])  # angle slider
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
                self._page_simulation = scroll_task_page(
                    simulation, "SMBS_SimulationScroll"
                )
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
                select_lines = QtWidgets.QPushButton(tr("Выбрать линии гиба"))
                order_body.addWidget(style_action(select_lines, compact=True))
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
                    QtWidgets.QPushButton(tr("Отменить линию"))
                )
                self.manual_undo_btn.clicked.connect(self._undo_manual_bend)
                self.manual_clear_btn = style_action(
                    QtWidgets.QPushButton(tr("Очистить"))
                )
                self.manual_clear_btn.clicked.connect(self._clear_manual_order)
                undo_row.addWidget(self.manual_undo_btn)
                undo_row.addWidget(self.manual_clear_btn)
                order_body.addLayout(undo_row)
                self.saved_sequences_btn = style_action(
                    QtWidgets.QPushButton(tr("Сохранённые варианты")), compact=True
                )
                self.saved_sequences_btn.setToolTip(
                    "Загрузить ранее сохранённый ручной порядок из JSON "
                    "текущего FCStd. При необходимости проверить его заново."
                )
                self.saved_sequences_btn.clicked.connect(
                    self._select_saved_sequence
                )
                order_body.addWidget(self.saved_sequences_btn)
                self.auto_orientation_checkbox = QtWidgets.QCheckBox(
                    "Подбирать положение заготовки (0° / 180°)"
                )
                self.auto_orientation_checkbox.setChecked(True)
                self.auto_orientation_checkbox.setToolTip(
                    "Для гибов с автоматически заданным положением OCC может "
                    "проверить второе положение. Галочка в колонке «180°» "
                    "закрепляет выбранное положение конкретного гиба."
                )
                self.auto_orientation_checkbox.setParent(self)
                self.auto_orientation_checkbox.hide()
                self.operations.setMinimumHeight(220)
                self.operations.setMaximumHeight(16777215)
                self.operations.setSizePolicy(
                    QtWidgets.QSizePolicy.Expanding,
                    QtWidgets.QSizePolicy.Expanding,
                )
                insert(order_body, entries[8], 1)  # actual sequence table
                self.manual_simulate_btn = style_action(
                    QtWidgets.QPushButton(tr("Симулировать без проверки")), compact=True
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
                    QtWidgets.QPushButton(tr("Быстрая проверка OCC (5 углов)")), compact=True
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
                self.manual_validate_btn.setParent(self)
                self.manual_validate_btn.hide()
                self.manual_validate_3d_btn = style_action(
                    QtWidgets.QPushButton(tr("Проверить и показать")), primary=True
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
                self.manual_validate_3d_btn.setParent(self)
                self.manual_validate_3d_btn.hide()
                self.manual_lift_probe_btn.setParent(self)
                self.manual_lift_probe_btn.hide()
                self.manual_lift_preview_btn.setParent(self)
                self.manual_lift_preview_btn.hide()
                self.manual_release_probe_btn.setParent(self)
                self.manual_release_probe_btn.hide()
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
                    "Выберите гибы и нажмите «Быстрая проверка OCC»."
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
                    QtWidgets.QPushButton(tr("Показать коллизию в 3D"))
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
                sim_body.addWidget(order_card, 1)
                # Keep the live sequence directly beneath the fixed controls.
                playback_layout.addWidget(self.operations, 1)
                self.fast_search_btn = style_action(
                    QtWidgets.QPushButton(tr("FAST · найти последовательности (до 30 с)")),
                    compact=True,
                )
                self.fast_search_btn.setObjectName("SMBS_FastSequenceSearch")
                self.fast_search_btn.setToolTip(
                    "Экспериментальный поиск BFM-lite + Beam Search без OCC. "
                    "Дополнительная проверка грубой ОШИБКИ УСТАНОВКИ: "
                    "основная панель проходит через центральную ось Yₘ=0. "
                    "Такой вариант получает большой ШТРАФ, но не исключается. "
                    "Местные пересечения полок ДОПУСТИМЫ. "
                    "Первые пять вариантов — FAST с лимитом 30 с. "
                    "В поиске используется упрощённый 2D-контур "
                    "выбранного пуансона и матрицы: грубые пересечения "
                    "с панелями добавляют штраф, касания и зона гиба "
                    "не штрафуются. Полной 3D-гарантии нет. "
                    "Затем автоматически выполняются 2D-проверки и новые "
                    "поиски от КАЖДОЙ внешней стороны развёртки "
                    "(число сторон определяется настоящим внешним CAD-контуром). "
                    "Найденные 2D-пересечения возвращаются как штрафы "
                    "только для конкретных состояний и ориентаций. "
                    "Остальные проверки и время добавляются отдельно. "
                    "Полные коллизии, реальная установка и траектории 3D "
                    "НЕ подтверждены: все результаты UNVERIFIED."
                )
                self.fast_search_btn.clicked.connect(self._run_fast_sequence_search)
                self.fast_search_btn.setParent(self)
                self.fast_search_btn.hide()
                self.fast_save_template_btn = style_action(
                    QtWidgets.QPushButton("Сохранить проверенную схему гибки"),
                    compact=True,
                )
                self.fast_save_template_btn.setObjectName(
                    "SMBS_SaveOperatorBendTemplate"
                )
                self.fast_save_template_btn.setToolTip(
                    "Сохранить известный оператору правильный порядок и "
                    "развороты как геометрический шаблон для будущих "
                    "развёрток. Требует ручного подтверждения; НЕ "
                    "создаёт сертификат проверки столкновений."
                )
                self.fast_save_template_btn.clicked.connect(
                    self._save_operator_bend_template
                )
                playback_layout.addWidget(self.fast_save_template_btn)
                order_card.hide()
                for widget in (self.manual_order_input, self.manual_simulate_btn,
                               self.saved_sequences_btn, self.manual_check_summary):
                    widget.hide()
                select_lines.setText("☷")
                select_lines.setToolTip("Выбрать линии гиба / изменить порядок")
                select_lines.setFixedSize(27, 27)
                fit_layout = self._playback_button_grid
                index = fit_layout.indexOf(self.fit_btn)
                row, column, _rs, _cs = fit_layout.getItemPosition(index)
                fit_layout.addWidget(select_lines, row, column + 1)
                edit_controls = QtWidgets.QWidget()
                edit_layout = QtWidgets.QHBoxLayout(edit_controls)
                edit_layout.setContentsMargins(0, 0, 0, 0)
                edit_layout.setSpacing(3)
                for button in (self.move_up_btn, self.move_down_btn,
                               self.restore_order_btn, self.manual_undo_btn):
                    edit_layout.addWidget(button)
                cancel_edit = QtWidgets.QPushButton("Cancel")
                self.manual_undo_btn.setText("Back")
                edit_layout.addWidget(cancel_edit)
                for button, text, hint in (
                    (self.move_up_btn, "↑", "Move Up — переместить вверх"),
                    (self.move_down_btn, "↓", "Move Down — переместить вниз"),
                    (self.restore_order_btn, "↺", "Restore — восстановить порядок"),
                    (self.manual_undo_btn, "←", "Back — отменить последнюю линию"),
                    (cancel_edit, "✕", "Cancel — отменить редактирование"),
                ):
                    button.setText(text)
                    button.setToolTip(hint)
                    button.setMinimumWidth(24)
                    button.setMaximumWidth(16777215)
                    button.setFixedHeight(27)
                    button.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                         QtWidgets.QSizePolicy.Fixed)
                    edit_layout.setStretch(edit_layout.indexOf(button), 1)
                playback_layout.insertWidget(0, edit_controls)
                edit_controls.hide()
                playback_buttons = (self.reset_btn, self.prev_step_btn,
                                    self.play_btn, self.next_step_btn)

                def finish_sequence(*, cancel):
                    if self.session is not None and cancel:
                        self._apply_manual_order(self._sequence_before_edit)
                        self._manual_sequence_prefix = list(self._prefix_before_edit)
                        self._sync_manual_order_input()
                    self._toggle_manual_order(False)
                    edit_controls.hide()
                    select_lines.setText("☷")
                    select_lines.setToolTip("Выбрать линии гиба / изменить порядок")
                    for button in playback_buttons:
                        button.show()
                    if self.session is not None:
                        self._configure_for_active_bend()
                        self._draw_flat_pattern(reset_camera=False)

                def toggle_sequence():
                    if self.session is None:
                        self.activate_manual_mode()
                    elif not edit_controls.isHidden():
                        finish_sequence(cancel=False)
                    else:
                        self._sequence_before_edit = list(self.session.bend_order)
                        self._prefix_before_edit = list(self._manual_sequence_prefix)
                        self.activate_manual_mode()
                        edit_controls.show()
                        select_lines.setText("✓")
                        select_lines.setToolTip("Применить порядок гибов")
                        for button in playback_buttons:
                            button.hide()
                        self._update_operations_visual_state()

                select_lines.clicked.connect(toggle_sequence)
                cancel_edit.clicked.connect(lambda: finish_sequence(cancel=True))
                bottom_actions = QtWidgets.QHBoxLayout()
                self.manual_mesh_check_btn = style_action(
                    QtWidgets.QPushButton(tr("Mesh 3D (60 с)")), compact=True,
                )
                self.manual_mesh_check_btn.setToolTip(
                    "Выборочная проверка детали против выбранного пуансона и матрицы."
                )
                self.manual_mesh_check_btn.clicked.connect(self._run_mesh_sequence_check)
                self.manual_mesh_check_btn.setParent(self)
                self.manual_mesh_check_btn.hide()
                self.mesh_show_collision_btn = style_action(
                    QtWidgets.QPushButton(tr("Коллизии Mesh")), compact=True,
                )
                self.mesh_show_collision_btn.setToolTip(
                    "Следующая подтверждённая коллизия. После последней — обычный вид."
                )
                self.mesh_show_collision_btn.clicked.connect(self._show_next_mesh_collision)
                self.mesh_show_collision_btn.hide()
                bottom_actions.addWidget(self.mesh_show_collision_btn)
                sim_layout.addLayout(bottom_actions)

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
                self.import_tooling_btn = style_action(QtWidgets.QPushButton(tr("Импортировать…")), compact=True)
                self.import_tooling_btn.clicked.connect(self.open_full_tooling_library)
                import_row = QtWidgets.QHBoxLayout()
                import_row.addStretch(1)
                import_row.addWidget(self.import_tooling_btn)
                tooling_body.addLayout(import_row)
                self.tool_library_btn.hide()
                td_layout.addWidget(tooling_card)
                td_layout.setAlignment(QtCore.Qt.AlignTop)
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

                from smbs_core.kinematics2d.operation_cost import OperationWeights
                preferences = QtCore.QSettings("SMBS", "BendSeq")
                self._operation_weights = {
                    key: float(preferences.value("operation_weights/" + key, default, type=float))
                    for key, default in OperationWeights().to_dict().items()
                }

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
                for button in (self.physical_check_btn, self.full_physical_check_btn, self.check_btn):
                    button.setParent(self)
                    button.hide()
                diagnostics_body.addWidget(self.physical_check_result)
                insert(diagnostics_body, entries[10])
                advanced_layout.addWidget(diagnostics_card)

                from smbs_core.kinematics2d.developer_debug_log import set_debug_log_enabled
                debug_log = QtWidgets.QCheckBox(tr("Записывать журнал SMBS и сбои FreeCAD"))
                debug_log.setToolTip(str(_debug_desktop_directory() / "SMBS_debug_log.json"))
                debug_log.setChecked(preferences.value("debug_log/enabled", False, type=bool))

                def toggle_debug_log(enabled):
                    preferences.setValue("debug_log/enabled", enabled)
                    if not set_debug_log_enabled(enabled):
                        self.status.setText(tr("Не удалось создать журнал на рабочем столе."))

                debug_log.toggled.connect(toggle_debug_log)
                settings_layout.addWidget(debug_log)
                self.trace_checkbox = QtWidgets.QCheckBox(self)
                self.trace_checkbox.hide()
                self.trace_status = QtWidgets.QLabel(self)
                self.trace_status.hide()

                fcstd_card, fcstd_body = card("Данные SMBS в документе FCStd")
                self.fcstd_save_btn = style_action(
                    QtWidgets.QPushButton(tr("Сохранить в FCStd")), compact=True
                )
                self.fcstd_save_btn.setToolTip(
                    "Вложить JSON с порядком гибов, настройками и историей "
                    "проверок в этот документ и сохранить FCStd"
                )
                self.fcstd_save_btn.clicked.connect(
                    self._save_embedded_smbs_json_clicked
                )
                self.fcstd_load_btn = style_action(
                    QtWidgets.QPushButton(tr("Восстановить из FCStd")), compact=True
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
                    QtWidgets.QPushButton(tr("Дополнительно…")), compact=True
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

                # Core pages match the operator toolbar actions.
                # Only the selected page is shown. Specialist QDialogs may be
                # temporarily added and removed without creating tabs.
                page_specs = (
                    (tr("Оснастка"), self._page_tooling),
                    (tr("Симуляция"), self._page_simulation),
                    (tr("Настройки"), self._page_settings),
                )
                self._task_page_titles = {}
                for name, page in page_specs:
                    controls.addWidget(page)
                    self._task_page_titles[page] = name
                root.addWidget(views, 1)
                self._viewer_tabs = views
                self._task_stack = controls
                self._native_task_panel = None
                self.show_task_panel_btn = QtWidgets.QPushButton(tr("Показать панель управления"))
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
                badge.setText(tr({
                    "Ready": "Готово", "Running": "Расчёт…",
                    "Warning": "Предупреждение", "Error": "Ошибка",
                }.get(caption, caption)))

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
                    self._task_progress_caption.setText(tr("Расчёт…"))
                else:
                    self.task_progress.setRange(0, 100)
                    self._update_task_progress()
                self._on_task_status_change(self.status.text())

            def _set_task_result(self, key, state, summary, details="", findings=""):
                button = self._task_result_buttons[key]
                icons = {
                    "idle": QtWidgets.QStyle.SP_MessageBoxInformation,
                    "running": QtWidgets.QStyle.SP_BrowserReload,
                    "done": QtWidgets.QStyle.SP_DialogApplyButton,
                    "warning": QtWidgets.QStyle.SP_MessageBoxWarning,
                    "error": QtWidgets.QStyle.SP_MessageBoxCritical,
                }
                button.setIcon(button.style().standardIcon(icons[state]))
                button.setToolTip(f"{key} — {summary}")
                button.setAccessibleName(f"{key} — {summary}")
                self._task_results[key] = (summary, details, findings)
                if key == "Mesh" and state in ("idle", "running"):
                    self._mesh_task_result_identity = None

            def _show_task_result(self, key):
                if (key == "Mesh" and self._mesh_task_result_identity is not None
                        and (self.session is None
                             or self._mesh_task_result_identity != self._mesh_preview_identity())):
                    self._set_task_result(
                        "Mesh", "warning", tr("Деталь, порядок или оснастка изменились."),
                        tr("Запустите Mesh заново для текущей конфигурации."),
                    )
                    self._mesh_task_result_identity = None
                summary, details, findings = self._task_results[key]
                box = QtWidgets.QMessageBox(self)
                box.setWindowTitle(key)
                box.setTextFormat(QtCore.Qt.PlainText)
                box.setText(summary)
                if details:
                    box.setInformativeText(details)
                if findings:
                    box.setDetailedText(findings)
                (getattr(box, "exec", None) or box.exec_)()

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
                show_workspace_controls(self, tab=0)

            def open_settings_dialog(self):
                """Show all configuration controls on one contextual page."""
                show_workspace_controls(self, tab=2)

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
                if not self._playback_alive():
                    return
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
           …56423 tokens truncated…self._bound_smbs_document()
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

            def _current_clamp_config(self):
                """Use per-document clamps, otherwise current library defaults."""
                if self._project_clamp_config is not None:
                    return self._project_clamp_config
                from smbs_core.geometry_backend.io.clamp_validator import (
                    load_clamp_config,
                )
                return load_clamp_config(self.tool_library.library_root)

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
                    operation_orientation_overrides=(
                        self._manual_operation_orientation_overrides
                    ),
                    fast_advisory_orientation_ids=(
                        self._fast_advisory_orientation_keys
                    ),
                    backgauge_model=self.backgauge_model,
                    clamp_config=self._current_clamp_config(),
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
                saved_steps = payload["order"].get("operation_steps", [])
                if saved_steps:
                    self.session.controller.restore_operation_snapshot(saved_steps)
                    _desktop_debug_event(
                        "STAGED_BEND_PLAN_RESTORED",
                        steps=self.session.controller.operation_snapshot(),
                    )
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
                self._manual_operation_orientation_overrides = dict(
                    payload["order"].get("operation_orientation_overrides", {})
                )
                self._fast_advisory_orientation_keys = set(
                    payload["order"].get("fast_advisory_orientation_ids", [])
                )
                # FCStd is authoritative for document-specific settings, not
                # the last machine options used for an unrelated project.
                saved_tooling = payload["tooling"]
                if "backgauge" in saved_tooling:
                    config = saved_tooling["backgauge"]
                    if config is None:
                        self.backgauge_model = None
                    else:
                        from smbs_core.kinematics2d.backgauge_2d import (
                            BackgaugeModel2D,
                        )
                        from dataclasses import replace as dataclass_replace
                        self.backgauge_model = dataclass_replace(
                            BackgaugeModel2D.from_dict(config),
                            part_label=self.session.part_label,
                        )
                if "clamps" in saved_tooling:
                    config = saved_tooling["clamps"]
                    if config is not None:
                        from smbs_core.geometry_backend.io.clamp_validator import (
                            ClampConfig,
                        )
                        self._project_clamp_config = ClampConfig.from_dict(config)
                    else:
                        self._project_clamp_config = None
                self._backgauge_preview_cache.clear()
                self._machine_gauge_envelope_cache = None
                self._ergonomic_loading_cache_key = None
                self._manual_preview_unverified = self.session.controller.has_stages
                self._manual_selection_enabled = False
                self._manual_preview_notice.setText(
                    "ПРЕДГИБ / ДОГИБ: кинематическая симуляция, "
                    "коллизии НЕ проверены."
                    if self.session.controller.has_stages else
                    "РУЧНОЙ ПРОСМОТР — коллизии не проверены"
                )
                self._manual_preview_notice.setVisible(self.session.controller.has_stages)
                self._partial_preview_limit = None
                self._preview_only_planar_order = None
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
                    if key in self._operation_weights:
                        try:
                            number = float(value)
                            if math.isfinite(number) and 0.0 <= number <= 100000.0:
                                if self._operation_weights[key] != number:
                                    self._operation_weights[key] = number
                                    QtCore.QSettings("SMBS", "BendSeq").setValue(
                                        "operation_weights/" + key, number
                                    )
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
                    "Восстановлены порядок, этапы предгиба и настройки из FCStd. "
                    "Проверки: ИСТОРИЧЕСКИЕ, UNVERIFIED."
                    if self.session.controller.has_stages else
                    "Восстановлены порядок и настройки из FCStd. "
                    "Проверки: ИСТОРИЧЕСКИЕ, UNVERIFIED."
                )
                if warnings:
                    note += " Ограничения: " + "; ".join(warnings)
                self.fcstd_note.setText(note)
                return note

            def _mesh_preview_identity(self):
                return (
                    self._current_native_tooling_identity(),
                    tuple(self.session.controller.operation_steps),
                    self._effective_operation_orientation_map(),
                    frozenset(self._fast_advisory_orientation_keys),
                )

            def _show_next_mesh_collision(self):
                if self.session is None or self._mesh_collision_identity != self._mesh_preview_identity():
                    self.mesh_show_collision_btn.hide()
                    self.status.setText("Повторите Mesh-проверку для текущей детали и оснастки.")
                    return
                if not self._mesh_collision_hits:
                    return
                if self.session.playback_status == PlaybackStatus.PLAYING:
                    self.pause()
                self._stop_timer()
                index = self._mesh_collision_index if self._collision_display_active else None
                index = 0 if index is None else index + 1
                self._collision_display_active = index < len(self._mesh_collision_hits)
                self._mesh_collision_index = index if self._collision_display_active else None
                self._open_coin_preview()

            def _run_occ_check(self):
                if self.manual_validate_btn.isEnabled() and not self._task_progress_busy:
                    self._validate_export_manual_order(coarse_occ=True)

            def _run_mesh_sequence_check(self):
                if self.session is None or self.timer.isActive():
                    self.status.setText("Mesh: загрузите деталь и остановите симуляцию.")
                    return
                from smbs_core.kinematics2d.mesh_sequence_screen import screen_mesh_sequence

                if self._task_progress_busy:
                    return
                self._set_task_result("Mesh", "running", tr("Проверка коллизий…"))
                self.manual_mesh_check_btn.setEnabled(False)
                self._set_task_busy(True)
                try:
                    cycle = self._ensure_press_cycle_controller()
                    operations = self.session.controller.operation_steps
                    prefix = tuple(self._manual_sequence_prefix)
                    if prefix and not self.session.controller.has_stages:
                        operations = operations[:len(prefix)]

                    def progress(index, count, operation):
                        self.status.setText(f"Mesh: {index + 1}/{count} · {operation}")
                        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)

                    report = screen_mesh_sequence(
                        self.session, cycle,
                        tuple(step.operation_id for step in operations),
                        progress=progress,
                    )
                    _desktop_debug_event("MESH_SEQUENCE_RESULT", **report)
                    if self._mesh_collision_index is not None:
                        self._collision_display_active = False
                    self._mesh_collision_index = None
                    self._mesh_collision_hits = (
                        tuple(report["findings"])
                        + tuple({**hit, "conditional": True} for hit in report["conditional_findings"])
                    )
                    self._mesh_collision_identity = self._mesh_preview_identity()
                    self.mesh_show_collision_btn.setVisible(bool(self._mesh_collision_hits))
                    if self._mesh_collision_hits:
                        self._collision_display_active = False
                        self._show_next_mesh_collision()
                    else:
                        self._update_3d_preview(force=True)
                    message = (
                        f"Mesh: {len(report['completed'])}/{len(operations)} гибов, "
                        f"{report['elapsed_seconds']:.1f} с"
                    )
                    if report["findings"]:
                        hit = report["findings"][0]
                        tool = {"punch": "пуансон", "die": "матрица"}[hit["tool"]]
                        message += f" · пересечение: {hit['operation']}, {tool}, {hit['phase']}"
                    elif not (report["pending"] or report["unresolved"] or report["conditional_findings"]):
                        message += " · подтверждённых проникновений в выборке нет"
                    if report["conditional_findings"]:
                        message += f" · Conditional, торцы пуансона: {len(report['conditional_findings'])}"
                    if report["unresolved"]:
                        message += f" · не удалось уточнить: {len(report['unresolved'])}"
                    if report["pending"]:
                        message += " · осталось: " + ", ".join(report["pending"])
                        reason = report["reason"]
                        message += " · " + ("лимит времени" if reason == "TIME_BUDGET" else reason)
                    hits = len(report["findings"])
                    conditional = len(report["conditional_findings"])
                    unresolved = len(report["unresolved"])
                    if hits:
                        state, summary = "error", tr("Подтверждённых коллизий: {count}.", count=hits)
                    elif report["pending"] or unresolved:
                        state, summary = "warning", tr("Проверка завершена не полностью.")
                    elif conditional:
                        state, summary = "warning", tr("Условных пересечений на торцах: {count}.", count=conditional)
                    else:
                        state, summary = "done", tr("В проверенных положениях коллизий не обнаружено.")
                    tool_names = {"punch": "пуансон", "die": "матрица"}
                    findings = []
                    for label, entries in (
                        ("Коллизия", report["findings"]),
                        ("Торцевое пересечение", report["conditional_findings"]),
                        ("Не удалось уточнить", report["unresolved"]),
                    ):
                        for item in entries:
                            finding = (
                                f"{tr(label)} — {item['operation']}, {tr(tool_names[item['tool']])}, "
                                f"{item['component']}, {item['phase']}"
                            )
                            if item.get("reason"):
                                finding += f" — {item['reason']}"
                            findings.append(finding)
                    details = tr(
                        "Проверено гибов: {checked}/{total}. Время: {seconds:.1f} с.",
                        checked=len(report["completed"]), total=len(operations),
                        seconds=report["elapsed_seconds"],
                    )
                    if report["pending"]:
                        details += "\n" + tr("Осталось проверить: {order}", order=", ".join(report["pending"]))
                        reason = report["reason"]
                        details += "\n" + (tr("Достигнут лимит времени.") if reason == "TIME_BUDGET" else reason)
                    self._set_task_result("Mesh", state, summary, details, "\n".join(findings))
                    self._mesh_task_result_identity = self._mesh_collision_identity
                    self.status.setText(message)
                    self.manual_check_summary.setText(message)
                except (ValueError, RuntimeError, AttributeError) as exc:
                    self._set_task_result("Mesh", "error", tr("Ошибка проверки."), str(exc))
                    self.status.setText("Mesh: " + str(exc))
                finally:
                    self._set_task_busy(False)
                    self.manual_mesh_check_btn.setEnabled(True)

            def _validate_export_manual_order(self, coarse_occ=False, *, show_preview=False):
                """Validate selected manual order: Stage A/B plus actual FCStd 3D.

                The backgauge stays visual-only. Simulation/slider retain
                their separate UNVERIFIED and nonblocking preview paths.
                """
                if self.session is None:
                    self.status.setText("Сначала загрузите деталь.")
                    return
                if self.session.controller.has_stages:
                    self.status.setText(
                        "Проверка многоэтапного гиба ещё не реализована в OCC: "
                        "результат только UNVERIFIED. Не используйте обычную "
                        "проверку одного гиба для подтверждения догиба."
                    )
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
                    self.saved_sequences_btn, self.add_part_btn, self.import_tooling_btn,
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
                        tr("Показать коллизию в 3D")
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
                        tr("Показать коллизию в 3D")
                    )
                if show_controls:
                    show_workspace_controls(self, tab=1)
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
                show_workspace_controls(self, tab=1)

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
                if self.session.controller.has_stages:
                    self.status.setText(
                        "При предгибе изменяйте порядок строк таблицы перетаскиванием."
                    )
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
                if self._closing or not self._qt_is_valid(self):
                    return
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

            def _qt_debug_state(self):
                objects = ("timer", "_flat_view", "_flat_scene", "_viewer_tabs", "play_btn")
                return {
                    "closing": self._closing,
                    "workspace_valid": self._qt_is_valid(self),
                    "objects_valid": {
                        name: obj is not None and self._qt_is_valid(obj)
                        for name in objects if (obj := getattr(self, name, None)) is not None
                    },
                    "playing": self.playing,
                    "playback_status": self.session.playback_status if self.session else None,
                    "active_bend_id": self.session.active_bend_id if self.session else None,
                }

            def _apply_manual_order(self, prefix):
                if self._closing or not self._qt_is_valid(self):
                    _desktop_debug_event("MANUAL_ORDER_IGNORED", reason="workspace_destroyed")
                    return False
                prefix = tuple(prefix)
                started = time.perf_counter()
                stage = "set_bend_order"

                def checkpoint(next_stage):
                    nonlocal stage
                    stage = next_stage
                    _desktop_debug_event("MANUAL_ORDER_STAGE", stage=stage, prefix=prefix)

                _desktop_debug_event("MANUAL_ORDER_STARTED", prefix=prefix,
                                     qt_state=self._qt_debug_state())
                try:
                    applied = self._apply_manual_order_steps(prefix, checkpoint)
                except Exception as exc:
                    _desktop_debug_event("MANUAL_ORDER_ERROR", stage=stage, prefix=prefix,
                                         qt_state=self._qt_debug_state(), error=str(exc),
                                         traceback=traceback.format_exc())
                    raise
                _desktop_debug_event("MANUAL_ORDER_COMPLETED" if applied else
                                     "MANUAL_ORDER_REJECTED", stage=stage, prefix=prefix,
                                     elapsed_ms=round((time.perf_counter() - started) * 1000, 2))
                return applied

            def _apply_manual_order_steps(self, prefix, checkpoint):
                if self.session is None:
                    return False
                remainder = tuple(
                    bend_id for bend_id in self.session.canonical_order
                    if bend_id not in prefix
                )
                try:
                    self.session.set_bend_order(prefix + remainder)
                except SequenceError as exc:
                    _desktop_debug_event("MANUAL_ORDER_SEQUENCE_ERROR", error=str(exc),
                                         prefix=prefix, traceback=traceback.format_exc())
                    self.status.setText(str(exc))
                    return False
                self._manual_sequence_prefix = list(prefix)
                self._manual_3d_verdicts.clear()
                self._manual_retract_verdicts.clear()
                self._last_manual_sequence_report = None
                checkpoint("reset_validation")
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
                self._conditional_bends_status.clear()
                checkpoint("update_order_controls")
                self._sync_manual_order_input()
                self._populate_operations()
                checkpoint("configure_active_bend")
                self._configure_for_active_bend()
                if not self._qt_is_valid(self):
                    _desktop_debug_event("MANUAL_ORDER_INTERRUPTED", prefix=prefix)
                    return False
                checkpoint("draw_flat_pattern")
                self._draw_flat_pattern()
                checkpoint("update_status")
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

            def _create_flat_scene(self):
                self._flat_scene = QtWidgets.QGraphicsScene(self._flat_view)
                self._flat_view.setScene(self._flat_scene)
                return self._flat_scene

            def _draw_flat_pattern(self, reset_camera=False):
                view = getattr(self, "_flat_view", None)
                if self._closing or view is None or not self._qt_is_valid(view):
                    _desktop_debug_event("FLAT_PATTERN_DRAW_SKIPPED", qt_state=self._qt_debug_state())
                    return
                scene = view.scene()
                if scene is None or not self._qt_is_valid(scene):
                    scene = self._create_flat_scene()
                    self._trace_event("FLAT_PATTERN_SCENE_RECREATED", qt_state=self._qt_debug_state())
                self._flat_scene = scene
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
                controller = self.session.controller
                staged = controller.has_stages
                operation_labels = {}
                if staged:
                    names = {"PREBEND": "предгиб", "FINISH": "догиб", "SINGLE": "гиб"}
                    for index, step in enumerate(controller.operation_steps, 1):
                        operation_labels.setdefault(step.bend_id, []).append(
                            f"{index}. {step.operation_id} · {names[step.kind]} "
                            f"{step.target_angle_deg:+.2f}°"
                        )
                else:
                    order = (self._manual_sequence_prefix if self._manual_selection_enabled
                             else self.session.bend_order)
                    operation_labels = {
                        bend_id: [str(index)] for index, bend_id in enumerate(order, 1)
                    }
                active_step = controller.operation_at(self.session.active_bend_index)
                for conn in self.session.panel_build.connections:
                    bend_id = conn.bend_id
                    (x1, y1), (x2, y2) = conn.axis_xy
                    chosen = bend_id in operation_labels
                    active = chosen and (
                        active_step.bend_id == bend_id
                        if staged or not self._manual_selection_enabled
                        else self._manual_sequence_prefix[-1] == bend_id
                    )
                    color = "#e67e22" if active else ("#219653" if chosen else "#2864ac")
                    pen = QtGui.QPen(QtGui.QColor(color))
                    pen.setWidthF(2.3 if active else 1.4)
                    pen.setCosmetic(True)
                    item = scene.addLine(float(x1), -float(y1),
                                         float(x2), -float(y2), pen)
                    item.setData(0, bend_id)
                    item.setZValue(10)
                    tooltip = (
                        "\n".join(operation_labels.get(bend_id, ())) if staged
                        else "{} {:+.1f}°".format(bend_id, conn.angle_deg)
                    )
                    item.setToolTip(tooltip)
                    hit_pen = QtGui.QPen(QtGui.QColor(0, 0, 0, 0))
                    hit_pen.setWidth(13)
                    hit_pen.setCosmetic(True)
                    hit = scene.addLine(float(x1), -float(y1),
                                        float(x2), -float(y2), hit_pen)
                    hit.setZValue(12)
                    hit.setData(0, bend_id)
                    hit.setToolTip(tooltip)
                    mx, my = (float(x1)+float(x2))/2, -(float(y1)+float(y2))/2
                    number = ("\n".join(operation_labels[bend_id]) if chosen
                              else "{} {:+.0f}°".format(bend_id, conn.angle_deg))
                    label = scene.addSimpleText(number)
                    label.setBrush(QtGui.QBrush(QtGui.QColor(color)))
                    label.setData(0, bend_id)
                    label.setToolTip(tooltip)
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
                gauge = (
                    None if (
                        self._manual_selection_enabled
                        or self.session.controller.has_stages
                        or getattr(self, "_fast_apply_skip_table_gauge", False)
                    )
                    else self._backgauge_preview_position(idx - 1)
                )
                face_s = gauge[0].s_mm if gauge is not None and gauge[0] is not None else None
                op = self.session.controller.operation_at(idx - 1)
                if self.session.controller.has_stages:
                    done = (idx <= self.session.controller.completed_step_count)
                    status_str = (
                        "ЭТАП ГОТОВ" if done else ("АКТИВНЫЙ ЭТАП" if is_active else "ОЖИДАНИЕ")
                    ) + " [UNVERIFIED]"
                step_name = {
                    "PREBEND": "предгиб", "FINISH": "догиб", "SINGLE": "обычный",
                }[op.kind]
                return (
                    str(idx),
                    "{} · {} ({} → {})".format(
                        op.operation_id, step_name, b.fixed_panel, b.moving_panel
                    ),
                    "",  # 180° uses the native Qt check indicator.
                    "{:+.1f}°".format(b.angle_deg),
                    "{:+.1f}°".format(cur),
                    "{:.2f}".format(flat_mm) if flat_mm is not None else "—",
                    "{:.2f}".format(face_s) if face_s is not None else "—",
                    status_str,
                    retract_label,
                    "",
                )

            def _remove_prebend(self, bend_id, *, parent=None):
                """Undo the split for only this physical bend.

                Keep the final angle and the positions of all other operations.
                The controller selects the existing FINISH position as the
                restored ordinary bend. Confirmation prevents an accidental
                click in the operations table from changing the plan.
                """
                if self.session is None or self._task_progress_busy:
                    return False
                controller = self.session.controller
                prebend = next((
                    step for step in controller.operation_steps
                    if step.bend_id == bend_id and step.kind == "PREBEND"
                ), None)
                if prebend is None:
                    self.status.setText(
                        "Для {} предварительный гиб не задан.".format(bend_id)
                    )
                    return False
                if (self.timer.isActive() or self.playing
                        or not self.session.can_reorder()):
                    self.status.setText(
                        "Для удаления предгиба сначала остановите симуляцию "
                        "и нажмите Reset."
                    )
                    return False
                final = controller.target_for(bend_id)
                decision = QtWidgets.QMessageBox.question(
                    parent or self,
                    "Удалить предварительный гиб?",
                    (
                        "Удалить {} (промежуточный угол {:+.2f}°)?\n\n"
                        "Догиб {}.2 будет заменён обычным гибом {} "
                        "до {:+.2f}°.\n"
                        "Остальные операции, их порядок и углы не изменятся."
                    ).format(bend_id + ".1", prebend.target_angle_deg,
                             bend_id, bend_id, final),
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No,
                )
                if decision != QtWidgets.QMessageBox.Yes:
                    return False
                previous = controller.operation_snapshot()
                try:
                    controller.remove_prebend(bend_id)
                except SequenceError as exc:
                    # A failed removal must leave the previous plan intact.
                    try:
                        controller.restore_operation_snapshot(previous)
                    except SequenceError:
                        pass
                    self.status.setText("Предгиб не удалён: " + str(exc))
                    return False
                _desktop_debug_event(
                    "STAGED_PREBEND_REMOVED",
                    bend_id=bend_id,
                    removed_operation_id=prebend.operation_id,
                    restored_operation_id=bend_id,
                    final_angle_deg=final,
                    steps=controller.operation_snapshot(),
                )
                self._on_staged_plan_changed(
                    "{}: предгиб удалён. Обычный гиб восстановлен до "
                    "{:+.2f}°.".format(bend_id, final)
                )
                return True

            def _edit_prebend_dialog(self, bend_id):
                """Choose a pre-bend angle and drag ONLY that step among locked steps."""
                if self.session is None or self._task_progress_busy:
                    return
                if not self.session.can_reorder():
                    self.status.setText("Перед добавлением или изменением предгиба нажмите Reset.")
                    return
                controller = self.session.controller
                final = controller.target_for(bend_id)
                current = next((
                    step for step in controller.operation_steps
                    if step.bend_id == bend_id and step.kind == "PREBEND"
                ), None)
                dialog = QtWidgets.QDialog(self)
                dialog.setWindowTitle("Предварительный гиб / догиб — " + bend_id)
                dialog.setMinimumWidth(400)
                layout = QtWidgets.QVBoxLayout(dialog)
                explanation = QtWidgets.QLabel(
                    "Физический гиб: {}\\nКонечный угол: {:+.2f}° (не меняется)\\n"
                    "Задайте промежуточный абсолютный угол и перетащите "
                    "только строку предгиба между зафиксированными операциями."
                    .format(bend_id, final)
                )
                explanation.setWordWrap(True)
                layout.addWidget(explanation)
                row = QtWidgets.QFormLayout()
                spin = QtWidgets.QDoubleSpinBox(dialog)
                spin.setObjectName("SMBS_PreBendAngle")
                spin.setDecimals(2)
                spin.setSingleStep(1.0)
                if final > 0:
                    spin.setRange(0.01, max(0.01, final - 0.01))
                else:
                    spin.setRange(min(-0.01, final + 0.01), -0.01)
                spin.setValue(current.target_angle_deg if current else final / 2.0)
                spin.setSuffix("°")
                row.addRow("Промежуточный угол:", spin)
                layout.addLayout(row)

                # Modal placement preview. All existing operations are locked;
                # the newly selected PREBEND is the sole draggable item.
                preview = QtWidgets.QListWidget(dialog)
                preview.setObjectName("SMBS_PreBendPlacement")
                preview.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
                preview.setDefaultDropAction(QtCore.Qt.MoveAction)
                preview.setDropIndicatorShown(True)
                preview.setMinimumHeight(200)
                pre_id = bend_id + ".1"
                finish_id = bend_id + ".2"
                stages = []
                for step in controller.operation_steps:
                    if step.bend_id == bend_id and step.kind == "SINGLE":
                        stages.append((pre_id, bend_id, spin.value(), "PREBEND"))
                        stages.append((finish_id, bend_id, final, "FINISH"))
                    else:
                        stages.append((
                            step.operation_id, step.bend_id,
                            step.target_angle_deg, step.kind,
                        ))
                for op_id, physical_id, angle, kind in stages:
                    label = (
                        "{}  —  ПРЕДГИБ (перетащите)".format(op_id)
                        if op_id == pre_id else
                        "{}  —  догиб до {:+.1f}° (закреплён)".format(op_id, angle)
                        if op_id == finish_id else
                        "{}  —  {:+.1f}° (закреплён)".format(op_id, angle)
                    )
                    item = QtWidgets.QListWidgetItem(label)
                    item.setData(QtCore.Qt.UserRole, op_id)
                    flags = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
                    flags |= QtCore.Qt.ItemIsDropEnabled
                    if op_id == pre_id:
                        flags |= QtCore.Qt.ItemIsDragEnabled
                    item.setFlags(flags)
                    preview.addItem(item)
                    if op_id == pre_id:
                        preview.setCurrentItem(item)
                layout.addWidget(preview)
                hint = QtWidgets.QLabel(
                    "Догиб должен остаться после предгиба. "
                    "Угол догиба нельзя изменить: он задан развёрткой."
                )
                hint.setWordWrap(True)
                layout.addWidget(hint)

                buttons = QtWidgets.QDialogButtonBox(
                    QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
                    dialog,
                )
                def apply_dialog_order():
                    ids = tuple(
                        preview.item(i).data(QtCore.Qt.UserRole)
                        for i in range(preview.count())
                    )
                    if (pre_id not in ids or finish_id not in ids
                            or ids.index(pre_id) >= ids.index(finish_id)):
                        hint.setText(
                            "Ошибка: предварительный гиб должен стоять "
                            "перед соответствующим догибом."
                        )
                        return
                    dialog.accept()
                buttons.accepted.connect(apply_dialog_order)
                buttons.rejected.connect(dialog.reject)
                if current is not None:
                    # Keep this action obvious and separate from Save/Cancel.
                    # The old destructive-role button at the bottom of the
                    # button box was easy to overlook in the modal dialog.
                    delete_button = QtWidgets.QPushButton(
                        "Удалить предгиб и вернуть обычный гиб", dialog
                    )
                    delete_button.setObjectName("SMBS_DeletePrebend")
                    delete_button.setToolTip(
                        "Удалить {}.1, заменить {}.2 одним исходным гибом {} "
                        "и сохранить порядок остальных операций.".format(
                            bend_id, bend_id, bend_id
                        )
                    )
                    def remove_from_dialog():
                        if self._remove_prebend(bend_id, parent=dialog):
                            dialog.reject()
                    delete_button.clicked.connect(remove_from_dialog)
                    layout.addWidget(delete_button)
                layout.addWidget(buttons)
                result = dialog.exec() if hasattr(dialog, "exec") else dialog.exec_()
                try:
                    if result == 1:
                        order = tuple(
                            preview.item(i).data(QtCore.Qt.UserRole)
                            for i in range(preview.count())
                        )
                        original = controller.operation_snapshot()
                        try:
                            controller.add_prebend(bend_id, spin.value())
                            controller.reorder_operations(order)
                        except SequenceError:
                            controller.restore_operation_snapshot(original)
                            raise
                        operation = "Предгиб {}: {:+.2f}° → догиб до {:+.2f}°".format(
                            bend_id, spin.value(), final
                        )
                    else:
                        # Deletion is handled by _remove_prebend, including its
                        # confirmation, status update and preview invalidation.
                        return
                    _desktop_debug_event(
                        "STAGED_BEND_PLAN_CHANGED",
                        bend_id=bend_id,
                        steps=controller.operation_snapshot(),
                    )
                    self._on_staged_plan_changed(operation)
                except SequenceError as exc:
                    self.status.setText(str(exc))

            def _on_staged_operations_dropped(self, *_args):
                """Commit the Qt row move only after the native event settles."""
                if (self._closing or self._staged_dnd_pending
                        or self._staged_dnd_in_progress):
                    return
                self._staged_dnd_pending = True
                # Zero-delay callbacks may run in a nested FreeCAD event loop
                # before the native tree item widgets finish their move.
                QtCore.QTimer.singleShot(50, self._apply_staged_drop)

            def _apply_staged_drop(self):
                self._staged_dnd_pending = False
                if self._closing or not self._qt_is_valid(self.operations):
                    return
                if self.session is None or not self.session.controller.has_stages:
                    self._populate_operations()
                    return
                if (self.timer.isActive() or self.playing
                        or not self.session.can_reorder()):
                    self.status.setText(
                        "Порядок этапов нельзя менять во время симуляции; "
                        "нажмите Reset."
                    )
                    self._populate_operations()
                    return
                operation_ids = tuple(
                    self.operations.topLevelItem(idx).data(
                        0, int(QtCore.Qt.UserRole) + 1
                    )
                    for idx in range(self.operations.topLevelItemCount())
                )
                expected = tuple(
                    step.operation_id
                    for step in self.session.controller.operation_steps
                )
                _desktop_debug_event(
                    "STAGED_DROP_COMMIT_STARTED",
                    operation_ids=operation_ids,
                    previous_order=expected,
                )
                if (len(operation_ids) != len(expected)
                        or len(set(operation_ids)) != len(expected)
                        or set(operation_ids) != set(expected)):
                    _desktop_debug_event(
                        "STAGED_DROP_REJECTED",
                        reason="missing_or_duplicate_operation_id",
                        operation_ids=operation_ids,
                    )
                    self.status.setText("Некорректный перенос этапов, порядок восстановлен.")
                    self._populate_operations()
                    return
                try:
                    self.session.controller.reorder_operations(operation_ids)
                    self.session.active_bend_index = self.session.controller.active_index
                except SequenceError as exc:
                    _desktop_debug_event(
                        "STAGED_DROP_REJECTED",
                        reason=str(exc),
                        operation_ids=operation_ids,
                    )
                    self.status.setText(str(exc))
                    self._populate_operations()
                    return
                _desktop_debug_event(
                    "STAGED_BEND_STEPS_REORDERED", operation_ids=operation_ids,
                )
                self._on_staged_plan_changed(
                    "Порядок этапов: " + " → ".join(operation_ids)
                )

            def _on_staged_plan_changed(self, message, *, fast_apply=False):
                """Invalidate prior OCC evidence: split operations are NOT verified."""
                if (not self._playback_alive()
                        or not self._qt_is_valid(self.operations)
                        or not self._qt_is_valid(self.tooling_status)):
                    _desktop_debug_event(
                        "STAGED_REFRESH_SKIPPED_HOST_DESTROYED", step="entry",
                    )
                    return
                self._stop_timer()
                self._machine_pose_fault = None
                self._manual_sequence_prefix = []
                self._manual_orientation_order = ()
                self._manual_orientation_by_bend = {}
                valid_ops = {
                    step.operation_id for step in self.session.controller.operation_steps
                }
                self._manual_operation_orientation_overrides = {
                    op_id: value
                    for op_id, value in self._manual_operation_orientation_overrides.items()
                    if op_id in valid_ops and op_id not in self.session.canonical_order
                }
                self._fast_advisory_orientation_keys.intersection_update(valid_ops)
                self._manual_3d_verdicts.clear()
                self._manual_retract_verdicts.clear()
                self._last_manual_sequence_report = None
                self._embedded_diagnostics_history = None
                self._physical_check_signature = None
                self._clear_manual_check_feedback()
                self._backgauge_preview_cache.clear()
                self._manual_preview_unverified = self.session.controller.has_stages
                self._manual_preview_notice.setText(
                    "ПРЕДГИБ / ДОГИБ: кинематическая симуляция, "
                    "проверка коллизий последовательности НЕ выполнена."
                    if self.session.controller.has_stages else
                    "РУЧНОЙ ПРОСМОТР — коллизии не проверены"
                )
                self._manual_preview_notice.setVisible(
                    self.session.controller.has_stages
                )
                self._sync_manual_order_input()
                if fast_apply:
                    # Old press cycle may retain the previous active operation
                    # even though its sequence-controller identity is unchanged.
                    # Rebuild on the next explicit preview/simulation action.
                    self.press_cycle = None
                    # FAST changes operation metadata only. Do not enter
                    # FreeCAD native document/3D preview work from the Qt
                    # result-dialog completion callback. On macOS this can
                    # destroy the task host while the operation table updates.
                    self._populate_operations_fast()
                    if self._playback_alive() and self._qt_is_valid(self.status):
                        self.status.setText(message + " | 3D preview requires refresh")
                    _desktop_debug_event(
                        "FAST_APPLY_LIGHTWEIGHT_REFRESH",
                        workspace_alive=self._playback_alive(),
                        operation_count=len(self.session.controller.operation_steps),
                    )
                    return
                self._populate_operations()
                if (not self._playback_alive()
                        or not self._qt_is_valid(self.operations)):
                    _desktop_debug_event(
                        "STAGED_REFRESH_SKIPPED_HOST_DESTROYED",
                        step="after_operation_table",
                    )
                    return
                pc = self._ensure_press_cycle_controller()
                if pc is not None:
                    pc.select_operation(self.session.controller.active_index)
                self.session.active_bend_index = self.session.controller.active_index
                self._configure_for_active_bend()
                if not self._playback_alive():
                    return
                self._draw_flat_pattern(reset_camera=False)
                if (not self._playback_alive()
                        or not self._qt_is_valid(self.tooling_status)):
                    _desktop_debug_event(
                        "STAGED_REFRESH_SKIPPED_HOST_DESTROYED",
                        step="after_flat_pattern",
                    )
                    return
                self._update_tooling_status_label()
                if not self._playback_alive():
                    return
                self.draw()
                if self._playback_alive() and self._qt_is_valid(self.status):
                    self.status.setText(message + " | UNVERIFIED")

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
                self._manual_preview_unverified = bool(self.session.controller.has_stages)
                self._manual_preview_notice.setText(
                    "ПРЕДГИБ/ДОГИБ — предварительная симуляция, коллизии не проверены"
                    if self.session.controller.has_stages else
                    "РУЧНОЙ ПРОСМОТР — коллизии не проверены"
                )
                self._manual_preview_notice.setVisible(self.session.controller.has_stages)
                self._draw_flat_pattern()
                self._update_manual_hint()
                self._configure_for_active_bend()
                if self.tooling_setup.aligned and not self._manual_selection_enabled:
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

            def _populate_operations_fast(self):
                """Reuse normal editable table without native gauge previews.

                _fast_apply_skip_table_gauge is held by the caller while this
                executes. With every FAST orientation pinned, the orientation
                lookup does not invoke CAD ergonomic loading previews either.
                """
                if not self._playback_alive():
                    return
                self._populate_operations()

            def _populate_operations(self):
                if (not self._playback_alive()
                        or not self._qt_is_valid(self.operations)):
                    return
                signals_were_blocked = self.operations.blockSignals(True)
                try:
                    self.operations.clear()
                    if self.session is not None:
                        for _bend in self.session.ordered_bends:
                            self.operations.addTopLevelItem(QtWidgets.QTreeWidgetItem())
                        self._update_operations_visual_state()
                        if self._playback_alive() and self._qt_is_valid(self.operations):
                            self.operations.setDragEnabled(
                                self.session.controller.has_stages
                            )
                finally:
                    if self._qt_is_valid(self.operations):
                        self.operations.blockSignals(signals_were_blocked)

            def _update_operations_visual_state(self):
                if (self.session is None or self._closing
                        or self._staged_dnd_in_progress):
                    return
                # Picking lines only changes the order. Recalculate derived
                # machine positions when the user finishes editing.
                orientations = (
                    self._effective_operation_orientation_map()
                    if self.session.controller.has_stages else
                    self._manual_orientation_overrides
                    if self._manual_selection_enabled else
                    self._effective_loading_orientation_map()
                )
                if not self._qt_is_valid(self) or not self._qt_is_valid(self.operations):
                    return
                signals_were_blocked = self.operations.blockSignals(True)
                try:
                    count = self.operations.topLevelItemCount()
                    for idx, bend in enumerate(self.session.ordered_bends[:count], 1):
                        item = self.operations.topLevelItem(idx - 1)
                        bend_id = bend.bend_id
                        item.setData(0, QtCore.Qt.UserRole, bend_id)
                        op = self.session.controller.operation_at(idx - 1)
                        item.setData(0, int(QtCore.Qt.UserRole) + 1, op.operation_id)
                        cols = self._format_operation_row(idx, bend)
                        # Calculating machine gauge envelopes can be slow.
                        # The FreeCAD Tasks root may be destroyed meanwhile,
                        # including on macOS Qt6 OverlayManager callbacks.
                        if (not self._playback_alive()
                                or not self._qt_is_valid(self.operations)):
                            _desktop_debug_event(
                                "OPERATIONS_REFRESH_ABORTED", index=idx,
                                reason="host_destroyed_during_row_format",
                            )
                            return
                        for col, label in enumerate(cols):
                            item.setText(col, label)
                        item.setCheckState(
                            _LOADING_ORIENTATION_COLUMN,
                            QtCore.Qt.Checked
                            if reverse_axis_for_operation(
                                orientations, bend_id, op.operation_id
                            ) else QtCore.Qt.Unchecked,
                        )
                        tooltip = "Галочка — разворот заготовки на 180°. Снята — 0°."
                        if self.session.controller.has_stages:
                            tooltip += (
                                "\nНастройка относится только к операции "
                                + op.operation_id + ", а не ко всем этапам " + bend_id + "."
                            )
                        if (
                            (op.operation_id != bend_id and
                             op.operation_id in self._manual_operation_orientation_overrides)
                            or (op.operation_id == bend_id and
                                bend_id in self._manual_orientation_overrides)
                        ):
                            tooltip += (
                                "\n" + (
                                    "Положение предложено FAST (UNVERIFIED)."
                                    if op.operation_id in self._fast_advisory_orientation_keys
                                    else "Положение задано вручную."
                                ) + " Правый клик возвращает автоподбор."
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
                        if self.operations.itemWidget(item, 9) is None:
                            if op.kind == "PREBEND":
                                # Quick action next to Edit, plus the prominent
                                # deletion button inside the editing dialog.
                                controls = QtWidgets.QWidget(self.operations)
                                controls.setObjectName("SMBS_PreBendRowActions")
                                actions = QtWidgets.QHBoxLayout(controls)
                                actions.setContentsMargins(0, 0, 0, 0)
                                actions.setSpacing(3)
                                edit_btn = QtWidgets.QToolButton(controls)
                                edit_btn.setText("Изм.")
                                edit_btn.setToolTip(
                                    "Изменить предгиб " + op.operation_id
                                )
                                edit_btn.clicked.connect(
                                    lambda _checked=False, bid=bend_id:
                                    self._edit_prebend_dialog(bid)
                                )
                                delete_btn = QtWidgets.QToolButton(controls)
                                delete_btn.setText("×")
                                delete_btn.setFixedWidth(25)
                                delete_btn.setObjectName("SMBS_DeletePrebendRow")
                                delete_btn.setToolTip(
                                    "Удалить предгиб {} и вернуть обычный гиб {}".format(
                                        op.operation_id, bend_id
                                    )
                                )
                                delete_btn.clicked.connect(
                                    lambda _checked=False, bid=bend_id:
                                    QtCore.QTimer.singleShot(
                                        0, lambda: self._remove_prebend(bid)
                                    )
                                )
                                actions.addWidget(edit_btn)
                                actions.addWidget(delete_btn)
                                self.operations.setItemWidget(item, 9, controls)
                            else:
                                add_btn = QtWidgets.QToolButton(self.operations)
                                add_btn.setText("+" if op.kind == "SINGLE" else "Изм.")
                                add_btn.setToolTip(
                                    "Добавить или изменить предварительный угол гиба "
                                    + bend_id
                                )
                                add_btn.clicked.connect(
                                    lambda _checked=False, bid=bend_id:
                                    self._edit_prebend_dialog(bid)
                                )
                                self.operations.setItemWidget(item, 9, add_btn)
                        else:
                            widget = self.operations.itemWidget(item, 9)
                            if isinstance(widget, QtWidgets.QToolButton):
                                widget.setText(
                                    "+" if op.kind == "SINGLE" else "Изм."
                                )
                    if 0 <= self.session.active_bend_index < count:
                        active = self.operations.topLevelItem(self.session.active_bend_index)
                        if self.operations.currentItem() is not active:
                            self.operations.setCurrentItem(active)
                finally:
                    if self._qt_is_valid(self.operations):
                        self.operations.blockSignals(signals_were_blocked)

            def _configure_for_active_bend(self, *, angle_only=False):
                if self.session is None:
                    return
                target_mag = abs(self.session.target_angle_deg)
                max_steps = max(1, int(round(target_mag * _SLIDER_SCALE)))
                self.angle = self.session.current_angle_deg
                slider_pos = int(round(abs(self.angle) * _SLIDER_SCALE))
                self.slider.blockSignals(True)
                start_angle = (
                    self.session.controller.operation_start_deg(self.session.active_bend_index)
                    if self.session.controller.has_stages else 0.0
                )
                min_steps = int(round(abs(start_angle) * _SLIDER_SCALE))
                self.slider.setRange(min_steps, max_steps)
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
                if (self.session is None or current is None
                        or self._closing or self._staged_dnd_pending
                        or self._staged_dnd_in_progress):
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
                start = (
                    self.session.controller.operation_start_deg(self.session.active_bend_index)
                    if self.session.controller.has_stages else 0.0
                )
                mag = min(abs(target), max(abs(start), float(slider_val) / _SLIDER_SCALE))
                signed_angle = 0.0 if mag < 1e-9 else sign * mag
                if slider_val == self.slider.minimum():
                    signed_angle = start
                elif slider_val == self.slider.maximum():
                    signed_angle = target
                self.set_angle(signed_angle)

            def set_angle(self, signed_angle_deg: float):
                """Set the active bend to a signed angle manually (stops auto-playback if active)."""
                if self.session is None:
                    return
                self._machine_pose_fault = None
                if self.timer.isActive() or self.playing:
                    self._stop_timer()
                try:
                    controller = self.session.controller
                    if (controller.has_stages
                            and controller.active_index != controller.completed_step_count):
                        controller.seek_staged_operation_start(controller.active_index)
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
                if not self._playback_alive():
                    return
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
                    if not self.draw() or not self._playback_alive():
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
                    _desktop_debug_event("PLAYBACK_STEP_ERROR", error=str(exc),
                                         traceback=traceback.format_exc(),
                                         qt_state=self._qt_debug_state())
                    # A failed controller step must not leave a logically PLAYING
                    # FoldState after stopping the Qt timer. The last valid
                    # angles remain observable; resuming Full Cycle requires
                    # an explicit reset or mode change, never silent replay.
                    if not self._playback_alive():
                        return
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
                    "flat_pattern_attached": (
                        self._qt_is_valid(self._flat_view)
                        and self._qt_is_valid(self._flat_scene)
                        and self._flat_view.scene() is self._flat_scene
                    ),
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
                if self._closing:
                    # Qt can deliver Close multiple times to a retained MDI
                    # subwindow. Finalisation is destructive and must run once.
                    _hide_closed_workspace(self)
                    event.accept()
                    return
                _desktop_debug_event("WORKSPACE_CLOSE_STARTED", qt_state=self._qt_debug_state())
                if self._playback_alive():
                    self.timer.stop()
                self._closing = True
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
                self.playing = False
                close_workspace_controls(self)
                self._preview_3d.release()
                super().closeEvent(event)
                _hide_closed_workspace(self)
                event.accept()
                _desktop_debug_event("WORKSPACE_CLOSE_COMPLETED")

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
                       tuple(sorted(orientations.items())),
                       frozenset(self._fast_advisory_orientation_keys))
                cached = self._machine_gauge_envelope_cache
                if cached is not None and cached[0] == key:
                    return cached[1]
                from smbs_core.kinematics2d.gauge_preview import (
                    sequenced_gauge_envelopes,
                )
                values, errors = sequenced_gauge_envelopes(
                    session, stations_xm_mm=stations,
                    reverse_axis_by_bend=orientations,
                    rear_frame_bend_ids=self._fast_advisory_orientation_keys,
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
                    frozenset(self._fast_advisory_orientation_keys),
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
                if not self._playback_alive():
                    return False
                self._update_task_progress()
                self._update_loading_orientation_label()
                return self._update_3d_preview()

        return Panel(library_root=library_root)
