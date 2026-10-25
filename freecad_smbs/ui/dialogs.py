"""
dialogs.py - User dialog wrappers for SMBS FreeCAD Addon.

Headless-safe: PySide6 imports are strictly lazy inside functions.
Falls back to FreeCAD.Console if running without GUI display.
"""
from __future__ import annotations

import sys


def _print_fallback(prefix: str, title: str, msg: str) -> None:
    try:
        import FreeCAD
        if hasattr(FreeCAD, "Console"):
            FreeCAD.Console.PrintMessage(f"SMBS [{prefix}] {title}: {msg}\n")
            return
    except ImportError:
        pass
    print(f"SMBS [{prefix}] {title}: {msg}", file=sys.stderr)


def show_info(title: str, msg: str) -> None:
    """Display an informative message dialog."""
    try:
        from PySide6 import QtWidgets
        app = QtWidgets.QApplication.instance()
        if app is not None:
            QtWidgets.QMessageBox.information(None, title, msg)
            return
    except (ImportError, Exception):
        pass
    _print_fallback("INFO", title, msg)


def show_error(title: str, msg: str) -> None:
    """Display an error message dialog."""
    try:
        from PySide6 import QtWidgets
        app = QtWidgets.QApplication.instance()
        if app is not None:
            QtWidgets.QMessageBox.critical(None, title, msg)
            return
    except (ImportError, Exception):
        pass
    _print_fallback("ERROR", title, msg)


def show_unverified_warning(title: str, msg: str) -> None:
    """Display a warning dialog regarding unverified or failed sequence."""
    try:
        from PySide6 import QtWidgets
        app = QtWidgets.QApplication.instance()
        if app is not None:
            QtWidgets.QMessageBox.warning(None, title, msg)
            return
    except (ImportError, Exception):
        pass
    _print_fallback("WARNING", title, msg)
