"""One location for SMBS reports, motion traces and diagnostic logs.

Default: <installed SMBS addon>/reports/
For non-writable (e.g. Program Files) addon directories, fall back to
<user home>/pressbrake_traces/ and emit a visible warning. All callers use
the same directory selection, so files remain together for that installation.

No FreeCAD or third-party imports. No directory changes at module import time.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def addon_root() -> Path:
    # smbs_core/report_paths.py lives directly below the installed addon root.
    return Path(__file__).resolve().parent.parent


def reports_dir() -> Path:
    """Create/return writable folder; never silently lose diagnostic reports."""
    override = os.environ.get("SMBS_REPORTS_DIR", "").strip()
    primary = Path(override).expanduser() if override else addon_root() / "reports"
    # Keep a stable fallback so GUI and CLI use the same location.
    fallback = Path.home() / "pressbrake_traces"
    last_error = None
    for candidate in (primary, fallback):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            # Directory existence is not enough (read-only application folders).
            import tempfile
            with tempfile.TemporaryFile(dir=str(candidate)):
                pass
            if candidate != primary:
                print(
                    "SMBS WARNING: addon reports folder is not writable: {}. "
                    "Using: {}. Cause: {}".format(primary, candidate, last_error),
                    file=sys.stderr,
                )
            return candidate.resolve()
        except (OSError, ValueError) as exc:
            last_error = exc
    raise OSError(
        "SMBS cannot write reports in {} or {}: {}".format(
            primary, fallback, last_error,
        )
    )


def report_file(filename: str, *, legacy_override_env: str | None = None) -> Path:
    """Resolve single output file, honoring existing explicit per-script override."""
    if legacy_override_env:
        override = os.environ.get(legacy_override_env, "").strip()
        if override:
            target = Path(override).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            return target
    name = Path(filename)
    if name.name != filename or name.name in (".", ".."):
        raise ValueError("report filename must be a plain filename")
    return reports_dir() / filename
