"""GUI startup using FreeCAD's modern lowercase entry point."""
from . import enabled

if enabled():
    # Canonical workbench registration and FCStd paths remain unchanged.
    from freecad_smbs.startup_diagnostics import note_gui_entrypoint
    note_gui_entrypoint("namespace")
    from freecad_smbs import InitGui as _legacy_gui
