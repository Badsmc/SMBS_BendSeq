"""
ToolingPanel — FreeCAD UI panel for SMBS tooling library (S.71 C2a).

Headless-safe: FreeCAD and PySide imports are lazy inside methods.
Path selector omitted (Q-s71-6): canonical library path from toollibrary/.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

LIBRARY_TAB_TITLES = ("Пуансоны", "Матрицы", "Станина", "Задний упор", "Лапки")
SECTION_CATALOG_MM = (10.0, 15.0, 20.0, 50.0, 100.0)  # Q11=C reference display

_PanelClass: Optional[type] = None


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
                    self.resize(620, 500)

                    self.library = library
                    self.library_path = getattr(library, "library_root", None)

                    self._init_ui()
                    self._load_library()

                def _init_ui(self) -> None:
                    layout = QtWidgets.QVBoxLayout(self)

                    # Tabs widget (Punches and Dies in C2a)
                    self.tabs = QtWidgets.QTabWidget()
                    self.tabs.addTab(self._build_punch_tab(), "Пуансоны (0)")
                    self.tabs.addTab(self._build_die_tab(), "Матрицы (0)")
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

                    # Action buttons
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
                    cur_list = self.punch_list if self.tabs.currentIndex() == 0 else self.die_list
                    item = cur_list.currentItem()
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

                def _on_refresh(self) -> None:
                    self._refresh_lists()

            _PanelClass = _Dialog

        return _PanelClass(library, parent)
