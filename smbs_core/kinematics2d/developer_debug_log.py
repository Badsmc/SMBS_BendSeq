"""One continuously updated, readable SMBS development journal on Desktop.

A single JSON object with an append-only "events" array. Replaces only the
closing JSON suffix on each event (O(event size), not O(whole file)). Every
completed append leaves a valid JSON document; after an interrupted write
the next session salvages previously complete JSON records.

This is a developer diagnostic, NOT a collision certificate or telemetry
upload. No network access. No additional session or trace files required.
"""
from __future__ import annotations

import datetime as _datetime
import json
import os
from pathlib import Path
import platform
import sys
import threading
import time
import uuid
from collections.abc import Mapping

_SCHEMA = "smbs.desktop_developer_debug.v1"
_TAIL = b"\n]}\n"
_JOURNAL = None
_LOCK = threading.RLock()


def desktop_directory(home=None, *, environ=None, windows=None):
    """Resolve real Windows redirected Desktop (including OneDrive), or ~/Desktop."""
    home = Path.home() if home is None else Path(home)
    env = os.environ if environ is None else environ
    is_windows = os.name == "nt" if windows is None else bool(windows)
    if is_windows:
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "Desktop")
                raw = str(value)
                for name, val in env.items():
                    raw = raw.replace("%" + name + "%", str(val))
                    raw = raw.replace("%" + name.upper() + "%", str(val))
                result = Path(os.path.expandvars(raw)).expanduser()
                if result.is_dir():
                    return result
        except (ImportError, OSError, ValueError, TypeError):
            pass
        for base in (env.get("OneDrive"), env.get("OneDriveCommercial"),
                     env.get("OneDriveConsumer"), home):
            if base and (Path(base) / "Desktop").is_dir():
                return Path(base) / "Desktop"
    return home / "Desktop"


def _utc():
    return _datetime.datetime.now(_datetime.timezone.utc).isoformat(
        timespec="milliseconds"
    )


def _jsonable(value, *, depth=0):
    """Protect JSON file from Part.Shape, Qt wrappers, cycles and NaN."""
    if depth >= 12:
        return {"type": type(value).__name__, "repr": repr(value)[:1000]}
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if __import__("math").isfinite(value) else str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(k): _jsonable(v, depth=depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, (tuple, list, set)):
        return [_jsonable(v, depth=depth + 1) for v in value]
    if hasattr(value, "value") and isinstance(value.value, (str, int, float)):
        return _jsonable(value.value, depth=depth + 1)
    return {"type": type(value).__name__, "repr": repr(value)[:1000]}


