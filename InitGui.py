"""Temporary legacy entrypoint; shares the native workbench module exactly."""
from freecad.bendseq import enabled
if enabled():
    from freecad.bendseq.startup_diagnostics import note_gui_entrypoint
    note_gui_entrypoint("legacy")
    from freecad.bendseq import workbench
