"""GUI namespace startup. The canonical workbench registers once."""
from . import enabled

if enabled():
    from .startup_diagnostics import note_gui_entrypoint
    note_gui_entrypoint("namespace")
    from . import workbench
