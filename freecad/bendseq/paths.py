"""Canonical installation and writable user locations; safe without FreeCAD/Qt."""
from pathlib import Path
import os
import sys

PACKAGE = Path(__file__).resolve().parent
REPOSITORY = PACKAGE.parent.parent
RESOURCES = PACKAGE / "resources"
ICONS = RESOURCES / "icons"
PANELS = RESOURCES / "panels"
TRANSLATIONS = RESOURCES / "translations"
TEMPLATES = RESOURCES / "templates"
DEFAULT_TOOL_LIBRARY = RESOURCES / "default_toollibrary"


def freecad_user_directory():
    runtime = sys.modules.get("FreeCAD")
    if runtime is not None:
        try:
            directory = runtime.getUserAppDataDir()
            if directory:
                return Path(directory).expanduser()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            pass
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
                    or str(Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share"))
    return base / "FreeCAD"


def user_data():
    return freecad_user_directory() / "BendSeq"


def tool_library():
    return user_data() / "toollibrary"


def configuration():
    return user_data() / "config"


def user_templates():
    return user_data() / "templates"


def diagnostics():
    return user_data() / "diagnostics"


def sequences():
    return user_data() / "sequences"


def initialized_tool_library():
    # Delay disk writes until a library is requested, never at package import.
    from .user_data_migration import migrate_user_data
    migrate_user_data()
    return tool_library()
