"""PressBrakeSim interactive 2.5D GUI workspace.

The Qt panel lives here; commands remain lightweight FreeCAD entry points.
This migration preserves the historical _PreviewPanel import through the command shim.
"""
from __future__ import annotations

import hashlib
import math
import sys
import time
import traceback
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

from freecad_smbs.ui.pressbrake_common import (
    _qt, _SLIDER_SCALE,
    Z_LAYER_AUXILIARY, Z_LAYER_DIE, Z_LAYER_PUNCH,
    Z_LAYER_SHEET_OTHER, Z_LAYER_SHEET_PANELS, Z_LAYER_SHEET_ZONE,
    Z_LAYER_SHEET_SURFACES, Z_LAYER_SHEET_NEUTRAL_AND_TANGENTS,
)
from freecad_smbs.ui.diagnostics_logging import _LOG_FILE_PATH, _log, _warn, _error
from freecad_smbs.integration.part_session import LoadedPartSession, execute_load_part
from freecad_smbs.ui.orbit_camera import orbit_project, clamp_pitch

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
            """2D/3D zoomable viewer; optional 3D orbit controlled by parent Panel."""

            def __init__(self, parent=None, orbit_callback=None):
                super().__init__(parent)
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
                self._trace_frame_dt = None
                self._trace_directory = _LOG_FILE_PATH.parent / "pressbrake_traces"
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

                # Primary actions now belong to the FreeCAD workbench toolbar.
                # Keep the disabled Calculate control for diagnostics/old API users,
                # but never render a second row of duplicate actions in the workspace.
                self.calculate = _button(QtWidgets, "Расчёт", self.unavailable, False)
                self.calculate.setParent(self)
                self.calculate.hide()

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

                # The MDI workspace contains only the read-only views and status.
                # All interactive controls are hosted separately by FreeCAD Task Panel.
                for item in entries[:2]:
                    insert(root, item)

                views = QtWidgets.QTabWidget(self)
                views.setObjectName("SMBS_ViewerTabs")
                views.addTab(self.view, "2.5D · Гибка")
                self._orbit_yaw = 35.0
                self._orbit_pitch = 25.0
                self._orbit_roll = 0.0
                self._last_3d_frame_at = -1.0
                self._fixed_3d_view_rect = None
                self._preview_3d = Canvas(orbit_callback=self._orbit_3d)
                self._preview_3d.setToolTip(
                    "Колесо: масштаб | Левая кнопка + движение: вращение | "
                    "Shift + левая кнопка: поворот вокруг оси взгляда | Fit: сброс масштаба"
                )
                self._preview_3d.setObjectName("SMBS_3D_ReadOnly")
                self._preview_3d_scene = QtWidgets.QGraphicsScene(self._preview_3d)
                self._preview_3d.setScene(self._preview_3d_scene)
                views.addTab(self._preview_3d, "3D · Просмотр")
                views.currentChanged.connect(self._on_view_changed)

                controls = QtWidgets.QTabWidget()
                controls.setWindowTitle("PressBrakeSim — управление")
                controls.setObjectName("SMBS_ControlTabs")
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
                self.operations.setMaximumHeight(16777215)
                insert(sim_layout, entries[10], 1)  # operation table
                controls.addTab(simulation, "Симуляция")

                tooling = QtWidgets.QWidget()
                tool_layout = QtWidgets.QVBoxLayout(tooling)
                insert(tool_layout, entries[2])  # tooling group
                tool_layout.addStretch(1)
                controls.addTab(tooling, "Оснастка")

                settings = QtWidgets.QWidget()
                settings_layout = QtWidgets.QVBoxLayout(settings)
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
                controls.addTab(settings, "Настройки")

                root.addWidget(views, 1)
                self._viewer_tabs = views
                self._control_tabs = controls
                self._task_panel_host = None

            def show_control_tab(self, index: int):
                self._control_tabs.setCurrentIndex(index)

            def attach_task_panel(self, task):
                self._task_panel_host = task

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

                # Re-orient the camera, not the workpiece or its FoldState.
                # Orthographic projection with centroid-based orbit target.
                all_vertices = [v for f in faces for v in f.vertices]
                if not all_vertices:
                    scene.addSimpleText("3D: no visible faces")
                    return
                center = tuple(
                    sum(p[axis] for p in all_vertices) / len(all_vertices)
                    for axis in range(3)
                )
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
                    "3D FULL PART | {} | ORBIT CAMERA | {} | KINEMATIC_PREVIEW".format(
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

            def unavailable(self):
                pass  # Disabled controls for Phase 5B/6 features.

            def _auto_select_default_tooling(self):
                """Auto-select verified default tooling (puancon / matrica) if none selected."""
                if self.tooling_setup.punch is not None and self.tooling_setup.die is not None:
                    return
                try:
                    tools = self.tool_library.list_tools()
                    for t in tools:
                        if t.validation_status != ValidationStatus.INVALID:
                            if self.tooling_setup.punch is None and t.kind == ToolKind.PUNCH:
                                if "puancon" in t.tool_id.lower() or "punch" in t.tool_id.lower():
                                    self.tooling_setup.select_punch(t)
                            if self.tooling_setup.die is None and t.kind == ToolKind.DIE:
                                if "matrica" in t.tool_id.lower() or "die" in t.tool_id.lower():
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
                        self._preview_3d.reset_camera()
                    return
                rect = self.scene.sceneRect()
                if not rect.isEmpty():
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
                self._finish_motion_trace("WORKSPACE_CLOSED")
                self.timer.stop()
                self.playing = False
                task = self._task_panel_host
                self._task_panel_host = None
                if task is not None:
                    task.close_from_workspace()
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

        return Panel()
