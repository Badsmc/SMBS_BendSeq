"""Headless startup using FreeCAD's modern lowercase entry point."""
from . import enabled

if enabled():
    # Import the same module as legacy Init.py, never initialize twice.
    from freecad_smbs import Init as _legacy_init