def _initial_file(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    head = (
        '{"schema_version":' + json.dumps(_SCHEMA)
        + ',"description":"LOCAL DEBUG LOG; NO NETWORK; NOT A SAFETY CERTIFICATE",'
        + '"events":[\n]}\n'
    )
    with path.open("xb") as fh:
        fh.write(head.encode("utf-8"))
        fh.flush()
        os.fsync(fh.fileno())


def _recover_events(raw):
    """Recover complete records even if a power loss interrupted the last append."""
    txt = raw.decode("utf-8", errors="replace")
    marker = '"events":['
    begin = txt.find(marker)
    if begin < 0:
        return []
    idx = begin + len(marker)
    decoder = json.JSONDecoder()
    events = []
    while idx < len(txt):
        while idx < len(txt) and txt[idx] in " \r\n\t,":
            idx += 1
        if idx >= len(txt) or txt[idx] == "]":
            break
        try:
            obj, idx = decoder.raw_decode(txt, idx)
        except (ValueError, TypeError):
            break
        if not isinstance(obj, dict):
            break
        events.append(obj)
    return events


class DesktopDebugJournal:
    """Single writer for one valid JSON file; no background tasks or uploads."""

    def __init__(self, path=None):
        self.path = Path(path) if path else desktop_directory() / "SMBS_debug_log.json"
        self._lock = threading.RLock()
        self.session_id = str(uuid.uuid4())
        self._next = 1
        self.last_error = None
        self._init()
        self.emit(
            "SMBS_SESSION_STARTED",
            pid=os.getpid(),
            python=sys.version,
            platform=platform.platform(),
            log_path=str(self.path),
        )

    def _init(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            _initial_file(self.path)
            return
        raw = self.path.read_bytes()
        try:
            parsed = json.loads(raw)
            if parsed.get("schema_version") != _SCHEMA:
                raise ValueError("Unrecognized existing Desktop debug JSON schema")
            events = parsed["events"]
            if not isinstance(events, list):
                raise ValueError("Malformed debug events list")
            self._next = max(
                (int(x.get("sequence", 0)) for x in events
                 if isinstance(x, dict)),
                default=0,
            ) + 1
            if not raw.endswith(_TAIL):
                # Restore expected compact append suffix on legacy formatting.
                self._rewrite(events)
        except (UnicodeError, ValueError, TypeError, KeyError):
            events = _recover_events(raw)
            self._next = max(
                (int(x.get("sequence", 0)) for x in events
                 if isinstance(x, dict)), default=0,
            ) + 1
            events.append({
                "sequence": self._next, "utc": _utc(),
                "session_id": self.session_id,
                "event": "RECOVERED_INTERRUPTED_JOURNAL",
                "data": {
                    "raw_bytes": len(raw),
                    "recovered_event_count": len(events),
                    "discarded_partial_tail": True,
                },
            })
            self._next += 1
            self._rewrite(events)

    def _rewrite(self, events):
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            with tmp.open("wb") as fh:
                fh.write(
                    ('{"schema_version":' + json.dumps(_SCHEMA)
                     + ',"events":[\n').encode("utf-8")
                )
                for i, event in enumerate(events):
                    if i:
                        fh.write(b",\n")
                    fh.write(json.dumps(
                        event, ensure_ascii=False, separators=(",", ":"),
                    ).encode("utf-8"))
                fh.write(_TAIL)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def emit(self, event, **details):
        with self._lock:
            record = {
                "sequence": self._next,
                "utc": _utc(),
                "monotonic_ns": time.monotonic_ns(),
                "session_id": self.session_id,
                "event": str(event),
                "data": _jsonable(details),
            }
            payload = json.dumps(
                record, ensure_ascii=False, separators=(",", ":"),
            ).encode("utf-8")
            try:
                with self.path.open("r+b") as fh:
                    fh.seek(-len(_TAIL), os.SEEK_END)
                    # A newline before the closing array is always present.
                    # Find previous event without re-parsing entire history.
                    position = fh.tell()
                    fh.seek(max(0, position-2))
                    previous = fh.read(2)
                    separator = b",\n" if previous.rstrip().endswith(b"}") else b""
                    fh.seek(position)
                    fh.write(separator + payload + _TAIL)
                    fh.truncate()
                    fh.flush()
                    # Flush critical errors and per-bend verdicts to disk.
                    if (str(event).endswith(("ERROR", "COLLISION", "COMPLETED"))
                            or "EXCEPTION" in str(event)):
                        os.fsync(fh.fileno())
                self._next += 1
                self.last_error = None
                return True
            except OSError as exc:
                self.last_error = str(exc)
                return False


def get_debug_journal():
    global _JOURNAL
    with _LOCK:
        if _JOURNAL is None:
            _JOURNAL = DesktopDebugJournal()
        return _JOURNAL


def debug_event(event, **details):
    """Best-effort diagnostic: never take down FreeCAD or its OCC solver."""
    try:
        return get_debug_journal().emit(event, **details)
    except Exception:
        return False


def summarize_native_trajectory(result):
    """Every *sampled* machine pose and tool-pair clearance, without BRep blobs."""
    if result is None:
        return None
    out = {
        "bend_id": getattr(result, "bend_id", None),
        "evidence_status": _jsonable(
            getattr(result, "evidence_status", None)
        ),
        "continuous_certification_status": _jsonable(
            getattr(result, "continuous_certification_status", None)
        ),
        "sampled_ram_cycle": _jsonable(
            getattr(result, "ram_cycle_positions_mm", None)
        ),
        "total_states": getattr(result, "total_states_evaluated", None),
        "total_collision_queries": getattr(result, "total_collision_queries", None),
        "elapsed_ms": getattr(result, "elapsed_ms", None),
        "diagnostics": _jsonable(getattr(result, "diagnostics", ())),
        "phases": [],
    }
    for phase in getattr(result, "phase_results", ()) or ():
        ph = {
            "phase": phase.phase_name,
            "evidence_status": _jsonable(phase.evidence_status),
            "is_safe": getattr(phase, "is_safe", None),
            "is_conditional": getattr(phase, "is_conditional", None),
            "first_collision_bracket": _jsonable(
                getattr(phase, "first_collision_bracket", None),
            ),
            "unresolved_intervals": _jsonable(
                getattr(phase, "unresolved_intervals", ())
            ),
            "uninspected_intervals": _jsonable(
                getattr(phase, "uninspected_intervals", ())
            ),
            "discontinuities": _jsonable(
                getattr(phase, "discontinuities", ())
            ),
            "collision_queries": getattr(phase, "collision_queries_count", None),
            "max_punch_overlap_mm3": getattr(
                phase, "max_punch_overlap_volume_mm3", None
            ),
            "max_die_overlap_mm3": getattr(
                phase, "max_die_overlap_volume_mm3", None
            ),
            "elapsed_ms": getattr(phase, "elapsed_ms", None),
            "diagnostics": _jsonable(getattr(phase, "diagnostics", ())),
            "samples": [],
        }
        for state in getattr(phase, "states", ()) or ():
            collision = getattr(state, "collision_report", None)
            punch = getattr(collision, "punch_result", None)
            die = getattr(collision, "die_result", None)
            def pair(p):
                return None if p is None else {
                    "status": _jsonable(getattr(p, "status", None)),
                    "distance_mm": getattr(p, "distance_mm", None),
                    "overlap_volume_mm3": getattr(p, "overlap_volume_mm3", None),
                    "reason": getattr(p, "reason", None),
                    "tool_label": getattr(p, "tool_label", None),
                    "overlap_bbox_xyz_mm": getattr(
                        p, "overlap_bbox_xyz_mm", None,
                    ),
                }
            ph["samples"].append({
                "path_u": state.path_u,
                "phase": state.phase,
                "bend_id": state.active_bend_id,
                "angle_deg": state.signed_angle_deg,
                "ram_z_mm": state.ram_z_mm,
                "support_z_mm": state.support_plane_zm,
                "is_safe": state.is_safe,
                "is_conditional_safe": getattr(
                    state, "is_conditional_safe", False
                ),
                "completed_angles": _jsonable(
                    getattr(state, "completed_angles", None)
                ),
                "workpiece_transform": {
                    "rotation": _jsonable(
                        getattr(
                            getattr(state, "workpiece_placement", None),
                            "rotation", None,
                        )
                    ),
                    "translation": _jsonable(
                        getattr(
                            getattr(state, "workpiece_placement", None),
                            "translation", None,
                        )
                    ),
                },
                "punch_end_zone_analysis": (
                    state.punch_end_zone_analysis.to_dict()
                    if getattr(state, "punch_end_zone_analysis", None)
                    is not None else None
                ),
                "collision_status": _jsonable(
                    getattr(collision, "status", None)
                ),
                "punch": pair(punch),
                "die": pair(die),
                "discontinuity": getattr(
                    state, "discontinuity_reason", None
                ),
            })
        out["phases"].append(ph)
    return out
