"""Tooling library dialog for punches, dies, virtual clamps and backgauges."""
from __future__ import annotations
from freecad.bendseq.icon_theme import set_action_icon
from freecad.bendseq.ui.localization import tr, tr_message

from pathlib import Path
from typing import Any, Optional

_PanelClass: Optional[type] = None


def _parse_positions_mm(text: str) -> tuple[float, ...]:
    """Split comma-separated floats, sort, dedupe. Raises ValueError on non-float."""
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        return ()
    vals = [float(p) for p in parts]
    return tuple(sorted(set(vals)))


def _apply_clamp_config_fields(config: Any, fields: dict) -> Any:
    """dataclasses.replace(config, **fields). Raises ValueError on bad field names."""
    from dataclasses import replace

    try:
        return replace(config, **fields)
    except TypeError as e:
        raise ValueError(f"Invalid clamp config fields: {e}") from e


class ToolingPanel:
    """Lazy wrapper — real Qt base resolved at instance creation."""

    def __new__(cls, library: Any, parent: Any = None, *, clamps_only=False) -> Any:
        global _PanelClass
        if _PanelClass is None:
            from freecad.bendseq.commands.pressbrake_2d import _qt
            import FreeCAD as App
            QtCore, QtGui, QtWidgets = _qt()

            class _Dialog(QtWidgets.QDialog):
                def __init__(self, library: Any, parent: Any = None, *, clamps_only=False) -> None:
                    super().__init__(parent)
                    self.setWindowTitle(tr("smbs_tooling_library"))
                    screen = QtWidgets.QApplication.primaryScreen()
                    available = screen.availableGeometry() if screen else None
                    self.resize(min(700, available.width() - 40) if available else 700,
                                min(490, available.height() - 80) if available else 490)

                    self.library = library
                    self._workspace = parent if hasattr(parent, "_task_stack") else None
                    self.library_path = getattr(library, "library_root", None)
                    self._backgauge_fcstd = None
                    self._section_previews = {}

                    if clamps_only:
                        self.setWindowTitle(tr("virtual_clamps"))
                        layout = QtWidgets.QVBoxLayout(self)
                        layout.addWidget(self._build_clamps_tab())
                        self.status_label = QtWidgets.QLabel()
                        layout.addWidget(self.status_label)
                    else:
                        self._init_ui()
                        self._load_library()

                def _init_ui(self) -> None:
                    from freecad.bendseq.ui.pressbrake_theme import (
                        OUTER_MARGIN, SECTION_PADDING,
                        SECTION_GAP, style_action,
                    )
                    layout = QtWidgets.QVBoxLayout(self)
                    layout.setContentsMargins(
                        OUTER_MARGIN, OUTER_MARGIN,
                        OUTER_MARGIN, OUTER_MARGIN,
                    )
                    layout.setSpacing(SECTION_GAP)

                    self.tabs = QtWidgets.QTabWidget()
                    self.tabs.tabBar().setUsesScrollButtons(True)
                    self.tabs.addTab(self._build_punch_tab(), tr("punches_0"))
                    self.tabs.addTab(self._build_die_tab(), tr("dies_0"))
                    clamps_scroll = QtWidgets.QScrollArea()
                    clamps_scroll.setWidgetResizable(True)
                    clamps_scroll.setWidget(self._build_clamps_tab())
                    self.tabs.addTab(clamps_scroll, tr("clamps"))
                    self.tabs.addTab(self._build_backgauge_tab(), tr("backgauge"))
                    self.tabs.currentChanged.connect(self._on_selection_changed)

                    # Selected item details group box
                    self.info_group = QtWidgets.QFrame()
                    self.info_group.setObjectName("SMBS_SectionCard")
                    info_layout = QtWidgets.QVBoxLayout(self.info_group)
                    info_layout.setContentsMargins(
                        SECTION_PADDING, SECTION_PADDING,
                        SECTION_PADDING, SECTION_PADDING
                    )
                    info_layout.setSpacing(SECTION_GAP)
                    heading = QtWidgets.QLabel(tr("tool_details"))
                    heading.setObjectName("SMBS_SectionTitle")
                    heading.setAlignment(QtCore.Qt.AlignLeft)
                    info_layout.addWidget(heading)
                    self.lbl_selected_id = QtWidgets.QLabel(tr("selected_none"))
                    self.lbl_selected_meta = QtWidgets.QLabel(tr("metadata"))
                    self.lbl_selected_meta.setWordWrap(True)
                    info_layout.addWidget(self.lbl_selected_id)
                    info_layout.addWidget(self.lbl_selected_meta)
                    self.tool_picture = QtWidgets.QLabel()
                    self.tool_picture.setAlignment(QtCore.Qt.AlignCenter)
                    info_layout.addWidget(self.tool_picture)
                    self.info_group.setMaximumWidth(240)
                    self.tool_picture.setMinimumSize(180, 125)
                    self.tool_picture.setMaximumSize(220, 145)
                    content = QtWidgets.QHBoxLayout()
                    content.addWidget(self.tabs, 1)
                    content.addWidget(self.info_group)
                    layout.addLayout(content, 1)

                    self.tool_actions = QtWidgets.QWidget()
                    btn_layout = QtWidgets.QHBoxLayout(self.tool_actions)
                    self.import_tool_button = QtWidgets.QPushButton()
                    self.remove_tool_button = QtWidgets.QPushButton()
                    self.segments_button = QtWidgets.QPushButton()
                    self.generate_tool_button = QtWidgets.QPushButton(tr("parametric_tool_generator"))
                    for button, callback in (
                        (self.import_tool_button, self._import_selected_kind),
                        (self.remove_tool_button, self._remove_selected_kind),
                        (self.segments_button, self._configure_selected_kind),
                        (self.generate_tool_button, self._generate_tool),
                    ):
                        button.clicked.connect(callback)
                        btn_layout.addWidget(style_action(button))
                    layout.addWidget(self.tool_actions)
                    # Status bar label
                    self.status_label = QtWidgets.QLabel(tr("status_ready"))
                    self.status_label.setObjectName("SMBS_SectionCaption")
                    self.status_label.setWordWrap(True)
                    layout.addWidget(self.status_label)

                def _build_punch_tab(self) -> QtWidgets.QWidget:
                    widget = QtWidgets.QWidget()
                    vbox = QtWidgets.QVBoxLayout(widget)
                    vbox.setContentsMargins(16, 16, 16, 16)
                    vbox.setSpacing(16)

                    lbl_sections = QtWidgets.QLabel(
                        tr("set_the_length_and_quantity_of_segments_for_each")
                    )
                    lbl_sections.setObjectName("SMBS_SectionCaption")
                    lbl_sections.setWordWrap(True)
                    vbox.addWidget(lbl_sections)

                    self.punch_list = QtWidgets.QListWidget()
                    self.punch_list.itemSelectionChanged.connect(self._on_selection_changed)
                    vbox.addWidget(self.punch_list)
                    self.reverse_punch_checkbox = QtWidgets.QCheckBox(tr("rotate_punch_180"))
                    self.reverse_punch_checkbox.setToolTip(
                        tr("saved_for_this_punch_and_used_in_simulation_and")
                    )
                    self.reverse_punch_checkbox.setEnabled(False)
                    self.reverse_punch_checkbox.toggled.connect(self._save_punch_orientation)
                    vbox.addWidget(self.reverse_punch_checkbox)
                    return widget

                def _build_die_tab(self) -> QtWidgets.QWidget:
                    widget = QtWidgets.QWidget()
                    vbox = QtWidgets.QVBoxLayout(widget)
                    vbox.setContentsMargins(16, 16, 16, 16)
                    vbox.setSpacing(16)

                    self.die_list = QtWidgets.QListWidget()
                    self.die_list.itemSelectionChanged.connect(self._on_selection_changed)
                    vbox.addWidget(self.die_list)
                    return widget

                def _build_backgauge_tab(self) -> QtWidgets.QWidget:
                    widget = QtWidgets.QWidget()
                    vbox = QtWidgets.QVBoxLayout(widget)
                    vbox.setContentsMargins(16, 16, 16, 16)
                    vbox.setSpacing(16)

                    self.gauge_list = QtWidgets.QListWidget()
                    self.gauge_list.itemSelectionChanged.connect(self._on_selection_changed)
                    vbox.addWidget(self.gauge_list)
                    self._refresh_backgauge()
                    return widget

                def _show_picture(self, label, path, *, backgauge=False, mirrored=False):
                    from zipfile import BadZipFile
                    from xml.etree.ElementTree import ParseError

                    from freecad.bendseq.ui.tool_section_preview import (
                        draw_tool_section, tool_section_rings,
                    )

                    label.setToolTip("")
                    if path is None:
                        label.setText(tr("no_model_selected"))
                        return
                    try:
                        stamp = Path(path).stat().st_mtime_ns
                        color = label.palette().color(label.foregroundRole())
                        key = (Path(path), backgauge, mirrored, color.rgba())
                        cached = self._section_previews.get(key)
                        if cached is None or cached[0] != stamp:
                            rings = tool_section_rings(path, backgauge=backgauge)
                            image = draw_tool_section(
                                rings, color, QtCore, QtGui, mirrored=mirrored,
                            )
                            cached = self._section_previews[key] = (stamp, image)
                        label.setPixmap(cached[1])
                        label.setToolTip(tr("cross_section") + "\n" + str(path))
                    except (OSError, ValueError, RuntimeError, KeyError, BadZipFile, ParseError) as exc:
                        label.setText(tr("section_unavailable"))
                        label.setToolTip(tr_message(exc))

                def _refresh_backgauge(self):
                    from freecad.bendseq.resources.tooling_paths import resolve_backgauge_fcstd

                    self._backgauge_fcstd = resolve_backgauge_fcstd(self.library_path)
                    self.gauge_list.blockSignals(True)
                    self.gauge_list.clear()
                    if self._backgauge_fcstd is not None:
                        self.gauge_list.addItem(self._backgauge_fcstd.stem)
                        self.gauge_list.setCurrentRow(0)
                    self.gauge_list.blockSignals(False)
                    if hasattr(self, "info_group"):
                        self._on_selection_changed()

                def _backgauge_changed(self):
                    self._refresh_backgauge()
                    if self._workspace is not None:
                        self._workspace._backgauge_preview_cache.clear()
                        self._workspace._machine_gauge_envelope_cache = None
                        self._workspace._ergonomic_loading_cache_key = None
                        self._workspace.draw()

                def _import_backgauge(self):
                    from tempfile import NamedTemporaryFile
                    from shutil import copyfileobj
                    from zipfile import BadZipFile, ZipFile

                    filename, _ = QtWidgets.QFileDialog.getOpenFileName(
                        self, tr("load_backgauge"), "", "FreeCAD (*.FCStd)"
                    )
                    if not filename:
                        return
                    target = self._backgauge_fcstd or (
                        self.library_path / "backgauge" / "backgauge.FCStd"
                    )
                    if Path(filename).resolve() == target.resolve():
                        return
                    if target.exists() and QtWidgets.QMessageBox.question(
                        self, tr("backgauge"), tr("replace_the_current_model")
                    ) != QtWidgets.QMessageBox.Yes:
                        return
                    staged = None
                    try:
                        with ZipFile(filename) as archive:
                            archive.getinfo("Document.xml")
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with NamedTemporaryFile(dir=target.parent, delete=False) as temp:
                            staged = Path(temp.name)
                            with open(filename, "rb") as source:
                                copyfileobj(source, temp)
                        staged.replace(target)
                    except (OSError, BadZipFile, KeyError) as exc:
                        QtWidgets.QMessageBox.warning(self, tr("import_error"), tr_message(exc))
                        return
                    finally:
                        if staged is not None:
                            staged.unlink(missing_ok=True)
                    self._backgauge_changed()

                def _remove_backgauge(self):
                    if self._backgauge_fcstd is None:
                        return
                    if QtWidgets.QMessageBox.question(
                        self, tr("backgauge"), tr("remove_the_selected_model_from_the_library")
                    ) != QtWidgets.QMessageBox.Yes:
                        return
                    try:
                        self._backgauge_fcstd.unlink()
                    except OSError as exc:
                        QtWidgets.QMessageBox.warning(self, tr("removal_error"), tr_message(exc))
                        return
                    self._backgauge_changed()

                def _configure_backgauge(self):
                    if self._workspace is not None:
                        from freecad.bendseq.commands.backgauge_dialog import show_backgauge_settings

                        show_backgauge_settings(self._workspace, modal_parent=self)

                def _import_selected_kind(self):
                    if self.tabs.currentIndex() == 3:
                        self._import_backgauge()
                    else:
                        self._on_add_tool()

                def _remove_selected_kind(self):
                    if self.tabs.currentIndex() == 3:
                        self._remove_backgauge()
                    else:
                        self._on_remove_tool()

                def _configure_selected_kind(self):
                    if self.tabs.currentIndex() == 3:
                        self._configure_backgauge()
                    else:
                        self._on_edit_segments()

                def _build_clamps_tab(self) -> QtWidgets.QWidget:
                    widget = QtWidgets.QWidget()
                    vbox = QtWidgets.QVBoxLayout(widget)
                    vbox.setContentsMargins(16, 16, 16, 16)
                    vbox.setSpacing(16)

                    form = QtWidgets.QFormLayout()

                    self.spin_clamp_width = QtWidgets.QDoubleSpinBox()
                    self.spin_clamp_width.setRange(0.1, 500.0)
                    self.spin_clamp_width.setSingleStep(1.0)
                    self.spin_clamp_width.setSuffix(tr("mm_bd8bf092"))
                    form.addRow(tr("clamp_width_along_x_width_mm"), self.spin_clamp_width)

                    self.spin_clamp_height = QtWidgets.QDoubleSpinBox()
                    self.spin_clamp_depth = QtWidgets.QDoubleSpinBox()
                    for label, spin in ((tr("height"), self.spin_clamp_height),
                                        (tr("depth"), self.spin_clamp_depth)):
                        spin.setRange(0.1, 500.0)
                        spin.setSuffix(tr("mm_bd8bf092"))
                        form.addRow(label, spin)

                    self.spin_clamp_gap = QtWidgets.QDoubleSpinBox()
                    self.spin_clamp_gap.setRange(0.0, 200.0)
                    self.spin_clamp_gap.setSingleStep(1.0)
                    self.spin_clamp_gap.setSuffix(tr("mm_bd8bf092"))
                    self.spin_clamp_gap.setToolTip(tr("distance_between_adjacent_clamp_edges"))
                    form.addRow(tr("minimum_clamp_gap_gap_between_mm"), self.spin_clamp_gap)

                    self.spin_clamp_count = QtWidgets.QSpinBox()
                    self.spin_clamp_count.setRange(0, 1000)
                    self.spin_clamp_count.setSpecialValueText(tr("by_positions"))
                    form.addRow(tr("number_of_clamps"), self.spin_clamp_count)

                    self.spin_clamp_overhang = QtWidgets.QDoubleSpinBox()
                    self.spin_clamp_overhang.setRange(0.0, 1.0)
                    self.spin_clamp_overhang.setSingleStep(0.05)
                    self.spin_clamp_overhang.setDecimals(2)
                    form.addRow(tr("overhang_ratio"), self.spin_clamp_overhang)

                    self.spin_clamp_joint_tol = QtWidgets.QDoubleSpinBox()
                    self.spin_clamp_joint_tol.setRange(0.0, 10.0)
                    self.spin_clamp_joint_tol.setSingleStep(0.05)
                    self.spin_clamp_joint_tol.setDecimals(2)
                    self.spin_clamp_joint_tol.setSuffix(tr("mm_bd8bf092"))
                    form.addRow(tr("joint_tolerance_joint_tolerance_mm"), self.spin_clamp_joint_tol)

                    self.spin_clamp_min_under = QtWidgets.QSpinBox()
                    self.spin_clamp_min_under.setRange(1, 1000)
                    form.addRow(tr("minimum_clamps_under_a_segment"), self.spin_clamp_min_under)

                    self.edit_clamp_positions = QtWidgets.QLineEdit()
                    self.edit_clamp_positions.setPlaceholderText("-300, -100, 100, 300")
                    form.addRow(tr("clamp_centers_x_mm"), self.edit_clamp_positions)

                    self.chk_clamp_repositionable = QtWidgets.QCheckBox(tr("movable_clamps"))
                    form.addRow("", self.chk_clamp_repositionable)

                    self.chk_clamp_joints = QtWidgets.QCheckBox(tr("joints_must_rest_on_clamps"))
                    form.addRow("", self.chk_clamp_joints)

                    self.chk_clamp_no_gaps = QtWidgets.QCheckBox(tr("no_gaps_between_punch_sections"))
                    form.addRow("", self.chk_clamp_no_gaps)

                    vbox.addLayout(form)

                    btn_box = QtWidgets.QHBoxLayout()
                    btn_save = QtWidgets.QPushButton(tr("save_configuration"))
                    set_action_icon(btn_save, "btn_clamp_save_config.svg")
                    btn_save.clicked.connect(self._on_save_clamps)
                    btn_reset = QtWidgets.QPushButton(tr("reset"))
                    set_action_icon(btn_reset, "btn_clamp_reset.svg")
                    btn_reset.clicked.connect(self._on_reset_clamps)
                    btn_box.addWidget(btn_save)
                    btn_box.addWidget(btn_reset)
                    vbox.addLayout(btn_box)

                    self.clamp_scene = QtWidgets.QGraphicsScene(self)
                    self.clamp_preview = QtWidgets.QGraphicsView(self.clamp_scene)
                    self.clamp_preview.setMinimumHeight(100)
                    vbox.addWidget(self.clamp_preview, 1)
                    self._populate_clamps_ui()
                    return widget

                def _populate_clamps_ui(self) -> None:
                    if not self.library:
                        return
                    try:
                        cfg = (
                            self._workspace._current_clamp_config()
                            if self._workspace is not None
                            else self.library.get_clamp_config()
                        )
                    except Exception:
                        from freecad.bendseq.core.geometry_backend.io.clamp_validator import ClampConfig

                        cfg = ClampConfig.from_defaults()

                    self.spin_clamp_width.setValue(float(cfg.width_mm))
                    self.spin_clamp_height.setValue(cfg.height_mm)
                    self.spin_clamp_depth.setValue(cfg.depth_mm)
                    self.spin_clamp_gap.setValue(float(cfg.gap_between_mm))
                    self.spin_clamp_count.setValue(cfg.clamp_count)
                    self.spin_clamp_overhang.setValue(float(cfg.overhang_ratio))
                    self.spin_clamp_joint_tol.setValue(float(cfg.joint_tolerance_mm))
                    self.spin_clamp_min_under.setValue(int(cfg.min_clamps_under_punch))
                    pos_str = ", ".join(str(p) for p in cfg.positions_mm) if cfg.positions_mm else ""
                    self.edit_clamp_positions.setText(pos_str)
                    self.chk_clamp_repositionable.setChecked(bool(cfg.repositionable))
                    self.chk_clamp_joints.setChecked(bool(cfg.joints_must_be_on_clamps))
                    self.chk_clamp_no_gaps.setChecked(bool(cfg.no_gaps_between_punches))
                    self._draw_clamps(cfg)

                def _draw_clamps(self, cfg):
                    self.clamp_scene.clear()
                    for center in cfg.centres_mm:
                        self.clamp_scene.addRect(
                            center - cfg.width_mm / 2, 0, cfg.width_mm, cfg.height_mm,
                            QtGui.QPen(QtCore.Qt.NoPen), QtGui.QBrush(QtGui.QColor("#5485ac"))
                        )
                    if not cfg.centres_mm:
                        self.clamp_scene.addSimpleText(tr("no_clamps_configured"))
                    bounds = self.clamp_scene.itemsBoundingRect().adjusted(-5, -5, 5, 5)
                    self.clamp_preview.setSceneRect(bounds)
                    self.clamp_preview.fitInView(bounds, QtCore.Qt.KeepAspectRatio)

                def _on_reset_clamps(self) -> None:
                    self._populate_clamps_ui()
                    self._set_status(tr("clamp_settings_restored_to_saved_values"))

                def _on_save_clamps(self) -> None:
                    if not self.library:
                        return
                    try:
                        pos = _parse_positions_mm(self.edit_clamp_positions.text())
                    except ValueError as e:
                        self._set_status(tr("invalid_clamp_positions_c0f7c781", error=e), is_error=True)
                        QtWidgets.QMessageBox.warning(self, tr("input_error"), tr("invalid_clamp_position_format", error=e))
                        return

                    fields = {
                        "width_mm": self.spin_clamp_width.value(),
                        "height_mm": self.spin_clamp_height.value(),
                        "depth_mm": self.spin_clamp_depth.value(),
                        "gap_between_mm": self.spin_clamp_gap.value(),
                        "clamp_count": self.spin_clamp_count.value(),
                        "overhang_ratio": self.spin_clamp_overhang.value(),
                        "joint_tolerance_mm": self.spin_clamp_joint_tol.value(),
                        "min_clamps_under_punch": self.spin_clamp_min_under.value(),
                        "positions_mm": pos,
                        "repositionable": self.chk_clamp_repositionable.isChecked(),
                        "joints_must_be_on_clamps": self.chk_clamp_joints.isChecked(),
                        "no_gaps_between_punches": self.chk_clamp_no_gaps.isChecked(),
                    }

                    try:
                        cfg = (
                            self._workspace._current_clamp_config()
                            if self._workspace is not None
                            else self.library.get_clamp_config()
                        )
                        new_cfg = _apply_clamp_config_fields(cfg, fields)
                        if (self._workspace is None
                                or self._workspace.session is None):
                            # Standalone tooling and empty workspace retain
                            # the old shared-library configuration behavior.
                            self.library.set_clamp_config(new_cfg)
                        else:
                            # A loaded FCStd owns its own clamp configuration.
                            self._workspace._project_clamp_config = new_cfg
                        self._draw_clamps(new_cfg)
                        from freecad.bendseq.core.kinematics2d.developer_debug_log import debug_event
                        debug_event("CLAMP_CONFIG_SAVED",
                                    source=("project" if self._workspace is not None
                                            and self._workspace.session is not None else "library"),
                                    previous=cfg.to_dict(), config=new_cfg.to_dict())
                        if new_cfg != cfg and self._workspace is not None:
                            self._workspace._on_clamp_config_changed()
                        self._set_status(tr("clamp_configuration_saved"))
                        QtWidgets.QMessageBox.information(
                            self, tr("tooling_ab652d22"), (
                                tr("the_clamp_configuration_will_be_saved_in_this_fcstd")
                                if (self._workspace is not None and
                                    self._workspace.session is not None) else
                                tr("clamp_configuration_saved_to_the_library")
                            )
                        )
                    except Exception as e:
                        self._set_status(tr("could_not_save_clamp_configuration_cc57795d", error=e), is_error=True)
                        QtWidgets.QMessageBox.critical(
                            self, tr("error"), tr("could_not_save_clamp_configuration", error=e)
                        )

                def _set_status(self, text: str, is_error: bool = False) -> None:
                    # Keep text readable in the active FreeCAD theme.
                    self.status_label.setText(tr("status_e4572c00", text=tr_message(text)))
                    try:
                        if is_error:
                            App.Console.PrintError(f"SMBS ToolingPanel: {text}\n")
                        else:
                            App.Console.PrintMessage(f"SMBS ToolingPanel: {text}\n")
                    except Exception:
                        pass

                def _load_library(self) -> None:
                    from freecad.bendseq.core.geometry_backend.io.tooling_library import (
                        ToolingLibrary,
                        ToolingLibraryError,
                    )

                    try:
                        self._set_status(tr("library_loaded", path=self.library_path))
                        self._refresh_lists()
                    except ToolingLibraryError as e:
                        err_msg = str(e)
                        if (
                            "Missing required punch subdirectory" in err_msg
                            or "Missing required die subdirectory" in err_msg
                        ):
                            reply = QtWidgets.QMessageBox.question(
                                self,
                                tr("initialize_library"),
                                tr("the_directory_is_missing_the_required_punch_and_die", path=self.library_path),
                                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                            )
                            if reply == QtWidgets.QMessageBox.Yes:
                                try:
                                    (self.library_path / "punch").mkdir(parents=True, exist_ok=True)
                                    (self.library_path / "die").mkdir(parents=True, exist_ok=True)
                                    self.library = ToolingLibrary(self.library_path)
                                    self._set_status(tr("library_initialized"))
                                    self._refresh_lists()
                                    return
                                except Exception as create_err:
                                    self._set_status(tr("could_not_create_subdirectories", error=create_err), is_error=True)
                                    return
                        self._set_status(tr("could_not_load_tooling_library", error=e), is_error=True)
                        QtWidgets.QMessageBox.critical(self, tr("library_error"), tr_message(e))

                def _refresh_lists(self) -> None:
                    if not self.library:
                        return

                    # Retain the operator's exact tool selection through
                    # catalog refresh (notably after saving 13 segment types).
                    # Clearing the list previously selected the first item,
                    # so Preview could open a different tool instead of the selected model.
                    selected = self._current_descriptor()
                    selected_key = (
                        (selected.kind, selected.tool_id)
                        if selected is not None else None
                    )
                    self.punch_list.clear()
                    self.die_list.clear()

                    try:
                        catalog = self.library.list_tools()
                    except Exception as e:
                        self._set_status(tr("could_not_scan_tooling_directory", error=e), is_error=True)
                        return

                    for p in catalog.punches:
                        radius = p.metadata.get("radius_mm", "?")
                        angle = p.metadata.get("angle_deg", "?")
                        item = QtWidgets.QListWidgetItem(f"{p.tool_id}  (r={radius}mm, a={angle}°)")
                        item.setData(QtCore.Qt.UserRole, p)
                        self.punch_list.addItem(item)

                    for d in catalog.dies:
                        v_w = d.metadata.get("v_width_mm", "?")
                        angle = d.metadata.get("angle_deg", "?")
                        item = QtWidgets.QListWidgetItem(f"{d.tool_id}  (V={v_w}mm, a={angle}°)")
                        item.setData(QtCore.Qt.UserRole, d)
                        self.die_list.addItem(item)

                    self.tabs.setTabText(0, tr("punches", count=len(catalog.punches)))
                    self.tabs.setTabText(1, tr("dies", count=len(catalog.dies)))
                    if selected_key is not None:
                        widget = (
                            self.punch_list if selected_key[0] == "punch"
                            else self.die_list
                        )
                        for index in range(widget.count()):
                            desc = widget.item(index).data(QtCore.Qt.UserRole)
                            if (desc.kind, desc.tool_id) == selected_key:
                                widget.setCurrentRow(index)
                                break
                    for tool_list in (self.punch_list, self.die_list):
                        if tool_list.currentRow() < 0 and tool_list.count():
                            tool_list.setCurrentRow(0)
                    self._on_selection_changed()
                    self._set_status(tr("tool_list_refreshed"))

                def _current_descriptor(self) -> Any:
                    idx = self.tabs.currentIndex()
                    if idx == 0:
                        item = self.punch_list.currentItem()
                    elif idx == 1:
                        item = self.die_list.currentItem()
                    else:
                        return None
                    if item:
                        return item.data(QtCore.Qt.UserRole)
                    return None

                def _punch_profile_library(self):
                    if self._workspace is not None:
                        return self._workspace.tool_library
                    from freecad.bendseq.core.kinematics2d.tooling_25d import ToolLibrary25D

                    return ToolLibrary25D(self.library_path)

                def _refresh_punch_orientation(self, descriptor):
                    checkbox = self.reverse_punch_checkbox
                    profile = None
                    if descriptor is not None:
                        try:
                            profile = self._punch_profile_library().load_tool(
                                descriptor.tool_id, "PUNCH",
                            )
                        except (ValueError, RuntimeError, OSError) as exc:
                            self._set_status(str(exc), is_error=True)
                    previous = checkbox.blockSignals(True)
                    checkbox.setEnabled(profile is not None)
                    checkbox.setChecked(bool(profile and profile.punch_reversed))
                    checkbox.blockSignals(previous)

                def _save_punch_orientation(self, checked):
                    from dataclasses import replace

                    descriptor = self._current_descriptor()
                    if self.tabs.currentIndex() != 0 or descriptor is None:
                        return
                    library = self._punch_profile_library()
                    try:
                        profile = library.load_tool(descriptor.tool_id, "PUNCH")
                        updated = replace(profile, punch_reversed=checked)
                        library.save_tool(updated)
                    except (ValueError, RuntimeError, OSError) as exc:
                        self._refresh_punch_orientation(descriptor)
                        self._set_status(str(exc), is_error=True)
                        return
                    self._show_picture(self.tool_picture, descriptor.fcstd_path, mirrored=checked)
                    if self._workspace is not None:
                        self._workspace._on_punch_orientation_changed(updated)
                    self._set_status(tr("rotation", tool=updated.name,
                                        state=tr("enabled" if checked else "disabled")))

                def _on_selection_changed(self) -> None:
                    index = self.tabs.currentIndex()
                    tool_tab = index in (0, 1, 3)
                    self.info_group.setVisible(tool_tab)
                    self.tool_actions.setVisible(tool_tab)
                    self.generate_tool_button.setVisible(index in (0, 1))
                    if not tool_tab:
                        if index == 2:
                            self.clamp_preview.fitInView(
                                self.clamp_scene.sceneRect(), QtCore.Qt.KeepAspectRatio
                            )
                        return
                    if index == 3:
                        path = self._backgauge_fcstd if self.gauge_list.currentItem() else None
                        self.import_tool_button.setText(tr("import_backgauge"))
                        self.remove_tool_button.setText(tr("remove_backgauge"))
                        self.segments_button.setText(tr("backgauge_settings"))
                        set_action_icon(self.import_tool_button, "btn_library_gauge_import.svg")
                        set_action_icon(self.remove_tool_button, "btn_library_gauge_delete.svg")
                        set_action_icon(self.segments_button, "btn_library_gauge_settings.svg")
                        self.remove_tool_button.setEnabled(path is not None)
                        self.segments_button.setEnabled(self._workspace is not None)
                        self.lbl_selected_id.setText(
                            tr("selected", tool=path.stem) if path else tr("selected_none")
                        )
                        self.lbl_selected_meta.setText(
                            tr("path", path=path) if path else tr("metadata")
                        )
                        self._show_picture(self.tool_picture, path, backgauge=True)
                        return
                    self.generate_tool_button.setText(tr(("parametric_create_punch", "parametric_create_die")[index]))
                    self.import_tool_button.setText(tr(("import_punch", "import_die")[index]))
                    self.remove_tool_button.setText(tr(("remove_punch", "remove_die")[index]))
                    self.segments_button.setText(tr(("punch_segments", "die_segments")[index]))
                    set_action_icon(self.generate_tool_button, ("btn_create_punch.svg", "btn_create_die.svg")[index])
                    set_action_icon(self.import_tool_button, ("btn_library_punch_import.svg", "btn_library_die_import.svg")[index])
                    set_action_icon(self.remove_tool_button, ("btn_library_punch_delete.svg", "btn_library_die_delete.svg")[index])
                    set_action_icon(self.segments_button, ("btn_punch_segments.svg", "btn_die_segments.svg")[index])
                    desc = self._current_descriptor()
                    if index == 0:
                        self._refresh_punch_orientation(desc)
                    self.remove_tool_button.setEnabled(desc is not None)
                    self.segments_button.setEnabled(desc is not None)
                    self._show_picture(
                        self.tool_picture, desc.fcstd_path if desc else None,
                        mirrored=index == 0 and self.reverse_punch_checkbox.isChecked(),
                    )
                    if not desc:
                        self.lbl_selected_id.setText(tr("selected_none"))
                        self.lbl_selected_meta.setText(tr("metadata"))
                        return

                    sidecar_str = tr("yes") if desc.has_sidecar else tr("no")
                    self.lbl_selected_id.setText(tr(
                        "selected_metadata_file",
                        kind=tr(("punch", "die")[index]), tool=desc.tool_id,
                        sidecar=sidecar_str,
                    ))
                    meta_str = (
                        ", ".join(f"{k}={v}" for k, v in desc.metadata.items())
                        if desc.metadata
                        else tr("none")
                    )
                    segments = desc.metadata.get("segments") or []
                    inventory_text = ", ".join(
                        tr("mm_497c82f5").format(
                            float(seg.get("length_mm", 0)),
                            int(seg.get("quantity", 0)),
                        )
                        for seg in segments if isinstance(seg, dict)
                    ) or tr("not_set")
                    self.lbl_selected_meta.setText(
                        tr("metadata_segments_path",
                           metadata=meta_str, segments=inventory_text, path=desc.fcstd_path)
                    )

                def _generate_tool(self):
                    if not self.library or self.tabs.currentIndex() not in (0, 1):
                        return
                    from freecad.bendseq.ui.parametric_tool_dialog import show_generator
                    kind = ("punch", "die")[self.tabs.currentIndex()]
                    def on_created(path):
                        self._refresh_lists()
                        widget = self.punch_list if kind == "punch" else self.die_list
                        for index in range(widget.count()):
                            if widget.item(index).data(QtCore.Qt.UserRole).tool_id == path.stem:
                                widget.setCurrentRow(index)
                                break
                        if self._workspace is not None:
                            self._workspace._refresh_tooling_combos()

                    show_generator(self, self.library.library_root, kind, on_created=on_created)

                def _on_add_tool(self) -> None:
                    if not self.library:
                        return

                    cur_idx = self.tabs.currentIndex()
                    if cur_idx not in (0, 1):
                        QtWidgets.QMessageBox.information(
                            self,
                            tr("add_tool"),
                            tr("fcstd_models_can_be_added_on_the_punches_and"),
                        )
                        return

                    file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
                        self, tr("select_an_fcstd_tool_model"), "", "FreeCAD Models (*.FCStd)"
                    )
                    if not file_path:
                        return

                    from freecad.bendseq.core.geometry_backend.io.tooling_library import ToolingLibraryError

                    try:
                        from freecad.bendseq.integration.fcstd_tool_import import import_fcstd_tool
                        desc = import_fcstd_tool(self.library, Path(file_path), kind=("punch", "die")[cur_idx])
                        self._set_status(tr("added_tool", tool=desc.tool_id, kind=desc.kind))
                        self._refresh_lists()
                        target_list = self.punch_list if desc.kind == "punch" else self.die_list
                        self.tabs.setCurrentIndex(0 if desc.kind == "punch" else 1)
                        for index in range(target_list.count()):
                            candidate = target_list.item(index).data(QtCore.Qt.UserRole)
                            if candidate.tool_id == desc.tool_id:
                                target_list.setCurrentRow(index)
                                break
                        QtWidgets.QMessageBox.information(
                            self,
                            tr("tool_added"),
                            tr("tool_was_added_to_the_category", tool=desc.tool_id, kind=desc.kind),
                        )
                    except ToolingLibraryError as e:
                        self._set_status(tr("could_not_add_tool", error=e), is_error=True)
                        QtWidgets.QMessageBox.warning(self, tr("add_error"), tr_message(e))
                    except Exception as e:
                        # Catch-all dialog for unexpected system/filesystem errors during tool addition
                        self._set_status(tr("unexpected_error_caf118e2", error=e), is_error=True)
                        QtWidgets.QMessageBox.critical(self, tr("error"), tr("unexpected_error_caf118e2", error=e))

                def _on_remove_tool(self) -> None:
                    desc = self._current_descriptor()
                    if not desc:
                        QtWidgets.QMessageBox.information(
                            self, tr("remove_tool"), tr("select_a_tool_to_remove")
                        )
                        return

                    reply = QtWidgets.QMessageBox.question(
                        self,
                        tr("confirm_removal"),
                        tr("remove_tool_from_the_library", kind=desc.kind, tool=desc.tool_id),
                        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    )
                    if reply != QtWidgets.QMessageBox.Yes:
                        return

                    from freecad.bendseq.core.geometry_backend.io.tooling_library import ToolingLibraryError

                    try:
                        self.library.remove_tool(desc.tool_id, kind=desc.kind)
                        if self._workspace is not None:
                            selected = getattr(self._workspace.tooling_setup, desc.kind)
                            if selected is not None and selected.tool_id == desc.tool_id:
                                if desc.kind == "punch":
                                    self._workspace.select_punch(None)
                                else:
                                    self._workspace.select_die(None)
                        self._set_status(tr("removed_from", tool=desc.tool_id, kind=desc.kind))
                        self._refresh_lists()
                    except ToolingLibraryError as e:
                        self._set_status(tr("could_not_remove_tool", error=e), is_error=True)
                        QtWidgets.QMessageBox.warning(self, tr("removal_error"), tr_message(e))

                def _on_edit_segments(self, desc=None) -> None:
                    """Edit inventory for the selected punch/die, not global stock."""
                    if desc is None:
                        desc = self._current_descriptor()
                    if desc is None:
                        QtWidgets.QMessageBox.information(
                            self, tr("segments"), tr("select_a_punch_or_die")
                        )
                        return
                    from freecad.bendseq.core.kinematics2d.segment_assembly_planner import (
                        read_inventory, write_inventory,
                    )
                    workspace = self._workspace
                    try:
                        existing = read_inventory(desc.fcstd_path)
                    except (ValueError, OSError) as exc:
                        QtWidgets.QMessageBox.warning(
                            self, tr("segments"), tr_message(exc)
                        )
                        return
                    dialog = QtWidgets.QDialog(self)
                    dialog.setWindowTitle(tr("segments_49b4ad52") + desc.tool_id)
                    dialog.resize(470, 400)
                    layout = QtWidgets.QVBoxLayout(dialog)
                    explanation = QtWidgets.QLabel(
                        tr("available_stock_for_the_selected_tool_specify_segment_length")
                    )
                    explanation.setWordWrap(True)
                    layout.addWidget(explanation)
                    table = QtWidgets.QTableWidget(0, 2, dialog)
                    table.setObjectName("SMBS_SegmentStock")
                    table.setHorizontalHeaderLabels(
                        [tr("segment_length_mm"), tr("quantity")]
                    )
                    table.horizontalHeader().setStretchLastSection(True)
                    layout.addWidget(table)

                    def add_row(length=100.0, count=1):
                        row = table.rowCount()
                        table.insertRow(row)
                        spin_length = QtWidgets.QDoubleSpinBox(table)
                        spin_length.setRange(0.01, 10000.)
                        spin_length.setDecimals(3)
                        spin_length.setValue(float(length))
                        spin_count = QtWidgets.QSpinBox(table)
                        spin_count.setRange(0, 10000)
                        spin_count.setValue(int(count))
                        table.setCellWidget(row, 0, spin_length)
                        table.setCellWidget(row, 1, spin_count)

                    for segment in existing:
                        add_row(segment["length_mm"], segment["quantity"])
                    controls = QtWidgets.QHBoxLayout()
                    add_button = QtWidgets.QPushButton(tr("add"))
                    set_action_icon(add_button, "btn_segment_add_row.svg")
                    remove_button = QtWidgets.QPushButton(tr("remove_row"))
                    set_action_icon(remove_button, "btn_segment_delete_row.svg")
                    add_button.clicked.connect(lambda: add_row())
                    remove_button.clicked.connect(
                        lambda: table.removeRow(table.currentRow())
                        if table.currentRow() >= 0 else None
                    )
                    controls.addWidget(add_button)
                    controls.addWidget(remove_button)
                    layout.addLayout(controls)
                    buttons = QtWidgets.QDialogButtonBox(
                        QtWidgets.QDialogButtonBox.Save |
                        QtWidgets.QDialogButtonBox.Cancel,
                        parent=dialog,
                    )
                    layout.addWidget(buttons)
                    set_action_icon(buttons.button(QtWidgets.QDialogButtonBox.Save), "btn_segment_save.svg")
                    set_action_icon(buttons.button(QtWidgets.QDialogButtonBox.Cancel), "btn_dialog_cancel.svg")
                    error = QtWidgets.QLabel()
                    error.setWordWrap(True)
                    layout.addWidget(error)

                    def save():
                        quantities = {}
                        for i in range(table.rowCount()):
                            length, count = (table.cellWidget(i, col) for col in (0, 1))
                            length.interpretText()
                            count.interpretText()
                            size = length.value()
                            quantities[size] = quantities.get(size, 0) + count.value()
                        rows = [{"length_mm": size, "quantity": count}
                                for size, count in quantities.items()]
                        try:
                            saved = write_inventory(desc.fcstd_path, rows)
                        except (ValueError, OSError) as exc:
                            error.setText(tr_message(exc))
                            return
                        if workspace is not None and saved != existing:
                            workspace._on_tool_inventory_changed(desc.kind, desc.tool_id)
                        self._refresh_lists()
                        self._set_status(
                            tr("saved_segment_sizes").format(desc.tool_id, len(saved))
                        )
                        dialog.accept()

                    buttons.accepted.connect(save)
                    buttons.rejected.connect(dialog.reject)
                    run_dialog = getattr(dialog, "exec", None) or dialog.exec_
                    run_dialog()

            _PanelClass = _Dialog

        return _PanelClass(library, parent, clamps_only=clamps_only)
