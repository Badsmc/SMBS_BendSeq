"""File diagnostics for the FreeCAD press-brake UI.

Kept separate from Qt and geometry so diagnostics can be tested headlessly.
The runtime log location is deliberately outside the install directory.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_LOGGER: logging.Logger | None = None
def _pressbrake_user_data_directory():
    """Platform-specific writable diagnostics home, not the plugin checkout.

    FreeCAD installations may be under Program Files, a read-only AppImage,
    or a macOS .app bundle. Traces must never depend on repository permissions.
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(
            os.environ.get("APPDATA")
            or os.environ.get("LOCALAPPDATA")
            or str(Path.home() / "AppData" / "Roaming")
        )
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or
                    str(Path.home() / ".local" / "share"))
    return base / "FreeCAD" / "v1-1"


_LOG_FILE_PATH = _pressbrake_user_data_directory() / "pressbrake_2d.log"


def _get_file_logger() -> logging.Logger:
    """Return a non-blocking file logger decoupled from FreeCAD.Console and Qt NotificationArea."""
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER
    logger = logging.getLogger("smbs.pressbrake_2d")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        try:
            _LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(str(_LOG_FILE_PATH), encoding="utf-8")
            handler.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
            )
            logger.addHandler(handler)
        except OSError:
            logger.addHandler(logging.NullHandler())
    _LOGGER = logger
    return logger


def _log(message):
    """Write diagnostic message to file log without invoking FreeCAD.Console / NotificationArea."""
    try:
        _get_file_logger().info("%s", message)
    except Exception:
        pass


def _warn(message):
    """Write warning to file log without invoking FreeCAD.Console.PrintWarning (avoids NotificationArea deadlock)."""
    try:
        _get_file_logger().warning("%s", message)
    except Exception:
        pass


def _error(message):
    """Write unexpected error to file log without triggering FreeCAD NotificationArea popup deadlock."""
    try:
        _get_file_logger().error("%s", message)
    except Exception:
        pass


