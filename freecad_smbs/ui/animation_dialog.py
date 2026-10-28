"""
AnimationPlayerDialog — copy of SMBS_AnimationPlayer.FCMacro dialog (S.70, F.2+F.3a).

Adapted for workbench: no SMBS_REPO_PATH, no QFileDialog; artifacts passed in.
Headless-safe: all FreeCAD/PySide imports are lazy.
"""
from __future__ import annotations

from typing import Any, Optional

OBJECT_NAME = "SMBS_Animation"
TICK_INTERVAL_MS_DEFAULT = 500

_DialogClass: Optional[type] = None


def _to_fc_shape(shape: Any) -> Any:
    """OCC TopoDS_Shape -> FreeCAD Part.Shape. Lazy Part import."""
    if shape is None:
        return None
    import Part
    if isinstance(shape, Part.Shape):
        return shape
    if hasattr(Part, "__fromPythonOCC__"):
        try:
            return Part.__fromPythonOCC__(shape)
        except Exception:
            pass
    try:
        return Part.Shape(shape)
    except Exception:
        return None


def _update_feature(doc: Any, name: str, fc_shape: Any) -> Any:
    """In-place update of Part::Feature (macro parity). None -> safe remove."""
    import FreeCADGui as Gui
    if fc_shape is None:
        obj = doc.getObject(name)
        if obj is not None:
            doc.removeObject(name)
            doc.recompute()
            if hasattr(Gui, "updateGui"):
                Gui.updateGui()
        return None
    obj = doc.getObject(name)
    if obj is None:
        obj = doc.addObject("Part::Feature", name)
    obj.touch()
    obj.Shape = fc_shape
    obj.Visibility = True
    doc.recompute()
    if hasattr(Gui, "updateGui"):
        Gui.updateGui()
    return obj


def _compute_punch_offset_mm(frame: Any, max_stroke_mm: float = 100.0) -> float:
    """Computes punch descent offset in mm for animation playback (F.3b).

    Offset represents ram/punch stroke downwards from open height (0.0)
    towards bottom dead center (BDC). Step 0 (rest / flat pattern) is 0.0.
    Active bend steps descend towards BDC (positive offset down along Z).
    """
    if frame is None:
        return 0.0

    if isinstance(frame, dict):
        step_idx = frame.get("step_index", 0)
        is_bending = frame.get("is_bending")
        if is_bending is None:
            is_bending = (step_idx > 0)
    else:
        # Frame or StepView object
        step_view = getattr(frame, "step_view", frame)
        step_idx = getattr(step_view, "step_index", getattr(frame, "frame_index", 0))
        bend_id = getattr(step_view, "bend_id", None)
        is_bending = getattr(frame, "is_bending", bend_id is not None or step_idx > 0)

    if step_idx == 0 or not is_bending:
        return 0.0

    return float(max_stroke_mm)


