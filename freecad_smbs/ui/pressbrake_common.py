"""Qt binding and drawing constants shared by FreeCAD UI entry points."""
from __future__ import annotations

# Explicit QGraphicsScene Z-layer order so tooling never obscures the sheet
Z_LAYER_AUXILIARY = 0.0
Z_LAYER_DIE = 10.0
Z_LAYER_PUNCH = 15.0
Z_LAYER_SHEET_OTHER = 20.0
Z_LAYER_SHEET_PANELS = 30.0
Z_LAYER_SHEET_ZONE = 35.0
Z_LAYER_SHEET_SURFACES = 40.0
Z_LAYER_SHEET_NEUTRAL_AND_TANGENTS = 50.0


_SLIDER_SCALE = 10  # 0.1 degree slider resolution

def _qt():
    """Return QtCore, QtGui, QtWidgets for FreeCAD-supported bindings."""
    try:
        from PySide import QtCore, QtGui
        return QtCore, QtGui, QtGui
    except ImportError:
        try:
            from PySide2 import QtCore, QtGui, QtWidgets
            return QtCore, QtGui, QtWidgets
        except ImportError:
            from PySide6 import QtCore, QtGui, QtWidgets
            return QtCore, QtGui, QtWidgets


