"""PressBrakeSim 2.5D Qt visual system.

All selectors are scoped to SMBS objects and are applied only to the
addon's Tasks dock; FreeCAD's global palette and other workbenches are
never modified. Layout constants are in physical Qt pixels.
"""
from __future__ import annotations

OUTER_MARGIN = 20
SECTION_PADDING = 16
SECTION_GAP = 16
CORNER_RADIUS = 9

TASK_QSS = """
QWidget#SMBS_TaskDockRoot,
QWidget#SMBS_TaskContent {
    background: palette(window);
}
QFrame#SMBS_SectionCard {
    background: palette(base);
    border: 1px solid palette(midlight);
    border-radius: 9px;
}
QLabel#SMBS_SectionTitle {
    font-size: 13px;
    font-weight: 600;
    color: palette(window-text);
    background: transparent;
}
QLabel#SMBS_SectionCaption {
    font-size: 11px;
    color: palette(window-text);
    background: transparent;
}
QFrame#SMBS_TaskFooter {
    border-top: 1px solid palette(midlight);
    background: palette(window);
}
QLabel#SMBS_StatusLabel {
    font-size: 11px;
    color: palette(window-text);
}
QLabel#SMBS_StatusChip {
    font-size: 10px;
    font-weight: bold;
    border-radius: 6px;
    padding: 4px 8px;
    background: palette(midlight);
    color: palette(window-text);
}
QLabel#SMBS_StatusChip[state="running"] {
    background: #286B70;
    color: white;
}
QLabel#SMBS_StatusChip[state="success"] {
    background: #28745D;
    color: white;
}
QLabel#SMBS_StatusChip[state="warning"],
QLabel#SMBS_StatusChip[state="unverified"] {
    background: #84601E;
    color: white;
}
QLabel#SMBS_StatusChip[state="error"] {
    background: #9B4141;
    color: white;
}
QPushButton#SMBS_ActionButton,
QPushButton#SMBS_CompactButton {
    min-height: 36px;
    border: 1px solid palette(mid);
    border-radius: 7px;
    background: palette(button);
    color: palette(button-text);
    padding: 4px 8px;
}
QPushButton#SMBS_ActionButton:hover,
QPushButton#SMBS_CompactButton:hover {
    border-color: #48A99C;
    background: palette(midlight);
}
QPushButton#SMBS_ActionButton:pressed,
QPushButton#SMBS_CompactButton:pressed {
    border-color: #327F78;
    background: #327F78;
    color: white;
}
QPushButton#SMBS_ActionButton:disabled,
QPushButton#SMBS_CompactButton:disabled {
    border-color: palette(midlight);
    background: palette(window);
    color: palette(mid);
}
QPushButton#SMBS_PrimaryAction {
    min-height: 36px;
    padding: 4px 8px;
    border-radius: 7px;
    border: 1px solid #327F78;
    background: #327F78;
    color: white;
    font-weight: bold;
}
QPushButton#SMBS_PrimaryAction:hover {
    background: #40968D;
}
QPushButton#SMBS_PrimaryAction:pressed {
    background: #245E59;
}
QPushButton#SMBS_PrimaryAction:disabled {
    background: palette(midlight);
    color: palette(mid);
    border-color: palette(mid);
}
QProgressBar#SMBS_TaskProgress {
    min-height: 7px;
    max-height: 7px;
    border: 0px;
    border-radius: 3px;
    background: palette(midlight);
    text-align: center;
}
QProgressBar#SMBS_TaskProgress::chunk {
    background: #48A99C;
    border-radius: 3px;
}
QTreeWidget#SMBS_Operations {
    border: 1px solid palette(midlight);
    border-radius: 6px;
    background: palette(base);
    alternate-background-color: palette(alternate-base);
}
QTabWidget#SMBS_ControlTabs::pane {
    border: none;
}
QTabWidget#SMBS_ControlTabs QTabBar::tab {
    padding: 9px 11px;
    min-height: 24px;
}
QTabWidget#SMBS_ControlTabs QTabBar::tab:selected {
    border-bottom: 2px solid #48A99C;
}
"""


def section_card(QtCore, QtWidgets, title: str):
    """Create one semantic engineering card with 16px inner grid."""
    frame = QtWidgets.QFrame()
    frame.setObjectName("SMBS_SectionCard")
    layout = QtWidgets.QVBoxLayout(frame)
    layout.setContentsMargins(SECTION_PADDING, SECTION_PADDING,
                              SECTION_PADDING, SECTION_PADDING)
    layout.setSpacing(SECTION_GAP)
    heading = QtWidgets.QLabel(title)
    heading.setObjectName("SMBS_SectionTitle")
    heading.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
    layout.addWidget(heading)
    return frame, layout


def style_action(button, primary=False, compact=False):
    """Keep existing QPushButton / signal connections; only style its surface."""
    button.setObjectName(
        "SMBS_PrimaryAction" if primary else
        "SMBS_CompactButton" if compact else "SMBS_ActionButton"
    )
    button.setMinimumHeight(36)
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
