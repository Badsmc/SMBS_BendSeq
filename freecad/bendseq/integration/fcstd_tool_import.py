"""Import native tooling with geometry-derived metadata when no sidecar exists.

Original files are never modified. The existing library manager remains the
publisher and role-conflict validator. Native extraction uses the same XY
section / Z extrusion convention as simulation and the parametric generator.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

from freecad.bendseq.core.geometry_backend.io.tooling_library import ToolingLibraryError
from freecad.bendseq.core.kinematics2d.tooling_25d import (
    ToolKind, ToolLibrary25D, ValidationStatus,
)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _point(p):
    if len(p) != 2 or not all(_number(v) for v in p):
        raise ToolingLibraryError("Invalid coordinates in extracted tooling datum")
    return [float(p[0]), float(p[1]), 0.0]


def geometry_sidecar(profile, source):
    """Use verified source-coordinate datums, never filename-derived dimensions."""
    source = Path(source)
    if profile.validation_status != ValidationStatus.GEOMETRY_VERIFIED:
        raise ToolingLibraryError(
            "Tool geometry needs alignment or is invalid: "
            + "; ".join(profile.validation_messages)
        )
    if not _number(profile.length_mm) or profile.length_mm <= 0:
        raise ToolingLibraryError("A positive native tool extrusion length is required")
    if profile.kind == ToolKind.PUNCH and profile.punch_datums is not None:
        d = profile.punch_datums
        mount = d.mounting_base
        working = _point(d.working_tip)
        normal = [0.0, -1.0, 0.0]
        extra = {"tip_point": {"kind": "point", "position": working}}
        dims = {"angle_deg": d.included_angle_deg, "radius_mm": d.tip_radius_mm}
    elif profile.kind == ToolKind.DIE and profile.die_datums is not None:
        d = profile.die_datums
        mount = d.mounting_datum
        working = _point((d.v_groove_center[0], d.top_datum_plane_y))
        normal = [0.0, 1.0, 0.0]
        extra = {"v_center": {"kind": "axis", "origin": working, "direction": [0, 0, 1]}}
        dims = {"angle_deg": d.die_angle_deg, "v_width_mm": d.v_opening_mm,
                "shoulder_radius_mm": d.shoulder_radius_mm}
    else:
        raise ToolingLibraryError("Verified punch or die mounting and working datums are required")
    if d.source != "geometry":
        raise ToolingLibraryError("Automatic metadata requires geometry-derived datums")
    a, b = map(_point, mount)
    if abs(a[1] - b[1]) > 0.01 or abs(a[0] - b[0]) < 1.0:
        raise ToolingLibraryError("Mounting line is not a valid horizontal tool base")
    origin = [(a[0]+b[0])/2, (a[1]+b[1])/2, 0.0]
    datums = {
        "mounting_plane": {"kind": "plane", "origin": origin,
                           "normal": normal, "tolerance_mm": 0.05},
        "working_axis": {"kind": "axis", "origin": working,
                         "direction": [0, 0, 1], "tolerance_mm": 0.02},
        **extra,
    }
    metadata = {
        "reference_length_mm": float(profile.length_mm),
        "segments": [],
        "datum_source": "FCStd geometry",
        "geometry_validation": profile.validation_status.value,
        "manufacturing_certified": False,
        "limitations_25d": list(profile.limitations_25d),
    }
    if _number(profile.height_mm):
        metadata["height_mm"] = float(profile.height_mm)
    metadata.update({key: float(value) for key, value in dims.items() if _number(value)})
    return {"schema_version": "1.0", "id": source.stem, "kind": profile.kind.value.lower(),
            "source": source.name, "datums": datums, "metadata": metadata}


def import_fcstd_tool(library, source, *, kind):
    """Native UI import; keep supplied metadata and generate only missing files."""
    import FreeCAD as App
    source = Path(source).resolve()
    if not source.is_file() or source.suffix.lower() != ".fcstd":
        raise ToolingLibraryError("Select an existing FCStd tooling model")
    if kind not in ("punch", "die"):
        raise ToolingLibraryError("Choose punch or die")
    if ".." in source.stem or "\\" in source.stem:
        raise ToolingLibraryError("Invalid tool ID")
    previous = getattr(getattr(App, "ActiveDocument", None), "Name", None)
    sidecar = source.with_suffix(".tooling.json")
    try:
        if sidecar.exists():
            # Never replace or silently repair an existing sidecar.
            if not sidecar.is_file():
                raise ToolingLibraryError("Tooling sidecar is not a file")
            return library.add_tool(source, kind=kind)
        # The existing publisher uses the FCStd stem as the library ID.
        # Avoid overwriting orphan metadata and case-insensitive duplicates.
        destination = library.library_root / kind
        existing = {p.name.casefold() for p in destination.iterdir()}
        targets = {f"{source.stem}{suffix}".casefold()
                   for suffix in (".FCStd", ".tooling.json", ".profile25d.json")}
        if targets & existing:
            raise ToolingLibraryError("Tool ID already exists in library; use another ID")
        with TemporaryDirectory(prefix="bendseq-import-") as folder:
            staged = Path(folder) / (source.stem + ".FCStd")
            shutil.copy2(source, staged)
            profiles = ToolLibrary25D(library.library_root)
            profile = profiles._load_legacy_fcstd_tool(staged, ToolKind(kind.upper()))
            data = geometry_sidecar(profile, staged)
            staged.with_suffix(".tooling.json").write_text(
                json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            return library.add_tool(staged, kind=kind)
    except ToolingLibraryError:
        raise
    except Exception as exc:
        raise ToolingLibraryError(f"FCStd tooling import failed: {exc}") from exc
    finally:
        if previous and previous in App.listDocuments():
            App.setActiveDocument(previous)