class AnimationPlayerDialog:
    """Lazy wrapper — real Qt base resolved at instance creation."""

    def __new__(
        cls,
        artifacts: Any,
        player: Any,
        parent: Any = None,
        tick_interval_ms: int = TICK_INTERVAL_MS_DEFAULT,
    ) -> Any:
        global _DialogClass
        if _DialogClass is None:
            from PySide6 import QtCore, QtWidgets
            try:
                from smbs_core.geometry_backend.freecad_bootstrap import ensure_freecad_paths
                ensure_freecad_paths()
            except Exception:
                pass
            import FreeCAD as App
            import FreeCADGui as Gui

            class _Dialog(QtWidgets.QDialog):
                def __init__(
                    self,
                    artifacts: Any,
                    player: Any,
                    parent: Any = None,
                    tick_interval_ms: int = TICK_INTERVAL_MS_DEFAULT,
                ) -> None:
                    super().__init__(parent)
                    self.setWindowTitle("SMBS Animation Player")
                    self.resize(560, 260)

                    self.artifacts = artifacts
                    self.player = player
                    self.controller = None
                    self._first_show = True
                    self.machine = None
                    self.assembly = None
                    self.punch_shape = None
                    self.die_shape = None
                    self.machine_frame_shape = None
                    self.backgauge_shape = None

                    self._init_ui()
                    self._setup_timer(tick_interval_ms)
                    self._load_player_into_ui()

                def _init_ui(self) -> None:
                    layout = QtWidgets.QVBoxLayout(self)

                    self.lbl_header = QtWidgets.QLabel("—")
                    font = self.lbl_header.font()
                    font.setBold(True)
                    font.setPointSize(font.pointSize() + 2)
                    self.lbl_header.setFont(font)
                    layout.addWidget(self.lbl_header)

                    badges = QtWidgets.QHBoxLayout()
                    self.lbl_verif = QtWidgets.QLabel("—")
                    self.lbl_precision = QtWidgets.QLabel("—")
                    for lbl in (self.lbl_verif, self.lbl_precision):
                        lbl.setAlignment(QtCore.Qt.AlignCenter)
                        lbl.setMinimumHeight(28)
                        lbl.setStyleSheet("border-radius: 4px; padding: 2px 8px; color: white;")
                    badges.addWidget(self.lbl_verif)
                    badges.addWidget(self.lbl_precision)
                    layout.addLayout(badges)

                    self.lbl_warning = QtWidgets.QLabel("")
                    self.lbl_warning.setWordWrap(True)
                    self.lbl_warning.setStyleSheet("color: #616161; font-style: italic;")
                    layout.addWidget(self.lbl_warning)

                    tooling_row = QtWidgets.QHBoxLayout()
                    self.lbl_tooling = QtWidgets.QLabel("—")
                    self.lbl_machine_frame = QtWidgets.QLabel("—")
                    for lbl in (self.lbl_tooling, self.lbl_machine_frame):
                        lbl.setAlignment(QtCore.Qt.AlignCenter)
                        lbl.setMinimumHeight(28)
                        lbl.setStyleSheet(
                            "border-radius: 4px; padding: 2px 8px; color: white; background-color: rgb(127,127,127);"
                        )
                    tooling_row.addWidget(self.lbl_tooling)
                    tooling_row.addWidget(self.lbl_machine_frame)
                    layout.addLayout(tooling_row)

                    self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
                    self.slider.setMinimum(0)
                    self.slider.valueChanged.connect(self._on_slider_changed)
                    layout.addWidget(self.slider)

                    controls = QtWidgets.QHBoxLayout()
                    self.btn_reset = QtWidgets.QPushButton("Reset")
                    self.btn_back = QtWidgets.QPushButton("◀ Step")
                    self.btn_play = QtWidgets.QPushButton("▶ Play")
                    self.btn_fwd = QtWidgets.QPushButton("Step ▶")
                    self.cmb_direction = QtWidgets.QComboBox()
                    self.cmb_direction.addItem("Unfold (backward)")
                    self.cmb_direction.addItem("Fold (forward)")
                    self.btn_reset.clicked.connect(self._on_reset)
                    self.btn_back.clicked.connect(self._on_step_back)
                    self.btn_play.clicked.connect(self._on_play_pause)
                    self.btn_fwd.clicked.connect(self._on_step_fwd)
                    self.cmb_direction.currentIndexChanged.connect(self._on_direction_changed)
                    controls.addWidget(self.btn_reset)
                    controls.addWidget(self.btn_back)
                    controls.addWidget(self.btn_play)
                    controls.addWidget(self.btn_fwd)
                    controls.addWidget(self.cmb_direction)
                    layout.addLayout(controls)

                    self.lbl_status = QtWidgets.QLabel("")
                    self.lbl_status.setStyleSheet("color: #616161;")
                    layout.addWidget(self.lbl_status)

                def _setup_timer(self, ms: int) -> None:
                    self.timer = QtCore.QTimer(self)
                    self.timer.setInterval(ms)
                    self.timer.timeout.connect(self._on_tick)

                def _load_player_into_ui(self) -> None:
                    from smbs_core.viewer.fc_animation import AnimationController
                    self.controller = AnimationController(self.player)
                    try:
                        from smbs_core.models.tooling import ToolingKind
                        from smbs_core.Physics.machine_kinematics import PressBrakeMachine
                        self.machine = PressBrakeMachine.with_default_tooling()
                        self.assembly = self.machine.setup_assembly(self.player.backend)
                        self.punch_shape = self.assembly.get_world_shape(ToolingKind.PUNCH)
                        self.die_shape = self.assembly.get_world_shape(ToolingKind.DIE)
                        self.machine_frame_shape = self.assembly.get_world_shape(ToolingKind.MACHINE_FRAME)

                        from freecad_smbs.resources.tooling_paths import resolve_backgauge_fcstd, TOOL_LIBRARY_ROOT
                        bg_path = resolve_backgauge_fcstd(TOOL_LIBRARY_ROOT)
                        if bg_path and bg_path.is_file():
                            from smbs_core.geometry_backend.backends.fcstd_loader import FcstdToolingLoader
                            bg_tool = FcstdToolingLoader().load(str(bg_path), ToolingKind.BACKGAUGE)
                            self.backgauge_shape = bg_tool.shape
                        else:
                            self.backgauge_shape = None
                    except Exception as e:
                        # Rationale (O2): If tooling assembly fails, allow animation to proceed
                        # in degraded-but-honest mode (part kinematics still plays; tooling objects omitted).
                        self.lbl_status.setText(f"Tooling assembly failed: {e}")
                        self.machine = None
                        self.assembly = None
                    self.slider.setMaximum(self.player.total - 1)
                    self.controller.reset()
                    self._update_ui()
                    self.lbl_status.setText(f"Loaded run — {self.player.total} frames")
                    self._render_current()

                def _ensure_document(self) -> Any:
                    if App.ActiveDocument is None:
                        App.newDocument("SMBS_Animation")
                        try:
                            Gui.ActiveDocument = Gui.activeDocument()
                        except Exception:
                            pass
                    return App.ActiveDocument

                def _render_current(self) -> None:
                    frame = self.controller.current()
                    if frame is None:
                        return
                    doc = self._ensure_document()
                    fc_part = _to_fc_shape(frame.step_view.state.shape)
                    _update_feature(doc, OBJECT_NAME, fc_part)
                    fc_punch = _to_fc_shape(self.punch_shape) if self.punch_shape is not None else None
                    if fc_punch is not None:
                        punch_offset = _compute_punch_offset_mm(frame, max_stroke_mm=100.0)
                        if punch_offset != 0.0:
                            try:
                                import FreeCAD as App
                                fc_punch = fc_punch.translated(App.Vector(0.0, 0.0, -punch_offset))
                            except Exception:
                                pass
                    _update_feature(doc, "SMBS_Punch", fc_punch)
                    fc_die = _to_fc_shape(self.die_shape) if self.die_shape is not None else None
                    _update_feature(doc, "SMBS_Die", fc_die)
                    fc_frame = _to_fc_shape(self.machine_frame_shape) if self.machine_frame_shape is not None else None
                    _update_feature(doc, "SMBS_MachineFrame", fc_frame)
                    fc_backgauge = _to_fc_shape(self.backgauge_shape) if self.backgauge_shape is not None else None
                    _update_feature(doc, "SMBS_Backgauge", fc_backgauge)
                    if self._first_show:
                        try:
                            Gui.SendMsgToActiveView("ViewFit")
                        except Exception:
                            pass
                        self._first_show = False

                def _update_ui(self) -> None:
                    from smbs_core.viewer.fc_animation import (
                        format_verification_badge,
                        format_precision_badge,
                        format_step_header,
                        format_machine_frame_badge,
                        format_tooling_badge,
                    )
                    frame = self.controller.current()
                    if frame is None:
                        return
                    direction = getattr(self.player, "direction", "backward")
                    self.lbl_header.setText(format_step_header(frame, direction=direction))
                    vt, vc = format_verification_badge(frame)
                    pt, pc = format_precision_badge(frame)
                    self.lbl_verif.setText(vt)
                    self.lbl_verif.setStyleSheet(
                        f"background-color: rgb({int(vc[0]*255)},{int(vc[1]*255)},{int(vc[2]*255)}); "
                        f"border-radius: 4px; padding: 2px 8px; color: white;"
                    )
                    self.lbl_precision.setText(pt)
                    self.lbl_precision.setStyleSheet(
                        f"background-color: rgb({int(pc[0]*255)},{int(pc[1]*255)},{int(pc[2]*255)}); "
                        f"border-radius: 4px; padding: 2px 8px; color: white;"
                    )
                    self.lbl_warning.setText(frame.step_view.geometry_warning or "")
                    mt, mc = format_machine_frame_badge(self.machine_frame_shape is not None)
                    tt, tc = format_tooling_badge(self.punch_shape is not None, self.die_shape is not None)
                    self.lbl_machine_frame.setText(mt)
                    self.lbl_machine_frame.setStyleSheet(
                        f"background-color: rgb({int(mc[0]*255)},{int(mc[1]*255)},{int(mc[2]*255)}); "
                        f"border-radius: 4px; padding: 2px 8px; color: white;"
                    )
                    self.lbl_tooling.setText(tt)
                    self.lbl_tooling.setStyleSheet(
                        f"background-color: rgb({int(tc[0]*255)},{int(tc[1]*255)},{int(tc[2]*255)}); "
                        f"border-radius: 4px; padding: 2px 8px; color: white;"
                    )
                    self.slider.blockSignals(True)
                    self.slider.setValue(frame.frame_index)
                    self.slider.blockSignals(False)
                    self.btn_play.setText("❚❚ Pause" if self.controller.is_playing() else "▶ Play")

                def _on_direction_changed(self, index: int) -> None:
                    self.timer.stop()
                    direction = "forward" if index == 1 else "backward"
                    self.player.switch_direction(direction)
                    self.controller.reset()
                    self.slider.setMaximum(self.player.total - 1)
                    self._update_ui()
                    self._render_current()

                def _on_reset(self) -> None:
                    self.timer.stop()
                    self.controller.reset()
                    self._update_ui()
                    self._render_current()

                def _on_play_pause(self) -> None:
                    self.controller.toggle()
                    if self.controller.is_playing():
                        self.timer.start()
                    else:
                        self.timer.stop()
                    self._update_ui()
                    self._render_current()

                def _on_step_fwd(self) -> None:
                    self.timer.stop()
                    self.controller.step_forward()
                    self._update_ui()
                    self._render_current()

                def _on_step_back(self) -> None:
                    self.timer.stop()
                    self.controller.step_backward()
                    self._update_ui()
                    self._render_current()

                def _on_slider_changed(self, value: int) -> None:
                    self.timer.stop()
                    self.controller.pause()
                    # Seek: reset then advance to value (macro parity)
                    self.controller.player.reset()
                    for _ in range(value):
                        if self.controller.player.next() is None:
                            break
                    self._update_ui()
                    self._render_current()

                def _on_tick(self) -> None:
                    result = self.controller.tick()
                    if result is None or not self.controller.is_playing():
                        self.timer.stop()
                    self._update_ui()
                    self._render_current()

                def closeEvent(self, event: Any) -> None:
                    self.timer.stop()
                    doc = App.ActiveDocument
                    if doc is not None:
                        for name in (OBJECT_NAME, "SMBS_Punch", "SMBS_Die", "SMBS_MachineFrame", "SMBS_Backgauge"):
                            obj = doc.getObject(name)
                            if obj is not None:
                                doc.removeObject(name)
                    super().closeEvent(event)

            _DialogClass = _Dialog

        return _DialogClass(artifacts, player, parent, tick_interval_ms)
