"""Native FreeCAD/Qt presentation of the PressBrakeSim Tasks dock.

No addon color palette is installed. The FreeCAD application's QStyle and
QPalette control light/dark appearance, focus, disabled and hover states.
Only content spacing and font weights are adjusted here.
"""
from __future__ import annotations

from freecad.bendseq.ui.localization import language_metadata

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
    """A standard Qt group with native FreeCAD styling."""
    frame = QtWidgets.QGroupBox(title) if title else QtWidgets.QWidget()
    frame.setObjectName("SMBS_SectionCard")
    layout = QtWidgets.QVBoxLayout(frame)
    # QGroupBox title, border, spacing and margins follow FreeCAD QStyle.
    return frame, layout


def style_action(button, primary=False, compact=False):
    """Preserve the original connected button and let FreeCAD's style paint it.

    'primary' is intentionally not a color treatment: no custom accent fills,
    gradients or fixed width. Keep button size based on its own label.
    """
    button.setObjectName(
        "SMBS_PrimaryAction" if primary else
        "SMBS_CompactButton" if compact else "SMBS_ActionButton"
    )
    # Use standard size hints: no fixed height/width across DPI or themes.
    if not button.toolTip():
        button.setToolTip(button.text())
    return button


def classify_status(message: str) -> tuple[str, str]:
    """Conservative visual classification, never infer physical safety."""
    raw = str(message or "")
    upper = raw.upper()
    lower = raw.lower()
    tokens = language_metadata("ru").get("status_tokens", {})
    if any(term in lower for term in (
        *tokens.get("error", ()), "cannot", "failed", "error:",
        "could not", "unable", "unavailable", "stopped due to", "invalid", "rejected",
    )):
        return "error", "Error"
    if any(term in upper for term in ("UNVERIFIED", "UNSUPPORTED", "NOT_VERIFIED")):
        return "unverified", "Unverified"
    if any(term in lower for term in (
        *tokens.get("warning", ()), "conditional",
        "not checked", "not verified", "incomplete", "preliminary",
        "did not start", "not started", "no candidates found", "no solution",
    )):
        return "warning", "Warning"
    if any(term in lower for term in (
        *tokens.get("running", ()), "auto play: ",
        "checking", "searching", "calculating", "in progress",
    )):
        return "running", "Running"
    if any(term in lower for term in (
        *tokens.get("success", ()), "ready",
        "loaded", "saved", "completed", "found", "applied",
    )):
        return "success", "Ready"
    return "ready", "Ready"
