"""Native FreeCAD/Qt presentation of the PressBrakeSim Tasks dock.

No addon color palette is installed. The FreeCAD application's QStyle and
QPalette control light/dark appearance, focus, disabled and hover states.
Only content spacing and font weights are adjusted here.
"""
from __future__ import annotations

# Small, style-neutral layout metrics. Physical pixels; Qt scales on HiDPI.
OUTER_MARGIN = 6
SECTION_PADDING = 2
SECTION_GAP = 6
CORNER_RADIUS = 0

# Deliberately empty: a parent stylesheet interferes with FreeCAD's own dark
# theme, changing the scroll viewport, child widgets and QFrame backgrounds
# to light surfaces even when the main window is dark.
TASK_QSS = ""


def section_card(QtCore, QtWidgets, title: str):
    """A borderless semantic section that uses only the current native style."""
    frame = QtWidgets.QFrame()
    frame.setObjectName("SMBS_SectionCard")
    frame.setFrameShape(QtWidgets.QFrame.NoFrame)
    layout = QtWidgets.QVBoxLayout(frame)
    layout.setContentsMargins(SECTION_PADDING, 3, SECTION_PADDING, 3)
    layout.setSpacing(SECTION_GAP)
    if title:
        heading = QtWidgets.QLabel(title)
        heading.setObjectName("SMBS_SectionTitle")
        heading.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        heading_font = heading.font()
        heading_font.setBold(True)
        heading.setFont(heading_font)
        layout.addWidget(heading)
    return frame, layout



def _semantic_icon_name(label: str) -> str | None:
    """Map translated action captions to semantic artwork, not button order."""
    name = str(label or "").strip().casefold()
    groups = (
        (("dxf",), "control_dxf.svg"),
        (("mesh", "сетка", "triangulat"), "control_mesh.svg"),
        (("коллиз", "collision", "пересечен", "penetrat"), "control_collision.svg"),
        (("сохран", "save", "экспорт", "export"), "control_save.svg"),
        (("импорт", "import", "загруз", "load", "добав", "add"), "control_import.svg"),
        (("развёрт", "разверт", "unfold"), "control_unfold.svg"),
        (("пуансон", "матриц", "оснаст", "tooling", "tool library"), "gui_tooling.svg"),
        (("упор", "backgauge", "зажим", "clamp"), "gui_backgauge.svg"),
        (("повернут", "разворот", "rotate", "flip"), "control_rotate.svg"),
        (("отмен", "назад", "undo", "back"), "control_prev.svg"),
        (("восстанов", "сброс", "очист", "reset", "clear"), "control_reset.svg"),
        (("провер", "validate", "check", "рассчит", "calculate", "примен", "apply"), "control_validate.svg"),
        (("симулир", "simulation", "play", "воспроизв", "запустить", "start"), "control_play.svg"),
        (("настро", "setting", "дополнител", "option"), "gui_settings.svg"),
        (("вариант", "sequence", "последователь"), "gui_sequence_catalog.svg"),
        (("гиб", "bend", "детал", "part"), "gui_part.svg"),
        (("вписать", "fit", "целиком"), "control_fit.svg"),
    )
    for fragments, icon in groups:
        if any(fragment in name for fragment in fragments):
            return icon
    return None


def set_semantic_icon(button, label: str | None = None) -> None:
    """Use native QIcon and keep the inherited FreeCAD palette untouched."""
    from pathlib import Path
    icon_name = _semantic_icon_name(label if label is not None else button.text())
    if not icon_name:
        return
    icon_path = Path(__file__).resolve().parents[1] / "resources" / "icons" / icon_name
    if not icon_path.is_file():
        return
    from freecad_smbs.commands.pressbrake_2d import _qt
    _QtCore, QtGui, _QtWidgets = _qt()
    button.setIcon(QtGui.QIcon(str(icon_path)))

def style_action(button, primary=False, compact=False):
    """Preserve the original connected button and let FreeCAD's style paint it.

    'primary' is intentionally not a color treatment: no custom accent fills,
    gradients or fixed width. Keep button size based on its own label.
    """
    button.setObjectName(
        "SMBS_PrimaryAction" if primary else
        "SMBS_CompactButton" if compact else "SMBS_ActionButton"
    )
    button.setMinimumHeight(22)
    button.setMaximumHeight(29)
    button.setMaximumWidth(280)
    size_policy = button.sizePolicy()
    size_policy.setHorizontalPolicy(type(size_policy).Maximum)
    button.setSizePolicy(size_policy)
    if not button.toolTip():
        button.setToolTip(button.text())
    if button.icon().isNull():
        set_semantic_icon(button)
    return button


def classify_status(message: str) -> tuple[str, str]:
    """Conservative visual classification, never infer physical safety."""
    raw = str(message or "")
    upper = raw.upper()
    lower = raw.lower()
    if any(term in lower for term in (
        "ошибка", "сбой", "невозможно", "отклонен", "отклонён",
        "не удалось", "cannot", "failed", "error:", "стоп:",
    )):
        return "error", "Error"
    if any(term in upper for term in ("UNVERIFIED", "UNSUPPORTED", "NOT_VERIFIED")):
        return "unverified", "Unverified"
    if any(term in lower for term in (
        "conditional", "предупрежден", "предупреждён", "не подтвержден",
        "не подтверждён", "не проверен", "не проверён", "частичный",
    )):
        return "warning", "Warning"
    if any(term in lower for term in (
        "выполняется", "проверяется", "идёт поиск", "ищем",
        "auto play:", "поиск последовательности (глубина",
    )):
        return "running", "Running"
    if any(term in lower for term in (
        "загружен", "сохранён", "сохранен", "завершён",
        "завершен", "найден", "ready",
    )):
        return "success", "Ready"
    return "ready", "Ready"
