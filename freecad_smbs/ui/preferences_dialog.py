"""
preferences_dialog.py - Preferences dialog and parameter access for SMBS FreeCAD Addon.

Manages user configuration under FreeCAD parameter path:
BaseApp/Preferences/Mod/SMBS/
- ToolingLibraryPath: filesystem path to tooling library (default: canonical toollibrary/)
- SearchAlgorithm: default search algorithm ("astar", "dfs", "greedy")
- UnknownPolicy: collision policy for unassigned/unverified tools ("PRODUCTION", "EXPLORATORY")

Headless-safe:
- All PySide / Qt imports are lazy.
- get_smbs_preferences() / set_smbs_preferences() are pure Python safe.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from freecad_smbs.resources.tooling_paths import TOOL_LIBRARY_ROOT

DEFAULT_SEARCH_ALGORITHM = "astar"
DEFAULT_UNKNOWN_POLICY = "PRODUCTION"

_PREFS_FALLBACK: Dict[str, Any] = {
    "tooling_library_path": str(TOOL_LIBRARY_ROOT),
    "search_algorithm": DEFAULT_SEARCH_ALGORITHM,
    "unknown_policy": DEFAULT_UNKNOWN_POLICY,
    "tooling_set_id": "",
}

_DialogClass: Optional[type] = None


def get_smbs_preferences() -> Dict[str, Any]:
    """Retrieve current SMBS preferences from FreeCAD ParamGet or in-memory fallback."""
    try:
        import FreeCAD
        if FreeCAD is not None and hasattr(FreeCAD, "ParamGet"):
            param = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/SMBS")
            tool_path = param.GetString("ToolingLibraryPath", str(TOOL_LIBRARY_ROOT))
            if not tool_path:
                tool_path = str(TOOL_LIBRARY_ROOT)
            algo = param.GetString("SearchAlgorithm", DEFAULT_SEARCH_ALGORITHM)
            if not algo:
                algo = DEFAULT_SEARCH_ALGORITHM
            policy = param.GetString("UnknownPolicy", DEFAULT_UNKNOWN_POLICY)
            if not policy:
                policy = DEFAULT_UNKNOWN_POLICY
            tooling_set = param.GetString("ToolingSetId", "")
            return {
                "tooling_library_path": tool_path,
                "search_algorithm": algo,
                "unknown_policy": policy,
                "tooling_set_id": tooling_set,
            }
    except Exception:
        pass

    return dict(_PREFS_FALLBACK)


def set_smbs_preferences(prefs: Dict[str, Any]) -> None:
    """Save SMBS preferences to FreeCAD ParamGet and in-memory fallback."""
    global _PREFS_FALLBACK
    _PREFS_FALLBACK.update(prefs)

    try:
        import FreeCAD
        if FreeCAD is not None and hasattr(FreeCAD, "ParamGet"):
            param = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/SMBS")
            if "tooling_library_path" in prefs:
                param.SetString("ToolingLibraryPath", str(prefs["tooling_library_path"]))
            if "search_algorithm" in prefs:
                param.SetString("SearchAlgorithm", str(prefs["search_algorithm"]))
            if "unknown_policy" in prefs:
                param.SetString("UnknownPolicy", str(prefs["unknown_policy"]))
            if "tooling_set_id" in prefs:
                param.SetString("ToolingSetId", str(prefs["tooling_set_id"]))
    except Exception:
        pass


class PreferencesDialog:
    """
    Lazy wrapper for FreeCAD Qt Preferences Dialog.
    Instantiates PySide6 QDialog when invoked inside FreeCAD GUI.
    """

    def __new__(cls, parent: Any = None) -> Any:
        global _DialogClass
        if _DialogClass is None:
            from PySide6 import QtCore, QtWidgets

            class _Dialog(QtWidgets.QDialog):
                def __init__(self, parent: Any = None) -> None:
                    super().__init__(parent)
                    self.setWindowTitle("SMBS: Настройки")
                    self.resize(520, 280)
                    self._init_ui()

                def _init_ui(self) -> None:
                    from PySide6 import QtWidgets

                    current_prefs = get_smbs_preferences()
                    main_layout = QtWidgets.QVBoxLayout(self)
                    main_layout.setSpacing(12)

                    # Group box for settings
                    box = QtWidgets.QGroupBox("Параметры планировщика гибки")
                    grid = QtWidgets.QGridLayout(box)
                    grid.setSpacing(8)

                    # 1. Tooling Library Path
                    grid.addWidget(QtWidgets.QLabel("Путь к библиотеке оснастки:"), 0, 0, 1, 2)
                    self.edit_path = QtWidgets.QLineEdit(current_prefs["tooling_library_path"])
                    btn_browse = QtWidgets.QPushButton("Обзор…")
                    btn_browse.clicked.connect(self._on_browse)

                    path_layout = QtWidgets.QHBoxLayout()
                    path_layout.addWidget(self.edit_path, stretch=1)
                    path_layout.addWidget(btn_browse)
                    grid.addLayout(path_layout, 1, 0, 1, 2)

                    # 2. Search Algorithm
                    grid.addWidget(QtWidgets.QLabel("Алгоритм поиска по умолчанию:"), 2, 0)
                    self.combo_algo = QtWidgets.QComboBox()
                    self.combo_algo.addItem("astar (A* — оптимальный по эвристике)", "astar")
                    self.combo_algo.addItem("dfs (DFS — поиск в глубину)", "dfs")
                    self.combo_algo.addItem("greedy (Жадный unfold)", "greedy")

                    cur_algo = current_prefs["search_algorithm"]
                    idx = self.combo_algo.findData(cur_algo)
                    if idx >= 0:
                        self.combo_algo.setCurrentIndex(idx)
                    grid.addWidget(self.combo_algo, 2, 1)

                    # 3. Unknown Collision Policy
                    grid.addWidget(QtWidgets.QLabel("Политика неопределённых коллизий:"), 3, 0)
                    self.combo_policy = QtWidgets.QComboBox()
                    self.combo_policy.addItem("PRODUCTION (Строгая — отклонять UNKNOWN)", "PRODUCTION")
                    self.combo_policy.addItem("EXPLORATORY (Ознакомительная — разрешать UNKNOWN)", "EXPLORATORY")

                    cur_pol = current_prefs["unknown_policy"]
                    pol_idx = self.combo_policy.findData(cur_pol)
                    if pol_idx >= 0:
                        self.combo_policy.setCurrentIndex(pol_idx)
                    grid.addWidget(self.combo_policy, 3, 1)

                    # 4. Active Tooling Set
                    grid.addWidget(QtWidgets.QLabel("Активный набор оснастки (Tooling Set):"), 4, 0)
                    self.combo_tooling_set = QtWidgets.QComboBox()
                    self.combo_tooling_set.addItem("— Не выбран (отдельные инструменты) —", "")
                    lib_dir = Path(current_prefs["tooling_library_path"])
                    sets_dir = lib_dir / "sets"
                    if sets_dir.is_dir():
                        for p in sorted(sets_dir.glob("*.json")):
                            if not p.name.endswith(".tmp"):
                                self.combo_tooling_set.addItem(p.stem, p.stem)
                    cur_set = current_prefs.get("tooling_set_id", "")
                    set_idx = self.combo_tooling_set.findData(cur_set)
                    if set_idx >= 0:
                        self.combo_tooling_set.setCurrentIndex(set_idx)
                    grid.addWidget(self.combo_tooling_set, 4, 1)

                    main_layout.addWidget(box)

                    # Buttons
                    btn_layout = QtWidgets.QHBoxLayout()
                    btn_reset = QtWidgets.QPushButton("Сбросить по умолчанию")
                    btn_reset.clicked.connect(self._on_reset_defaults)
                    btn_layout.addWidget(btn_reset)
                    btn_layout.addStretch(1)

                    btn_box = QtWidgets.QDialogButtonBox(
                        QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel
                    )
                    btn_box.accepted.connect(self._on_save)
                    btn_box.rejected.connect(self.reject)
                    btn_layout.addWidget(btn_box)

                    main_layout.addLayout(btn_layout)

                def _on_browse(self) -> None:
                    from PySide6 import QtWidgets

                    current_dir = self.edit_path.text() or str(Path.cwd())
                    chosen = QtWidgets.QFileDialog.getExistingDirectory(
                        self, "Выберите директорию библиотеки оснастки (toollibrary)", current_dir
                    )
                    if chosen:
                        self.edit_path.setText(chosen)

                def _on_reset_defaults(self) -> None:
                    self.edit_path.setText(str(TOOL_LIBRARY_ROOT))
                    astar_idx = self.combo_algo.findData("astar")
                    if astar_idx >= 0:
                        self.combo_algo.setCurrentIndex(astar_idx)
                    prod_idx = self.combo_policy.findData("PRODUCTION")
                    if prod_idx >= 0:
                        self.combo_policy.setCurrentIndex(prod_idx)
                    self.combo_tooling_set.setCurrentIndex(0)

                def _on_save(self) -> None:
                    chosen_path = self.edit_path.text().strip()
                    chosen_algo = self.combo_algo.currentData()
                    chosen_policy = self.combo_policy.currentData()
                    chosen_set = self.combo_tooling_set.currentData() or ""

                    set_smbs_preferences({
                        "tooling_library_path": chosen_path,
                        "search_algorithm": chosen_algo,
                        "unknown_policy": chosen_policy,
                        "tooling_set_id": chosen_set,
                    })
                    self.accept()

            _DialogClass = _Dialog

        return _DialogClass(parent)
