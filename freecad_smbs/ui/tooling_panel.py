"""
ToolingPanel — FreeCAD UI panel for SMBS tooling library (S.71 C2b).

Headless-safe: FreeCAD and PySide imports are lazy inside methods.
Path selector omitted (Q-s71-6): canonical library path from toollibrary/.
5 tabs: Punches, Dies, Machine Frame (KL-16 honest), Backgauge, Clamps.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

LIBRARY_TAB_TITLES = ("Пуансоны", "Матрицы", "Станина", "Задний упор", "Лапки")
SECTION_CATALOG_MM = (10.0, 15.0, 20.0, 50.0, 100.0)  # Q11=C reference display

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

    def __new__(cls, library: Any, parent: Any = None) -> Any:
        global _PanelClass
        if _PanelClass is None:
            from PySide6 import QtCore, QtWidgets
            import FreeCAD as App
            import FreeCADGui as Gui

            class _Dialog(QtWidgets.QDialog):
                def __init__(self, library: Any, parent: Any = None) -> None:
                    super().__init__(parent)
                    self.setWindowTitle("SMBS: Библиотека оснастки")
                    self.resize(650, 560)

                    self.library = library
                    self.library_path = getattr(library, "library_root", None)
                    self._backgauge_fcstd = None
                    self._clamp_fcstd = None

                    self._init_ui()
                    self._load_library()

                def _init_ui(self) -> None:
                    layout = QtWidgets.QVBoxLayout(self)

                    # 5-tab widget (Punches, Dies, Machine Frame, Backgauge, Clamps)
                    self.tabs = QtWidgets.QTabWidget()
                    self.tabs.addTab(self._build_punch_tab(), "Пуансоны (0)")
                    self.tabs.addTab(self._build_die_tab(), "Матрицы (0)")
                    self.tabs.addTab(self._build_machine_frame_tab(), LIBRARY_TAB_TITLES[2])
                    self.tabs.addTab(self._build_backgauge_tab(), LIBRARY_TAB_TITLES[3])
                    self.tabs.addTab(self._build_clamps_tab(), LIBRARY_TAB_TITLES[4])
                    self.tabs.currentChanged.connect(self._on_selection_changed)
                    layout.addWidget(self.tabs)

                    # Selected item details group box
                    self.info_group = QtWidgets.QGroupBox("Сведения об инструменте")
                    info_layout = QtWidgets.QVBoxLayout(self.info_group)
                    self.lbl_selected_id = QtWidgets.QLabel("Выбрано: нет")
                    self.lbl_selected_meta = QtWidgets.QLabel("Метаданные: —")
                    self.lbl_selected_meta.setWordWrap(True)
                    info_layout.addWidget(self.lbl_selected_id)
                    info_layout.addWidget(self.lbl_selected_meta)
                    layout.addWidget(self.info_group)

                    # Action buttons for catalog tools
                    btn_layout = QtWidgets.QHBoxLayout()
                    btn_add = QtWidgets.QPushButton("Добавить инструмент...")
                    btn_add.clicked.connect(self._on_add_tool)
                    btn_remove = QtWidgets.QPushButton("Удалить")
                    btn_remove.clicked.connect(self._on_remove_tool)
                    btn_preview = QtWidgets.QPushButton("Просмотр")
                    btn_preview.clicked.connect(self._on_preview_tool)
                    btn_refresh = QtWidgets.QPushButton("Обновить")
                    btn_refresh.clicked.connect(self._on_refresh)

                    btn_layout.addWidget(btn_add)
                    btn_layout.addWidget(btn_remove)
                    btn_layout.addWidget(btn_preview)
                    btn_layout.addWidget(btn_refresh)
                    layout.addLayout(btn_layout)

                    # Status bar label
                    self.status_label = QtWidgets.QLabel("Статус: Готово")
                    self.status_label.setStyleSheet("color: gray;")
                    layout.addWidget(self.status_label)

                def _build_punch_tab(self) -> QtWidgets.QWidget:
                    widget = QtWidgets.QWidget()
                    vbox = QtWidgets.QVBoxLayout(widget)

                    lbl_sections = QtWidgets.QLabel("Секции Q11: 10, 15, 20, 50, 100 мм (автоподбор в pipeline)")
                    lbl_sections.setStyleSheet("color: #555; font-style: italic;")
                    vbox.addWidget(lbl_sections)

                    self.punch_list = QtWidgets.QListWidget()
                    self.punch_list.itemSelectionChanged.connect(self._on_selection_changed)
                    vbox.addWidget(self.punch_list)
                    return widget

                def _build_die_tab(self) -> QtWidgets.QWidget:
                    widget = QtWidgets.QWidget()
                    vbox = QtWidgets.QVBoxLayout(widget)

                    self.die_list = QtWidgets.QListWidget()
                    self.die_list.itemSelectionChanged.connect(self._on_selection_changed)
                    vbox.addWidget(self.die_list)
                    return widget

                def _build_machine_frame_tab(self) -> QtWidgets.QWidget:
                    widget = QtWidgets.QWidget()
                    vbox = QtWidgets.QVBoxLayout(widget)

                    presses_dir = self.library_path / "presses" if self.library_path else None
                    frame_path = presses_dir / "machine_frame.FCStd" if presses_dir else None
                    machine_frame_exists = frame_path.exists() if frame_path else False

                    from smbs_core.viewer.fc_animation import format_machine_frame_badge

                    txt, rgb = format_machine_frame_badge(machine_frame_exists)

                    lbl_badge = QtWidgets.QLabel(txt)
                    lbl_badge.setStyleSheet(
                        f"background-color: rgb({int(rgb[0]*255)}, {int(rgb[1]*255)}, {int(rgb[2]*255)});"
                        f" color: white; font-weight: bold; padding: 4px 8px; border-radius: 4px;"
                    )
                    lbl_badge.setAlignment(QtCore.Qt.AlignCenter)
                    vbox.addWidget(lbl_badge)

                    lbl_info = QtWidgets.QLabel(
                        "Станина пресса (Machine Frame):\n\n"
                        "• Модель: toollibrary/presses/machine_frame.FCStd\n"
                        "• Статус: отсутствует (KL-16 pending, P1 OPEN)\n"
                        "• В симуляции используется упрощённое габаритное представление.\n"
                        "• После поставки модели KL-16 станина будет автоматически загружаться в просмотрщик."
                    )
                    lbl_info.setWordWrap(True)
                    vbox.addWidget(lbl_info)
                    vbox.addStretch()
                    return widget

                def _build_backgauge_tab(self) -> QtWidgets.QWidget:
                    widget = QtWidgets.QWidget()
                    vbox = QtWidgets.QVBoxLayout(widget)

                    from freecad_smbs.ui.badges import BADGE_TOOLING_MISSING, get_badge_hex
                    from freecad_smbs.resources.tooling_paths import resolve_backgauge_fcstd

                    if self.library_path:
                        self._backgauge_fcstd = resolve_backgauge_fcstd(self.library_path)
                    exists = self._backgauge_fcstd is not None

                    if exists:
                        badge_text = f"BACKGAUGE: PRESENT ({self._backgauge_fcstd.name}, no sidecar)"
                        badge_hex = "#2E7D32"
                    else:
                        badge_text = BADGE_TOOLING_MISSING
                        badge_hex = get_badge_hex(BADGE_TOOLING_MISSING)

                    lbl_badge = QtWidgets.QLabel(badge_text)
                    lbl_badge.setStyleSheet(
                        f"background-color: {badge_hex}; color: white; font-weight: bold; "
                        f"padding: 4px 8px; border-radius: 4px;"
                    )
                    lbl_badge.setAlignment(QtCore.Qt.AlignCenter)
                    vbox.addWidget(lbl_badge)

                    if exists:
                        info_text = (
                            "Задний упор (Backgauge):\n\n"
                            f"• Файл: {self._backgauge_fcstd.name}\n"
                            "• Статус: загружен (без .tooling.json sidecar — datums не валидированы).\n"
                            "• Расчёт позиционирования: BackgaugeCalculator.compute_step_backgauge().\n"
                            "• Оси: X_mm (глубина), R_mm (высота), Z1_mm, Z2_mm (боковые пальцы)."
                        )
                    else:
                        info_text = (
                            "Задний упор (Backgauge):\n\n"
                            "• 3D геометрия отсутствует (TOOLING: MISSING).\n"
                            "• Требуется: toollibrary/backgauge/backgauge.FCStd.\n"
                            "• Расчёт позиционирования: BackgaugeCalculator.compute_step_backgauge()."
                        )
                    lbl_info = QtWidgets.QLabel(info_text)
                    lbl_info.setWordWrap(True)
                    vbox.addWidget(lbl_info)

                    btn_preview = QtWidgets.QPushButton("Просмотр в 3D")
                    btn_preview.setEnabled(exists)
                    btn_preview.clicked.connect(self._on_preview_backgauge)
                    vbox.addWidget(btn_preview)

                    vbox.addStretch()
                    return widget

                def _build_clamps_tab(self) -> QtWidgets.QWidget:
                    widget = QtWidgets.QWidget()
                    vbox = QtWidgets.QVBoxLayout(widget)

                    from freecad_smbs.ui.badges import BADGE_TOOLING_MISSING, get_badge_hex
                    from freecad_smbs.resources.tooling_paths import resolve_clamp_fcstd

                    if self.library_path:
                        self._clamp_fcstd = resolve_clamp_fcstd(self.library_path)
                    exists = self._clamp_fcstd is not None

                    if exists:
                        badge_text = f"CLAMP: PRESENT ({self._clamp_fcstd.name}, no sidecar)"
                        badge_hex = "#2E7D32"
                    else:
                        badge_text = BADGE_TOOLING_MISSING
                        badge_hex = get_badge_hex(BADGE_TOOLING_MISSING)

                    lbl_badge = QtWidgets.QLabel(badge_text)
                    lbl_badge.setStyleSheet(
                        f"background-color: {badge_hex}; color: white; font-weight: bold; "
                        f"padding: 4px 8px; border-radius: 4px;"
                    )
                    lbl_badge.setAlignment(QtCore.Qt.AlignCenter)
                    vbox.addWidget(lbl_badge)

                    lbl_info = QtWidgets.QLabel(
                        "Лапки крепления инструмента (Clamps Config):\n"
                        "Параметры ниже сохраняются в config.json библиотеки и используются "
                        "валидатором цепочки пуансонов (Q2)."
                    )
                    lbl_info.setWordWrap(True)
                    vbox.addWidget(lbl_info)

                    # Form for 9 editable fields
                    form = QtWidgets.QFormLayout()

                    self.spin_clamp_width = QtWidgets.QDoubleSpinBox()
                    self.spin_clamp_width.setRange(0.1, 500.0)
                    self.spin_clamp_width.setSingleStep(1.0)
                    self.spin_clamp_width.setSuffix(" мм")
                    form.addRow("Ширина лапки (width_mm):", self.spin_clamp_width)

                    self.spin_clamp_gap = QtWidgets.QDoubleSpinBox()
                    self.spin_clamp_gap.setRange(0.0, 200.0)
                    self.spin_clamp_gap.setSingleStep(1.0)
                    self.spin_clamp_gap.setSuffix(" мм")
                    form.addRow("Зазор между лапками (gap_between_mm):", self.spin_clamp_gap)

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
                    self.spin_clamp_min_under.setRange(0, 10)
                    form.addRow("Мин. лапок под секцией (min_clamps_under_punch):", self.spin_clamp_min_under)

                    self.edit_clamp_positions = QtWidgets.QLineEdit()
                    self.edit_clamp_positions.setPlaceholderText("например: 50.0, 150.0, 250.0")
                    form.addRow("Позиции лапок (positions_mm):", self.edit_clamp_positions)

                    self.chk_clamp_repositionable = QtWidgets.QCheckBox("Лапки подвижные (repositionable)")
                    form.addRow("", self.chk_clamp_repositionable)

                    self.chk_clamp_joints = QtWidgets.QCheckBox("Стыки должны лежать на лапках (joints_must_be_on_clamps)")
                    form.addRow("", self.chk_clamp_joints)

                    self.chk_clamp_no_gaps = QtWidgets.QCheckBox("Без зазоров между пуансонами (no_gaps_between_punches)")
                    form.addRow("", self.chk_clamp_no_gaps)

                    vbox.addLayout(form)

                    btn_preview = QtWidgets.QPushButton("Просмотр в 3D (по positions_mm)")
                    btn_preview.setEnabled(exists)
                    btn_preview.clicked.connect(self._on_preview_clamps)
                    vbox.addWidget(btn_preview)

                    btn_box = QtWidgets.QHBoxLayout()
                    btn_save = QtWidgets.QPushButton("Сохранить конфиг")
                    btn_save.clicked.connect(self._on_save_clamps)
                    btn_reset = QtWidgets.QPushButton("Сбросить")
                    btn_reset.clicked.connect(self._on_reset_clamps)
                    btn_box.addWidget(btn_save)
                    btn_box.addWidget(btn_reset)
                    vbox.addLayout(btn_box)

                    vbox.addStretch()

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
                    self.spin_clamp_gap.setValue(float(cfg.gap_between_mm))
                    self.spin_clamp_overhang.setValue(float(cfg.overhang_ratio))
                    self.spin_clamp_joint_tol.setValue(float(cfg.joint_tolerance_mm))
                    self.spin_clamp_min_under.setValue(int(cfg.min_clamps_under_punch))
                    pos_str = ", ".join(str(p) for p in cfg.positions_mm) if cfg.positions_mm else ""
                    self.edit_clamp_positions.setText(pos_str)
                    self.chk_clamp_repositionable.setChecked(bool(cfg.repositionable))
                    self.chk_clamp_joints.setChecked(bool(cfg.joints_must_be_on_clamps))
                    self.chk_clamp_no_gaps.setChecked(bool(cfg.no_gaps_between_punches))

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
                        "gap_between_mm": self.spin_clamp_gap.value(),
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
                    desc = self._current_descriptor()
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
                    self.lbl_selected_meta.setText(f"Метаданные: {meta_str}\nПуть: {desc.fcstd_path}")

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
                        desc = self.library.add_tool(Path(file_path))
                        self._set_status(f"Добавлен инструмент '{desc.tool_id}' ({desc.kind})")
                        self._refresh_lists()
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
                        self._set_status(f"Удалён '{desc.tool_id}' из {desc.kind}")
                        self._refresh_lists()
                    except ToolingLibraryError as e:
                        self._set_status(f"Ошибка удаления: {e}", is_error=True)
                        QtWidgets.QMessageBox.warning(self, "Ошибка удаления", str(e))

                def _on_preview_tool(self) -> None:
                    desc = self._current_descriptor()
                    if not desc:
                        QtWidgets.QMessageBox.information(
                            self, "Просмотр", "Пожалуйста, выберите инструмент для просмотра."
                        )
                        return

                    if App.ActiveDocument is None:
                        App.newDocument("SMBS_ToolingPreview")
                        try:
                            Gui.ActiveDocument = Gui.activeDocument()
                        except Exception:
                            pass

                    doc = App.ActiveDocument
                    try:
                        doc.mergeProject(str(desc.fcstd_path))
                        doc.recompute()
                        Gui.SendMsgToActiveView("ViewFit")
                        self._set_status(f"Инструмент '{desc.tool_id}' отображён в 3D виде.")
                    except Exception as e:
                        self._set_status(f"Ошибка отображения инструмента: {e}", is_error=True)
                        QtWidgets.QMessageBox.warning(self, "Ошибка просмотра", f"Не удалось отобразить форму: {e}")

                def _on_preview_backgauge(self) -> None:
                    if self._backgauge_fcstd is None:
                        return
                    if App.ActiveDocument is None:
                        App.newDocument("SMBS_BackgaugePreview")
                        try:
                            Gui.ActiveDocument = Gui.activeDocument()
                        except Exception:
                            pass
                    doc = App.ActiveDocument
                    try:
                        doc.mergeProject(str(self._backgauge_fcstd))
                        doc.recompute()
                        Gui.SendMsgToActiveView("ViewFit")
                        self._set_status(f"Задний упор отображён: {self._backgauge_fcstd.name}")
                    except Exception as e:
                        self._set_status(f"Ошибка отображения заднего упора: {e}", is_error=True)
                        QtWidgets.QMessageBox.warning(self, "Ошибка просмотра", str(e))

                def _on_preview_clamps(self) -> None:
                    if self._clamp_fcstd is None:
                        return
                    try:
                        positions = _parse_positions_mm(self.edit_clamp_positions.text())
                    except ValueError as e:
                        QtWidgets.QMessageBox.warning(
                            self, "Ошибка позиций", f"Некорректные positions_mm:\n{e}"
                        )
                        return

                    import Part
                    import FreeCAD as App

                    original_active = App.ActiveDocument
                    temp_name = "SMBS_ClampTemplate_Temp"
                    try:
                        App.closeDocument(temp_name)
                    except Exception:
                        pass

                    temp_doc = App.newDocument(temp_name)
                    try:
                        temp_doc.mergeProject(str(self._clamp_fcstd))
                        temp_doc.recompute()
                        shapes = [
                            o.Shape for o in temp_doc.Objects
                            if hasattr(o, "Shape") and o.Shape is not None
                        ]
                        if not shapes:
                            raise RuntimeError(
                                f"В файле {self._clamp_fcstd.name} нет Shape-объектов"
                            )
                        template = Part.makeCompound(shapes)
                    finally:
                        App.closeDocument(temp_name)

                    if original_active is not None:
                        try:
                            App.setActiveDocument(original_active.Name)
                        except Exception:
                            pass
                    target_doc = original_active
                    if target_doc is None:
                        target_doc = App.newDocument("SMBS_ClampsPreview")
                        try:
                            Gui.ActiveDocument = Gui.activeDocument()
                        except Exception:
                            pass

                    for obj in list(target_doc.Objects):
                        if obj.Name.startswith("SMBS_Clamp_"):
                            target_doc.removeObject(obj.Name)

                    actual = positions if positions else (0.0,)
                    for i, x in enumerate(actual):
                        copy = template.copy()
                        copy.translate(App.Vector(float(x), 0.0, 0.0))
                        obj = target_doc.addObject("Part::Feature", f"SMBS_Clamp_{i:02d}")
                        obj.Shape = copy

                    target_doc.recompute()
                    Gui.SendMsgToActiveView("ViewFit")
                    self._set_status(
                        f"Лапки: {len(actual)} экземпляр(ов) "
                        f"(positions={'empty→origin' if not positions else list(actual)})"
                    )

                def closeEvent(self, event: Any) -> None:
                    doc = App.ActiveDocument
                    if doc is not None:
                        for obj in list(doc.Objects):
                            if obj.Name.startswith("SMBS_Clamp_") or obj.Name in (
                                "SMBS_BackgaugePreview", "SMBS_ClampsPreview"
                            ):
                                try:
                                    doc.removeObject(obj.Name)
                                except Exception:
                                    pass
                    super().closeEvent(event)

                def _on_refresh(self) -> None:
                    self._refresh_lists()

            _PanelClass = _Dialog

        return _PanelClass(library, parent)
