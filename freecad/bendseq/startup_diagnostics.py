"""Identify actual GUI entrypoints and installed interface bytes without Qt."""
from hashlib import sha256
from pathlib import Path
import sys

_GUI_ENTRYPOINTS = []
_INTERFACE_FILES = (
    "Init.py", "InitGui.py", "package.xml",
    "freecad/bendseq/__init__.py", "freecad/bendseq/init.py",
    "freecad/bendseq/init_gui.py",
    "freecad/bendseq/workbench.py", "freecad/bendseq/startup_diagnostics.py",
    "freecad/bendseq/integration/dxf_manual.py",
    "freecad/bendseq/ui/parametric_tool_dialog.py",
    "freecad/bendseq/ui/tooling_panel.py",
    "freecad/bendseq/ui/native_preferences.py",
    "freecad/bendseq/ui/pdf_export_task.py",
    "freecad/bendseq/ui/lifecycle_debug.py",
)


def note_gui_entrypoint(name):
    """Record which wrapper called the shared canonical module."""
    if name not in _GUI_ENTRYPOINTS:
        _GUI_ENTRYPOINTS.append(name)


def startup_snapshot(root):
    """Report observed wrappers and a separate UI fingerprint.

    Do not alter the existing v1 native benchmark fingerprint.
    Loaded entrypoints are observed, not inferred from files on disk.
    """
    root = Path(root)
    hashes = {}
    for name in _INTERFACE_FILES:
        try:
            hashes[name] = sha256((root / name).read_bytes()).hexdigest()
        except OSError:
            hashes[name] = None
    canonical = "\n".join(f"{name}={digest}" for name, digest in hashes.items())
    complete = all(hashes.values())
    module = sys.modules.get("freecad.bendseq.workbench")
    return {
        "gui_entrypoints_seen": list(_GUI_ENTRYPOINTS),
        "first_gui_entrypoint": _GUI_ENTRYPOINTS[0] if _GUI_ENTRYPOINTS else None,
        "canonical_gui_module": getattr(module, "__file__", None),
        "namespace_gui_imported": "freecad.bendseq.init_gui" in sys.modules,
        "interface_sha256": hashes,
        "interface_composite_sha256": sha256(canonical.encode("utf-8")).hexdigest() if complete else None,
        "interface_files_complete": complete,
    }
