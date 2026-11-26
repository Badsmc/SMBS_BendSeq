"""BendSeq native FreeCAD namespace package (no startup side effects)."""
from .core import __version__
from .paths import REPOSITORY as _ROOT


def enabled():
    """Apply FreeCAD disable/metadata policy to the actual installation folder."""
    import FreeCAD as App
    if ((_ROOT / "ADDON_DISABLED").exists()
            or (_ROOT.parent / "ALL_ADDONS_DISABLED").exists()):
        return False
    disabled = App.ConfigGet("DisabledAddons") if hasattr(App, "ConfigGet") else ""
    if _ROOT.name in (disabled or "").split(";"):
        return False
    manifest = _ROOT / "package.xml"
    return manifest.is_file() and App.Metadata(str(manifest)).supportsCurrentFreeCAD()
