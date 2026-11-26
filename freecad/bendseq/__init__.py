"""FreeCAD namespace entry point, preserving legacy FCStd Python module names.

Do not add freecad/__init__.py: FreeCAD owns the shared namespace.
"""
from pathlib import Path
from importlib.util import find_spec

_ROOT = Path(__file__).resolve().parents[2]


def enabled():
    """Apply the real checkout's metadata/disable flag to namespace startup.

    FreeCAD 1.1's namespace scanner checks Mod/bendseq, which is not
    necessarily the name of a manually installed or ZIP checkout.
    """
    import FreeCAD as App
    # Match FreeCAD's legacy loader disable policy for the actual directory.
    # Its namespace scanner instead assumes the directory is named bendseq.
    if ((_ROOT / "ADDON_DISABLED").exists()
            or (_ROOT.parent / "ALL_ADDONS_DISABLED").exists()):
        return False
    disabled = App.ConfigGet("DisabledAddons") if hasattr(App, "ConfigGet") else ""
    if _ROOT.name in (disabled or "").split(";"):
        return False
    manifest = _ROOT / "package.xml"
    if not manifest.is_file():
        return False
    if not App.Metadata(str(manifest)).supportsCurrentFreeCAD():
        return False
    for name in ("freecad_smbs", "smbs_core"):
        spec = find_spec(name)
        if spec is None or not spec.origin or Path(spec.origin).resolve().parent.parent != _ROOT:
            raise ImportError("BendSeq: missing or conflicting package " + name + "; keep one installation.")
    return True
