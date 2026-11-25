"""Minimal 24px icons, with the original FreeCAD resources as fallback.

This module has no mandatory Qt import; paths work during Workbench discovery.
"""
from pathlib import Path

_ORIGINAL = Path(__file__).resolve().parent / "resources" / "icons"
_MINIMAL = _ORIGINAL / "minimal24"


def is_dark_theme() -> bool:
    """Read the active Qt application palette without creating an application."""
    try:
        from PySide import QtWidgets
    except ImportError:
        return False
    app = QtWidgets.QApplication.instance()
    if app is None:
        return False
    color = app.palette().window().color()
    luminance = (299 * color.red() + 587 * color.green() + 114 * color.blue()) / 1000
    return luminance < 128


def icon_path(name: str) -> Path:
    """Find the selected icon for the current theme, falling back to legacy SVG."""
    name = Path(name).name
    if name.endswith(".svg"):
        theme = "dark" if is_dark_theme() else "light"
        selected = _MINIMAL / theme / name
        if selected.is_file():
            return selected
    return _ORIGINAL / name
