"""Bounded, structured, per-search CAD rear-gauge diagnostics (JSON Lines).

One JSON object per line keeps the log usable even if a FreeCAD search
is interrupted or OCC fails. No FreeCAD import, no placeholder geometry.
Only explicit GUI/CLI requests create files; no trace in ordinary core tests.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path


def _safe_number(value):
    x = float(value)
    return round(x, 6) if math.isfinite(x) else None


def bbox_xyz(shape):
    """Six machine-coordinate bounds; None for a missing OCC shape."""
    if shape is None:
        return None
    box = shape.BoundBox
    return {
        "xmin": _safe_number(box.XMin),
        "xmax": _safe_number(box.XMax),
        "ymin": _safe_number(box.YMin),
        "ymax": _safe_number(box.YMax),
        "zmin": _safe_number(box.ZMin),
        "zmax": _safe_number(box.ZMax),
    }


def point_xyz(point):
    return [_safe_number(point.x), _safe_number(point.y), _safe_number(point.z)]


class BackgaugeCADTrace:
    """Write bounded JSONL events to an explicit user-writable destination.

    Open once in append mode for each event, so a crash leaves all completed
    events intact. A single search gets a unique file from its caller.
    """

    def __init__(self, path, *, max_events=1500):
        if max_events < 1:
            raise ValueError("max_events must be positive")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.limit = int(max_events)
        self.events = 0
        self.truncated = False
        with self.path.open("w", encoding="utf-8") as out:
            out.write(json.dumps({
                "schema": "SMBS_BACKGAUGE_CAD_TRACE_V1",
                "event": "TRACE_STARTED",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "max_events": self.limit,
                "units": {"length": "mm", "volume": "mm3"},
                "machine_frame": {
                    "X_m": "along bend line",
                    "Y_m": "operator-positive; rear stop at negative Y_m",
                    "Z_m": "up",
                    "s": "-Y_m, positive towards rear",
                },
                "certification": "DIAGNOSTIC_ONLY_NO_SWEPT_PATH",
            }, ensure_ascii=False) + "\n")

    def emit(self, event, **fields):
        if self.events >= self.limit:
            if not self.truncated:
                self.truncated = True
                self._append({
                    "schema": "SMBS_BACKGAUGE_CAD_TRACE_V1",
                    "event": "TRACE_TRUNCATED",
                    "max_events": self.limit,
                })
            return
        self.events += 1
        self._append({
            "schema": "SMBS_BACKGAUGE_CAD_TRACE_V1",
            "event": str(event),
            **fields,
        })

    def _append(self, record):
        with self.path.open("a", encoding="utf-8") as out:
            out.write(json.dumps(record, ensure_ascii=False,
                                 separators=(",", ":"), allow_nan=False) + "\n")


def fresh_trace_path(directory):
    """Construct a unique path without requiring Git/repo write access."""
    from uuid import uuid4
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path(directory) / (
        "smbs_backgauge_cad_{}_{}.jsonl".format(stamp, uuid4().hex[:8])
    )
