"""Tooling library dialog for punches, dies, virtual clamps and backgauges."""
from __future__ import annotations

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
            from freecad_smbs.commands.pressbrake_2d import _qt
            import FreeCAD as App
            QtCore, QtGui, QtWidgets = _qt()

            class _Dialog(QtWidgets.QDialog):
                def __init__(self, library: Any, parent: Any = None, *, clamps_only=False) -> None:
                    super().__init__(parent)
                    self.setWindowTitle("SMBS: Библиотека оснастки")
                    self.resize(650, 560)

                    self.library = library
                    self._workspace = parent if hasattr(parent, "_task_stack") else None
                    self.library_path = getattr(library, "library_root", None)
                    self._backgauge_fcstd = None

                    if clamps_only:
                        self.setWindowTitle("Виртуальные зажимы")
                        layout = QtWidgets.QVBoxLayout(self)
                        layout.addWidget(self._build_clamps_tab())
                        self.status_label = QtWidgets.QLabel()
                        layout.addWidget(self.status_label)
                    else:
                        self._init_ui()
                        self._load_library()

                def _init_ui(self) -> None:
                    from freecad_smbs.ui.pressbrake_theme import (
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
                    self.tabs.setIconSize(QtCore.QSize(17, 17))
                    self.tabs.addTab(self._build_punch_tab(), "Пуансоны (0)")
                    self.tabs.addTab(self._build_die_tab(), "Матрицы (0)")
                    self.tabs.addTab(self._build_clamps_tab(), "Зажимы")
                    self.tabs.addTab(self._build_backgauge_tab(), "Задний упор")
                    self.tabs.currentChanged.connect(self._on_selection_changed)
                    layout.addWidget(self.tabs)

                    # Selected item details group box
                    self.info_group = QtWidgets.QFrame()
                    self.info_group.setObjectName("SMBS_SectionCard")
                    info_layout = QtWidgets.QVBoxLayout(self.info_group)
                    info_layout.setContentsMargins(
                        SECTION_PADDING, SECTION_PADDING,
                        SECTION_PADDING, SECTION_PADDING
                    )
                    info_layout.setSpacing(SECTION_GAP)
                    heading = QtWidgets.QLabel("Сведения об инструменте")
                    heading.setObjectName("SMBS_SectionTitle")
                    heading.setAlignment(QtCore.Qt.AlignLeft)
                    info_layout.addWidget(heading)
                    self.lbl_selected_id = QtWidgets.QLabel("Выбрано: нет")
                    self.lbl_selected_meta = QtWidgets.QLabel("Метаданные: —")
                    self.lbl_selected_meta.setWordWrap(True)
                    info_layout.addWidget(self.lbl_selected_id)
                    info_layout.addWidget(self.lbl_selected_meta)
                    self.tool_picture = QtWidgets.QLabel()
                    self.tool_picture.setAlignment(QtCore.Qt.AlignCenter)
                    info_layout.addWidget(self.tool_picture)
                    layout.addWidget(self.info_group)

                    self.tool_actions = QtWidgets.QWidget()
                    btn_layout = QtWidgets.QHBoxLayout(self.tool_actions)
                    self.import_tool_button = QtWidgets.QPushButton()
                    self.remove_tool_button = QtWidgets.QPushButton()
                    self.segments_button = QtWidgets.QPushButton()
                    for button, callback in (
                        (self.import_tool_button, self._on_add_tool),
                        (self.remove_tool_button, self._on_remove_tool),
                        (self.segments_button, lambda: self._on_edit_segments()),
                    ):
                        button.clicked.connect(callback)
                        btn_layout.addWidget(style_action(button))
                    layout.addWidget(self.tool_actions)
                    # Status bar label
                    self.status_label = QtWidgets.QLabel("Статус: Готово")
                    self.status_label.setObjectName("SMBS_SectionCaption")
                    self.status_label.setWordWrap(True)
                    layout.addWidget(self.status_label)

                def _build_punch_tab(self) -> QtWidgets.QWidget:
                    widget = QtWidgets.QWidget()
                    vbox = QtWidgets.QVBoxLayout(widget)
                    vbox.setContentsMargins(16, 16, 16, 16)
                    vbox.setSpacing(16)

                    lbl_sections = QtWidgets.QLabel(
                        "Назначьте длину и количество имеющихся сегментов "
                        "для каждого конкретного пуансона."
                    )
                    lbl_sections.setObjectName("SMBS_SectionCaption")
                    lbl_sections.setWordWrap(True)
                    vbox.addWidget(lbl_sections)

                    self.punch_list = QtWidgets.QListWidget()
                    self.punch_list.itemSelectionChanged.connect(self._on_selection_changed)
                    vbox.addWidget(self.punch_list)
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

                    self.gauge_picture = QtWidgets.QLabel()
                    self.gauge_picture.setAlignment(QtCore.Qt.AlignCenter)
                    vbox.addWidget(self.gauge_picture, 1)
                    actions = QtWidgets.QHBoxLayout()
                    for title, callback in (
                        ("Загрузить", self._import_backgauge),
                        ("Удалить", self._remove_backgauge),
                        ("Настройки заднего упора", self._configure_backgauge),
                    ):
                        button = QtWidgets.QPushButton(title)
                        button.clicked.connect(callback)
                        actions.addWidget(button)
                    vbox.addLayout(actions)
                    self._refresh_backgauge()
                    return widget

                def _show_picture(self, label, path):
                    from zipfile import BadZipFile, ZipFile

                    image = QtGui.QPixmap()
                    if path is not None:
                        try:
                            with ZipFile(path) as archive:
                                image.loadFromData(archive.read("thumbnails/Thumbnail.png"))
                        except (OSError, BadZipFile, KeyError):
                            pass
                    if image.isNull():
                        label.setText("Миниатюра отсутствует" if path else "Модель не выбрана")
                    else:
                        label.setPixmap(image.scaled(
                            220, 145, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
                        ))

                def _refresh_backgauge(self):
                    from freecad_smbs.resources.tooling_paths import resolve_backgauge_fcstd

                    self._backgauge_fcstd = resolve_backgauge_fcstd(self.library_path)
                    self._show_picture(self.gauge_picture, self._backgauge_fcstd)
                    self.gauge_picture.setToolTip(str(self._backgauge_fcstd or ""))

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
                        self, "Загрузить задний упор", "", "FreeCAD (*.FCStd)"
                    )
                    if not filename:
                        return
                    target = self._backgauge_fcstd or (
                        self.library_path / "backgauge" / "backgauge.FCStd"
                    )
                    if Path(filename).resolve() == target.resolve():
                        return
                    if target.exists() and QtWidgets.QMessageBox.question(
                        self, "Задний упор", "Заменить текущую модель?"
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
                        QtWidgets.QMessageBox.warning(self, "Ошибка импорта", str(exc))
                        return
                    finally:
                        if staged is not None:
                            staged.unlink(missing_ok=True)
                    self._backgauge_changed()

                def _remove_backgauge(self):
                    if self._backgauge_fcstd is None:
                        return
                    if QtWidgets.QMessageBox.question(
                        self, "Задний упор", "Удалить выбранную модель из библиотеки?"
                    ) != QtWidgets.QMessageBox.Yes:
                        return
                    try:
                        self._backgauge_fcstd.unlink()
                    except OSError as exc:
                        QtWidgets.QMessageBox.warning(self, "Ошибка удаления", str(exc))
                        return
                    self._backgauge_changed()

                def _configure_backgauge(self):
                    if self._workspace is not None:
                        from freecad_smbs.commands.backgauge_dialog import show_backgauge_settings

                        show_backgauge_settings(self._workspace, modal_parent=self)

                def _build_clamps_tab(self) -> QtWidgets.QWidget:
                    widget = QtWidgets.QWidget()
                    vbox = QtWidgets.QVBoxLayout(widget)
                    vbox.setContentsMargins(16, 16, 16, 16)
                    vbox.setSpacing(16)

                    form = QtWidgets.QFormLayout()

                    self.spin_clamp_width = QtWidgets.QDoubleSpinBox()
                    self.spin_clamp_width.setRange(0.1, 500.0)
                    self.spin_clamp_width.setSingleStep(1.0)
                    self.spin_clamp_width.setSuffix(" мм")
                    form.addRow("Длина лапки вдоль оси X (width_mm):", self.spin_clamp_width)

                    self.spin_clamp_height = QtWidgets.QDoubleSpinBox()
                    self.spin_clamp_depth = QtWidgets.QDoubleSpinBox()
                    for label, spin in (("Высота", self.spin_clamp_height),
                                        ("Глубина", self.spin_clamp_depth)):
                        spin.setRange(0.1, 500.0)
                        spin.setSuffix(" мм")
                        form.addRow(label, spin)

                    self.spin_clamp_gap = QtWidgets.QDoubleSpinBox()
                    self.spin_clamp_gap.setRange(0.0, 200.0)
                    self.spin_clamp_gap.setSingleStep(1.0)
                    self.spin_clamp_gap.setSuffix(" мм")
                    self.spin_clamp_gap.setToolTip("Расстояние между краями соседних зажимов.")
                    form.addRow("Мин. зазор между лапками (gap_between_mm):", self.spin_clamp_gap)

                    self.spin_clamp_count = QtWidgets.QSpinBox()
                    self.spin_clamp_count.setRange(0, 1000)
                    self.spin_clamp_count.setSpecialValueText("По координатам")
                    form.addRow("Число зажимов", self.spin_clamp_count)

                    self.spin_clamp_overhang = QtWidgets.QDoubleSpinBox()
                    self.spin_clamp_overhang.setRange(0.0, 1.0)
                    self.spin_clamp_overhang.setSingleStep(0.05)
                    self.spin_clamp_overhang.setDecimals(2)
                    form.addRow("Коэфф. свеса (overhang_ratio):", self.spin_clamp_overhang)

                    self.spin_clamp_joint_tol = QtWidgets.QDoubleSpinBox()
                    self.spin_clamp_joint_tol.setRange(0.0, 10.0)
                    self.spin_clamp_joint_tol.setSingleStep(0.05)
                    self.spin_clamp_joint_tol.setDecimals(2)
                    self.spin_clamp_joint_tol.setSuffix(" мм")
                    form.addRow("Допуск стыка (joint_tolerance_mm):", self.spin_clamp_joint_tol)

                    self.spin_clamp_min_under = QtWidgets.QSpinBox()
                    self.spin_clamp_min_under.setRange(1, 1000)
                    form.addRow("Мин. лапок под секцией (min_clamps_under_punch):", self.spin_clamp_min_under)

                    self.edit_clamp_positions = QtWidgets.QLineEdit()
                    self.edit_clamp_positions.setPlaceholderText("-300, -100, 100, 300")
                    form.addRow("Центры зажимов X, мм", self.edit_clamp_positions)

                    self.chk_clamp_repositionable = QtWidgets.QCheckBox("Лапки подвижные (repositionable)")
                    form.addRow("", self.chk_clamp_repositionable)

                    self.chk_clamp_joints = QtWidgets.QCheckBox("Стыки должны лежать на лапках (joints_must_be_on_clamps)")
                    form.addRow("", self.chk_clamp_joints)

                    self.chk_clamp_no_gaps = QtWidgets.QCheckBox("Без зазоров между пуансонами (no_gaps_between_punches)")
                    form.addRow("", self.chk_clamp_no_gaps)

                    vbox.addLayout(form)

                    btn_box = QtWidgets.QHBoxLayout()
                    btn_save = QtWidgets.QPushButton("Сохранить конфиг")
                    btn_save.clicked.connect(self._on_save_clamps)
                    btn_reset = QtWidgets.QPushButton("Сбросить")
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
                        cfg = self.library.get_clamp_config()
                    except Exception:
                        from smbs_core.geometry_backend.io.clamp_validator import ClampConfig

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
                        self.clamp_scene.addSimpleText("Зажимы не заданы")
                    bounds = self.clamp_scene.itemsBoundingRect().adjusted(-5, -5, 5, 5)
                    self.clamp_preview.setSceneRect(bounds)
                    self.clamp_preview.fitInView(bounds, QtCore.Qt.KeepAspectRatio)

                def _on_reset_clamps(self) -> None:
                    self._populate_clamps_ui()
                    self._set_status("Параметры лапок сброшены к сохранённым значениям.")

                def _on_save_clamps(self) -> None:
                    if not self.library:
                        return
                    try:
                        pos = _parse_positions_mm(self.edit_clamp_positions.text())
                    except ValueError as e:
                        self._set_status(f"Ошибка в позициях лапок: {e}", is_error=True)
                        QtWidgets.QMessageBox.warning(self, "Ошибка ввода", f"Некорректный формат позиций лапок:\n{e}")
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
                        cfg = self.library.get_clamp_config()
                        new_cfg = _apply_clamp_config_fields(cfg, fields)
                        self.library.set_clamp_config(new_cfg)
                        self._draw_clamps(new_cfg)
                        if new_cfg != cfg and self._workspace is not None:
                            self._workspace._on_tool_inventory_changed()
                        self._set_status("Конфигурация лапок успешно сохранена.")
                        QtWidgets.QMessageBox.information(
                            self, "Оснастка", "Конфигурация лапок сохранена в библиотеку."
                        )
                    except Exception as e:
                        self._set_status(f"Ошибка сохранения лапок: {e}", is_error=True)
                        QtWidgets.QMessageBox.critical(
                            self, "Ошибка", f"Не удалось сохранить конфигурацию лапок:\n{e}"
                        )

                def _set_status(self, text: str, is_error: bool = False) -> None:
                    color = "red" if is_error else "black"
                    self.status_label.setStyleSheet(f"color: {color};")
                    self.status_label.setText(f"Статус: {text}")
                    try:
                        if is_error:
                            App.Console.PrintError(f"SMBS ToolingPanel: {text}\n")
                        else:
                            App.Console.PrintMessage(f"SMBS ToolingPanel: {text}\n")
                    except Exception:
                        pass

                def _load_library(self) -> None:
                    from smbs_core.geometry_backend.io.tooling_library import (
                        ToolingLibrary,
                        ToolingLibraryError,
                    )

                    try:
                        self._set_status(f"Библиотека загружена: {self.library_path}")
                        self._refresh_lists()
                    except ToolingLibraryError as e:
                        err_msg = str(e)
                        if (
                            "Missing required punch subdirectory" in err_msg
                            or "Missing required die subdirectory" in err_msg
                        ):
                            reply = QtWidgets.QMessageBox.question(
                                self,
                                "Инициализация библиотеки",
                                f"В директории '{self.library_path}' отсутствуют обязательные подкаталоги punch/ и die/.\n\n"
                                "Создать подкаталоги сейчас?",
                                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                            )
                            if reply == QtWidgets.QMessageBox.Yes:
                                try:
                                    (self.library_path / "punch").mkdir(parents=True, exist_ok=True)
                                    (self.library_path / "die").mkdir(parents=True, exist_ok=True)
                                    self.library = ToolingLibrary(self.library_path)
                                    self._set_status("Библиотека инициализирована.")
                                    self._refresh_lists()
                                    return
                                except Exception as create_err:
                                    self._set_status(f"Не удалось создать подкаталоги: {create_err}", is_error=True)
                                    return
                        self._set_status(f"Ошибка загрузки библиотеки: {e}", is_error=True)
                        QtWidgets.QMessageBox.critical(self, "Ошибка библиотеки", str(e))

                def _refresh_lists(self) -> None:
                    if not self.library:
                        return

                    # Retain the operator's exact tool selection through
                    # catalog refresh (notably after saving 13 segment types).
                    # Clearing the list previously selected the first item,
                    # so Preview could open 'puancon' instead of AP_1017.
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
                        self._set_status(f"Не удалось просканировать каталог: {e}", is_error=True)
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

                    self.tabs.setTabText(0, f"Пуансоны ({len(catalog.punches)})")
                    self.tabs.setTabText(1, f"Матрицы ({len(catalog.dies)})")
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
                    self._set_status("Список инструментов обновлён.")

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

                def _on_selection_changed(self) -> None:
                    index = self.tabs.currentIndex()
                    tool_tab = index in (0, 1)
                    self.info_group.setVisible(tool_tab)
                    self.tool_actions.setVisible(tool_tab)
                    if not tool_tab:
                        if index == 2:
                            self.clamp_preview.fitInView(
                                self.clamp_scene.sceneRect(), QtCore.Qt.KeepAspectRatio
                            )
                        return
                    kind = ("Punch", "Die")[index]
                    self.import_tool_button.setText("Импорт " + kind)
                    self.remove_tool_button.setText("Удалить " + kind)
                    self.segments_button.setText("Сегменты " + kind)
                    desc = self._current_descriptor()
                    self.remove_tool_button.setEnabled(desc is not None)
                    self.segments_button.setEnabled(desc is not None)
                    self._show_picture(self.tool_picture, desc.fcstd_path if desc else None)
                    if not desc:
                        self.lbl_selected_id.setText("Выбрано: нет")
                        self.lbl_selected_meta.setText("Метаданные: —")
                        return

                    sidecar_str = "ДА" if desc.has_sidecar else "НЕТ"
                    self.lbl_selected_id.setText(f"Выбрано: [{desc.kind}] {desc.tool_id} (Sidecar: {sidecar_str})")
                    meta_str = (
                        ", ".join(f"{k}={v}" for k, v in desc.metadata.items())
                        if desc.metadata
                        else "нет"
                    )
                    segments = desc.metadata.get("segments") or []
                    inventory_text = ", ".join(
                        "{:g} мм × {}".format(
                            float(seg.get("length_mm", 0)),
                            int(seg.get("quantity", 0)),
                        )
                        for seg in segments if isinstance(seg, dict)
                    ) or "не заданы"
                    self.lbl_selected_meta.setText(
                        f"Метаданные: {meta_str}\n"
                        f"Сегменты: {inventory_text}\nПуть: {desc.fcstd_path}"
                    )

                def _on_add_tool(self) -> None:
                    if not self.library:
                        return

                    cur_idx = self.tabs.currentIndex()
                    if cur_idx not in (0, 1):
                        QtWidgets.QMessageBox.information(
                            self,
                            "Добавление инструмента",
                            "Добавление моделей (.FCStd) поддерживается на вкладках 'Пуансоны' и 'Матрицы'.",
                        )
                        return

                    file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
                        self, "Выберите модель инструмента FCStd", "", "FreeCAD Models (*.FCStd)"
                    )
                    if not file_path:
                        return

                    from smbs_core.geometry_backend.io.tooling_library import ToolingLibraryError

                    try:
                        desc = self.library.add_tool(Path(file_path), kind=("punch", "die")[cur_idx])
                        self._set_status(f"Добавлен инструмент '{desc.tool_id}' ({desc.kind})")
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
                            "Инструмент добавлен",
                            f"Инструмент '{desc.tool_id}' успешно добавлен в категорию {desc.kind}.",
                        )
                    except ToolingLibraryError as e:
                        self._set_status(f"Ошибка добавления: {e}", is_error=True)
                        QtWidgets.QMessageBox.warning(self, "Ошибка добавления", str(e))
                    except Exception as e:
                        # Catch-all dialog for unexpected system/filesystem errors during tool addition
                        self._set_status(f"Неожиданная ошибка: {e}", is_error=True)
                        QtWidgets.QMessageBox.critical(self, "Ошибка", f"Неожиданная ошибка: {e}")

                def _on_remove_tool(self) -> None:
                    desc = self._current_descriptor()
                    if not desc:
                        QtWidgets.QMessageBox.information(
                            self, "Удаление инструмента", "Пожалуйста, выберите инструмент для удаления."
                        )
                        return

                    reply = QtWidgets.QMessageBox.question(
                        self,
                        "Подтверждение удаления",
                        f"Удалить инструмент [{desc.kind}] '{desc.tool_id}' из библиотеки?",
                        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    )
                    if reply != QtWidgets.QMessageBox.Yes:
                        return

                    from smbs_core.geometry_backend.io.tooling_library import ToolingLibraryError

                    try:
                        self.library.remove_tool(desc.tool_id, kind=desc.kind)
                        if self._workspace is not None:
                            selected = getattr(self._workspace.tooling_setup, desc.kind)
                            if selected is not None and selected.tool_id == desc.tool_id:
                                if desc.kind == "punch":
                                    self._workspace.select_punch(None)
                                else:
                                    self._workspace.select_die(None)
                        self._set_status(f"Удалён '{desc.tool_id}' из {desc.kind}")
                        self._refresh_lists()
                    except ToolingLibraryError as e:
                        self._set_status(f"Ошибка удаления: {e}", is_error=True)
                        QtWidgets.QMessageBox.warning(self, "Ошибка удаления", str(e))

                def _on_edit_segments(self, desc=None) -> None:
                    """Edit inventory for the selected punch/die, not global stock."""
                    if desc is None:
                        desc = self._current_descriptor()
                    if desc is None:
                        QtWidgets.QMessageBox.information(
                            self, "Сегменты", "Выберите пуансон или матрицу."
                        )
                        return
                    from smbs_core.kinematics2d.segment_assembly_planner import (
                        read_inventory, write_inventory,
                    )
                    workspace = self._workspace
                    try:
                        existing = read_inventory(desc.fcstd_path)
                    except (ValueError, OSError) as exc:
                        QtWidgets.QMessageBox.warning(
                            self, "Сегменты", str(exc)
                        )
                        return
                    dialog = QtWidgets.QDialog(self)
                    dialog.setWindowTitle("Сегменты: " + desc.tool_id)
                    dialog.resize(470, 400)
                    layout = QtWidgets.QVBoxLayout(dialog)
                    explanation = QtWidgets.QLabel(
                        "Фактический складской набор для выбранной модели. "
                        "Длина в мм, количество — число имеющихся секций. "
                        "Данные хранятся в .tooling.json."
                    )
                    explanation.setWordWrap(True)
                    layout.addWidget(explanation)
                    table = QtWidgets.QTableWidget(0, 2, dialog)
                    table.setObjectName("SMBS_SegmentStock")
                    table.setHorizontalHeaderLabels(
                        ["Длина сегмента, мм", "Количество, шт."]
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
                    add_button = QtWidgets.QPushButton("Добавить")
                    remove_button = QtWidgets.QPushButton("Удалить строку")
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
                            error.setText(str(exc))
                            return
                        if workspace is not None and saved != existing:
                            workspace._on_tool_inventory_changed(desc.kind, desc.tool_id)
                        self._refresh_lists()
                        self._set_status(
                            "{}: сохранено {} видов сегментов".format(desc.tool_id, len(saved))
                        )
                        dialog.accept()

                    buttons.accepted.connect(save)
                    buttons.rejected.connect(dialog.reject)
                    run_dialog = getattr(dialog, "exec", None) or dialog.exec_
                    run_dialog()

            _PanelClass = _Dialog

        return _PanelClass(library, parent, clamps_only=clamps_only)
