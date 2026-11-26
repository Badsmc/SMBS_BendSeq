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

from freecad_smbs.ui.native_preferences import NativePreferences
from freecad_smbs.ui.localization import interface_language, tr, tr_message
from freecad_smbs.icon_theme import icon_path as _smbs_icon_path

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
            message = tr("smbs_json_was_not_embedded_in_the_fcstd", error=exc)
            _error(message + "\n" + traceback.format_exc())
            panel.status.setText(message)



def _tooling_status_text(setup):
    """Present headless tooling status in the FreeCAD interface language."""
    geometry = {
        "NONE": tr("none_selected"),
        "INVALID": tr("invalid"),
        "NEEDS_ALIGNMENT": tr("needs_alignment"),
        "IMPORTED": tr("imported"),
        "VERIFIED": tr("verified"),
    }.get(setup.geometry_status_summary(), setup.geometry_status_summary())
    result = tr(
        "punch_die_geometry_collision_not_checked",
        punch=setup.punch.name if setup.punch is not None else "—",
        die=setup.die.name if setup.die is not None else "—",
        geometry=geometry,
    )
    if setup.match_active_bend_length:
        length = (tr("mm_94cbf584", length=setup.active_bend_length_mm)
                  if setup.active_bend_length_mm else tr("active_bend"))
        result += tr("tool_length_virtual", length=length)
    return result


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
            tr("no_freecad_document_is_open_open_a_sheet_metal")
        )
    if not sel_list:
        raise SelectionWarning(
            tr("no_part_selected_select_a_sheet_metal_body_bend")
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
        msg = tr("could_not_load_the_part", error=exc)
        _error(msg + "\n" + traceback.format_exc())
        _log("load_part: exit (error, session preserved)")
        return "error", current_session, msg

    b = new_session.active_bend
    z = b.bend_zone
    msg = (
        tr("part_loaded_panels_bends_t_mm_inner_r_mm").format(
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
            tr("no_sheetmetal_unfold_object_unfold_was_found_create_an")
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
        tr("could_not_unambiguously_match_to_a_sheetmetal_unfold").format(
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
        self._form_ref = form
        self._attached = True
        QtCore, _QtGui, _QtWidgets = _qt()
        panel = self

        class LifetimeGuard(QtCore.QObject):
            def eventFilter(self, watched, event):
                # Hide/HideToParent is normal on minimise, tab changes,
                # overlay collapse and document activation. It is NOT close.
                if event.type() == QtCore.QEvent.DeferredDelete:
                    panel._detach()
                return False

        # FreeCAD may replace Tasks without calling reject()/closed().
        # Rescue reusable controls before their disposable parent is deleted.
        self._lifetime_guard = LifetimeGuard(workspace)
        form.installEventFilter(self._lifetime_guard)
        # TaskDialogPython can synchronously delete its TaskBox (rather than
        # post DeferredDelete). QWidget emits destroyed before deleting its
        # children. Rescue the reusable widgets without touching the dying
        # parent's layout in that callback.
        form.destroyed.connect(self._form_destroyed)
        workspace.destroyed.connect(self._owner_destroyed)

    def _owner_destroyed(self, *_args):
        # The native task may outlive its MDI owner. Do not call back into
        # the dying workspace or retain a live callback to its controls.
        self._attached = False
        self.workspace = None
        try:
            self._form_ref.setEnabled(False)
            self._form_ref.hide()
        except (RuntimeError, ReferenceError):
            pass

    def _form_destroyed(self, *_args):
        self._detach(destroying=True)

    def owns_active_dialog(self, control):
        """Use FreeCAD 1.1's native TaskDialog content, not mere activeDialog."""
        if not self._attached or self.workspace is None:
            return False
        form = self._form_ref
        if not self.workspace._qt_is_valid(form):
            return False
        try:
            active = control.activeTaskDialog()
            return active is not None and any(
                self.workspace._qt_is_valid(box) and
                (box is form or box.isAncestorOf(form))
                for box in active.getDialogContent()
            )
        except (AttributeError, RuntimeError, ReferenceError):
            return False

    def autoClosedOnTransactionChange(self):
        self._detach()

    def autoClosedOnDeletedDocument(self):
        self._detach()

    def getStandardButtons(self):
        # Native Close button: FreeCAD supplies the label, icon and layout.
        _QtCore, _QtGui, QtWidgets = _qt()
        close = QtWidgets.QDialogButtonBox.StandardButton.Close
        return int(getattr(close, "value", close))

    def isAllowedAlterDocument(self):
        return True

    def isAllowedAlterSelection(self):
        # SheetMetal's original unfold may be selected while Tasks is open.
        return True

    def isAllowedAlterView(self):
        return True

    def _detach(self, *, destroying=False):
        if not self._attached:
            return
        _desktop_debug_event("TASKS_CLOSE_DETACH", panel="SMBS_NativeTasksRoot")
        self._attached = False
        workspace = self.workspace
        self.workspace = None
        if workspace is None or not workspace._qt_is_valid(workspace):
            return
        # Disconnect the old owner callback and retire its event filter.
        # The workspace can survive many native task open/close cycles.
        try:
            workspace.destroyed.disconnect(self._owner_destroyed)
        except (RuntimeError, ReferenceError, TypeError):
            pass
        guard = self._lifetime_guard
        if workspace._qt_is_valid(guard):
            if not destroying and workspace._qt_is_valid(self._form_ref):
                self._form_ref.removeEventFilter(guard)
            guard.deleteLater()
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
            if not destroying:
                old_parent = child.parentWidget()
                old_layout = old_parent.layout() if old_parent is not None and workspace._qt_is_valid(old_parent) else None
                if old_layout is not None:
                    old_layout.removeWidget(child)
            child.setParent(workspace)
            child.hide()
        if getattr(workspace, "_native_task_panel", None) is self:
            workspace._native_task_panel = None
            workspace._task_section_label = None
            if workspace._qt_is_valid(workspace.show_task_panel_btn) and not getattr(workspace,"_closing",False):
                workspace.show_task_panel_btn.show()

    def closed(self):
        # FreeCAD can close Tasks externally when another edit begins.
        self._detach()

    def accept(self):
        return self.reject()

    def reject(self):
        # FreeCAD TaskView removes the dialog after a successful reject.
        # Avoid a second, re-entrant closeDialog during native teardown.
        self._detach()
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
    caption = workspace._task_page_titles.get(section, tr("tasks"))
    header = getattr(workspace, "_task_section_label", None)
    if header is not None:
        header.setTitle(caption)

    active = bool(Gui.Control.activeDialog())
    panel = getattr(workspace, "_native_task_panel", None)
    if panel is not None and active and panel.owns_active_dialog(Gui.Control):
        _desktop_debug_event(
            "TASKS_SWITCH_SECTION", section=str(caption), index=tab,
        )
        # FreeCAD Tasks already owns this form. Never call showDialog
        # twice or create an extra Qt window simply to switch SMBS pages.
        workspace.show_task_panel_btn.hide()
        Gui.Control.showTaskView()
        return panel
    if panel is not None and active:
        # Another native task owns the surface. Do not detach or close a
        # potentially suspended SMBS task while another document is active.
        workspace.status.setText(tr("finish_the_current_freecad_task_then_reopen_smbs"))
        return None
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
            tr("finish_the_current_freecad_task_then_reopen_smbs")
        )
        workspace.status.setText(message)
        workspace.show_task_panel_btn.show()
        _warn(message)
        return None

    form = QtWidgets.QWidget()
    form.setObjectName("SMBS_NativeTasksRoot")
    form.setWindowTitle("SMBS")
    layout = QtWidgets.QVBoxLayout(form)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    # FreeCAD creates its native collapsible TaskBox from form.windowTitle.
    # Use a normal Qt group box for the changing operator page title.
    section_box = QtWidgets.QGroupBox(caption, form)
    section_box.setObjectName("SMBS_TaskSection")
    section_layout = QtWidgets.QVBoxLayout(section_box)
    section_layout.addWidget(workspace._task_stack)
    layout.addWidget(section_box)
    workspace._task_section_label = section_box

    footer = QtWidgets.QFrame(form)
    footer.setObjectName("SMBS_TaskFooter")
    footer_layout = QtWidgets.QVBoxLayout(footer)
    footer_layout.setContentsMargins(OUTER_MARGIN, 0, OUTER_MARGIN, 0)
    footer_layout.setSpacing(SECTION_GAP)
    row = QtWidgets.QHBoxLayout()
    row.addWidget(workspace._task_status_chip)
    row.addStretch(1)
    row.addWidget(workspace._task_result_icons)
    row.addWidget(workspace._task_progress_caption)
    footer_layout.addLayout(row)
    footer_layout.addWidget(workspace.task_progress)
    workspace.status.setObjectName("SMBS_StatusLabel")
    workspace.status.setWordWrap(True)
    workspace.status.setMaximumHeight(16777215)
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
    workspace._native_task_panel = panel
    try:
        # This opens the actual native FreeCAD Tasks tab, not a new dock.
        native_dialog = Gui.Control.showDialog(panel)
        # FreeCAD 1.1 returns its native TaskDialog wrapper. A simulation is
        # not an edit transaction: visibility/undo changes must not close it.
        # Deleting its associated document does require teardown.
        if native_dialog is not None:
            native_dialog.setAutoCloseOnTransactionChange(False)
            native_dialog.setAutoCloseOnDeletedDocument(True)
        Gui.Control.showTaskView()
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
    # TaskView provides the scroll surface. Keep specialist forms in that
    # native layout rather than creating a second, nested scrollbar.
    page_layout.addWidget(widget)

    bottom = QtWidgets.QWidget(page)
    bottom_layout = QtWidgets.QHBoxLayout(bottom)
    bottom_layout.setContentsMargins(
        OUTER_MARGIN, SECTION_GAP // 2, OUTER_MARGIN, SECTION_GAP // 2
    )
    bottom_layout.addStretch(1)
    close_btn = style_action(QtWidgets.QPushButton(tr("close_section")))
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
    owns = panel.owns_active_dialog(Gui.Control)
    panel.closed()
    if owns:
        Gui.Control.closeDialog()
    _desktop_debug_event("TASKS_CLOSE_REQUESTED")


def _has_loaded_part():
    """Single prerequisite for commands following the Detail action."""
    workspace = get_workspace_widget()
    return bool(workspace is not None and workspace.session is not None)


class PressBrake2DCommand:
    def GetResources(self):
        return {
            "Pixmap": str(_smbs_icon_path("gui_simulation.svg")),
            "MenuText": tr("simulation"),
            "ToolTip": tr("open_the_coin3d_simulation_and_freecad_tasks_panel"),
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
            "Pixmap": str(_smbs_icon_path("gui_part.svg")),
            "MenuText": tr("part"),
            "ToolTip": tr("load_the_selected_sheetmetal_unfold"),
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
            message = tr("select_a_sheetmetal_unfold") + str(exc)
            App.Console.PrintWarning(message + "\\n")
            # Feedback must remain visible with FreeCAD's Report View hidden.
            _QtCore, _QtGui, QtWidgets = _qt()
            QtWidgets.QMessageBox.information(
                Gui.getMainWindow(), tr("smbs_part"), message
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
            App.Console.PrintWarning(tr("smbs_no_part_loaded_n"))
            return
        show_workspace_controls(workspace, tab=1)
        Gui.updateGui()


class ToolingTabCommand:
    def GetResources(self):
        return {
            "Pixmap": str(_smbs_icon_path("gui_tooling.svg")),
            "MenuText": tr("tooling_ab652d22"),
            "ToolTip": tr("open_simulator_tooling"),
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
            "Pixmap": str(_smbs_icon_path("gui_backgauge.svg")),
            "MenuText": tr("backgauge"),
            "ToolTip": tr("preview_the_fcstd_backgauge_position_against_the_flat_outline"),
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
            "Pixmap": str(_smbs_icon_path("gui_sequence_catalog.svg")),
            "MenuText": tr("sequences"),
            "ToolTip": tr("open_bend_sequences_saved_as_json"),
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
            "Pixmap": str(_smbs_icon_path("gui_manual.svg")),
            "MenuText": tr("sequence_and_checks"),
            "ToolTip": tr("set_the_bend_order_manually_simulate_or_check_collisions"),
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
        return {"Pixmap": str(_smbs_icon_path(self.icon)), "MenuText": tr(self.label),
                "ToolTip": tr(self.label)}

    def IsActive(self):
        return _has_loaded_part()

    def Activated(self):
        workspace = get_workspace_widget()
        if workspace is None or workspace.session is None:
            return
        getattr(workspace, self.method)()


class FastSearchCommand(_WorkspaceActionCommand):
    label = "fast_find_sequences_up_to_30_s"
    method = "_run_fast_sequence_search"
    icon = "gui_simulation.svg"


class BendInformationCommand(_WorkspaceActionCommand):
    label = "information"
    method = "_show_bend_information"
    icon = "gui_part.svg"


class BendInstructionsPdfCommand(_WorkspaceActionCommand):
    label = "bend_plan_pdf"
    method = "_save_bend_instructions_pdf"
    icon = "control_save.svg"


class MeshCheckCommand(_WorkspaceActionCommand):
    label = "Mesh 3D"
    method = "_open_mesh_menu"
    icon = "control_mesh.svg"


class SettingsCommand:
    """Open all simulation, machine and diagnostic settings."""

    def GetResources(self):
        return {
            "Pixmap": str(_smbs_icon_path("gui_settings.svg")),
            "MenuText": tr("settings"),
            "ToolTip": tr("bend_mode_speed_checks_tolerances_and_logging"),
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
                    tr("scroll_to_zoom_drag_with_the_middle_button_or")
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
                super().__init__(tr_message(message), workspace)
                self._workspace = workspace

            def setText(self, message):
                visible = tr_message(message)
                super().setText(visible)
                self.setToolTip(visible)
                if hasattr(self, "_workspace"):
                    self._workspace._on_task_status_change(visible)

        class TaskPhysicalResultLabel(QtWidgets.QLabel):
            """Mirror the actual validator output into the compact simulation card."""

            def __init__(self, workspace, message):
                super().__init__(tr_message(message), workspace)
                self._workspace = workspace

            def setText(self, message):
                visible = tr_message(message)
                super().setText(visible)
                self.setToolTip(visible)
                if hasattr(self, "_workspace"):
                    self._workspace._on_physical_result_change(visible)

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
                self._latest_mesh_collision = None
                self._mesh_collision_hits = ()
                self._mesh_collision_index = None
                self._mesh_collision_identity = None
                self._mesh_grouped = NativePreferences(QtCore).value("mesh/grouped_view",True,type=bool)
                self._mesh_sheet_transparency = 35
                self._mesh_tool_transparency = 55
                self._collision_inspection = None
                self._alternate_retract_inspection = None
                self._last_assisted_lift_inspection = None
                self._last_assisted_lift_report = None
                self._collision_display_active = False
                self._manual_3d_verdicts = {}
                self._manual_retract_verdicts = {}
                self._backgauge_settings = NativePreferences(QtCore)
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
                    self, tr("select_a_sheet_metal_part_in_freecad_and_click")
                )
                self.status.setWordWrap(True)
                root.addWidget(self.status)


                # Phase 5A: Tooling section (Import / Select / Align / Reset / Hide-Show / Library)
                self.tooling_group = QtWidgets.QGroupBox(tr("tooling_punch_and_die"))
                t_layout = QtWidgets.QVBoxLayout(self.tooling_group)
                t_layout.setContentsMargins(16, 16, 16, 16)
                t_layout.setSpacing(16)

                t_row1 = QtWidgets.QHBoxLayout()
                self.import_punch_btn = _button(QtWidgets, tr("import_punch"), self.import_punch_dialog, True)
                self.import_die_btn = _button(QtWidgets, tr("import_die"), self.import_die_dialog, True)
                self.tool_library_btn = _button(QtWidgets, tr("tooling_library"), self.open_full_tooling_library, True)
                for w in (self.import_punch_btn, self.import_die_btn, self.tool_library_btn):
                    t_row1.addWidget(w)
                t_layout.addLayout(t_row1)

                t_row2 = QtWidgets.QHBoxLayout()
                t_row2.addWidget(QtWidgets.QLabel(tr("punch_ba8192e8")))
                self.select_punch_combo = QtWidgets.QComboBox()
                self.select_punch_combo.currentIndexChanged.connect(self._on_punch_combo_changed)
                t_row2.addWidget(self.select_punch_combo, 1)
                t_layout.addLayout(t_row2)

                t_row3 = QtWidgets.QHBoxLayout()
                t_row3.addWidget(QtWidgets.QLabel(tr("die_f845e0f8")))
                self.select_die_combo = QtWidgets.QComboBox()
                self.select_die_combo.currentIndexChanged.connect(self._on_die_combo_changed)
                t_row3.addWidget(self.select_die_combo, 1)
                t_layout.addLayout(t_row3)

                # Development virtual tool length & controlled punch end-zone policy
                self.virtual_tool_len_checkbox = QtWidgets.QCheckBox(
                    tr("virtual_tool_length_equals_bend_length")
                )
                self.virtual_tool_len_checkbox.setChecked(False)
                self.virtual_tool_len_checkbox.setToolTip(
                    tr("sets_the_virtual_punch_and_die_length_to_the")
                )
                self.virtual_tool_len_checkbox.toggled.connect(self._on_virtual_tool_length_toggled)
                t_layout.addWidget(self.virtual_tool_len_checkbox)

                ez_row = QtWidgets.QHBoxLayout()
                ez_row.addWidget(QtWidgets.QLabel(tr("punch_end_zone_mm")))
                self.end_zone_len_spin = QtWidgets.QDoubleSpinBox()
                self.end_zone_len_spin.setRange(0.0, 200.0)
                self.end_zone_len_spin.setDecimals(1)
                self.end_zone_len_spin.setSingleStep(1.0)
                self.end_zone_len_spin.setValue(10.0)
                self.end_zone_len_spin.valueChanged.connect(self._on_end_zone_length_changed)
                ez_row.addWidget(self.end_zone_len_spin)
                t_layout.addLayout(ez_row)

                self.allow_end_zone_checkbox = QtWidgets.QCheckBox(
                    tr("conditional_punch_end_zone_allowance")
                )
                self.allow_end_zone_checkbox.setChecked(False)
                self.allow_end_zone_checkbox.setToolTip(
                    tr("allows_conditional_overlap_within_the_specified_regions_at_the")
                )
                self.allow_end_zone_checkbox.toggled.connect(self._on_allow_end_zone_toggled)
                t_layout.addWidget(self.allow_end_zone_checkbox)
                self.tooling_setup.end_zone_length_mm = float(self.end_zone_len_spin.value())
                self.tooling_setup.allow_end_zone_collision = bool(self.allow_end_zone_checkbox.isChecked())

                self.tooling_status = QtWidgets.QLabel(_tooling_status_text(self.tooling_setup))
                self.tooling_status.setWordWrap(True)
                t_layout.addWidget(self.tooling_status)
                self.physical_check_result = TaskPhysicalResultLabel(
                    self, tr("unverified_collision_check_has_not_been_run")
                )
                self.physical_check_result.setWordWrap(True)
                t_layout.addWidget(self.physical_check_result)
                root.addWidget(self.tooling_group)

                # Phase 5B: Press Cycle section (Bend Only / Full Cycle, Ram Position, Cycle Phase, Speed & Config)
                self.press_cycle_group = QtWidgets.QGroupBox(tr("simulation_mode"))
                pc_layout = QtWidgets.QVBoxLayout(self.press_cycle_group)
                pc_layout.setContentsMargins(0, 0, 0, 0)
                pc_layout.setSpacing(16)

                pc_row1 = QtWidgets.QGridLayout()
                pc_row1.setHorizontalSpacing(16)
                pc_row1.setVerticalSpacing(16)
                pc_row1.addWidget(QtWidgets.QLabel(tr("mode")), 0, 0)
                self.cycle_mode_combo = QtWidgets.QComboBox()
                self.cycle_mode_combo.addItem(tr("bend_only"), CycleExecutionMode.BEND_ONLY.value)
                self.cycle_mode_combo.addItem(tr("full_cycle"), CycleExecutionMode.FULL_CYCLE.value)
                self.cycle_mode_combo.setCurrentIndex(1)
                self.cycle_mode_combo.currentIndexChanged.connect(self._on_cycle_mode_combo_changed)
                pc_row1.addWidget(self.cycle_mode_combo, 0, 1)
                pc_row1.addWidget(QtWidgets.QLabel(tr("speed_s")), 1, 0)
                self.cycle_speed_spin = QtWidgets.QDoubleSpinBox()
                self.cycle_speed_spin.setRange(1.0, 720.0)
                self.cycle_speed_spin.setValue(self.cycle_config.bend_rate_deg_s)
                self.cycle_speed_spin.setSingleStep(15.0)
                self.cycle_speed_spin.valueChanged.connect(self._on_cycle_speed_changed)
                pc_row1.addWidget(self.cycle_speed_spin, 1, 1)

                self.cycle_config_btn = _button(QtWidgets, tr("cycle_settings_17d1b587"), self.open_cycle_config_dialog, True)
                pc_row1.addWidget(self.cycle_config_btn, 2, 1)
                pc_row1.setColumnStretch(0, 0)
                pc_row1.setColumnStretch(1, 1)
                pc_layout.addLayout(pc_row1)

                pc_row2 = QtWidgets.QVBoxLayout()
                self.cycle_phase_label = QtWidgets.QLabel(tr("cycle_phase_idle"))
                self.ram_position_label = QtWidgets.QLabel(
                    tr("ram_position_mm_stroke_mm",
                       height=self.tooling_setup.ram_z_mm, stroke=0.0)
                )
                pc_row2.addWidget(self.cycle_phase_label)
                pc_row2.addWidget(self.ram_position_label)
                pc_layout.addLayout(pc_row2)
                self.loading_orientation_status = QtWidgets.QLabel(
                    tr("operator_backgauge_feed_direction_pending")
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
                self.reset_btn = _playback_button(QtCore, QtWidgets, tr("reset"), self.reset, False)
                self.prev_step_btn = _playback_button(QtCore, QtWidgets, "|<<", self.previous_step, False)
                self.play_btn = _playback_button(
                    QtCore, QtWidgets, tr("play"), self._toggle_play_pause, False
                )
                self.play_btn.setToolTip(tr("play_pause_or_resume_the_selected_simulation_mode"))
                self.next_step_btn = _playback_button(QtCore, QtWidgets, ">>|", self.next_step, False)
                self.fit_btn = _playback_button(QtCore, QtWidgets, tr("fit"), self.fit, True)
                self.add_part_btn = _playback_button(QtCore, QtWidgets, "+", LoadPartCommand().Activated, True)
                self.add_part_btn.setObjectName("SMBS_AddPart")
                self.add_part_btn.setIcon(QtGui.QIcon(str(_smbs_icon_path("control_add.svg"))))
                self.add_part_btn.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
                buttons = (
                    self.add_part_btn, self.reset_btn, self.prev_step_btn, self.play_btn,
                    self.next_step_btn, self.fit_btn,
                )
                captions = (tr("add_the_selected_sheetmetal_unfold"), "Reset", "|<<", "Play / Pause", ">>|", "Fit")
                row.setColumnMinimumWidth(0, 0)
                for col, (widget, caption) in enumerate(zip(buttons, captions)):
                    # Native compact controls: one row, no separate labels and
                    # no duplicate Pause button. Text updates in the original
                    # _sync_playback_buttons() callback.
                    widget.setToolTip(caption)
                    # The native Qt style supplies the size hint.
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
                self.move_up_btn = _button(QtWidgets, tr("move_up_b7f3da21"), self.move_selected_up, False)
                self.move_down_btn = _button(QtWidgets, tr("move_down_89fe1ada"), self.move_selected_down, False)
                self.restore_order_btn = _button(QtWidgets, tr("restore_order_22643a41"), self.restore_order, False)
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
                    tr("step_ee899dd5"), tr("bend"), "180°", tr("target"), tr("current_angle"), tr("before_bend_mm"),
                    tr("gauge_s_mm"), tr("status"), tr("retract"), tr("prebend"),
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
                    tr("rotate_the_blank_180_around_the_vertical_axis_for"),
                )
                self.operations.setRootIsDecorated(False)
                self.operations.setHorizontalScrollBarPolicy(
                    QtCore.Qt.ScrollBarAsNeeded
                )
                self.operations.setObjectName("SMBS_Operations")
                self.operations.setToolTip(
                    tr("before_bend_gives_the_flat_pattern_distance_to_the")
                )
                self.operations.setAlternatingRowColors(True)
                self.operations.setMinimumHeight(220)
                self.operations.setToolTip(
                    self.operations.toolTip() + tr("drag_the_rows_after_adding_a_pre_bend_the")
                )
                self.operations.currentItemChanged.connect(self._on_operation_selected)
                self.operations.itemChanged.connect(self._on_operation_orientation_changed)
                self.operations.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
                self.operations.customContextMenuRequested.connect(
                    self._show_operation_orientation_menu
                )
                root.addWidget(self.operations)

                self.check_result = QtWidgets.QLabel()
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
                if len(entries) != 10:
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
                # Let the native QStyle determine the progress-bar height.
                self.task_progress.setRange(0, 100)
                self.task_progress.setValue(0)
                self._task_status_chip = QtWidgets.QLabel(tr("ready"), self)
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
                    button.setIcon(QtGui.QIcon(str(_smbs_icon_path(
                        "gui_simulation.svg" if key == "FAST" else "control_mesh.svg"
                    ))))
                    button.clicked.connect(
                        lambda _checked=False, name=key: self._show_task_result(name)
                    )
                    icons_layout.addWidget(button)
                    self._task_result_buttons[key] = button
                    self._set_task_result(key, "idle", tr("check_has_not_run_yet")
                                          if key == "Mesh" else tr("search_has_not_run_yet"))
                status_font = self._task_status_chip.font()
                status_font.setBold(True)
                self._task_status_chip.setFont(status_font)

                views = QtWidgets.QTabWidget(self)
                views.setObjectName("SMBS_ViewerTabs")
                views.setDocumentMode(True)
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
                        _smbs_icon_path("gui_part.svg")
                    )), "Coin3D")
                self._flat_view = Canvas(bend_click_callback=self._select_flat_bend)
                self._flat_view.setObjectName("SMBS_FlatPattern_ManualOrder")
                self._create_flat_scene()
                views.addTab(self._flat_view, QtGui.QIcon(str(
                    _smbs_icon_path("gui_sequence_catalog.svg")
                )), tr("flat_pattern_sequence"))
                views.currentChanged.connect(self._on_view_changed)

                class CurrentTaskStack(QtWidgets.QStackedWidget):
                    """Native scroll height is determined by the visible page."""
                    def sizeHint(self):
                        page = self.currentWidget()
                        return page.sizeHint() if page is not None else super().sizeHint()

                    def minimumSizeHint(self):
                        page = self.currentWidget()
                        return page.minimumSizeHint() if page is not None else super().minimumSizeHint()

                controls = CurrentTaskStack()
                controls.setObjectName("SMBS_TaskStack")
                controls.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                                       QtWidgets.QSizePolicy.Preferred)
                controls.currentChanged.connect(lambda *_: controls.updateGeometry())

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
                        QtWidgets.QLayout.SetDefaultConstraint
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
                # The native Tasks header already says "Simulation": no duplicate
                # section title or large diagnostic card above playback.
                playback = QtWidgets.QWidget(simulation)
                playback.setObjectName("SMBS_FixedPlayback")
                playback_layout = QtWidgets.QVBoxLayout(playback)
                playback_layout.setContentsMargins(0, 0, 0, 0)
                sim_layout.addWidget(playback)
                body = QtWidgets.QWidget()
                sim_body = QtWidgets.QVBoxLayout(body)
                sim_body.setContentsMargins(0, 0, 0, 0)
                # Same native TaskView scrolling as SheetMetal and CAM.
                sim_layout.addWidget(body)
                for btn in (
                    self.reset_btn, self.prev_step_btn, self.next_step_btn,
                    self.fit_btn,
                ):
                    style_action(btn, compact=True)
                style_action(self.play_btn, primary=True)
                insert(playback_layout, entries[5])  # Reset / Prev / Play-Pause / Next / Fit
                self.slider.setToolTip(
                    tr("drag_the_slider_to_preview_the_active_operation_between")
                )
                insert(playback_layout, entries[6])  # angle slider
                self._manual_preview_notice = QtWidgets.QLabel(
                    tr("manual_preview_collisions_unchecked")
                )
                self._manual_preview_notice.setWordWrap(True)
                self._manual_preview_notice.hide()
                sim_body.addWidget(self._manual_preview_notice)

                self._coin_frame_status = QtWidgets.QLabel()
                self._coin_frame_status.setWordWrap(True)
                sim_body.addWidget(self._coin_frame_status)
                self.machine_preview_checkbox = QtWidgets.QCheckBox(tr("show_machine"))
                self.machine_preview_checkbox.setChecked(True)
                self.machine_preview_checkbox.toggled.connect(lambda: self.draw())
                sim_body.addWidget(self.machine_preview_checkbox)
                self._page_simulation = scroll_task_page(
                    simulation, "SMBS_SimulationScroll"
                )
                order_card, order_body = card(tr("manual_bend_order_and_checks"))
                self.manual_order_input = QtWidgets.QLineEdit()
                self.manual_order_input.setObjectName("SMBS_ManualOrderInput")
                self.manual_order_input.setAccessibleName(tr("bend_sequence"))
                self.manual_order_input.setPlaceholderText(tr("order_for_example_2_1_3_4"))
                self.manual_order_input.setToolTip(
                    tr("enter_the_flat_pattern_bend_labels_such_as_2")
                )
                self.manual_order_input.returnPressed.connect(self._apply_typed_manual_order)
                order_body.addWidget(self.manual_order_input)
                select_lines = QtWidgets.QPushButton(tr("select_bend_lines_69c2093c"))
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
                    QtWidgets.QPushButton(tr("undo_line"))
                )
                self.manual_undo_btn.clicked.connect(self._undo_manual_bend)
                self.manual_clear_btn = style_action(
                    QtWidgets.QPushButton(tr("clear"))
                )
                self.manual_clear_btn.clicked.connect(self._clear_manual_order)
                undo_row.addWidget(self.manual_undo_btn)
                undo_row.addWidget(self.manual_clear_btn)
                order_body.addLayout(undo_row)
                self.saved_sequences_btn = style_action(
                    QtWidgets.QPushButton(tr("saved_sequences")), compact=True
                )
                self.saved_sequences_btn.setToolTip(
                    tr("load_a_manual_sequence_saved_in_this_fcstd_document")
                )
                self.saved_sequences_btn.clicked.connect(
                    self._select_saved_sequence
                )
                order_body.addWidget(self.saved_sequences_btn)
                self.auto_orientation_checkbox = QtWidgets.QCheckBox(
                    tr("select_workpiece_orientation_0_180")
                )
                self.auto_orientation_checkbox.setChecked(True)
                self.auto_orientation_checkbox.setToolTip(
                    tr("occ_can_try_the_opposite_orientation_for_automatically_placed")
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
                    QtWidgets.QPushButton(tr("simulate_without_checking")), compact=True
                )
                self.manual_simulate_btn.setToolTip(
                    tr("preview_the_manual_sequence_using_the_latest_valid_3d")
                )
                self.manual_simulate_btn.clicked.connect(
                    lambda: self.simulate_manual_order_unverified()
                )
                # Orientation evidence remains in the status and tooltips.
                self.manual_validate_3d_btn = style_action(
                    QtWidgets.QPushButton(tr("check_and_show")), primary=True
                )
                self.manual_validate_3d_btn.setObjectName(
                    "SMBS_ManualNative3DCheck"
                )
                self.manual_validate_3d_btn.setToolTip(
                    tr("check_selected_bends_with_the_fcstd_punch_and_die")
                )
                self.manual_validate_3d_btn.clicked.connect(
                    lambda: self._validate_export_manual_order(show_preview=True)
                )
                self.manual_lift_probe_btn = style_action(
                    QtWidgets.QPushButton(tr("check_occ_lift_and_show_movement")),
                    compact=True,
                )
                self.manual_lift_probe_btn.setObjectName("SMBS_ManualAssistedRetractProbe")
                self.manual_lift_probe_btn.setToolTip(
                    tr("available_after_a_confirmed_punch_collision_during_retract_tests")
                )
                self.manual_lift_probe_btn.clicked.connect(self._inspect_assisted_retract)
                self.manual_lift_probe_btn.setEnabled(False)
                self.manual_lift_preview_btn = style_action(
                    QtWidgets.QPushButton(tr("preview_assisted_lift_in_simulation")),
                    compact=True,
                )
                self.manual_lift_preview_btn.setObjectName("SMBS_ManualAssistedRetractPreview")
                self.manual_lift_preview_btn.setToolTip(
                    tr("play_the_full_cycle_and_preview_the_proposed_lift")
                )
                self.manual_lift_preview_btn.clicked.connect(self._play_assisted_retract_preview)
                self.manual_lift_preview_btn.setEnabled(False)
                self.manual_release_probe_btn = style_action(
                    QtWidgets.QPushButton(tr("check_lateral_withdrawal_with_occ")),
                    compact=True,
                )
                self.manual_release_probe_btn.setObjectName("SMBS_ManualAssistedReleaseProbe")
                self.manual_release_probe_btn.setToolTip(
                    tr("explore_lateral_withdrawal_in_both_machine_y_directions_after")
                )
                self.manual_release_probe_btn.clicked.connect(self._inspect_assisted_release)
                self.manual_release_probe_btn.setEnabled(False)
                self.manual_validate_3d_btn.setParent(self)
                self.manual_validate_3d_btn.hide()
                self.manual_lift_probe_btn.setParent(self)
                self.manual_lift_probe_btn.hide()
                self.manual_lift_preview_btn.setParent(self)
                self.manual_lift_preview_btn.hide()
                self.manual_release_probe_btn.setParent(self)
                self.manual_release_probe_btn.hide()
                self.manual_lift_probe_hint = QtWidgets.QLabel(
                    tr("assisted_lift_is_experimental_first_identify_a_punch_collision")
                )
                self.manual_lift_probe_hint.setWordWrap(True)
                self.manual_lift_probe_hint.hide()
                # Verification must have a visible result in the Manual
                # workspace, not a file-save dialog as its only feedback.
                self.manual_check_summary = QtWidgets.QLabel(
                    tr("select_bends_and_start_the_check")
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
                    tr("results_for_each_checked_bend_will_appear_here")
                )
                self.manual_check_details.hide()
                order_body.addWidget(self.manual_check_details)
                self.manual_show_collision_btn = style_action(
                    QtWidgets.QPushButton(tr("show_collision_in_3d"))
                )
                self.manual_show_collision_btn.setObjectName(
                    "SMBS_ShowNativeCollision"
                )
                self.manual_show_collision_btn.setToolTip(
                    tr("open_the_3d_viewer_at_the_collision_pose_red")
                )
                self.manual_show_collision_btn.clicked.connect(
                    self._show_first_collision_in_3d
                )
                self.manual_show_collision_btn.setEnabled(False)
                self.manual_show_collision_btn.hide()
                self.manual_exact_occ_btn = style_action(
                    QtWidgets.QPushButton(tr("open_exact_occ_overlap")),
                    compact=True,
                )
                self.manual_exact_occ_btn.setObjectName(
                    "SMBS_OpenExactNativeOCCCollision"
                )
                self.manual_exact_occ_btn.setToolTip(
                    tr("open_an_unsaved_freecad_document_with_the_actual_tools")
                )
                self.manual_exact_occ_btn.clicked.connect(
                    self._open_exact_collision_inspector
                )
                self.manual_exact_occ_btn.setEnabled(False)
                self.manual_exact_occ_btn.hide()
                order_body.addWidget(self.manual_show_collision_btn)
                order_body.addWidget(self.manual_exact_occ_btn)
                sim_body.addWidget(order_card, 1)
                # Keep the live sequence directly beneath the fixed controls.
                playback_layout.addWidget(self.operations, 1)
                self.fast_save_template_btn = style_action(
                    QtWidgets.QPushButton(tr("save_verified_bend_template")),
                    compact=True,
                )
                self.fast_save_template_btn.setObjectName(
                    "SMBS_SaveOperatorBendTemplate"
                )
                self.fast_save_template_btn.setToolTip(
                    tr("save_the_known_sequence_and_orientations_as_a_template")
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
                select_lines.setToolTip(tr("select_bend_lines_edit_order"))
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
                cancel_edit = QtWidgets.QPushButton(tr("cancel"))
                self.manual_undo_btn.setText(tr("back"))
                edit_layout.addWidget(cancel_edit)
                for button, text, hint in (
                    (self.move_up_btn, "↑", tr("move_up")),
                    (self.move_down_btn, "↓", tr("move_down")),
                    (self.restore_order_btn, "↺", tr("restore_order")),
                    (self.manual_undo_btn, "←", tr("undo_last_line")),
                    (cancel_edit, "✕", tr("cancel_editing")),
                ):
                    button.setText(text)
                    button.setToolTip(tr(hint))
                    button.setMinimumWidth(24)
                    button.setMaximumWidth(16777215)
                    button.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                         QtWidgets.QSizePolicy.Fixed)
                    edit_layout.setStretch(edit_layout.indexOf(button), 1)
                playback_layout.insertWidget(0, edit_controls)
                edit_controls.hide()
                playback_buttons = (self.reset_btn, self.prev_step_btn,
                                    self.play_btn, self.next_step_btn)

                def close_sequence_edit_controls():
                    # Only lightweight widget state, safe during FAST commit.
                    self._manual_selection_enabled = False
                    edit_controls.hide()
                    select_lines.setText("☷")
                    select_lines.setToolTip(tr("select_bend_lines_edit_order"))
                    for button in playback_buttons:
                        button.show()

                self._close_sequence_edit_controls = close_sequence_edit_controls

                def finish_sequence(*, cancel):
                    if self.session is not None and cancel:
                        self._apply_manual_order(self._sequence_before_edit)
                        self._manual_sequence_prefix = list(self._prefix_before_edit)
                        self._sync_manual_order_input()
                    close_sequence_edit_controls()
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
                        select_lines.setToolTip(tr("apply_bend_sequence"))
                        for button in playback_buttons:
                            button.hide()
                        self._update_operations_visual_state()

                select_lines.clicked.connect(toggle_sequence)
                cancel_edit.clicked.connect(lambda: finish_sequence(cancel=True))
                bottom_actions = QtWidgets.QHBoxLayout()
                self.mesh_show_collision_btn = style_action(
                    QtWidgets.QPushButton(tr("mesh_collisions")), compact=True,
                )
                self.mesh_show_collision_btn.setToolTip(
                    tr("show_the_next_confirmed_collision_return_to_the_standard")
                )
                self.mesh_show_collision_btn.clicked.connect(self._show_next_mesh_collision)
                self.mesh_show_collision_btn.hide()
                bottom_actions.addWidget(self.mesh_show_collision_btn)
                sim_layout.addLayout(bottom_actions)

                # RETRACT warning belongs to Settings, not Simulation.
                self.retract_warning_checkbox = QtWidgets.QCheckBox(
                    tr("retract_continue_2d_search_with_a_warning")
                )
                self.retract_warning_checkbox.setChecked(False)
                self.retract_warning_checkbox.setToolTip(
                    tr("diagnostic_only_approach_bending_bottom_and_backgauge_intersections_block")
                )

                tooling = QtWidgets.QWidget()
                td_layout = QtWidgets.QVBoxLayout(tooling)
                tooling_card, tooling_body = card(tr("tooling_and_machine"))
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
                tool_main_layout.addRow(tr("punch"), self.select_punch_combo)
                tool_main_layout.addRow(tr("die"), self.select_die_combo)
                # Keep diagnostics in the shared task status area, not
                # as an extra paragraph below the two selectors.
                self.tooling_status.setParent(self)
                self.tooling_status.hide()
                tooling_body.addWidget(tool_main)
                self.import_tooling_btn = style_action(QtWidgets.QPushButton(tr("import_1900fd2f")), compact=True)
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

                cycle_card, cycle_body = card(tr("simulation_mode"))
                self.press_cycle_group.setTitle("")
                self.press_cycle_group.setFlat(True)
                style_action(self.cycle_config_btn)
                insert(cycle_body, entries[3])  # Mode / Full Cycle / Speed
                # Only used by automatic search: deliberately hidden in ManualMode.
                self.retract_warning_checkbox.hide()
                settings_layout.addWidget(cycle_card)

                tool_opts_card, tool_opts_layout = card(tr("tooling_settings"))
                tool_opts_layout.addWidget(self.virtual_tool_len_checkbox)
                zone_row = QtWidgets.QGridLayout()
                zone_row.setHorizontalSpacing(SECTION_GAP)
                end_zone_caption = QtWidgets.QLabel(
                    tr("punch_end_zone_mm")
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
                preferences = NativePreferences(QtCore)
                self._operation_weights = {
                    key: float(preferences.value("operation_weights/" + key, default, type=float))
                    for key, default in OperationWeights().to_dict().items()
                }

                diagnostics_card, diagnostics_body = card(
                    tr("results_diagnostics")
                )
                self._simulation_diagnostics_summary = QtWidgets.QLabel(
                    tr("collision_check_has_not_run")
                )
                self._simulation_diagnostics_summary.setObjectName(
                    "SMBS_SectionCaption"
                )
                self._simulation_diagnostics_summary.setWordWrap(True)
                diagnostics_body.addWidget(self._simulation_diagnostics_summary)
                self._on_physical_result_change(
                    self.physical_check_result.text()
                )
                diagnostics_body.addWidget(self.physical_check_result)
                insert(diagnostics_body, entries[9])
                advanced_layout.addWidget(diagnostics_card)

                from smbs_core.kinematics2d.developer_debug_log import set_debug_log_enabled
                debug_log = QtWidgets.QCheckBox(tr("log_smbs_activity_and_freecad_crashes"))
                debug_log.setToolTip(str(_debug_desktop_directory() / "SMBS_debug_log.json"))
                debug_log.setChecked(preferences.value("debug_log/enabled", False, type=bool))

                def toggle_debug_log(enabled):
                    preferences.setValue("debug_log/enabled", enabled)
                    if not set_debug_log_enabled(enabled):
                        self.status.setText(tr("could_not_create_the_desktop_log"))

                debug_log.toggled.connect(toggle_debug_log)
                settings_layout.addWidget(debug_log)
                self.trace_checkbox = QtWidgets.QCheckBox(self)
                self.trace_checkbox.hide()
                self.trace_status = QtWidgets.QLabel(self)
                self.trace_status.hide()

                fcstd_card, fcstd_body = card(tr("smbs_data_in_the_fcstd_document"))
                self.fcstd_save_btn = style_action(
                    QtWidgets.QPushButton(tr("save_to_fcstd")), compact=True
                )
                self.fcstd_save_btn.setToolTip(
                    tr("embed_bend_order_settings_and_check_history_as_json")
                )
                self.fcstd_save_btn.clicked.connect(
                    self._save_embedded_smbs_json_clicked
                )
                self.fcstd_load_btn = style_action(
                    QtWidgets.QPushButton(tr("restore_from_fcstd")), compact=True
                )
                self.fcstd_load_btn.setToolTip(
                    tr("restore_saved_order_and_settings_historical_results_require_a")
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
                    tr("ctrl_s_embeds_the_json_after_a_part_is")
                )
                self.fcstd_note.setWordWrap(True)
                # Detailed save/restore feedback is already shown in the
                # persistent task footer status; avoid a second paragraph.
                self.fcstd_note.hide()
                settings_layout.addWidget(fcstd_card)
                advanced_toggle = style_action(
                    QtWidgets.QPushButton(tr("advanced")), compact=True
                )
                advanced_toggle.setCheckable(True)
                advanced_toggle.setChecked(False)
                advanced_toggle.setToolTip(
                    tr("tooling_parameters_additional_occ_checks_and_diagnostics")
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
                    (tr("tooling_ab652d22"), self._page_tooling),
                    (tr("simulation"), self._page_simulation),
                    (tr("settings"), self._page_settings),
                )
                self._task_page_titles = {}
                for name, page in page_specs:
                    controls.addWidget(page)
                    self._task_page_titles[page] = name
                root.addWidget(views, 1)
                self._viewer_tabs = views
                self._task_stack = controls
                self._native_task_panel = None
                self.show_task_panel_btn = QtWidgets.QPushButton(tr("show_controls"))
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
                    "Ready": "ready", "Running": "working",
                    "Warning": "warning", "Error": "error",
                    "Unverified": "unverified_5f496335",
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
                    self._task_progress_caption.setText(tr("working"))
                else:
                    self.task_progress.setRange(0, 100)
                    self._update_task_progress()
                self._on_task_status_change(self.status.text())

            def _set_task_result(self, key, state, summary, details="", findings=""):
                summary, details, findings = (tr_message(value) for value in (summary, details, findings))
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
                        "Mesh", "warning", tr("part_sequence_or_tooling_has_changed"),
                        tr("run_mesh_again_for_the_current_setup"),
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
                caption = tr("bends_5f815307").format(value)
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
                    (self.prev_step_btn, "control_step_back.svg"),
                    (self.next_step_btn, "control_step_forward.svg"),
                    (self.reset_btn, "control_reset.svg"),
                    (self.move_up_btn, "control_step_back.svg"),
                    (self.move_down_btn, "control_step_forward.svg"),
                    (self.saved_sequences_btn, "gui_sequence_catalog.svg"),
                    (self.manual_simulate_btn, "gui_simulation.svg"),
                    (self.manual_validate_3d_btn, "control_validate.svg"),
                    (self.manual_show_collision_btn, "control_collision.svg"),
                    (self.manual_exact_occ_btn, "control_collision.svg"),
                    (self.fast_save_template_btn, "control_save.svg"),
                    (self.fcstd_save_btn, "control_save.svg"),
                    (self.fcstd_load_btn, "control_add.svg"),
                    (self.mesh_show_collision_btn, "control_collision.svg"),
                    (self.import_tooling_btn, "gui_tooling.svg"),
                    (self.restore_order_btn, "control_reset.svg"),
                    (self.manual_undo_btn, "control_prev.svg"),
                    (self.manual_clear_btn, "control_reset.svg"),
                    (self.show_task_panel_btn, "gui_simulation.svg"),
                    (self.tool_library_btn, "tooling.svg"),
                    (self.import_punch_btn, "gui_tooling.svg"),
                    (self.import_die_btn, "gui_tooling.svg"),
                    (self.cycle_config_btn, "gui_backgauge.svg"),
                )
                for widget, icon_name in icon_pairs:
                    asset = _smbs_icon_path(icon_name)
                    if not asset.is_file():
                        raise FileNotFoundError(
                            "SMBS GUI icon is missing: " + str(asset)
                        )
                    widget.setIcon(QtGui.QIcon(str(asset)))

            def _on_trace_checkbox_toggled(self, enabled):
                if enabled:
                    if self.session is not None:
                        self._start_motion_trace(manual=True)
                    else:
                        self.trace_status.setText(
                            tr("load_a_part_first_recording_will_start_with_play").format(self._trace_directory)
                        )
                else:
                    self._finish_motion_trace("RECORDING_DISABLED")

            def _choose_trace_directory(self):
                if self.motion_trace is not None and self.motion_trace.is_open:
                    self.trace_status.setText(
                        tr("finish_the_current_recording_before_changing_folders")
                    )
                    return
                chosen = QtWidgets.QFileDialog.getExistingDirectory(
                    self, tr("select_a_folder_for_jsonl_and_json_recording"),
                    str(self._trace_directory.parent)
                )
                if not chosen:
                    return
                try:
                    target = Path(chosen).expanduser().resolve()
                    target.mkdir(parents=True, exist_ok=True)
                    self._trace_directory = target
                    self.trace_status.setText(
                        tr("recording_folder_click_start_jsonl_recording").format(
                            self._trace_directory
                        )
                    )
                except OSError as exc:
                    self.trace_status.setText(
                        tr("could_not_access_directory").format(chosen, exc)
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
                    message = tr("could_not_open_recording_folder").format(
                        self._trace_directory, exc
                    )
                    self.trace_status.setText(message)
                    _warn(message)

            def _start_motion_trace(self, manual=False):
                if self.session is None:
                    if manual:
                        self.trace_status.setText(
                            tr("load_a_part_before_starting_the_recording")
                        )
                    return
                if not manual and not self.trace_checkbox.isChecked():
                    return
                if self.motion_trace is not None and self.motion_trace.is_open:
                    self.trace_status.setText(
                        tr("recording_continues").format(self.motion_trace.path)
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
                        tr("version_2d_build_recording_file").format(
                            project_version, source_digest[:12],
                            self._trace_directory.resolve(), filename
                        )
                    )
                    _log("Motion trace START: {}".format(filename))
                except Exception as exc:
                    message = tr("could_not_create_jsonl_in").format(
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
                        tr("no_active_recording_folder").format(
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
                        tr("version_build_recording_completed_jsonl_json").format(
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
                    message = tr("could_not_create_json_report").format(exc)
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
                        self.trace_status.setText(tr("trace_error", error=exc))
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
                if not self._playback_alive():
                    return False
                viewer = self._preview_3d
                if self.session is None:
                    viewer.clear_preview()
                    self._coin_frame_status.setText(tr("load_a_sheetmetal_unfold"))
                    return True
                if not viewer._available:
                    message = tr("coin3d_unavailable_check_pivy_coin_in_freecad_python")
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
                mesh_hit = None
                mesh_group = ()
                if (self._collision_display_active and not show_proposal
                        and self._mesh_collision_index is not None):
                    if self._mesh_collision_identity == self._mesh_preview_identity():
                        from smbs_core.kinematics2d.collision_groups import representative_hit
                        groups = self._mesh_view_groups()
                        if 0 <= self._mesh_collision_index < len(groups):
                            mesh_group = groups[self._mesh_collision_index]
                            mesh_hit = representative_hit(mesh_group)
                        else:
                            self._mesh_collision_index = None
                            self._collision_display_active = False
                    else:
                        self._mesh_collision_index = None
                        self._collision_display_active = False
                        self.mesh_show_collision_btn.hide()
                inspection = (self._collision_inspection
                              if self._collision_display_active and not show_proposal and mesh_hit is None
                              else None)
                try:
                    viewer.attach_document(self._smbs_document_name)
                    highlighted = ()
                    bounds = []
                    inspected_length = None
                    current_verdict = None
                    if mesh_hit:
                        pose = mesh_hit["sampled_machine_pose"]
                        bend_id = mesh_hit["bend"]
                        ram_z = mesh_hit["ram_z_mm"]
                        fold_state = FoldState(self.session.fold_state.graph, self.session.fold_state.fixed_panel)
                        for previous, angle in pose["angles_deg"].items():
                            fold_state.set_angle(previous, angle)
                        placement = PlacementTransform3D(
                            rotation=tuple(tuple(row) for row in pose["rotation"]),
                            translation=tuple(pose["translation"]),
                        )
                        inspected_length = math.dist(*fold_state.bends[bend_id].axis_xyz)
                        from smbs_core.kinematics2d.collision_groups import projected_zones, component_colors
                        zones = projected_zones(self.session.fold_state.graph,self.session.fold_state.fixed_panel,
                                                mesh_group,mesh_hit,grouped=self._mesh_grouped)
                        bounds = [zone for zone in zones if zone["corners_xyz"]]
                        highlighted = component_colors(zones,all_components=not self._mesh_grouped)
                        confirmed = sum(not h.get("conditional") for h in mesh_group)
                        message = (f"Mesh {self._mesh_collision_index+1}/{len(self._mesh_view_groups())} · "
                                   + tr("mesh_group_summary",operation=mesh_hit["operation"],confirmed=confirmed,
                                        conditional=len(mesh_group)-confirmed))
                        if self._mesh_grouped:
                            message += " · " + tr("mesh_group_reference",phase=mesh_hit["phase"],
                                                   angle=round(float(mesh_hit["angle_deg"]),2))
                        else:
                            message += f" · {mesh_hit['phase']} · {mesh_hit['tool']}" + tr(
                                "mm_4c0af55e",component=mesh_hit["component"],volume=mesh_hit["occ_overlap_volume_mm3"])
                        if any(zone["component_only"] for zone in zones):
                            message += " · " + tr("mesh_component_only")
                    elif inspection:
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
                        expected = compute_machine_pose(
                            fold_state, bend_id,
                            phase="NATIVE_SAMPLE",
                            support_plane_zm=float(actual["support_plane_zm"]),
                            reverse_axis=bool(self._effective_loading_orientation_map()[bend_id]),
                            # The native recorded pose was computed without
                            # the ergonomic rear-frame override; compare
                            # against the same authoritative frame here.
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
                        message = tr("z_mm_occ_collision", bend=bend_id, phase=actual['phase'], z=ram_z)
                    else:
                        fold_state = self.session.fold_state
                        placement = self._current_preview_machine_placement()
                        ram_z = float(self.tooling_setup.ram_z_mm)
                        current_verdict = self._manual_3d_verdicts.get(bend_id)
                        message = (
                            "OCC: {} (SAMPLED)".format(current_verdict) if current_verdict
                            else tr("simulation_without_a_verified_occ_result")
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
                    viewer.set_sheet_faces(faces, highlighted=highlighted,
                                           transparency=self._mesh_sheet_transparency/100 if mesh_hit else 0.0)
                    tooling = self.tooling_setup
                    stations, contact, gauge_note = self._coin_backgauge_positions(
                        self.session.bend_order.index(bend_id), at_collision=bool(inspection or mesh_hit),
                    )
                    messages = viewer.update_machine(
                        self.tool_library.library_root, tooling, ram_z,
                        frame_visible=self.machine_preview_checkbox.isChecked(),
                        gauge_stations=stations, gauge_contact=contact,
                    )
                    section_preview = proposal[1] if show_proposal else None
                    if show_proposal:
                        message = tr("segment_layout_preview", bend=bend_id)
                        if not tooling.visible:
                            tooling = replace(tooling, visible=True)
                    if section_preview is None and not inspection and not mesh_hit:
                        from freecad_smbs.ui.tool_segment_display import suggested_sections
                        section_preview = suggested_sections(self, math.dist(*fold_state.bends[bend_id].axis_xyz))
                        if section_preview:
                            message += " · " + tr("suggested_segment_layout_visual_only")
                    viewer.update_tools(
                        tooling, ram_z, active_bend_length_mm=(
                            inspected_length if inspected_length is not None
                            else math.dist(*fold_state.bends[bend_id].axis_xyz)
                        ),
                        section_preview=section_preview,
                        transparency=self._mesh_tool_transparency/100 if mesh_hit else 0.0,
                    )
                    viewer.set_collision_boxes(bounds)
                    status = " · ".join(filter(None, (message, *messages, gauge_note)))
                    if self._coin_frame_status.text() != status:
                        self._coin_frame_status.setText(status)
                    previous = self._last_3d_frame_at
                    self._last_3d_frame_at = now
                    self._last_3d_rendered_bend = bend_id
                    self._coin_last_error = None
                    if not self._playback_alive():
                        return False
                    if not inspection and not mesh_hit and not show_proposal:
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
                    if not self._playback_alive():
                        _desktop_debug_event("COIN3D_LIFETIME_ERROR", bend_id=bend_id,
                                             error=str(exc), traceback=traceback.format_exc(),
                                             qt_state=self._qt_debug_state())
                        return False
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
                """Select available local tools; an empty library is supported."""
                if self.tooling_setup.punch is not None and self.tooling_setup.die is not None:
                    return
                try:
                    tools = sorted(self.tool_library.list_tools(), key=lambda tool: tool.tool_id)
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
                self.select_punch_combo.addItem(tr("none_selected_cb3ce06b"), None)
                for idx, p in enumerate(punches, 1):
                    self.select_punch_combo.addItem(f"{p.name} ({p.validation_status.value})", p.tool_id)
                    if cur_p == p.tool_id:
                        self.select_punch_combo.setCurrentIndex(idx)
                self.select_punch_combo.blockSignals(False)

                self.select_die_combo.blockSignals(True)
                self.select_die_combo.clear()
                self.select_die_combo.addItem(tr("none_selected_3435e2d6"), None)
                for idx, d in enumerate(dies, 1):
                    self.select_die_combo.addItem(f"{d.name} ({d.validation_status.value})", d.tool_id)
                    if cur_d == d.tool_id:
                        self.select_die_combo.setCurrentIndex(idx)
                self.select_die_combo.blockSignals(False)

            def _show_bend_information(self):
                if self.session is None:
                    self.status.setText(tr("load_a_flat_pattern_first"))
                    return
                if not self._apply_typed_manual_order():
                    return
                from freecad_smbs.ui.bend_information import show_bend_information

                show_bend_information(self, embed_task_dialog)

            def _save_bend_instructions_pdf(self):
                if self.session is None or self._task_progress_busy:
                    return
                if activate_task_dialog(self, "pdf_export"):
                    return
                if not self._apply_typed_manual_order():
                    return
                from freecad_smbs.ui.pdf_export_task import show_pdf_export_task
                show_pdf_export_task(self, embed_task_dialog)

            def _tool_assembly_inputs(self, *, require_pair=True):
                from smbs_core.geometry_backend.io.clamp_validator import load_clamp_config
                from smbs_core.kinematics2d.segment_assembly_planner import read_inventory

                punch, die = self.tooling_setup.punch, self.tooling_setup.die
                if require_pair and (punch is None or die is None):
                    raise ValueError(tr("select_a_punch_and_die_first"))

                def stock(tool):
                    if tool is None:
                        return ()
                    path = Path(tool.source_file)
                    if path.suffix.lower() != ".fcstd" or not path.is_file():
                        return ()
                    return read_inventory(path)

                p_inventory, d_inventory = stock(punch), stock(die)
                if require_pair and (not p_inventory or not d_inventory):
                    raise ValueError(tr("set_punch_and_die_inventory_using_segments_and_quantities"))
                clamp_config = (
                    self._project_clamp_config
                    if self._project_clamp_config is not None
                    else load_clamp_config(self.tool_library.library_root)
                )
                return p_inventory, d_inventory, clamp_config

            def _tool_assembly_order(self):
                return tuple(self._manual_sequence_prefix) or self.session.bend_order

            def _calculate_common_tool_assembly(self, order):
                from smbs_core.kinematics2d.segment_assembly_planner import plan_pair_for_bend
                from smbs_core.kinematics2d.tooling_assembly import ToolAssembly

                p_inventory, d_inventory, cfg = self._tool_assembly_inputs()
                if not cfg.centres_mm:
                    raise ValueError(tr("set_clamp_centres_or_their_count_in_the_clamps"))
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
                self.physical_check_result.setText(tr("tooling_changed_check_the_current_setup"))
                self._manual_preview_notice.setText(tr("manual_preview_collisions_unchecked"))
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
                        _warn(tr("simulation_segment_layout", error=exc))
                    self._simulation_layouts[length] = sections or None
                return self._simulation_layouts[length]

            def _on_punch_orientation_changed(self, punch):
                selected = self.tooling_setup.punch
                if selected is None or selected.tool_id != punch.tool_id:
                    return
                if self.timer.isActive() or self.playing:
                    self.pause()
                # Segment positions and lengths do not change when the tool turns.
                self.tooling_setup.punch = punch
                self._last_manual_sequence_report = None
                self._manual_3d_verdicts.clear()
                self._manual_retract_verdicts.clear()
                self._conditional_bends_status.clear()
                self._latest_mesh_collision = None
                self._mesh_collision_hits = ()
                self._mesh_collision_index = None
                self._mesh_collision_identity = None
                self.mesh_show_collision_btn.hide()
                self._clear_manual_check_feedback()
                self._sync_tooling_to_active_bend()
                self.draw()

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
                    self.status.setText(tr("stock_or_clamps_changed_calculate_the_layout_again"))
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
                    self.status.setText(tr("load_a_part_before_calculating_a_layout"))
                    return
                if self._task_progress_busy or self.timer.isActive() or self.playing:
                    self.status.setText(tr("stop_the_simulation_or_wait_for_the_check_to"))
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
                    stock = ", ".join(tr("mm_b6bfc67c", length=length, count=count) for length, count in counts.items())
                    lines = [tr("mm", label=label, total=sum(lengths), stock=stock)]
                    if clamp_spans:
                        center = (spans[0][0] + spans[-1][1]) / 2.0
                        lines.append(tr("punch_center_at_x_mm", center=center))
                    support = punch_segment_supports(spans, clamp_spans)
                    for index, ((lo, hi), length, contacts) in enumerate(zip(spans, lengths, support), 1):
                        entry = tr("mm_x", index=index, length=length, lo=lo, hi=hi)
                        if contacts:
                            overlaps = ", ".join(tr("no_mm", number=number, overlap=overlap)
                                                 for number, overlap in contacts)
                            entry += tr("supported_by_clamps", overlaps=overlaps)
                        lines.append(entry)
                    return lines

                def describe_shortfall(required, punch_length):
                    missing = required - punch_length
                    if missing > 1e-6:
                        return [tr("punch_is_mm_shorter_than_the_bend_line_occ", missing=missing)]
                    return []

                def spans_for(plan, kind):
                    return tuple((item["start_x_mm"], item["end_x_mm"])
                                 for item in plan.get(kind + "_segments", ()))

                def describe(plan):
                    punch_plan = plan.get("punch_preview", plan)
                    lines = []
                    if "punch_preview" in plan:
                        if not d_inventory:
                            lines.append(tr("set_the_selected_die_inventory_to_calculate_its_segments"))
                        else:
                            lines.append(tr("no_suitable_set_found_in_the_die_inventory"))
                    status = punch_plan["status"]
                    if status == "NO_INVENTORY_COMBINATION":
                        reason = (tr("the_punch_inventory_cannot_provide_the_required_length")
                                  if p_inventory else tr("set_the_selected_punch_inventory"))
                        return lines + [f"  {reason}"]
                    if status == "ASSEMBLY_SEARCH_LIMIT":
                        return lines + [tr("punch_segment_search_limit_reached")]
                    if status != "ASSEMBLY_CANDIDATE_UNVERIFIED":
                        return lines + [tr("no_punch_configuration_supported_by_the_specified_clamps_was")]
                    clamps = punch_plan["clamps"]
                    zones = clamp_spans_for_centres(clamps["centers_mm"], clamps["width_mm"])
                    lines.extend(describe_sections(tr("punch"), spans_for(punch_plan, "punch"), zones))
                    if spans_for(plan, "die"):
                        lines.extend(describe_sections(tr("die"), spans_for(plan, "die")))
                    lines.extend(describe_shortfall(plan["required_mm"], punch_plan["punch_total_mm"]))
                    centres = ", ".join(f"{x:+.3f}" for x in clamps["centers_mm"])
                    lines.append(tr("clamp_centers_along_x_mm", centres=centres))
                    if clamps["proposed_reposition"]:
                        lines.append(tr("suggested_clamp_positions_verify_them_against_the_machine"))
                    return lines

                punch, die = self.tooling_setup.punch, self.tooling_setup.die
                titles = [
                    tr("punch_380adcf7", tool=punch.tool_id if punch else '—'),
                    tr("die_e6fa66a4", tool=die.tool_id if die else '—'),
                    tr("clamps_mm_along_x_clearance_from_mm", width=cfg.width_mm, gap=cfg.gap_between_mm),
                    tr("x_0_at_machine_center_segments_are_listed_from"),
                ]
                if nominal_clamps:
                    titles.append(tr("a_cad_crossbeam_row_is_shown_for_calculation_set"))
                lines = [tr("segment_layout_by_bend"), ""]
                for bend_id, plan in reports:
                    lines.append(tr("bend_line_mm", bend=bend_id, length=plan['required_mm']))
                    lines.extend(describe(plan))
                    lines.append("")
                per_bend = "\n".join(lines)

                self._close_tool_assembly_report()
                dialog = QtWidgets.QDialog(self)
                layout = QtWidgets.QVBoxLayout(dialog)
                bend_picker = QtWidgets.QComboBox()
                bend_picker.setAccessibleName(tr("bend_for_section_preview"))
                for bend_id, plan in reports:
                    bend_picker.addItem(tr("mm_df291b2a", bend=bend_id, length=plan['required_mm']), bend_id)
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
                apply_btn = QtWidgets.QPushButton(tr("apply_common_layout"))
                apply_btn.setObjectName("SMBS_ApplyCommonToolAssembly")
                apply_btn.setToolTip(tr("use_one_layout_sized_for_the_longest_bend_in"))
                layout.addWidget(apply_btn)
                clear_btn = QtWidgets.QPushButton(tr("clear_common_layout"))
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
                        feedback.setText(tr("set_clamp_positions_or_the_clamp_count_for_the"))
                    elif sections["punch"]:
                        lengths = " + ".join(f"{hi - lo:g}" for lo, hi in sections["punch"])
                        feedback.setText(tr("punch_mm_in_coin3d_apply_the_shared_set_for", bend=bend_id, lengths=lengths))
                    else:
                        feedback.setText(tr("no_suitable_segments_for_preview", bend=bend_id))
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
                        header.extend(["", tr("shared_set_active_in_coin3d_and_occ_mm", length=assembly.bend_length_mm)])
                        header.extend(describe_sections(
                            tr("punch"), assembly.punch_spans_xm, assembly.clamp_spans_xm,
                        ))
                        header.extend(describe_sections(tr("die"), assembly.die_spans_xm))
                        header.extend(describe_shortfall(
                            assembly.bend_length_mm,
                            sum(hi - lo for lo, hi in assembly.punch_spans_xm),
                        ))
                        centres = ", ".join(f"{(lo + hi) / 2:+.3f}" for lo, hi in assembly.clamp_spans_xm)
                        header.append(tr("clamp_centers_along_x_mm", centres=centres))
                    edit.setPlainText("\n".join(header) + "\n\n" + per_bend)

                def apply_common():
                    if self._task_progress_busy or self.timer.isActive() or self.playing:
                        feedback.setText(tr("stop_the_simulation_or_wait_for_the_check_to"))
                        return
                    try:
                        order = self._tool_assembly_order()
                        assembly = self._calculate_common_tool_assembly(order)
                        self._verify_tool_assembly_geometry(assembly)
                    except Exception as exc:
                        feedback.setText(tr_message(exc))
                        return
                    end_preview()
                    self._apply_tool_assembly(assembly)
                    _desktop_debug_event(
                        "TOOL_ASSEMBLY_APPLIED", bend_order=order,
                        punch_sections=assembly.punch_spans_xm, die_sections=assembly.die_spans_xm,
                        clamp_zones=assembly.clamp_spans_xm,
                    )
                    feedback.setText(tr("common_layout_applied_run_mesh_to_check_for_collisions"))
                    update_report()

                def clear_common():
                    end_preview()
                    self._apply_tool_assembly(None)
                    feedback.setText(tr("original_tooling_restored"))
                    update_report()

                update_report()
                apply_btn.clicked.connect(apply_common)
                clear_btn.clicked.connect(clear_common)
                embed_task_dialog(self, dialog, tr("tool_layout"), "tool_assembly")
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
                """Confirm an END_ZONE_ONLY punch collision in the selected UI language.
                Return True when the operator accepts the warning, False on cancellation.
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
                msg_box.setWindowTitle(tr("punch_end_zone_overlap_conditional"))
                msg_box.setText(
                    tr("the_part_overlaps_the_punch_end_zone_a_punch")
                )
                msg_box.setInformativeText(
                    tr("operation_bend_punch_side_intersection_volume_mm_punch_end").format(
                        bend_id, side_str, vol_mm3, ez_mm
                    )
                )
                btn_continue = msg_box.addButton(
                    tr("continue_with_warning"), QtWidgets.QMessageBox.AcceptRole
                )
                msg_box.addButton(tr("cancel_555ad1c0"), QtWidgets.QMessageBox.RejectRole)
                msg_box.exec()
                return msg_box.clickedButton() == btn_continue

            def open_backgauge_dialog(self):
                from freecad_smbs.commands.backgauge_dialog import (
                    show_backgauge_settings,
                )
                show_backgauge_settings(self)

            def _selected_operation_weights(self):
                from smbs_core.kinematics2d.operation_cost import OperationWeights
                return OperationWeights(**self._operation_weights)

            def _save_operator_bend_template(self):
                """Explicitly record an operator-known full plan for later FAST."""
                from smbs_core.kinematics2d.fast_bend_templates import (
                    store_operator_template, BendTemplateError,
                )
                if (self.session is None
                        or self._task_progress_busy
                        or self.playing
                        or self.timer.isActive()):
                    self.status.setText(
                        tr("stop_the_simulation_and_load_a_part_before_saving")
                    )
                    return
                controller = self.session.controller
                steps = tuple(controller.operation_steps)
                if not steps:
                    return
                title, accepted = QtWidgets.QInputDialog.getText(
                    self,
                    tr("save_bending_plan"),
                    tr("name_of_the_verified_bending_plan"),
                )
                if not accepted or not title.strip():
                    return
                reply = QtWidgets.QMessageBox.question(
                    self, tr("confirm_bending_plan"),
                    tr("do_you_already_know_the_correct_bend_sequence_and"),
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No,
                )
                if reply != QtWidgets.QMessageBox.Yes:
                    return
                reverse = {}
                for step in steps:
                    reverse[step.operation_id] = bool(
                        self._manual_operation_orientation_overrides.get(
                            step.operation_id,
                            self._manual_orientation_overrides.get(
                                step.bend_id, False
                            )
                        )
                    )
                tooling_info = {
                    "punch":str(getattr(
                        getattr(self.tooling_setup,"punch",None),
                        "tool_id",""
                    )),
                    "die":str(getattr(
                        getattr(self.tooling_setup,"die",None),
                        "tool_id",""
                    )),
                }
                try:
                    stored = store_operator_template(
                        self.session.fold_state, steps,
                        getattr(self.session.panel_build,
                                "outline_vertices_xy", ()),
                        self.session.panel_vertices_xy,
                        tuple(step.operation_id for step in steps),
                        reverse, title=title.strip(),
                        tooling=tooling_info,
                    )
                except (BendTemplateError, OSError, ValueError) as exc:
                    self.status.setText(tr("template_not_saved") + tr_message(exc))
                    _desktop_debug_event(
                        "FAST_TEMPLATE_SAVE_ERROR",
                        error=type(exc).__name__ + ":" + str(exc),
                    )
                    return
                self.status.setText(
                    tr("plan_saved_fast_will_suggest_it_when_the_geometry").format(
                        stored["title"]
                    )
                )
                _desktop_debug_event(
                    "FAST_TEMPLATE_SAVED", title=stored["title"],
                    template_id=stored["template_id"],
                    shape_sha256=stored["shape_sha256"],
                    path=stored["path"],
                )

            def _run_fast_sequence_search(self):
                """Opt-in bounded shortlist; NEVER claim 3D clearance."""
                import FreeCADGui as Gui
                if self.session is None:
                    self.status.setText(tr("fast_load_a_sheetmetal_unfold_first"))
                    return
                if (self._task_progress_busy or self.playing or self.timer.isActive()
                        or not self.session.can_reorder()):
                    self.status.setText(
                        tr("fast_stop_and_reset_the_simulation_before_changing_or")
                    )
                    return
                existing = getattr(self, "_fast_result_dialog", None)
                if existing is not None and self._qt_is_valid(existing):
                    existing.raise_()
                    existing.activateWindow()
                    return
                self._set_task_result("FAST", "running", tr("searching_for_a_sequence"))
                from smbs_core.kinematics2d.fast_bfm_beam_search import (
                    search_fast_candidates, FastSearchError,
                )
                controller = self.session.controller
                operations = tuple(controller.operation_steps)
                pinned = {}
                for step in operations:
                    if step.operation_id in self._fast_advisory_orientation_keys:
                        # FAST-generated checkbox values are not user locks.
                        continue
                    if step.operation_id in self._manual_operation_orientation_overrides:
                        pinned[step.operation_id] = self._manual_operation_orientation_overrides[
                            step.operation_id
                        ]
                    elif step.bend_id in self._manual_orientation_overrides:
                        pinned[step.operation_id] = self._manual_orientation_overrides[
                            step.bend_id
                        ]
                # An operator-recorded recipe is a high-priority search
                # SEED only for the exact canonical geometry. It never
                # bypasses the independent selected-tool Stage B pass.
                template_hits = ()
                try:
                    from smbs_core.kinematics2d.fast_bend_templates import (
                        find_operator_templates, BendTemplateError,
                    )
                    template_hits = find_operator_templates(
                        self.session.fold_state, operations,
                        getattr(self.session.panel_build,
                                "outline_vertices_xy", ()),
                        self.session.panel_vertices_xy,
                    )
                except (BendTemplateError, OSError, ValueError) as exc:
                    _desktop_debug_event(
                        "FAST_TEMPLATE_MATCH_SKIPPED",
                        reason=type(exc).__name__ + ":" + str(exc),
                    )
                # Never override operator-locked 0/180 poses.
                template_hits = tuple(
                    hit for hit in template_hits
                    if all(hit.candidate.orientations().get(k) == v
                           for k,v in pinned.items())
                )
                _desktop_debug_event(
                    "FAST_TEMPLATE_MATCH",
                    matched_count=len(template_hits),
                    titles=[hit.title for hit in template_hits],
                    evidence="GEOMETRY_MATCH_REQUIRES_NEW_2D_3D_CHECKS",
                )
                try:
                    report = search_fast_candidates(
                        self.session.fold_state, operations,
                        fixed_orientations=pinned,
                        panel_vertices_xy=self.session.panel_vertices_xy,
                        weights=self._selected_operation_weights().to_dict(),
                        screen_tool_center_axis=True,
                        axis_clearance_mm=1.0,
                        tooling_setup=self.tooling_setup,
                        backgauge_model=self.backgauge_model,
                        limit_seconds=30.0,
                        beam_width=32,
                        max_candidates=5,
                    )
                except (FastSearchError, ValueError, TypeError) as exc:
                    self.status.setText(tr("fast_search_failed", error=exc))
                    self._set_task_result("FAST", "error", tr("search_failed"), str(exc))
                    _desktop_debug_event("FAST_SEARCH_REJECTED", reason=str(exc))
                    return
                self._last_fast_candidates_report = report
                _desktop_debug_event(
                    "FAST_SEARCH_RESULT", report=report.to_dict()
                )
                _desktop_debug_event(
                    "FAST_PROXY_TOOL_SUMMARY",
                    available=report.proxy_tool_available,
                    tool_ids={
                        "punch": str(getattr(
                            getattr(self.tooling_setup,"punch",None),
                            "tool_id","",
                        )),
                        "die": str(getattr(
                            getattr(self.tooling_setup,"die",None),
                            "tool_id","",
                        )),
                    },
                    checked_transitions=report.proxy_tool_checks,
                    penalized_transitions=report.proxy_tool_penalized,
                    findings=list(report.proxy_tool_findings),
                    scope="SIMPLIFIED_SELECTED_2D_PROFILES_SOFT_ONLY",
                )
                count = len(report.candidates)
                fast_details = tr("search_took_s_use_mesh_to_check_for_3d", seconds=report.elapsed_seconds)
                if count or template_hits:
                    self._set_task_result(
                        "FAST", "done", tr("sequences_found_templates", count=count, templates=len(template_hits)),
                        fast_details + "\n" + tr("select_a_sequence_in_the_fast_window"),
                    )
                else:
                    self._set_task_result(
                        "FAST", "warning", tr("search_finished_no_sequences_found"), fast_details,
                    )
                if not report.candidates and not template_hits:
                    self.status.setText(
                        tr("fast_no_candidates_found_in_s_this_does_not", seconds=report.elapsed_seconds)
                    )
                    return

                # Parent the transient chooser to FreeCAD's main window,
                # not to a Tasks widget managed by OverlayManager on macOS.
                # open() avoids a nested QDialog.exec() event loop.
                dialog = QtWidgets.QDialog(Gui.getMainWindow())
                dialog.setObjectName("SMBS_FAST_ResultsDialog")
                dialog.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
                dialog.setWindowTitle(tr("fast_candidate_sequences_unverified"))
                dialog.setMinimumWidth(610)
                dialog.setMinimumHeight(300)
                layout = QtWidgets.QVBoxLayout(dialog)
                hint = QtWidgets.QLabel(tr(
                    "fast_s_states_transitions_2d_axis_checks_penalized_inconclusive",
                    seconds=report.elapsed_seconds, states=report.explored_states,
                    transitions=report.expanded_transitions, checks=report.axis_screen_checks,
                    penalized=report.axis_screen_penalized, unknown=report.axis_screen_unknown,
                ))
                hint.setWordWrap(True)
                layout.addWidget(hint)
                # Use a native sortable QTableWidget, not a flattened
                # sentence per candidate. Column 0 stores the original
                # candidate index independently of visual sorting.
                choices = QtWidgets.QTableWidget(dialog)
                choices.setObjectName("SMBS_FastCandidateTable")
                choices.setColumnCount(11)
                choices.setHorizontalHeaderLabels((
                    "№", "2D", tr("cost"), tr("2d_bends"), tr("start"),
                    tr("first_bend"), "180°", tr("axis"), tr("2d_penalty"),
                    tr("sequence"), tr("reason"),
                ))
                choices.setSelectionBehavior(
                    QtWidgets.QAbstractItemView.SelectRows
                )
                choices.setSelectionMode(
                    QtWidgets.QAbstractItemView.SingleSelection
                )
                choices.setEditTriggers(
                    QtWidgets.QAbstractItemView.NoEditTriggers
                )
                choices.setAlternatingRowColors(True)
                choices.verticalHeader().setVisible(False)
                choices.setWordWrap(False)
                choices.horizontalHeader().setStretchLastSection(True)
                for column, width in enumerate(
                        (48, 48, 90, 85, 110, 72, 115, 100, 85, 340, 250)):
                    choices.setColumnWidth(column, width)
                choices.setMinimumHeight(310)
                dialog.setMinimumWidth(1010)
                dialog.setMinimumHeight(470)

                class _FastSortableItem(QtWidgets.QTableWidgetItem):
                    """Numeric UserRole makes '23,55' sort before '1593'."""
                    def __lt__(self, other):
                        left = self.data(QtCore.Qt.UserRole)
                        right = other.data(QtCore.Qt.UserRole)
                        if (isinstance(left, (float, int))
                                and isinstance(right, (float, int))):
                            return left < right
                        return super().__lt__(other)

                def selected_source_index():
                    row = choices.currentRow()
                    item = choices.item(row, 0) if row >= 0 else None
                    if item is None:
                        return -1
                    value = item.data(int(QtCore.Qt.UserRole) + 1)
                    return int(value) if value is not None else -1

                all_candidates = []
                candidate_sources = []
                known_keys = set()
                current_tools = {
                    "punch":str(getattr(
                        getattr(self.tooling_setup,"punch",None),
                        "tool_id",""
                    )),
                    "die":str(getattr(
                        getattr(self.tooling_setup,"die",None),
                        "tool_id",""
                    )),
                }
                for hit in template_hits:
                    candidate = hit.candidate
                    key = (
                        candidate.operation_ids,
                        candidate.orientation_by_operation,
                    )
                    if key in known_keys:
                        continue
                    known_keys.add(key)
                    all_candidates.append(candidate)
                    changed_tools = any(
                        hit.source_tooling.get(kind)
                        and hit.source_tooling[kind] != current_tools[kind]
                        for kind in ("punch","die")
                    )
                    candidate_sources.append(
                        "template: " + (tr("template") if tr_message(hit.title, language="en") == "Template" else hit.title) +
                        (" (different tooling)" if changed_tools else "")
                    )
                for candidate in report.candidates:
                    key = (
                        candidate.operation_ids,
                        candidate.orientation_by_operation,
                    )
                    if key in known_keys:
                        continue
                    known_keys.add(key)
                    all_candidates.append(candidate)
                    candidate_sources.append("FAST")
                self._last_fast_shortlist_screen_report = None
                screen_cache = {}
                forced_sides_done = [False]
                feedback_rounds = [0]
                ranked_once = [False]
                show_rejected = QtWidgets.QCheckBox(
                    tr("show_candidates_excluded_by_the_coarse_2d_check"),
                    dialog,
                )
                show_rejected.setObjectName(
                    "SMBS_FastShowCoarseRejected"
                )
                show_rejected.setChecked(False)
                coarse_status = QtWidgets.QLabel(dialog)
                coarse_status.setObjectName("SMBS_FastCoarseGateStatus")
                coarse_status.setWordWrap(True)

                def populate_fast_choices(screen_report=None):
                    previous = selected_source_index()
                    previous_sort_column = (
                        choices.horizontalHeader().sortIndicatorSection()
                        if choices.isSortingEnabled() else 0
                    )
                    previous_sort_order = (
                        choices.horizontalHeader().sortIndicatorOrder()
                        if choices.isSortingEnabled()
                        else QtCore.Qt.AscendingOrder
                    )
                    order = (
                        screen_report.ranked_indices
                        if screen_report is not None
                        else tuple(range(len(all_candidates)))
                    )
                    if screen_report is not None and not show_rejected.isChecked():
                        from smbs_core.kinematics2d.fast_collision_gate import (
                            coarse_tool_gate,
                        )
                        blocked = set(
                            coarse_tool_gate(screen_report).forbidden_transitions
                        )
                        order = tuple(
                            index for index in order
                            if (candidate_sources[index].startswith("template:")
                                or screen_report.checks[index].failed_transition
                                not in blocked)
                        )
                    choices.setSortingEnabled(False)
                    choices.setRowCount(len(order))
                    if screen_report is None:
                        coarse_status.setText(
                            tr("punch_and_die_have_not_been_checked_yet")
                        )
                    elif not order and not show_rejected.isChecked():
                        coarse_status.setText(
                            tr("every_candidate_had_a_possible_coarse_2d_obstruction_review")
                        )
                    else:
                        coarse_status.setText(
                            tr("showing_of_candidates_screening_is_preliminary_3d_not_checked").format(
                                len(order), len(all_candidates)
                            )
                        )
                    for row, source_index in enumerate(order):
                        candidate = all_candidates[source_index]
                        check = (
                            screen_report.checks[source_index]
                            if screen_report is not None else None
                        )
                        status = check.status if check is not None else "NOT_CHECKED"
                        # These symbols refer only to SAMPLED 2D evidence;
                        # even green does NOT certify real machine clearance.
                        if status.startswith("SAMPLED_2D_TOOLS"):
                            marker, marker_rank = "✅", 0
                            verdict = (
                                tr("no_intersections_found_in_the_selected_2d_sections_this")
                            )
                        elif status in (
                                "POTENTIAL_2D_INTERFERENCE",
                                "STATIC_TOOLING_BLOCK"):
                            marker, marker_rank = "❌", 2
                            verdict = (
                                tr("preliminary_obstacle_possible_collision_or_tooling_mismatch_this_does")
                            )
                        else:
                            marker, marker_rank = "⚠️", 1
                            verdict = (
                                tr("the_check_is_incomplete_or_exceeded_its_budget_neither")
                            )
                        reversed_ids = [
                            op_id for op_id, reverse
                            in candidate.orientation_by_operation if reverse
                        ]
                        axis_label = (
                            "{:.0f}".format(candidate.axis_penalty)
                            if candidate_sources[source_index] == "FAST"
                            else "—"
                        )
                        is_template = candidate_sources[
                            source_index
                        ].startswith("template:")
                        if is_template:
                            verdict += tr("ntemplate_cost_was_not_calculated_the_saved_plan_has")
                        details = (
                            verdict + "\\n" + tr("status_156979c7") + status +
                            tr("npunch_die_profiles_approximate_2d").format(
                                candidate.proxy_tool_penalty
                            ) +
                            (tr("nreason") + check.first_issue
                             if check and check.first_issue else "") +
                            tr("n3d_not_checked")
                        )
                        values = (
                            str(row+1),
                            marker,
                            "—" if is_template else "{:.2f}".format(
                                candidate.estimated_cost
                            ).replace(".", ","),
                            "{}/{}".format(
                                check.clear_prefix_steps if check else 0,
                                len(candidate.operation_ids)
                            ),
                            (candidate_sources[source_index].replace("template: ", tr("template_e69f50ad"), 1)
                             .replace(" (different tooling)", tr("different_tooling")) if is_template
                             else candidate_sources[source_index]),
                            candidate.operation_ids[0],
                            ", ".join(reversed_ids) if reversed_ids else "—",
                            axis_label,
                            "{:.0f}".format(
                                candidate.feedback_penalty +
                                candidate.proxy_tool_penalty
                            ),
                            " → ".join(candidate.operation_ids),
                            check.first_issue if check and check.first_issue
                            else "—",
                        )
                        keys = (
                            row+1,
                            marker_rank,
                            (float("inf") if is_template
                             else float(candidate.estimated_cost)),
                            (check.clear_prefix_steps if check else 0),
                            candidate_sources[source_index],
                            candidate.operation_ids[0],
                            len(reversed_ids),
                            (float(candidate.axis_penalty)
                             if candidate_sources[source_index] == "FAST"
                             else -1.0),
                            float(
                                candidate.feedback_penalty +
                                candidate.proxy_tool_penalty
                            ),
                            values[9],
                            values[10],
                        )
                        for column, (value, sort_key) in enumerate(zip(values, keys)):
                            item = _FastSortableItem(value)
                            item.setData(QtCore.Qt.UserRole, sort_key)
                            item.setToolTip(details)
                            if column == 0:
                                item.setData(
                                    int(QtCore.Qt.UserRole) + 1, source_index
                                )
                            choices.setItem(row, column, item)
                    choices.setSortingEnabled(True)
                    choices.sortItems(
                        previous_sort_column, previous_sort_order
                    )
                    if screen_report is not None and not ranked_once[0]:
                        # Initial row was a provisional FAST default.
                        desired = order[0] if order else -1
                        ranked_once[0] = True
                    else:
                        desired = previous if previous >= 0 else (
                            order[0] if order else -1
                        )
                    for row in range(choices.rowCount()):
                        cell = choices.item(row, 0)
                        if (cell is not None
                                and cell.data(int(QtCore.Qt.UserRole) + 1) == desired):
                            choices.selectRow(row)
                            break
                    choices.resizeRowsToContents()

                legend = QtWidgets.QLabel(
                    tr("sampled_2d_sections_clear_unchecked_time_limit_possible_2d")
                )
                legend.setWordWrap(True)
                layout.addWidget(legend)
                layout.addWidget(coarse_status)
                layout.addWidget(show_rejected)
                show_rejected.toggled.connect(
                    lambda *_: populate_fast_choices(
                        self._last_fast_shortlist_screen_report
                    )
                )
                populate_fast_choices()
                layout.addWidget(choices)
                screen_button = QtWidgets.QPushButton(
                    tr("check_again_find_more_sequences"), dialog
                )
                screen_button.setObjectName("SMBS_FastShortlist2DScreen")
                layout.addWidget(screen_button)

                def log_template_2d_replay(stage, current_screen):
                    # Diagnostic only. An operator-recorded template is
                    # NEVER assumed to have passed machine collision tests.
                    findings = []
                    for idx, source in enumerate(candidate_sources):
                        if (not source.startswith("template:")
                                or idx >= len(current_screen.checks)):
                            continue
                        template_candidate = all_candidates[idx]
                        check = current_screen.checks[idx]
                        findings.append({
                            "candidate_index": idx,
                            "source": source,
                            "operation_ids": list(
                                template_candidate.operation_ids
                            ),
                            "orientation_by_operation": (
                                template_candidate.orientations()
                            ),
                            "2d_status": check.status,
                            "sampled_clear_prefix": (
                                check.clear_prefix_steps
                            ),
                            "total_operations": len(
                                template_candidate.operation_ids
                            ),
                            "first_issue": check.first_issue,
                            "failed_transition": (
                                list(check.failed_transition)
                                if check.failed_transition else None
                            ),
                            "evidence": (
                                "OPERATOR_RECIPE_2D_ADVISORY_3D_UNVERIFIED"
                            ),
                        })
                    if findings:
                        _desktop_debug_event(
                            "FAST_TEMPLATE_2D_REPLAY",
                            stage=stage,
                            current_tooling=dict(current_tools),
                            findings=findings,
                        )

                def check_fast_shortlist_2d():
                    # Start after QDialog.open(): users see FAST candidates
                    # first, while the optional Stage B ranking is separate.
                    if (not self._qt_is_valid(dialog)
                            or not self._playback_alive()
                            or self._fast_result_dialog is not dialog):
                        return
                    if (self.session is None or
                            self.session.controller is not controller or
                            tuple(controller.operation_steps) != operations):
                        hint.setText(
                            tr("2d_the_operation_list_changed_run_fast_again")
                        )
                        return
                    from smbs_core.kinematics2d.fast_shortlist_screening import (
                        screen_fast_shortlist,
                    )
                    screen_button.setEnabled(False)
                    postprocess_started = time.perf_counter()
                    _desktop_debug_event(
                        "FAST_SHORTLIST_2D_STARTED",
                        candidate_count=len(all_candidates),
                        operation_count=len(operations),
                        budget_seconds=3.0,
                    )
                    hint.setText(
                        tr("fast_search_finished_a_separate_preliminary_2d_check_is")
                    )
                    try:
                        screen = screen_fast_shortlist(
                            self.session.fold_state, operations,
                            tuple(all_candidates), self.session.panel_vertices_xy,
                            self.tooling_setup,
                            backgauge_model=self.backgauge_model,
                            budget_seconds=3.0,
                            transition_cache=screen_cache,
                        )
                        _desktop_debug_event(
                            "FAST_SHORTLIST_2D_SCREEN_INITIAL",
                            report=screen.to_dict(),
                        )
                        log_template_2d_replay("INITIAL", screen)
                        side_report = None
                        if not forced_sides_done[0]:
                            from smbs_core.kinematics2d.fast_multiside_feedback import (
                                search_all_outer_sides,
                            )
                            # The complete unfolded outer contour is authoritative.
                            # Panel hulls / BBoxes are not real exterior sides.
                            outer_outline = getattr(
                                self.session.panel_build,
                                "outline_vertices_xy", (),
                            )
                            outer_cad_edges = getattr(
                                self.session.panel_build,
                                "outline_edge_polylines_xy", (),
                            )
                            side_report = search_all_outer_sides(
                                self.session.fold_state, operations,
                                tuple(all_candidates), outer_outline,
                                self.session.panel_vertices_xy,
                                outer_edge_polylines_xy=outer_cad_edges,
                                fixed_orientations=pinned,
                                weights=self._selected_operation_weights().to_dict(),
                                backgauge_model=self.backgauge_model,
                                screen=screen,
                                tooling_setup=self.tooling_setup,
                                budget_seconds=2.5,
                            )
                            forced_sides_done[0] = True
                            _desktop_debug_event(
                                "FAST_FORCED_ALL_SIDES_RESULT",
                                report=side_report.to_dict(),
                            )
                            for found in side_report.new_candidates:
                                all_candidates.append(found.candidate)
                                side_display = {
                                    "LEFT": tr("left"),
                                    "RIGHT": tr("right"),
                                    "TOP": tr("above"),
                                    "BOTTOM": tr("below"),
                                }.get(
                                    found.side_name,
                                    found.side_name.replace("SIDE_", tr("side")),
                                )
                                candidate_sources.append(
                                    side_display + (
                                        "" if found.axis_aligned
                                        else tr("approximate_orientation")
                                    )
                                )
                            if side_report.new_candidates:
                                # Give novel side starts the first chance to
                                # use remaining Stage B sample budget.
                                side_begin = len(all_candidates) - len(
                                    side_report.new_candidates
                                )
                                screen = screen_fast_shortlist(
                                    self.session.fold_state, operations,
                                    tuple(all_candidates),
                                    self.session.panel_vertices_xy,
                                    self.tooling_setup,
                                    backgauge_model=self.backgauge_model,
                                    budget_seconds=3.0,
                                    transition_cache=screen_cache,
                                    priority_candidate_indices=range(
                                        side_begin, len(all_candidates)
                                    ),
                                )
                        # Real selected-tool profiles have now been sampled
                        # by Stage B. Before generating yet more variants,
                        # restart FAST under STRICT *state-specific* 2D
                        # punch/die penetration exclusions. This never
                        # forbids a whole bend or claims physical 3D proof.
                        from smbs_core.kinematics2d.fast_collision_gate import (
                            rerun_fast_avoiding_tool_collisions,
                        )
                        coarse_rerun = rerun_fast_avoiding_tool_collisions(
                            self.session.fold_state, operations,
                            tuple(all_candidates),
                            self.session.panel_vertices_xy, screen,
                            weights=self._selected_operation_weights().to_dict(),
                            backgauge_model=self.backgauge_model,
                            fixed_orientations=pinned,
                            tooling_setup=self.tooling_setup,
                            limit_seconds=1.5,
                            max_candidates=8,
                        )
                        _desktop_debug_event(
                            "FAST_COARSE_TOOL_RERUN",
                            report=coarse_rerun.to_dict(),
                        )
                        if coarse_rerun.candidates:
                            coarse_begin = len(all_candidates)
                            for candidate in coarse_rerun.candidates:
                                all_candidates.append(candidate)
                                candidate_sources.append(
                                    tr("without_known_2d_intersections")
                                )
                            screen = screen_fast_shortlist(
                                self.session.fold_state, operations,
                                tuple(all_candidates),
                                self.session.panel_vertices_xy,
                                self.tooling_setup,
                                backgauge_model=self.backgauge_model,
                                budget_seconds=3.0,
                                transition_cache=screen_cache,
                                priority_candidate_indices=range(
                                    coarse_begin, len(all_candidates)
                                ),
                            )
                        # Next, retry the problematic bend with opposite
                        # 0/180 orientations, preserving manual pins.
                        # At most three rounds per result dialog.
                        from smbs_core.kinematics2d.fast_collision_gate import (
                            coarse_tool_gate,
                        )
                        current_gate = coarse_tool_gate(screen)
                        _desktop_debug_event(
                            "FAST_COARSE_TOOL_GATE_INITIAL",
                            report=current_gate.to_dict(),
                        )
                        feedback_report = None
                        # NO_TOOLING and wholly indeterminate sample passes
                        # provide no collision evidence. Do not consume
                        # limited feedback search rounds on those cases.
                        has_feedback = any(
                            check.failed_transition is not None
                            for check in screen.checks
                        )
                        if feedback_rounds[0] < 3 and has_feedback:
                            from smbs_core.kinematics2d.fast_multiside_feedback import (
                                search_feedback_recovery,
                            )
                            feedback_report = search_feedback_recovery(
                                self.session.fold_state, operations,
                                tuple(all_candidates),
                                self.session.panel_vertices_xy,
                                screen,
                                coarse_gate=current_gate,
                                fixed_orientations=pinned,
                                weights=self._selected_operation_weights().to_dict(),
                                tooling_setup=self.tooling_setup,
                                backgauge_model=self.backgauge_model,
                                budget_seconds=1.5,
                                max_cases=8,
                            )
                            feedback_rounds[0] += 1
                            _desktop_debug_event(
                                "FAST_FEEDBACK_RECOVERY_RESULT",
                                round=feedback_rounds[0],
                                report=feedback_report.to_dict(),
                            )
                            if feedback_report.new_candidates:
                                feedback_begin = len(all_candidates)
                                for found in feedback_report.new_candidates:
                                    all_candidates.append(found.candidate)
                                    flipped_ids = found.side_name.replace(
                                        "FLIP_", ""
                                    ).split("+")
                                    candidate_flags = (
                                        found.candidate.orientations()
                                    )
                                    candidate_sources.append(
                                        tr("change") + ", ".join(
                                            operation + " → " +
                                            ("180°" if candidate_flags.get(
                                                operation, False
                                            ) else "0°")
                                            for operation in flipped_ids
                                        )
                                    )
                                screen = screen_fast_shortlist(
                                    self.session.fold_state, operations,
                                    tuple(all_candidates),
                                    self.session.panel_vertices_xy,
                                    self.tooling_setup,
                                    backgauge_model=self.backgauge_model,
                                    budget_seconds=3.0,
                                    transition_cache=screen_cache,
                                    priority_candidate_indices=range(
                                        feedback_begin, len(all_candidates)
                                    ),
                                )
                        final_gate = coarse_tool_gate(screen)
                        _desktop_debug_event(
                            "FAST_COARSE_TOOL_GATE_FINAL",
                            report=final_gate.to_dict(),
                            excluded_rows=sum(
                                item.failed_transition in set(
                                    final_gate.forbidden_transitions
                                ) for item in screen.checks
                            ),
                        )
                        # The Stage-B shortlist can detect B01/P11 even
                        # when the FAST beam's best proxy candidate was clear.
                        # Diagnose those exact failed transitions in all four
                        # legacy 2.5D presentations, without accepting a
                        # reversed signed punch face as a feasible setup.
                        try:
                            from smbs_core.kinematics2d.fast_proxy_tool_screen import (
                                diagnose_shortlist_obstructions_25d,
                            )
                            orientation_study = diagnose_shortlist_obstructions_25d(
                                self.session.fold_state, self.session.panel_vertices_xy,
                                operations, screen, self.tooling_setup,
                                max_transitions=5, budget_seconds=1.5,
                            )
                            _desktop_debug_event(
                                "FAST_25D_ORIENTATION_STAGE_B_DIAGNOSTIC",
                                report=orientation_study,
                            )
                        except Exception as study_exc:
                            _desktop_debug_event(
                                "FAST_25D_ORIENTATION_STAGE_B_ERROR",
                                error=type(study_exc).__name__ + ":" + str(study_exc),
                                traceback=traceback.format_exc(),
                            )
                        self._last_fast_shortlist_screen_report = screen
                        postprocess_elapsed = (
                            time.perf_counter() - postprocess_started
                        )
                        _desktop_debug_event(
                            "FAST_SHORTLIST_2D_SCREEN", report=screen.to_dict()
                        )
                        log_template_2d_replay("FINAL", screen)
                        clear = sum(entry.status.startswith("SAMPLED_2D_TOOLS") for entry in screen.checks)
                        blocked = sum(entry.status in ("POTENTIAL_2D_INTERFERENCE", "STATIC_TOOLING_BLOCK")
                                      for entry in screen.checks)
                        self._set_task_result(
                            "FAST", "warning" if blocked or clear < len(all_candidates) else "done",
                            tr("preliminary_2d_check_finished_sequences", count=len(all_candidates)),
                            tr("no_issues_found_in_2d_obstructions_inconclusive",
                               clear=clear, blocked=blocked, unknown=len(all_candidates) - clear - blocked)
                            + "\n" + fast_details,
                        )
                        _desktop_debug_event(
                            "FAST_POSTPROCESS_SUMMARY",
                            fast_seconds=report.elapsed_seconds,
                            additional_seconds=postprocess_elapsed,
                            total_seconds=report.elapsed_seconds +
                                          postprocess_elapsed,
                            candidate_count=len(all_candidates),
                            preliminary_clear=sum(
                                entry.status.startswith("SAMPLED_2D_TOOLS")
                                for entry in screen.checks
                            ),
                            preliminary_obstructions=sum(
                                entry.status in (
                                    "POTENTIAL_2D_INTERFERENCE",
                                    "STATIC_TOOLING_BLOCK")
                                for entry in screen.checks
                            ),
                            unverified=sum(
                                not entry.status.startswith("SAMPLED_2D_TOOLS")
                                and entry.status not in (
                                    "POTENTIAL_2D_INTERFERENCE",
                                    "STATIC_TOOLING_BLOCK")
                                for entry in screen.checks
                            ),
                        )
                        if not self._qt_is_valid(choices):
                            return
                        populate_fast_choices(screen)
                        clear = sum(
                            entry.status.startswith("SAMPLED_2D_TOOLS")
                            for entry in screen.checks
                        )
                        interferences = sum(
                            entry.status == "POTENTIAL_2D_INTERFERENCE"
                            for entry in screen.checks
                        )
                        hint.setText(
                            tr("fast_s_additional_stages_s_last_2d_s_probes")
                            .format(
                                report.elapsed_seconds, postprocess_elapsed,
                                screen.elapsed_seconds,
                                screen.actual_probes, screen.cache_hits,
                                clear, interferences,
                                len(side_report.side_findings) if side_report is not None
                                else tr("unchanged"),
                                sum(item.get("status") not in (
                                    "BUDGET_EXCEEDED", "REUSED_EQUIVALENT_START",
                                    "NO_FIRST_BEND_AVAILABLE")
                                    for item in side_report.side_findings)
                                if side_report is not None else tr("unchanged"),
                                sum(item.get("status") == "REUSED_EQUIVALENT_START"
                                    for item in side_report.side_findings)
                                if side_report is not None else tr("unchanged"),
                                sum(item.get("status") == "BUDGET_EXCEEDED"
                                    for item in side_report.side_findings)
                                if side_report is not None else tr("unchanged"),
                                len(coarse_rerun.candidates),
                                (len(feedback_report.new_candidates)
                                 if feedback_report is not None else 0),
                                len(final_gate.forbidden_transitions),
                                sum(
                                    item.failed_transition in set(
                                        final_gate.forbidden_transitions
                                    ) for item in screen.checks
                                ),
                                len(all_candidates),
                                choices.rowCount(),
                            )
                        )
                    except Exception as exc:
                        _desktop_debug_event(
                            "FAST_SHORTLIST_2D_ERROR",
                            error=type(exc).__name__ + ":" + str(exc),
                            traceback=traceback.format_exc(),
                        )
                        if self._playback_alive():
                            self._set_task_result("FAST", "warning", tr("the_2d_check_failed"), str(exc))
                        if self._qt_is_valid(hint):
                            hint.setText(
                                tr("2d_check_unavailable_fast_candidates_remain_unverified")
                                .format(str(exc))
                            )
                    finally:
                        if self._qt_is_valid(screen_button):
                            screen_button.setEnabled(True)

                screen_button.clicked.connect(check_fast_shortlist_2d)
                buttons = QtWidgets.QDialogButtonBox(
                    QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
                    dialog,
                )
                buttons.button(QtWidgets.QDialogButtonBox.Ok).setText(
                    tr("apply_unverified_sequence")
                )
                buttons.button(QtWidgets.QDialogButtonBox.Cancel).setText(tr("close"))
                buttons.accepted.connect(dialog.accept)
                buttons.rejected.connect(dialog.reject)
                layout.addWidget(buttons)

                def on_finished(result):
                    # A sorted row is NOT its original candidate
                    # index. Read stable index from column 0 UserRole + 1
                    # before Qt destroys the nonblocking result dialog.
                    index = (
                        selected_source_index()
                        if self._qt_is_valid(choices) else -1
                    )
                    selected_item = (
                        choices.item(choices.currentRow(), 0)
                        if self._qt_is_valid(choices)
                        and choices.currentRow() >= 0 else None
                    )
                    visible_number = (
                        int(selected_item.text())
                        if selected_item is not None else index + 1
                    )
                    self._fast_result_dialog = None
                    _desktop_debug_event(
                        "FAST_RESULT_DIALOG_FINISHED",
                        accepted=bool(result == QtWidgets.QDialog.Accepted),
                        selected_index=index,
                        workspace_alive=self._playback_alive(),
                    )
                    if (result != QtWidgets.QDialog.Accepted
                            or not 0 <= index < len(all_candidates)):
                        return
                    selected = all_candidates[index]
                    # Apply AFTER the native dialog closes and FreeCAD
                    # finishes its overlay/task bookkeeping.
                    QtCore.QTimer.singleShot(
                        0, lambda: self._apply_fast_sequence_candidate(
                            selected, controller, operations, pinned, index,
                            visible_number,
                        )
                    )

                dialog.finished.connect(on_finished)
                self._fast_result_dialog = dialog
                _desktop_debug_event("FAST_RESULT_DIALOG_OPENED")
                dialog.open()
                # Preserve the 30-second maximum FAST search budget. Perform the
                # separate bounded 2D screening after the result window is
                # visible, not in the combinatorial search loop.
                QtCore.QTimer.singleShot(0, check_fast_shortlist_2d)

            def _apply_fast_sequence_candidate(
                    self, selected, controller, operations, pinned, index,
                    visible_number=None):
                """Commit a shortlist candidate outside the Qt dialog loop."""
                if not self._playback_alive():
                    _desktop_debug_event(
                        "FAST_APPLY_SKIPPED", reason="workspace_destroyed"
                    )
                    return
                required_widgets = (
                    self.operations, self.status, self.tooling_status,
                    self._flat_view, self._task_stack,
                )
                if not all(self._qt_is_valid(w) for w in required_widgets):
                    _desktop_debug_event(
                        "FAST_APPLY_SKIPPED", reason="task_widgets_destroyed",
                        qt_state=self._qt_debug_state(),
                    )
                    return
                if (self.session is None
                        or self.session.controller is not controller
                        or not self.session.can_reorder()
                        or tuple(controller.operation_steps) != operations):
                    self.status.setText(
                        tr("fast_part_or_operations_changed_run_the_search_again")
                    )
                    return
                original_order = tuple(self.session.bend_order)
                old_operation_orientation = dict(
                    self._manual_operation_orientation_overrides
                )
                old_bend_orientation = dict(self._manual_orientation_overrides)
                old_advisory = set(self._fast_advisory_orientation_keys)
                _desktop_debug_event(
                    "FAST_APPLY_STARTED",
                    selected=selected.to_dict(), qt_state=self._qt_debug_state(),
                )
                try:
                    if controller.has_stages:
                        controller.reorder_operations(selected.operation_ids)
                    else:
                        self.session.set_bend_order(selected.operation_ids)
                    orientation = selected.orientations()
                    self._manual_orientation_overrides = {
                        step.bend_id: orientation[step.operation_id]
                        for step in operations if step.kind == "SINGLE"
                    }
                    self._manual_operation_orientation_overrides = {
                        step.operation_id: orientation[step.operation_id]
                        for step in operations if step.kind != "SINGLE"
                    }
                    self._fast_advisory_orientation_keys = (
                        set(selected.operation_ids) - set(pinned)
                    )
                    self.session.active_bend_index = (
                        self.session.controller.active_index
                    )
                    # Updating a whole operation table synchronously
                    # creates a heavy multi-bend CAD gauge preview; defer
                    # that calculation until the next normal UI refresh.
                    self._fast_apply_skip_table_gauge = True
                    try:
                        self._on_staged_plan_changed(
                            tr("fast_unverified_candidate_applied_a_separate_physical_check_is", number=(
                                visible_number if visible_number is not None
                                else index + 1
                            )),
                            fast_apply=True,
                        )
                    finally:
                        self._fast_apply_skip_table_gauge = False
                    # If the normal 3D redraw already computed the new
                    # automatic CAD-gauge envelopes, their cached values
                    # are cheap to place back into the information table.
                    # Never force a second expensive gauge computation
                    # as part of the FAST dialog commit.
                    cache = self._machine_gauge_envelope_cache
                    if (self._playback_alive()
                            and self._qt_is_valid(self.operations)
                            and cache is not None
                            and cache[0][1] == tuple(self.session.bend_order)):
                        self._update_operations_visual_state()
                    _, details, _ = self._task_results["FAST"]
                    self._set_task_result(
                        "FAST", "done", tr("bend_sequence_applied"),
                        tr("sequence_d3eaa949", order=" → ".join(selected.operation_ids)) + "\n" + details,
                    )
                    _desktop_debug_event(
                        "FAST_CANDIDATE_APPLIED",
                        selected=selected.to_dict(),
                        evidence="UNVERIFIED_3D_NOT_CHECKED",
                    )
                except Exception as exc:
                    if not self._playback_alive():
                        # FreeCAD/Qt has destroyed the host: NEVER touch
                        # labels, dialog, timer or native task controls now.
                        _desktop_debug_event(
                            "FAST_APPLY_HOST_DESTROYED",
                            error=str(exc), traceback=traceback.format_exc(),
                        )
                        return
                    try:
                        if controller.has_stages:
                            controller.reorder_operations(
                                [step.operation_id for step in operations]
                            )
                        else:
                            self.session.set_bend_order(original_order)
                    except Exception:
                        _error("FAST rollback failed: " + traceback.format_exc())
                    self._manual_operation_orientation_overrides = (
                        old_operation_orientation
                    )
                    self._manual_orientation_overrides = old_bend_orientation
                    self._fast_advisory_orientation_keys = old_advisory
                    if self._qt_is_valid(self.status):
                        self.status.setText(
                            tr("fast_candidate_could_not_be_applied", error=exc)
                        )
                    self._set_task_result("FAST", "error", tr("sequence_could_not_be_applied"), str(exc))
                    _error("FAST apply failed: " + traceback.format_exc())

            def open_sequence_catalog_dialog(self):
                from freecad_smbs.commands.sequence_catalog_dialog import show_sequence_catalog
                show_sequence_catalog(self)

            def assign_catalog_sequence(self, entry):
                """Replay an imported order and bind it to the existing simulator."""
                from smbs_core.kinematics2d.sequence_search_2d import (
                    validate_partial_planar_prefix,
                )
                if self.session is None:
                    raise ValueError(tr("load_a_sheetmetal_part_first_e8487563"))
                if self.timer.isActive() or self.session.playback_status == PlaybackStatus.PLAYING:
                    raise ValueError(tr("stop_playback_before_changing_the_sequence"))
                sequence = tuple(entry.sequence)
                current = tuple(self.session.canonical_order)
                if (not sequence or len(sequence) > len(current)
                        or len(set(sequence)) != len(sequence)
                        or any(b not in current for b in sequence)):
                    raise ValueError(tr("the_bends_in_json_do_not_match_the_current"))
                if (entry.origin == "MANUAL" and entry.model_label
                        and entry.model_label != self.session.part_label):
                    raise ValueError(
                        tr("manual_json_belongs_to_another_part") + entry.model_label
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
                        tr("repeat_2d_check_failed") + reason
                    )
                self._stop_timer()
                self.session.reset_all_bends()
                remainder = [bend for bend in current if bend not in sequence]
                self.session.set_bend_order(list(sequence) + remainder)
                is_partial = len(sequence) < len(current)
                self._partial_preview_limit = len(sequence) if is_partial else None
                self._preview_only_planar_order = sequence
                self._manual_sequence_prefix = list(sequence)
                self._close_sequence_edit_controls()
                self._sync_manual_order_input()
                self._selected_planar_gauge_order = ()
                self._selected_planar_gauge_positions = {}
                self._selected_planar_gauge_model_id = None
                self._backgauge_preview_cache.clear()
                self._conditional_bends_status.clear()
                self._populate_operations()
                self._configure_for_active_bend()
                self._sync_tooling_to_active_bend()
                self.draw()
                self._draw_flat_pattern(reset_camera=False)
                self.status.setText(
                    tr("catalog_simulation")
                    .format(len(sequence), len(current), entry.origin, reason)
                )
                self.physical_check_result.setText(
                    tr("unverified_repeating_the_2d_check_does_not_certify_3d")
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
                    self.status.setText(tr("load_a_part_first"))
                    return
                saved = tuple(self._saved_full_sequences)
                if not saved:
                    self.status.setText(
                        tr("no_complete_2d_sequences_have_been_saved_yet")
                    )
                    return
                labels = [
                    "{}. {}".format(i + 1, " → ".join(item["bend_ids"]))
                    for i, item in enumerate(saved)
                ]
                label, accepted = QtWidgets.QInputDialog.getItem(
                    self, tr("saved_sequences"), tr("sequence_5e891e6a"),
                    labels, 0, False
                )
                if not accepted or label not in labels:
                    return
                item = saved[labels.index(label)]
                order = tuple(item["bend_ids"])
                if (len(order) != len(self.session.canonical_order)
                        or set(order) != set(self.session.canonical_order)):
                    self.status.setText(
                        tr("this_sequence_does_not_match_the_current_part")
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
                    tr("unverified_sequence_restored_from_fcstd_run_the_collision_check")
                )
                self.status.setText(
                    tr("saved_order_selected_unverified")
                    .format(" → ".join(order))
                )

            def _update_tooling_status_label(self):
                if not self._playback_alive() or not self._qt_is_valid(self.tooling_status):
                    _desktop_debug_event(
                        "TOOLING_STATUS_UPDATE_SKIPPED", reason="host_destroyed",
                    )
                    return
                self.tooling_status.setText(_tooling_status_text(self.tooling_setup))
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
                        tr("cycle_phase",
                           phase=phase_val, mode=sim_val, contact=contact_val)
                        + (tr("proposed_lift_mm_unverified", lift=lift)
                           if lift > 0.0 else "")
                    )
                    self.ram_position_label.setText(
                        tr("ram_position_mm_stroke_mm",
                           height=ram_z, stroke=ram_stroke)
                    )
                else:
                    sim_val = getattr(getattr(self.cycle_config, "simulation_mode", None), "value", "NORMAL")
                    ram_z = float(getattr(self.tooling_setup, "ram_z_mm", 0.0) or 0.0)
                    ram_stroke = float(getattr(self.tooling_setup, "ram_stroke_mm", 0.0) or 0.0)
                    self.cycle_phase_label.setText(
                        tr("cycle_phase_idle_ed82db72", mode=sim_val)
                    )
                    self.ram_position_label.setText(
                        tr("ram_position_mm_stroke_mm",
                           height=ram_z, stroke=ram_stroke)
                    )

            def _rear_frame_for_preview(self, index=None):
                """FAST flags are relative to the fixed-panel rear frame."""
                if self.session is None:
                    return None
                controller = self.session.controller
                if index is None:
                    index = controller.active_index
                step = controller.operation_steps[index]
                if step.operation_id in self._fast_advisory_orientation_keys:
                    return self.session.panel_vertices_xy
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
                if item is None or self.session is None:
                    return
                bend_id = item.data(0, QtCore.Qt.UserRole)
                operation_id = item.data(0, int(QtCore.Qt.UserRole) + 1) or bend_id
                if self.session.controller.has_stages and operation_id != bend_id:
                    explicitly_set = (
                        operation_id in self._manual_operation_orientation_overrides
                    )
                else:
                    explicitly_set = bend_id in self._manual_orientation_overrides
                if not explicitly_set:
                    return
                menu = QtWidgets.QMenu(self.operations)
                automatic = menu.addAction(tr("choose_orientation_automatically"))
                run_menu = getattr(menu, "exec", None) or menu.exec_
                if run_menu(self.operations.viewport().mapToGlobal(pos)) == automatic:
                    self._set_manual_loading_override(item, None)

            def _set_manual_loading_override(self, item, reverse_axis):
                """Set 0°/180° independently for one operation, or restore auto."""
                if self.session is None:
                    return
                bend_id = item.data(0, QtCore.Qt.UserRole)
                operation_id = item.data(0, int(QtCore.Qt.UserRole) + 1) or bend_id
                if bend_id not in self.session.bend_order:
                    return
                if (self._task_progress_busy or self.timer.isActive()
                        or self.session.playback_status == PlaybackStatus.PLAYING):
                    self._update_operations_visual_state()
                    self.status.setText(
                        tr("stop_the_simulation_or_wait_for_the_check_to")
                    )
                    return
                order = tuple(self.session.bend_order)
                # Clicking a checkbox makes this a deliberate user choice,
                # not a disposable orientation from the FAST shortlist.
                self._fast_advisory_orientation_keys.discard(operation_id)
                if self.session.controller.has_stages and operation_id != bend_id:
                    # Partial and finish operations each have their own override.
                    # A plain SINGLE step retains legacy bend-ID persistence.
                    # B03.1 and B03.2 never write to the same dictionary key.
                    if reverse_axis is None:
                        self._manual_operation_orientation_overrides.pop(
                            operation_id, None
                        )
                    else:
                        self._manual_operation_orientation_overrides[
                            operation_id
                        ] = bool(reverse_axis)
                elif reverse_axis is None:
                    self._manual_orientation_overrides.pop(bend_id, None)
                else:
                    self._manual_orientation_overrides[bend_id] = bool(reverse_axis)

                self._machine_pose_fault = None
                self._highlighted_end_zone_info = None
                self._manual_3d_verdicts.clear()
                self._manual_retract_verdicts.clear()
                self._conditional_bends_status.clear()
                self._last_manual_sequence_report = None
                self._backgauge_preview_cache.clear()
                prefix_size = len(self._manual_sequence_prefix)
                self._partial_preview_limit = (
                    prefix_size if 0 < prefix_size < len(order) else None
                )
                self._clear_manual_check_feedback()
                self._manual_preview_notice.setText(
                    tr("prebend_finish_stage_collisions_not_checked")
                    if self.session.controller.has_stages else
                    tr("manual_preview_collisions_unchecked")
                )
                self.physical_check_result.setText(
                    tr("workpiece_orientation_has_changed_run_the_collision_check_again")
                )
                if self.session.controller.active_operation_id != operation_id:
                    # Checkbox changes do not automatically select the row.
                    self.operations.setCurrentItem(
                        item, _LOADING_ORIENTATION_COLUMN
                    )
                else:
                    cycle = self._ensure_press_cycle_controller()
                    if cycle is not None and cycle.phase == CyclePhase.TRANSFER:
                        cycle.select_operation(self.session.active_bend_index)
                    self._update_operations_visual_state()
                    self._update_tooling_status_label()
                    self.draw()
                choices = self._effective_operation_orientation_map()
                selected_reverse = reverse_axis_for_operation(
                    choices, bend_id, operation_id
                )
                _desktop_debug_event(
                    "MANUAL_LOADING_180_CHANGED",
                    bend_id=bend_id,
                    operation_id=operation_id,
                    operator_side="-s_LEFT",
                    machine_rear_side="+s_RIGHT",
                    reverse_axis=selected_reverse,
                    manual_override=reverse_axis is not None,
                    selected_order=list(order),
                    orientation_by_bend=self._effective_loading_orientation_map(),
                    orientation_by_operation=choices,
                    physical_status="UNVERIFIED",
                )
                pose = "180°" if selected_reverse else "0°"
                source = tr("automatic_orientation") if reverse_axis is None else tr("manual")
                self.status.setText(
                    tr("orientation_collisions_in_this_orientation_have_not_been_checked",
                       operation=operation_id, pose=pose, source=source)
                )

            def _manual_orientation_selection(self, bend_ids):
                """Select from the single map used by both previews and OCC."""
                choices = self._effective_loading_orientation_map()
                return {bid: bool(choices[bid]) for bid in bend_ids if bid in choices}

            def _effective_operation_orientation_map(self):
                """Physical defaults + stage-specific 180° values for Coin3D.

                Only staged operations use separate keys; plain ManualMode
                retains exactly the same single-per-bend orientation contract.
                """
                choices = self._effective_loading_orientation_map()
                if self.session is None or not self.session.controller.has_stages:
                    return choices
                return expanded_operation_orientations(
                    choices,
                    self.session.controller.operation_steps,
                    self._manual_operation_orientation_overrides,
                )

            def _effective_loading_orientation_map(self):
                """Use the same per-bend placement for Coin3D and validation."""
                if self.session is None:
                    self._ergonomic_loading_status = "NO_PART"
                    return {}
                order = tuple(self.session.bend_order)
                # Reuse explicit FAST/manual orientations without running
                # the expensive CAD ergonomic suggestion generator. An
                # already chosen machine pose is authoritative for previews;
                # recomputing its default needlessly opens native documents.
                known = dict(self._manual_orientation_overrides)
                for step in self.session.controller.operation_steps:
                    if (step.operation_id in self._manual_operation_orientation_overrides
                            and step.bend_id not in known):
                        known[step.bend_id] = bool(
                            self._manual_operation_orientation_overrides[
                                step.operation_id
                            ]
                        )
                if all(bend_id in known for bend_id in order):
                    self._ergonomic_loading_status = (
                        "FAST_OR_MANUAL_ORIENTATION_UNVERIFIED"
                    )
                    return {bend_id: bool(known[bend_id]) for bend_id in order}
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
                finger_count = len(self._backgauge_stations()) if (
                    model is not None and model.automatic and model.cad_geometry_enabled
                ) else 0
                key = (id(self.session), id(self.session.fold_state), order, finger_count)
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
                        centered_finger_count=finger_count,
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
                orientations = self._effective_operation_orientation_map()
                return compute_machine_pose(
                    fs, bid,
                    phase="BEND_ONLY",
                    support_plane_zm=float(sheet_support_z),
                    reverse_axis=bool(reverse_axis_for_operation(
                        orientations, bid, self.session.controller.active_operation_id
                    )),
                    rear_panel_vertices_xy=self._rear_frame_for_preview(),
                )

            def _update_loading_orientation_label(self):
                """Expose the actual machine direction, never CAD-certify a hint."""
                if self.session is None:
                    self.loading_orientation_status.setText(
                        tr("operator_backgauge_no_part_loaded")
                    )
                    return
                choices = self._effective_operation_orientation_map()
                bend_id = str(self.session.active_bend_id)
                op_id = self.session.controller.active_operation_id
                reversed_pose = reverse_axis_for_operation(choices, bend_id, op_id)
                source = self._ergonomic_loading_status
                if (
                    op_id in self._manual_operation_orientation_overrides
                    or bend_id in self._manual_orientation_overrides
                ):
                    evidence = tr("position_set_manually_93ea2627")
                elif source.startswith("SELECTED_2D"):
                    evidence = tr("selected_from_2d_search_3d_not_checked")
                elif source == "AUTO_SHORT_REAR_UNVERIFIED":
                    evidence = tr("short_side_toward_the_backgauge_contact_not_checked")
                else:
                    evidence = source
                self.loading_orientation_status.setText(
                    tr("operator_s_backgauge_s")
                    .format(
                        op_id,
                        (tr("undetermined") if bend_id not in choices else
                         tr("rotate_180") if reversed_pose else tr("normally_0")),
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
                        self.press_cycle.loading_rear_frame_for_step = self._rear_frame_for_preview
                        self.press_cycle.loading_orientation_by_bend = (
                            self._effective_operation_orientation_map()
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
                self.press_cycle.loading_rear_frame_for_step = self._rear_frame_for_preview
                self.press_cycle.loading_orientation_by_bend = (
                    self._effective_operation_orientation_map()
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
                dlg.setWindowTitle(tr("press_cycle_settings_kinematic_preview"))
                vbox = QtWidgets.QVBoxLayout(dlg)

                note = QtWidgets.QLabel(
                    tr("these_settings_control_punch_movement_and_angle_change_in")
                )
                note.setWordWrap(True)
                vbox.addWidget(note)

                form = QtWidgets.QFormLayout()
                sp_app = QtWidgets.QDoubleSpinBox()
                sp_app.setRange(0.5, 500.0)
                sp_app.setValue(self.cycle_config.approach_speed_mm_s)
                form.addRow(tr("approach_speed_mm_s"), sp_app)

                sp_ret = QtWidgets.QDoubleSpinBox()
                sp_ret.setRange(0.5, 500.0)
                sp_ret.setValue(self.cycle_config.retract_speed_mm_s)
                form.addRow(tr("retract_speed_mm_s"), sp_ret)

                sp_rate = QtWidgets.QDoubleSpinBox()
                sp_rate.setRange(1.0, 720.0)
                sp_rate.setValue(self.cycle_config.bend_rate_deg_s)
                form.addRow(tr("bend_rate_s"), sp_rate)

                sp_clr = QtWidgets.QDoubleSpinBox()
                sp_clr.setRange(0.5, 200.0)
                sp_clr.setValue(self.cycle_config.initial_clearance_mm)
                form.addRow(tr("initial_clearance_mm"), sp_clr)

                sp_strk = QtWidgets.QDoubleSpinBox()
                sp_strk.setRange(0.5, 150.0)
                sp_strk.setValue(self.cycle_config.preview_stroke_mm)
                form.addRow(tr("preview_stroke_mm"), sp_strk)

                sp_dwell = QtWidgets.QDoubleSpinBox()
                sp_dwell.setRange(0.0, 30.0)
                sp_dwell.setSingleStep(0.05)
                sp_dwell.setValue(self.cycle_config.bottom_dwell_s)
                form.addRow(tr("bottom_dwell_s"), sp_dwell)
                vbox.addLayout(form)

                btn_row = QtWidgets.QHBoxLayout()
                btn_ok = QtWidgets.QPushButton(tr("apply"))
                btn_cancel = QtWidgets.QPushButton(tr("cancel"))
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
                embed_task_dialog(self, dlg, tr("cycle_settings"), "cycle_config")

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
                    tr("tooling_aligned_with_bend_line_punch_clearance_mm").format(
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
                label = tr("punch_punch") if kind == ToolKind.PUNCH else tr("die_die")
                file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
                    self,
                    tr("import_profile_28fb1a0a", label=label),
                    str(self.tool_library.library_root),
                    "Tool Files (*.dxf *.DXF *.step *.STEP *.stp *.STP *.FCStd);;All Files (*)",
                )
                if not file_path:
                    return
                default_name = Path(file_path).stem
                name, ok = QtWidgets.QInputDialog.getText(
                    self,
                    tr("name_for", label=label),
                    tr("enter_a_name_for_the_library_tool"),
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
                        tr("tool_imported_and_saved",
                           name=prof.name, kind=prof.kind.value, status=prof.validation_status.value)
                    )
                except Exception as exc:
                    msg = tr("tool_import_failed", error=exc)
                    _warn(msg)
                    self.status.setText(msg)

            def open_full_tooling_library(self):
                """Open the single canonical library with segment inventory."""
                from smbs_core.geometry_backend.io.tooling_library import (
                    ToolingLibrary,
                )
                from freecad_smbs.ui.tooling_panel import ToolingPanel
                try:
                    library = ToolingLibrary(self.tool_library.library_root)
                    panel = ToolingPanel(library, parent=self)
                    try:
                        (getattr(panel, "exec", None) or panel.exec_)()
                    finally:
                        panel.deleteLater()
                        self._refresh_tooling_combos()
                except Exception as exc:
                    QtWidgets.QMessageBox.warning(
                        self, tr("tooling_library"), str(exc)
                    )

            def _playback_alive(self):
                return (not self._closing and self._qt_is_valid(self)
                        and self._qt_is_valid(self.timer))

            def _stop_timer(self):
                if not self._playback_alive():
                    return
                self.timer.stop()
                self.playing = False
                self._last_tick_monotonic = None
                self._sync_playback_buttons()

            def _sync_playback_buttons(self):
                if self.session is None:
                    self._update_task_progress()
                    self.play_btn.setText(tr("play"))
                    self.play_btn.setIcon(QtGui.QIcon(str(_smbs_icon_path("control_play.svg"))))
                    self.move_up_btn.setEnabled(False)
                    self.move_down_btn.setEnabled(False)
                    self.restore_order_btn.setEnabled(False)
                    return
                st = self.session.playback_status
                self._update_task_progress()
                self.playing = st == PlaybackStatus.PLAYING
                bend_order = getattr(self.session, "bend_order", [])
                if st == PlaybackStatus.PLAYING:
                    self.play_btn.setText(tr("pause"))
                    icon_name = "control_pause.svg"
                elif st == PlaybackStatus.PAUSED:
                    self.play_btn.setText(tr("resume"))
                    icon_name = "control_play.svg"
                else:
                    self.play_btn.setText(tr("play"))
                    icon_name = "control_play.svg"
                self.play_btn.setIcon(QtGui.QIcon(str(_smbs_icon_path(icon_name))))

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
                    outcome, new_session, msg = "loaded", prepared_session, tr("dxf_flat_pattern_calculated")
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

                self._set_task_result("FAST", "idle", tr("search_has_not_run_for_this_part"))
                self._set_task_result("Mesh", "idle", tr("this_part_has_not_been_checked_yet"))
                self._finish_motion_trace("NEW_PART_LOADED")
                self._stop_timer()
                # 3D surface preview uses this session's FoldState directly;
                # never read or mutate the original source document again.
                self._machine_pose_fault = None
                self._last_3d_frame_at = -1.0
                self.tooling_setup.set_tool_assembly(None)
                self._close_tool_assembly_report()
                self._simulation_source = None
                if prepared_session is None:
                    unfold = resolve_selected_unfold(doc, selection)
                    self._simulation_source = (doc.Name, unfold)
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
                self._manual_sequence_prefix = (
                    list(new_session.bend_order)
                    if getattr(new_session.unfold_data, "dxf_manual", False)
                    else []
                )
                self._manual_orientation_order = ()
                self._manual_orientation_by_bend = {}
                self._manual_orientation_overrides.clear()
                self._manual_operation_orientation_overrides.clear()
                self._fast_advisory_orientation_keys.clear()
                self._project_clamp_config = None
                self._manual_preview_unverified = False
                self._manual_selection_enabled = False
                self._manual_preview_notice.hide()
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
                    restore_message = tr("smbs_json_could_not_be_restored_automatic_json_writing", error=exc)
                    self._smbs_allow_auto_embed = False
                    self.fcstd_note.setText(restore_message)
                self._sync_manual_order_input()
                try:
                    self._register_embedded_smbs_observer()
                except Exception as exc:
                    _warn("SMBS save observer not registered: " + str(exc))
                    restore_message += tr("automatic_json_saving_is_unavailable", error=exc)
                self.status.setText(
                    msg + (" | " + restore_message if restore_message else "")
                )
                self.check_result.setText(msg)
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
                    raise RuntimeError(tr("load_a_sheetmetal_part_first"))
                document = App.getDocument(self._smbs_document_name)
                if document is None:
                    raise RuntimeError(tr("the_selected_part_document_is_closed"))
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
                self.manual_simulate_btn.setText(tr("simulate_without_checking"))
                self.manual_check_summary.setText(
                    tr("the_current_sequence_has_not_been_checked")
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
                        tr("assisted_lift_is_experimental_first_identify_a_punch_collision")
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
                        tr("orientation_bend_angle").format(
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
                        lines.append(tr("tooling") + item["tool_setup_reason"])
                    if lengths.get("punch_effective_length_mm") is not None:
                        lines.append(
                            tr("punch_length_source_mm_occ_mm_mode").format(
                                lengths.get("punch_source_length_mm"),
                                lengths.get("punch_effective_length_mm"),
                                tr("virtual") if lengths.get(
                                    "is_virtual_tool_geometry"
                                ) else tr("real"),
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
                                tr("central_intersection_reducing_the_allowed_end_zones_will_not").format(
                                    ", ".join(length_diagnosis["central_hits"])
                                )
                            )
                        elif length_diagnosis["status"] == "END_ZONE_ONLY_BBOX_UNVERIFIED":
                            lines.append(
                                tr("end_intersection_bbox_actual_tool_relief_not_checked")
                                .format(", ".join(
                                    length_diagnosis["end_zone_hits"]
                                ))
                            )
                        tool_label = {
                            "PUNCH": tr("punch_44f13e39"),
                            "DIE": tr("die_f5194d67"),
                            "PUNCH_AND_DIE": tr("punch_and_die"),
                        }.get(blocker.get("tool"), tr("unknown_source"))
                        lines.append(
                            tr("problem_phase_z_mm_punch_mm_die_mm").format(
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
                            tr("max_penetration_at_checked_positions_punch_mm_die_mm").format(
                                float(blocker.get("punch_overlap_mm3") or 0),
                                float(blocker.get("die_overlap_mm3") or 0),
                            )
                        )
                        bbox_dict = blocker.get("intersection_bbox_xyz_mm") or {}
                        for kind, coords in bbox_dict.items():
                            if isinstance(coords, dict):
                                lines.append(
                                    tr("region_machine_xyz_mm_x_y_z").format(
                                        kind.upper(),
                                        coords["xmin"], coords["xmax"],
                                        coords["ymin"], coords["ymax"],
                                        coords["zmin"], coords["zmax"],
                                    )
                                )
                        bracket = blocker.get("sampled_onset_bracket") or {}
                        if bracket:
                            lines.append(
                                tr("collision_onset_sampled_without_a_blocking_collision_with_a").format(
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
                                    tr("intersecting_components_native_occ") +
                                    ", ".join(
                                        tr("mm_f506aa9e").format(
                                            hit.get("component_id", "?"),
                                            float(hit.get("overlap_volume_mm3", 0.0)),
                                        )
                                        for hit in names[:8]
                                    )
                                )
                            else:
                                lines.append(
                                    tr("specific_panel_not_confirmed") +
                                    str(attribution.get("status", "UNVERIFIED"))
                                )
                        lines.append(
                            tr("code").format(
                                blocker.get("phase_status", "UNVERIFIED"),
                                str(blocker.get("reason", ""))[:250],
                            )
                        )
                    elif phase_evidence:
                        # Do not show the first 650 bytes of timings from
                        # APPROACH; they obscure later BENDING/BOTTOM failure.
                        if phase_evidence.startswith("native_d634_rejected:"):
                            lines.append(
                                tr("d6_rejected_the_trajectory_stage_and_cause_unknown_details")
                            )
                        else:
                            lines.append("  " + phase_evidence[:240])
                    if isinstance(tool_pair, dict):
                        lines.append(
                            tr("punch_die_positions").format(
                                tool_pair.get("status", "UNVERIFIED"),
                                tool_pair.get("checked_ram_positions", 0),
                                (" | " + str(tool_pair.get("reason", "")))
                                if tool_pair.get("status") != "SAMPLED_CLEAR" else "",
                            )
                        )
                        if tool_pair.get("status") == "COLLISION":
                            lines.append(
                                tr("punch_die_phases_z_mm_mm").format(
                                    ", ".join(tool_pair.get("phases", ())),
                                    tool_pair.get("ram_z_mm", "—"),
                                    tool_pair.get("overlap_volume_mm3", "—"),
                                )
                            )
                    lines.append(
                        tr("retract_start_z_mm_end_z_mm_samples").format(
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
                        tr("no_bend_has_been_verified_check_the_selected_fcstd")
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
                    self.status.setText(tr("there_is_no_saved_3d_collision_to_display"))
                    return
                if self.session.playback_status == PlaybackStatus.PLAYING:
                    self.pause()
                self._stop_timer()
                if self._mesh_collision_index is not None:
                    self._mesh_collision_index = None
                    self._collision_display_active = False
                self._collision_display_active = not self._collision_display_active
                if hasattr(self, "manual_show_collision_btn"):
                    self.manual_show_collision_btn.setText(
                        tr("return_to_standard_3d_view")
                        if self._collision_display_active else
                        tr("show_collision_in_3d")
                    )
                if not self._open_coin_preview():
                    return False
                self._trace_event(
                    "MANUAL_COLLISION_3D_INSPECTION",
                    bend_id=self._collision_inspection.get("bend_id"),
                    blocker=self._collision_inspection.get("first_blocker"),
                )
                self.status.setText(
                    tr("collision_location_shown_in_machine_xyz_the_overview_geometry")
                    if self._collision_display_active else
                    tr("returning_to_normal_3d_view_the_collision_remains_in")
                )

            def _current_native_tooling_identity(self):
                """Capture the exact current inputs for on-demand OCC replay."""
                return {
                    "punch_id": getattr(
                        getattr(self.tooling_setup, "punch", None),
                        "tool_id", None,
                    ),
                    "punch_reversed": bool(getattr(self.tooling_setup.punch, "punch_reversed", False)),
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
                        tr("tooling_changed_after_the_3d_check_check_the_current")
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
                        tr("tooling_or_position_changed_run_the_3d_check_again")
                    )
                    return False
                self._stop_timer()
                self.status.setText(tr("occ_checking_assisted_lift_of_the_part_and_punch"))
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
                    tr("lift_positions_checked_the_original_collision_remains_engagement_and").format(
                        report["status"], len(report["samples"]),
                        tr("coupled_motion_is_shown_in_the_full_simulation")
                        if can_preview else
                        tr("the_experimental_pose_is_separate_from_the_current_sequence")
                        if inspection.get("alternative_only", False)
                        else tr("coupled_motion_is_disabled")
                    )
                )
                self.manual_lift_probe_hint.setText(
                    tr("this_is_a_conditional_preview_of_assumed_engagement_not")
                    .format(tr("lift_simulation_enabled") if can_preview
                            else tr("lift_simulation_disabled"),
                            tr("the_2d_and_occ_bottom_positions_do_not_match")
                            if report.get("preview_alignment", {}).get("matched") is False
                            else "")
                )
                self.manual_check_details.appendPlainText(
                    tr("lift_experiment_positions").format(
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
                self.status.setText(tr("occ_checking_lateral_withdrawal_of_the_lifted_part"))
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
                    tr("occ_exit_checked_y_directions_the_original_collision_remains")
                    .format(report["status"], directions or tr("none"))
                )
                self.status.setText(message)
                self.manual_check_details.appendPlainText(
                    "\n{} — {}\n{}".format(
                        inspection["bend_id"], message,
                        "; ".join(
                            tr("y_mm_positions").format(
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
                    self.status.setText(tr("check_assisted_lift_with_occ_first"))
                    return
                self._stop_timer()
                self._machine_pose_fault = None
                try:
                    self.set_cycle_mode(CycleExecutionMode.FULL_CYCLE)
                    pc.reset()
                    self.session.active_bend_index = pc.sequence.active_index
                    self._configure_for_active_bend()
                    self._collision_display_active = False
                    self.manual_show_collision_btn.setText(tr("show_collision_in_3d"))
                    self.play_all()
                except (PressCycleError, SequenceError, ValueError) as exc:
                    self.status.setText(tr("lift_simulation_unavailable") + tr_message(exc))
                    return
                _desktop_debug_event(
                    "MANUAL_ASSISTED_RETRACT_PREVIEW_STARTED",
                    bend_id=bend_id, existing_verdict="COLLISION",
                    physical_certification="UNVERIFIED",
                )
                self.status.setText(
                    tr("simulation_assumed_coupled_lift_on_retract_original_collision_remains")
                    .format(bend_id)
                )

            def _open_exact_collision_inspector(self):
                """Open a separate native FreeCAD scene with actual OCC solids."""
                inspection = self._collision_inspection
                if not self._native_collision_identity_matches(inspection):
                    self.status.setText(
                        tr("there_is_no_current_native_3d_overlap_to_inspect")
                    )
                    return False
                self._stop_timer()
                self.status.setText(
                    tr("building_the_exact_occ_scene_in_a_separate_document")
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
                        translate=tr,
                    )
                except Exception as exc:
                    message = "{}: {}".format(type(exc).__name__, exc)
                    self.status.setText(
                        tr("could_not_reproduce_the_occ_intersection_geometry_tolerances_were", error=message)
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
                    tr("document_opened_red_solids_show_actual_occ_intersections_the")
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
                    note = tr("not_saved_to_fcstd") + str(exc)
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
                    return tr("result_saved_inside_fcstd") + filename
                _desktop_debug_event(
                    "FCSTD_MANUAL_DIAGNOSTICS_PENDING_FIRST_SAVE",
                    document_name=getattr(document, "Name", None),
                )
                return (
                    tr("result_embedded_in_the_open_document_save_it_as")
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
                    raise RuntimeError(tr("wrong_document_for_smbs_data"))
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
                            self, tr("save_part_with_smbs_json"),
                            proposed, "FreeCAD (*.FCStd)",
                        )
                        if not target:
                            self.status.setText(
                                tr("json_is_staged_in_the_document_saving_the_fcstd")
                            )
                            return
                        document.saveAs(str(target))
                    self.fcstd_note.setText(
                        tr("json_embedded_smbs_data_sessionjson_saved_in", file=document.FileName)
                    )
                    self.status.setText(
                        tr("smbs_json_saved_inside_fcstd_check_results_are_historical")
                    )
                except Exception as exc:
                    _error("FCStd SMBS save failed: " + traceback.format_exc())
                    self.status.setText(tr("could_not_embed_json", error=exc))

            def _restore_embedded_smbs_json_clicked(self):
                try:
                    document = self._bound_smbs_document()
                    message = self._restore_embedded_smbs_json(document)
                    self.status.setText(message)
                except Exception as exc:
                    _error("FCStd SMBS load failed: " + traceback.format_exc())
                    self.status.setText(tr("could_not_read_json", error=exc))

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
                        tr("this_document_has_no_embedded_smbs_json_yet")
                    )
                    return tr("no_saved_smbs_session_in_the_fcstd_file")
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
                    tr("prebend_finish_kinematic_simulation_collisions_not_checked")
                    if self.session.controller.has_stages else
                    tr("manual_preview_collisions_unchecked")
                )
                self._manual_preview_notice.setVisible(self.session.controller.has_stages)
                self._partial_preview_limit = None
                self._preview_only_planar_order = None
                self._last_manual_sequence_report = None
                self._manual_3d_verdicts.clear()
                self._manual_retract_verdicts.clear()
                self._clear_manual_check_feedback()
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
                                tr("could_not_be_restored_selection_cleared").format(key, exc)
                            )
                            continue
                    try:
                        if key == "punch":
                            self.tooling_setup.select_punch(profile)
                        else:
                            self.tooling_setup.select_die(profile)
                    except ToolingError as exc:
                        warnings.append(
                            tr("could_not_be_restored").format(key, exc)
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
                    warnings.append(tr("cycle_settings_54803e7e") + str(exc))

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
                    warnings.append(tr("tooling_tolerances") + str(exc))

                if opts.get("segment_assembly_active"):
                    try:
                        assembly = self._calculate_common_tool_assembly(prefix or order)
                        self._verify_tool_assembly_geometry(assembly)
                        self.tooling_setup.set_tool_assembly(assembly)
                    except Exception as exc:
                        warnings.append(tr("tool_section_assembly_could_not_be_restored") + str(exc))

                for key, value in payload["simulation"].get(
                    "operation_weights", {}
                ).items():
                    if key in self._operation_weights:
                        try:
                            number = float(value)
                            if math.isfinite(number) and 0.0 <= number <= 100000.0:
                                if self._operation_weights[key] != number:
                                    self._operation_weights[key] = number
                                    # Restored FCStd weights belong to this project only.
                        except (TypeError, ValueError):
                            warnings.append(tr("operation_cost") + str(key))

                self._refresh_tooling_combos()
                self._ensure_press_cycle_controller()
                try:
                    self._sync_tooling_to_active_bend()
                except (ToolingError, PressCycleError, ValueError) as exc:
                    warnings.append(tr("tooling_alignment", error=exc))
                self._populate_operations()
                self._configure_for_active_bend()
                self._draw_flat_pattern(reset_camera=False)
                self.draw()
                self.physical_check_result.setText(
                    tr("smbs_history_restored_from_fcstd_run_the_checks_again")
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
                        tr("the_fcstd_file_contains_a_previous_result_run_the")
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
                            tr("historical_from_fcstd_only_alternative_lifting_can_be_checked")
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
                    tr("sequence_prebend_stages_and_settings_restored_from_fcstd_checks")
                    if self.session.controller.has_stages else
                    tr("sequence_and_settings_restored_from_fcstd_checks_are_historical")
                )
                if warnings:
                    note += tr("limitations") + "; ".join(warnings)
                self.fcstd_note.setText(note)
                return note

            def _mesh_preview_identity(self):
                return (
                    self._current_native_tooling_identity(),
                    tuple(self.session.controller.operation_steps),
                    self._effective_operation_orientation_map(),
                    frozenset(self._fast_advisory_orientation_keys), interface_language(),
                )

            def _mesh_view_groups(self):
                from smbs_core.kinematics2d.collision_groups import collision_groups
                order = tuple(op.operation_id for op in self.session.controller.operation_steps) if self.session else ()
                return collision_groups(self._mesh_collision_hits,grouped=self._mesh_grouped,operation_order=order)

            def _show_next_mesh_collision(self):
                if self.session is None or self._mesh_collision_identity != self._mesh_preview_identity():
                    self.mesh_show_collision_btn.hide()
                    self.status.setText(tr("run_mesh_again_for_the_current_part_and_tooling"))
                    return
                if not self._mesh_collision_hits:
                    return
                if self.session.playback_status == PlaybackStatus.PLAYING:
                    self.pause()
                self._stop_timer()
                index = self._mesh_collision_index if self._collision_display_active else None
                index = 0 if index is None else index + 1
                self._collision_display_active = index < len(self._mesh_view_groups())
                self._mesh_collision_index = index if self._collision_display_active else None
                self._open_coin_preview()

            def _open_mesh_menu(self):
                if self.session is None or self._task_progress_busy:
                    return
                if activate_task_dialog(self, "mesh"):
                    return
                from freecad_smbs.ui.mesh_preferences import mesh_defaults, save_mesh_defaults
                preferences = NativePreferences(QtCore)
                default_budget, default_overlap = mesh_defaults()
                dialog = QtWidgets.QDialog(self)
                dialog.setWindowTitle("Mesh 3D")
                form = QtWidgets.QFormLayout(dialog)
                budget = QtWidgets.QSpinBox()
                budget.setRange(1, 3600)
                budget.setValue(default_budget)
                form.addRow(tr("time_limit_s"), budget)
                overlap = QtWidgets.QDoubleSpinBox()
                overlap.setDecimals(6)
                overlap.setRange(0.000001, 1000.0)
                overlap.setSingleStep(0.0001)
                overlap.setValue(default_overlap)
                form.addRow(tr("minimum_penetration_volume_mm"), overlap)
                mode = QtWidgets.QComboBox()
                mode.addItem(tr("mesh_grouped_mode"),True)
                mode.addItem(tr("mesh_exact_mode"),False)
                mode.setCurrentIndex(0 if self._mesh_grouped else 1)
                form.addRow(tr("mesh_view_mode"),mode)
                sheet_alpha = QtWidgets.QSpinBox()
                tool_alpha = QtWidgets.QSpinBox()
                for spin,value,label in ((sheet_alpha,self._mesh_sheet_transparency,"mesh_sheet_alpha"),
                                         (tool_alpha,self._mesh_tool_transparency,"mesh_tool_alpha")):
                    spin.setRange(0,100)
                    spin.setSuffix(" %")
                    spin.setValue(value)
                    form.addRow(tr(label),spin)
                next_hit = QtWidgets.QPushButton(tr("mesh_next_view"))
                form.addRow(next_hit)
                next_hit.clicked.connect(self._show_next_mesh_collision)

                def view_changed(*_):
                    previous = self._mesh_grouped
                    self._mesh_grouped = bool(mode.currentData())
                    self._mesh_sheet_transparency = sheet_alpha.value()
                    self._mesh_tool_transparency = tool_alpha.value()
                    preferences.setValue("mesh/grouped_view",self._mesh_grouped)
                    if previous != self._mesh_grouped:
                        self._mesh_collision_index = None
                        self._collision_display_active = False
                        if self.session and self._mesh_collision_identity == self._mesh_preview_identity() and self._mesh_collision_hits:
                            self._show_next_mesh_collision()
                            return
                    if self._collision_display_active:
                        self._update_3d_preview(force=True)
                mode.currentIndexChanged.connect(view_changed)
                sheet_alpha.valueChanged.connect(view_changed)
                tool_alpha.valueChanged.connect(view_changed)
                run = QtWidgets.QPushButton(tr("run_mesh"))
                export = QtWidgets.QPushButton(tr("save_latest_collision"))
                run.setIcon(QtGui.QIcon(str(_smbs_icon_path("control_mesh.svg"))))
                export.setIcon(QtGui.QIcon(str(_smbs_icon_path("control_collision_export.svg"))))
                form.addRow(run)
                form.addRow(export)

                def refresh_export():
                    export.setEnabled(
                        self.session is not None
                        and self._latest_mesh_collision is not None
                        and self._mesh_collision_identity == self._mesh_preview_identity()
                    )

                def start_check():
                    save_mesh_defaults(budget.value(), overlap.value())
                    self._run_mesh_sequence_check()
                    if self._qt_is_valid(export):
                        refresh_export()

                run.clicked.connect(start_check)
                export.clicked.connect(self._save_latest_mesh_collision)
                refresh_export()
                embed_task_dialog(self, dialog, "Mesh 3D", "mesh")

            def _save_latest_mesh_collision(self):
                if (self.session is None or self._latest_mesh_collision is None
                        or self._mesh_collision_identity != self._mesh_preview_identity()):
                    return
                path, _ = QtWidgets.QFileDialog.getSaveFileName(
                    self, tr("save_latest_collision"),
                    "MeshCollision.FCStd", "FreeCAD (*.FCStd)",
                )
                if not path:
                    return
                if not path.lower().endswith(".fcstd"):
                    path += ".FCStd"
                import FreeCAD as App

                hit, component, tool, common = self._latest_mesh_collision
                doc = App.newDocument("MeshCollision")
                try:
                    for name, label, shape, color, transparency in (
                        ("Sheet", hit["component"], component, (0.7, 0.7, 0.7), 75),
                        ("Tool", tr({"punch": "punch_44f13e39", "die": "die_f5194d67"}[hit["tool"]]),
                         tool, (0.5, 0.6, 0.8), 75),
                        ("Overlap", f'{hit["operation"]} · {hit["phase"]}',
                         common, (1.0, 0.0, 0.0), 0),
                    ):
                        obj = doc.addObject("Part::Feature", name)
                        obj.Label = label
                        obj.Shape = shape
                        obj.ViewObject.ShapeColor = color
                        obj.ViewObject.Transparency = transparency
                    doc.recompute()
                    doc.saveAs(path)
                except Exception as exc:
                    App.closeDocument(doc.Name)
                    QtWidgets.QMessageBox.warning(self, tr("error"), tr_message(exc))

            def _run_mesh_sequence_check(self):
                if self.session is None or self.timer.isActive():
                    self.status.setText(tr("mesh_load_a_part_and_stop_the_simulation"))
                    return
                from smbs_core.kinematics2d.mesh_sequence_screen import screen_mesh_sequence

                if self._task_progress_busy:
                    return
                self._set_task_result("Mesh", "running", tr("checking_for_collisions"))
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

                    from freecad_smbs.ui.mesh_preferences import mesh_defaults
                    default_budget, default_overlap = mesh_defaults()
                    latest_collision = None

                    def capture_collision(hit, component, tool, common):
                        nonlocal latest_collision
                        latest_collision = (hit, component, tool, common)

                    report = screen_mesh_sequence(
                        self.session, cycle,
                        tuple(step.operation_id for step in operations),
                        budget_seconds=default_budget,
                        min_overlap_mm3=default_overlap,
                        progress=progress, on_collision=capture_collision,
                    )
                    self._latest_mesh_collision = latest_collision
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
                    hits = len(report["findings"])
                    conditional = len(report["conditional_findings"])
                    unresolved = len(report["unresolved"])
                    if hits:
                        state, summary = "error", tr("confirmed_collisions", count=hits)
                    elif report["pending"] or unresolved:
                        state, summary = "warning", tr("the_check_is_incomplete")
                    elif conditional:
                        state, summary = "warning", tr("conditional_end_zone_overlaps", count=conditional)
                    else:
                        state, summary = "done", tr("no_collisions_found_at_the_checked_positions")
                    tool_names = {"punch": tr("punch_44f13e39"), "die": tr("die_f5194d67")}
                    findings = []
                    for label, entries in (
                        (tr("collision_951bdd0b"), report["findings"]),
                        (tr("end_zone_overlap"), report["conditional_findings"]),
                        (tr("could_not_verify"), report["unresolved"]),
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
                        "bends_checked_time_s",
                        checked=len(report["completed"]), total=len(operations),
                        seconds=report["elapsed_seconds"],
                    )
                    if report["pending"]:
                        details += "\n" + tr("remaining", order=", ".join(report["pending"]))
                        reason = report["reason"]
                        details += "\n" + (tr("time_limit_reached") if reason == "TIME_BUDGET" else reason)
                    self._set_task_result("Mesh", state, summary, details, "\n".join(findings))
                    self._mesh_task_result_identity = self._mesh_collision_identity
                    message = f"Mesh · {summary} {details}"
                    self.status.setText(message)
                    self.manual_check_summary.setText(message)
                except (ValueError, RuntimeError, AttributeError) as exc:
                    self._set_task_result("Mesh", "error", tr("check_failed"), str(exc))
                    self.status.setText("Mesh: " + tr_message(exc))
                finally:
                    self._set_task_busy(False)

            def _validate_export_manual_order(self, coarse_occ=False, *, show_preview=False):
                """Validate selected manual order: Stage A/B plus actual FCStd 3D.

                The backgauge stays visual-only. Simulation/slider retain
                their separate UNVERIFIED and nonblocking preview paths.
                """
                if self.session is None:
                    self.status.setText(tr("load_a_part_first"))
                    return
                if self.session.controller.has_stages:
                    self.status.setText(
                        tr("occ_does_not_verify_staged_bends_the_result_is")
                    )
                    return
                if self._task_progress_busy or not self._apply_typed_manual_order():
                    return
                prefix = tuple(self._manual_sequence_prefix)
                order = tuple(self.session.bend_order)
                if prefix and order[:len(prefix)] != prefix:
                    self.status.setText(
                        tr("the_manual_order_changed_renumber_the_bend_lines")
                    )
                    return
                selected = prefix or order  # Move Up/Down is a valid full order.
                if not selected:
                    self.status.setText(tr("no_bends_selected"))
                    return
                if (self.timer.isActive()
                        or self.session.playback_status == PlaybackStatus.PLAYING):
                    self.status.setText(
                        tr("stop_the_simulation_before_checking_the_manual_sequence")
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
                    self.manual_validate_3d_btn,
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
                    tr("bends",
                        check=tr("quick_occ_check_5_angles") if coarse_occ
                        else tr("full_adaptive_3d_check"),
                        done=len(selected), total=len(order),
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
                            tr("select_a_valid_punch_and_die_with_verified_geometric"),
                        )
                        return

                    self._sync_tooling_to_active_bend()
                    if not self.tooling_setup.aligned:
                        self._record_manual_precheck_failure(
                            selected,
                            tr("the_selected_tooling_is_not_aligned_with_the_current"),
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
                            tr("virtual_length_is_incompatible_with_physical_tool_sections"),
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
                            tr("3d_check_bend", step=step_no, total=count, bend=bend_id)
                        )

                    def on_orientation_retry(bend_id, reverse_axis):
                        degrees = 180 if reverse_axis else 0
                        update_progress(
                            tr("checking_second_orientation", bend=bend_id, degrees=degrees)
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
                        tr("checking_the_sequence_assisted_lift_becomes_available_if_a")
                    )
                    self.manual_show_collision_btn.setText(
                        tr("show_collision_in_3d")
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
                            self._conditional_bends_status[step["bend_id"]] = tr("conditional_end_zone_allowance_physical_confirmation_of_tool_relief")
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
                                tr("retract_blocked_by_the_punch_check_the_assumed_coupled")
                            ).format(
                                lift_candidate["bend_id"],
                                (tr("alternative_orientation").format(
                                    180 if lift_candidate.get(
                                        "reverse_axis", False
                                    ) else 0
                                ) if is_alternate else ""),
                            ) if can_lift else
                            tr("coupled_lift_check_is_available_when_the_workpiece_intersects")
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
                            tr("assisted_lift_no_confirmed_punch_collision_during_retract_yet")
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
                        self.manual_simulate_btn.setText(tr("simulate_result"))
                    reason = physical["reason"]
                    last_step = physical["steps"][-1] if physical["steps"] else {}
                    blocker = last_step.get("first_blocker") or {}
                    last_bend = last_step.get("bend_id", "")
                    tool_setup_issue = {
                        "INSUFFICIENT_PUNCH_COVERAGE": tr("punch_does_not_cover_the_bend_line"),
                        "INSUFFICIENT_DIE_COVERAGE": tr("die_does_not_cover_the_bend_line"),
                        "TOOL_ASSEMBLY_GAP": tr("gap_between_tool_segments_on_the_bend_line"),
                        "MISSING_TOOLING_EVIDENCE": tr("tool_dimensions_could_not_be_verified"),
                    }.get(last_step.get("tool_setup_status"))
                    if physical_status == "COLLISION":
                        pair = last_step.get("punch_die_pair") or {}
                        if pair.get("status") == "COLLISION":
                            message = tr("punch_collides_with_die", bend=last_bend)
                        else:
                            tool = {
                                "PUNCH": tr("punch_44f13e39"), "DIE": tr("die_f5194d67"),
                                "PUNCH_AND_DIE": tr("punch_and_die"),
                            }.get(blocker.get("tool"), tr("tooling_3285810a"))
                            phase = blocker.get("phase", "")
                            message = tr("workpiece_collision", bend=last_bend, tool=tool, phase=phase)
                    elif physical_status == "SAMPLED_3D_CLEAR_NOT_CONTINUOUS":
                        message = tr("no_collisions_at_the_checked_3d_positions")
                    elif physical_status == "CONDITIONAL":
                        message = tr("conditional_result_intersection_at_the_punch_end")
                    elif reason == "coarse_OCC_samples_only_uninspected_intervals_and_tool_pair":
                        message = tr("quick_check_no_collisions_at_sampled_positions")
                    elif reason == "selected_manual_prefix_only_remaining_bends_not_checked":
                        message = tr("no_blocking_collisions_in_the_selected_part_of_the")
                    elif tool_setup_issue:
                        message = tr("message", bend=last_bend, issue=tool_setup_issue)
                    elif last_bend:
                        message = tr("3d_check_incomplete", bend=last_bend)
                    else:
                        message = tr("3d_check_unavailable_check_the_tooling_files")

                    summary_lines = [
                        tr("bends_processed", message=message, complete=complete_steps, total=len(order)),
                    ]
                    if changed_orientation_bends:
                        summary_lines.append(tr("orientation_changed_by_180_for", bends=", ".join(changed_orientation_bends)))
                    both_blocked = [
                        item["bend_id"] for item in orientation_attempts
                        if item["result"] == "BOTH_ORIENTATIONS_COLLIDE"
                    ]
                    if both_blocked:
                        summary_lines.append(tr("collision_in_both_orientations_b94aca3c", bends=", ".join(both_blocked)))
                    if crosscheck:
                        summary_lines.append(
                            tr("2d_and_3d_results_disagree", bends=", ".join(item["bend_id"] for item in crosscheck))
                        )
                    elif disagreement:
                        summary_lines.append(tr("2d_diagnostics_are_incomplete"))
                    summary_lines.append(
                        tr("the_3d_check_samples_positions_with_the_punch_and")
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
                            tr("not_saved_to_fcstd") + str(storage_exc)
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
                        tr("manual_sequence_check_failed", error=exc),
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
                    self.status.setText(tr("load_the_part_s_flat_pattern_first"))
                    return
                if self._task_progress_busy or not self._apply_typed_manual_order():
                    return
                order = tuple(self.session.bend_order)
                if not order:
                    self.status.setText(tr("there_are_no_bends_to_simulate"))
                    return
                prefix = tuple(self._manual_sequence_prefix)
                if prefix and order[:len(prefix)] != prefix:
                    self.status.setText(
                        tr("the_manual_order_changed_review_the_selected_bend_lines")
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
                        tr("show_collision_in_3d")
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
                        raise RuntimeError(tr("reset_changed_the_selected_bend_sequence"))
                except Exception as exc:
                    self._manual_preview_unverified = False
                    self.status.setText(
                        tr("could_not_prepare_manual_simulation") + str(exc)
                    )
                    return
                self._configure_for_active_bend()
                self._update_tooling_status_label()
                self._manual_preview_notice.setText(
                    self.manual_check_summary.text().splitlines()[0]
                    if matched_occ_report is not None else
                    tr("manual_preview_collisions_unchecked")
                )
                self._manual_preview_notice.show()
                if matched_occ_report is None:
                    self.physical_check_result.setText(
                        tr("unverified_manual_simulation_tool_backgauge_and_machine_collisions_have")
                    )
                self._open_coin_preview()
                if not autoplay:
                    return
                # Camera stays fixed; use the explicit Fit control to reframe.
                self.play_all()
                if self.session.playback_status == PlaybackStatus.PLAYING:
                    scope = (
                        tr("first_bends").format(
                            preview_count, len(order)
                        ) if preview_count < len(order) else
                        tr("all_bends").format(len(order))
                    )
                    self.status.setText(
                        tr("manual_simulation").format(
                            scope,
                            tr("saved_sampled_occ_results")
                            if matched_occ_report is not None
                            else tr("collisions_not_checked"),
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
                    self.status.setText(tr("load_a_sheetmetal_unfold_first"))
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
                    self.status.setText(tr("load_a_sheetmetal_unfold_first"))
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
                        tr("order_collisions_not_checked").format(
                            len(prefix),
                            len(self.session.bend_order) if self.session else 0,
                            " → ".join(prefix) or tr("sequence_is_empty"),
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
                    self.status.setText(tr("load_a_sheetmetal_unfold_first"))
                    return False
                if self.session.controller.has_stages:
                    self.status.setText(
                        tr("for_a_staged_bend_reorder_operations_by_dragging_table")
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
                        tr("line_is_already_numbered").format(
                            bend_id, self._manual_sequence_prefix.index(bend_id) + 1)
                    )
                    return
                if bend_id not in self.session.fold_state.bends:
                    self.status.setText(tr("unknown_line") + str(bend_id))
                    return
                if not self.session.can_reorder():
                    self.status.setText(
                        tr("stop_and_reset_the_simulation_before_changing_the_sequence")
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
                    tr("manual_order_not_checked").format(
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
                self._flat_drawn_operation_ids = ()
                if self.session is None:
                    scene.addSimpleText(tr("flat_pattern_not_loaded"))
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
                from freecad_smbs.ui.flat_sequence_order import displayed_operations, operation_numbers
                controller = self.session.controller
                staged = controller.has_stages
                steps = displayed_operations(controller,
                    manual_prefix=self._manual_sequence_prefix,
                    editing=self._manual_selection_enabled,
                    catalog_prefix=self._preview_only_planar_order or ())
                self._flat_drawn_operation_ids = tuple(step.operation_id for step in steps)
                names = {"PREBEND": tr("prebend_a2eedeaa"), "FINISH": tr("finish_bend_34eca2bf"), "SINGLE": tr("bend_c27b8b99")}
                operation_labels = {
                    bid: [f"{index}. {step.operation_id} · {names[step.kind]} {step.target_angle_deg:+.2f}°"
                          if staged else str(index) for index,step in entries]
                    for bid,entries in operation_numbers(steps).items()
                }
                active_step = controller.operation_at(self.session.active_bend_index)
                for conn in self.session.panel_build.connections:
                    bend_id = conn.bend_id
                    (x1, y1), (x2, y2) = conn.axis_xy
                    chosen = bend_id in operation_labels
                    active = chosen and (
                        active_step.bend_id == bend_id
                        if staged or not self._manual_selection_enabled
                        else bool(steps) and steps[-1].bend_id == bend_id
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
                        else "{}{} {:+.1f}°".format(
                            (" / ".join(operation_labels[bend_id]) + ". ") if chosen else "",
                            bend_id, conn.angle_deg)
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
                    "SAMPLED_CLEAR": tr("passed"),
                    "CONDITIONAL": tr("conditional"),
                    "COLLISION": tr("collision"),
                    "NOT_EVALUATED": tr("not_run"),
                    "UNVERIFIED": tr("not_checked"),
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
                        tr("stage_complete") if done else (tr("active_stage") if is_active else tr("waiting"))
                    ) + " [UNVERIFIED]"
                step_name = {
                    "PREBEND": tr("prebend_a2eedeaa"), "FINISH": tr("finish_bend_34eca2bf"), "SINGLE": tr("normal"),
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
                    self.status.setText(tr("no_pre_bend_is_defined_for", bend=bend_id))
                    return False
                if (self.timer.isActive() or self.playing
                        or not self.session.can_reorder()):
                    self.status.setText(
                        tr("stop_and_reset_the_simulation_before_removing_the_prebend")
                    )
                    return False
                final = controller.target_for(bend_id)
                decision = QtWidgets.QMessageBox.question(
                    parent or self,
                    tr("remove_prebend"),
                    tr("remove_intermediate_angle_finish_bend_will_become_a_regular",
                       prebend=bend_id + ".1", intermediate=prebend.target_angle_deg,
                       finish=bend_id + ".2", bend=bend_id, final=final),
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
                    self.status.setText(tr("could_not_remove_pre_bend", error=exc))
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
                    tr("pre_bend_removed_regular_bend_restored_to",
                       bend=bend_id, angle=final)
                )
                return True

            def _edit_prebend_dialog(self, bend_id):
                """Choose a pre-bend angle and drag ONLY that step among locked steps."""
                if self.session is None or self._task_progress_busy:
                    return
                if not self.session.can_reorder():
                    self.status.setText(tr("reset_the_simulation_before_adding_or_editing_the_prebend"))
                    return
                controller = self.session.controller
                final = controller.target_for(bend_id)
                current = next((
                    step for step in controller.operation_steps
                    if step.bend_id == bend_id and step.kind == "PREBEND"
                ), None)
                dialog = QtWidgets.QDialog(self)
                dialog.setWindowTitle(tr("pre_bend_finish_bend", bend=bend_id))
                dialog.setMinimumWidth(400)
                layout = QtWidgets.QVBoxLayout(dialog)
                explanation = QtWidgets.QLabel(
                    tr("physical_bend_nfinal_angle_unchanged_nset_an_intermediate_absolute",
                       bend=bend_id, angle=final)
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
                row.addRow(tr("intermediate_angle"), spin)
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
                        tr("pre_bend_drag", operation=op_id)
                        if op_id == pre_id else
                        tr("finish_to_fixed", operation=op_id, angle=angle)
                        if op_id == finish_id else
                        tr("fixed", operation=op_id, angle=angle)
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
                    tr("keep_the_finish_bend_after_its_prebend_the_flat")
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
                            tr("the_prebend_must_precede_its_finish_bend")
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
                        tr("remove_prebend_and_restore_single_bend"), dialog
                    )
                    delete_button.setObjectName("SMBS_DeletePrebend")
                    delete_button.setToolTip(
                        tr("remove_1_replace_2_with_the_original_bend_and").format(
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
                        operation = tr("prebend_finish_at").format(
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
                        tr("reset_the_simulation_before_reordering_stages")
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
                    self.status.setText(tr("invalid_stage_move_the_original_order_was_restored"))
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
                    tr("stage_order") + " → ".join(operation_ids)
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
                self._partial_preview_limit = None
                self._preview_only_planar_order = None
                if fast_apply:
                    self._close_sequence_edit_controls()
                    self._selected_planar_gauge_order = ()
                    self._selected_planar_gauge_positions = {}
                    self._selected_planar_gauge_model_id = None
                    self._machine_gauge_envelope_cache = None
                    self._ergonomic_loading_cache_key = None
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
                self._clear_manual_check_feedback()
                self._backgauge_preview_cache.clear()
                self._manual_preview_unverified = self.session.controller.has_stages
                self._manual_preview_notice.setText(
                    tr("prebend_finish_kinematic_simulation_sequence_collision_check_not_performed")
                    if self.session.controller.has_stages else
                    tr("manual_preview_collisions_unchecked")
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
                    if not self._playback_alive() or not self._qt_is_valid(self._flat_view):
                        return
                    # QGraphicsScene is independent of native OCC/Coin3D.
                    # Do not skip this redraw even if the flat tab is already
                    # selected: currentChanged will not fire in that case.
                    self._draw_flat_pattern(reset_camera=False)
                    if self._playback_alive() and self._qt_is_valid(self.status):
                        self.status.setText(message + " | 3D preview requires refresh")
                    _desktop_debug_event(
                        "FAST_APPLY_LIGHTWEIGHT_REFRESH",
                        workspace_alive=self._playback_alive(),
                        operation_count=len(self.session.controller.operation_steps),
                        operation_ids=tuple(op.operation_id for op in self.session.controller.operation_steps),
                        flat_operation_ids=getattr(self,"_flat_drawn_operation_ids",()),
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
                self._preview_only_planar_order = None
                self._close_sequence_edit_controls()
                self._sync_manual_order_input()
                self._manual_3d_verdicts.clear()
                self._manual_retract_verdicts.clear()
                self._last_manual_sequence_report = None
                self._clear_manual_check_feedback()
                self._manual_preview_unverified = bool(self.session.controller.has_stages)
                self._manual_preview_notice.setText(
                    tr("prebend_finish_preliminary_simulation_collisions_not_checked")
                    if self.session.controller.has_stages else
                    tr("manual_preview_collisions_unchecked")
                )
                self._manual_preview_notice.setVisible(self.session.controller.has_stages)
                self._draw_flat_pattern()
                self._update_manual_hint()
                self._configure_for_active_bend()
                if self.tooling_setup.aligned and not self._manual_selection_enabled:
                    self._sync_tooling_to_active_bend()
                    self.draw()
                self.status.setText(
                    tr("bend_sequence_kinematically_simulated").format(
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
                        tooltip = tr("checked_rotate_the_blank_180_unchecked_0")
                        if self.session.controller.has_stages:
                            tooltip += tr("this_setting_applies_only_to_operation_not_to_every", operation=op.operation_id, bend=bend_id)
                        if (
                            (op.operation_id != bend_id and
                             op.operation_id in self._manual_operation_orientation_overrides)
                            or (op.operation_id == bend_id and
                                bend_id in self._manual_orientation_overrides)
                        ):
                            tooltip += (
                                "\n" + (
                                    tr("position_suggested_by_fast_unverified")
                                    if op.operation_id in self._fast_advisory_orientation_keys
                                    else tr("position_set_manually")
                                ) + tr("right_click_restores_automatic_selection")
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
                                edit_btn.setText(tr("edit"))
                                edit_btn.setToolTip(
                                    tr("edit_prebend") + op.operation_id
                                )
                                edit_btn.clicked.connect(
                                    lambda _checked=False, bid=bend_id:
                                    self._edit_prebend_dialog(bid)
                                )
                                delete_btn = QtWidgets.QToolButton(controls)
                                delete_btn.setText("×")
                                delete_btn.setObjectName("SMBS_DeletePrebendRow")
                                delete_btn.setToolTip(
                                    tr("remove_prebend_and_restore_normal_bend").format(
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
                                add_btn.setText("+" if op.kind == "SINGLE" else tr("edit"))
                                add_btn.setToolTip(
                                    tr("add_or_edit_intermediate_bend_angle")
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
                                    "+" if op.kind == "SINGLE" else tr("edit")
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
                    if self._viewer_tabs.currentWidget() is self._flat_view:
                        self._draw_flat_pattern(reset_camera=False)

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
                    _error(tr("invalid_angle_change_rejected").format(exc))
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
                    self.status.setText(tr(
                        "full_cycle_stopped_due_to_geometry_switch_to_bend",
                        reason=self._machine_pose_fault))
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
                    self.status.setText(tr("auto_play_bend_timer_ms",
                                           mode=self.execution_mode.value,
                                           bend=self.session.active_bend_id,
                                           timer=self.timer.interval()))
                else:
                    self.status.setText(tr("auto_play_did_not_start_status",
                                           status=self.session.playback_status.value,
                                           reason=self._machine_pose_fault or tr("check_the_sequence_state")))
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
                        tr("cannot_continue_this_full_cycle_choose_bend_only_in").format(
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
                    tr("playback_reached_the_check_boundary_bends")
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
                        tr("auto_play_stopped", status=self.session.playback_status.value)
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
                                tr("return_to_standard_3d_view")
                            )
                        rendered = self._open_coin_preview()
                        reason = (
                            tr("occ_collision_angle_ram_z_mm")
                        ).format(
                            native_hit["bend_id"], hit_phase,
                            float(native_pose["signed_angle_deg"]),
                            float(native_pose["ram_z_mm"]),
                            tr("saved_occ_frame_displayed_in_coin3d")
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
                        self.status.setText(tr("auto_play_completed_bends",
                                               mode=self.execution_mode.value,
                                               count=len(self.session.bend_order),
                                               warning=(tr("manual_preview_without_collision_checks")
                                                        if self._manual_preview_unverified else "")))
                        _log("Sequence playback completed")
                    else:
                        phase = (
                            pc.phase.value
                            if pc is not None and
                            self.execution_mode == CycleExecutionMode.FULL_CYCLE
                            else "BEND_ONLY"
                        )
                        self.status.setText(
                            tr("auto_play_bend_angle_ms",
                                mode=self.execution_mode.value,
                                phase=phase,
                                bend=self.session.active_bend_id,
                                angle=self.session.current_angle_deg,
                                timer=self.timer.interval(),
                                check=(" | OCC:" + str(
                                    self._manual_3d_verdicts.get(
                                        self.session.active_bend_id,
                                        "UNVERIFIED",
                                    )
                                ) + tr("sampled_check")
                                 if self._matching_manual_occ_report(
                                     tuple(self.session.bend_order)
                                 ) is not None else
                                 " | UNVERIFIED"))
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
                    self.status.setText(reason + tr("state_frozen_use_reset_or_change_mode_to_restart"))

            def fit(self):
                if not self._preview_3d._native and self._viewer_tabs.currentWidget() is self._flat_view:
                    self._flat_view.reset_camera()
                else:
                    self._preview_3d.reset_camera(whole_scene=self.machine_preview_checkbox.isChecked())

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
                self.status.setText(tr("simulation_stopped_b76a8926", reason=reason))

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
                finger_count = len(self._backgauge_stations())
                key = (id(session), tuple(session.bend_order), finger_count,
                       tuple(sorted(orientations.items())),
                       frozenset(self._fast_advisory_orientation_keys))
                cached = self._machine_gauge_envelope_cache
                if cached is not None and cached[0] == key:
                    return cached[1]
                from smbs_core.kinematics2d.gauge_preview import (
                    sequenced_gauge_envelopes,
                )
                values, errors = sequenced_gauge_envelopes(
                    session, finger_count=finger_count,
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
                        self._sequenced_gauge_envelopes(orientations).get(bend_id, ((), ()))[1]
                        if model.automatic else ()
                    )
                    result = visual_gauge_target(model, bend_id, envelopes, translate=tr)
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
                stations = self._gauge_stations_for_index(index)
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
                        next_stations = self._gauge_stations_for_index(index + 1)
                        if len(stations) == len(next_stations):
                            stations = tuple(x + (next_x - x) * t
                                             for x, next_x in zip(stations, next_stations))
                        note = ""
                return stations, (s, z), note

            def _gauge_stations_for_index(self, index):
                if not self.backgauge_model.automatic:
                    return self._backgauge_stations()
                orientations = self._effective_loading_orientation_map()
                bend_id = self.session.bend_order[index]
                return self._sequenced_gauge_envelopes(orientations).get(bend_id, ((), ()))[0]

            def draw(self):
                if not self._playback_alive():
                    return False
                self._update_task_progress()
                self._update_loading_orientation_label()
                return self._update_3d_preview()

        return Panel(library_root=library_root)
