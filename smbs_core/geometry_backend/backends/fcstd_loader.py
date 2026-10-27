"""
fcstd_loader.py - Loads Tooling objects from .FCStd files and .tooling.json sidecars.
Supports Q1=D 4-level fallback chain (property -> name -> sidecar -> inferred).
"""

import os
import json
from enum import Enum
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any

from smbs_core.models.tooling import Tooling, ToolingKind, DatumRole, Datum, GeometryStatus
from smbs_core.geometry_backend.backends.fcstd_reader import read_fcstd

try:
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCC.Core.gp import gp_Trsf
    from OCC.Core.Bnd import Bnd_Box
    from OCC.Core.BRepBndLib import brepbndlib
    HAS_OCC = True
except ImportError:
    HAS_OCC = False


class AmbiguousShapeError(ValueError):
    """Raised when an .FCStd document contains multiple candidate shapes and target object is ambiguous."""
    pass


class RoleResolutionError(ValueError):
    """Raised on invalid role specifications or FreeCAD document inspection errors."""
    pass


class RoleResolutionSource(str, Enum):
    """Q1=D provenance: where the role came from."""
    PROPERTY = "property"      # from FCStd object SMBS_Role property
    NAME = "name"              # from FCStd object name (PUNCH, DIE, ...)
    SIDECAR = "sidecar"        # from .tooling.json "kind" field
    INFERRED = "inferred"      # heuristic (heights, ratios) — confirmed=False


# Map valid role strings to ToolingKind
ROLE_STRING_MAP: Dict[str, ToolingKind] = {
    "punch": ToolingKind.PUNCH,
    "die": ToolingKind.DIE,
    "frame": ToolingKind.MACHINE_FRAME,
    "machine_frame": ToolingKind.MACHINE_FRAME,
    "backgauge": ToolingKind.BACKGAUGE,
    "ram": ToolingKind.RAM,
    "bed": ToolingKind.MACHINE_BED,
    "machine_bed": ToolingKind.MACHINE_BED,
}

# Substring patterns for NAME level matching
NAME_PATTERNS: List[Tuple[str, ToolingKind]] = [
    ("punch", ToolingKind.PUNCH),
    ("puncher", ToolingKind.PUNCH),
    ("die", ToolingKind.DIE),
    ("matrica", ToolingKind.DIE),
    ("matrix", ToolingKind.DIE),
    ("frame", ToolingKind.MACHINE_FRAME),
    ("stanina", ToolingKind.MACHINE_FRAME),
    ("backgauge", ToolingKind.BACKGAUGE),
    ("upor", ToolingKind.BACKGAUGE),
    ("stop", ToolingKind.BACKGAUGE),
    ("ram", ToolingKind.RAM),
    ("bed", ToolingKind.MACHINE_BED),
]


def resolve_role(
    fcstd_path: Path | str,
    sidecar_data: Optional[dict] = None,
    explicit_target_object_name: Optional[str] = None,
) -> Tuple[Optional[ToolingKind], Optional[RoleResolutionSource], str]:
    """
    Q1=D fallback chain. Does NOT load full OCC geometry — inspects metadata.

    Returns:
        (kind, source, diagnostic_message)
        - kind: ToolingKind or None if unresolved
        - source: RoleResolutionSource or None if unresolved
        - diagnostic: human-readable explanation for manifest.diagnostics

    Fallback order:
      1. PROPERTY — open FCStd doc (headless via FreeCAD), read SMBS_Role from
         target object (or any object if target_object_name is None).
      2. NAME — open FCStd doc, read object Name/Label substring match.
      3. SIDECAR — sidecar_data.get("kind") if provided or from sidecar file.
      4. INFERRED — heuristic. NOT IMPLEMENTED in S.22c; returns (None, None, msg).
    """
    path_obj = Path(fcstd_path)
    fcstd_str = str(path_obj.resolve())

    # 1. PROPERTY level & 2. NAME level via FreeCAD document inspection
    if path_obj.exists() and path_obj.is_file():
        # Ensure FreeCAD lib path is present if available
        try:
            from smbs_core.geometry_backend.freecad_bootstrap import ensure_freecad_paths
            ensure_freecad_paths()
        except ImportError:
            pass

        try:
            import FreeCAD as App  # type: ignore
        except ImportError:
            raise RoleResolutionError("FreeCAD not available for property-level role resolution")

        doc = None
        try:
            doc = App.openDocument(fcstd_str)
            if doc is not None:
                objects = doc.Objects

                # Filter candidates if explicit_target_object_name specified
                candidates = objects
                if explicit_target_object_name:
                    matched = [o for o in objects if o.Name == explicit_target_object_name]
                    if matched:
                        candidates = matched

                # Level 1: PROPERTY
                for obj in candidates:
                    props = getattr(obj, "PropertiesList", [])
                    if "SMBS_Role" in props:
                        val = getattr(obj, "SMBS_Role", None)
                        if val is not None and str(val).strip():
                            val_str = str(val).strip().lower()
                            if val_str not in ROLE_STRING_MAP:
                                raise RoleResolutionError(
                                    f"Invalid SMBS_Role='{val}' in object '{obj.Name}' of {path_obj.name}. "
                                    f"Expected one of: {list(ROLE_STRING_MAP.keys())}"
                                )
                            kind = ROLE_STRING_MAP[val_str]
                            return kind, RoleResolutionSource.PROPERTY, f"Resolved role '{kind.value}' via property SMBS_Role on '{obj.Name}'"

                # Level 2: NAME
                for obj in candidates:
                    names_to_check = [getattr(obj, "Name", ""), getattr(obj, "Label", "")]
                    for name_val in names_to_check:
                        if not name_val:
                            continue
                        name_lower = name_val.lower()
                        for pattern, matched_kind in NAME_PATTERNS:
                            if pattern in name_lower:
                                return (
                                    matched_kind,
                                    RoleResolutionSource.NAME,
                                    f"Resolved role '{matched_kind.value}' via name pattern '{pattern}' in object '{name_val}'"
                                )
        except RoleResolutionError:
            raise
        except Exception as err:
            # FCStd exists but couldn't be inspected — treat as corruption / incompatible file.
            # Do NOT silently fall through to sidecar: that would mask a real problem.
            raise RoleResolutionError(
                f"FreeCAD failed to inspect '{path_obj.name}' for role resolution: {err}. "
                f"File may be corrupted or generated by an incompatible FreeCAD version."
            ) from err
        finally:
            if doc is not None:
                try:
                    App.closeDocument(doc.Name)
                except Exception:
                    pass

    # Level 3: SIDECAR
    if sidecar_data is None:
        sidecar_file = path_obj.parent / f"{path_obj.stem}.tooling.json"
        if sidecar_file.exists():
            try:
                with open(sidecar_file, "r", encoding="utf-8") as f:
                    sidecar_data = json.load(f)
            except Exception:
                sidecar_data = None

    if sidecar_data and isinstance(sidecar_data, dict):
        raw_kind = sidecar_data.get("kind")
        if raw_kind is not None and str(raw_kind).strip():
            raw_kind_str = str(raw_kind).strip().lower()
            if raw_kind_str not in ROLE_STRING_MAP:
                raise RoleResolutionError(
                    f"Invalid kind='{raw_kind}' in sidecar for {path_obj.name}. "
                    f"Expected one of: {list(ROLE_STRING_MAP.keys())}"
                )
            kind = ROLE_STRING_MAP[raw_kind_str]
            return kind, RoleResolutionSource.SIDECAR, f"Resolved role '{kind.value}' via sidecar JSON"

    # Level 4: INFERRED (Not implemented in S.22c)
    return None, None, "Role could not be resolved from property, name, or sidecar; inferred heuristic not implemented (S.22d)"


class FcstdToolingLoader:
    """Loads Tooling from .FCStd and sidecar .tooling.json with Q1=D role resolution."""

    def load(
        self,
        path: str | Path,
        kind: Optional[ToolingKind] = None,
        target_object_name: Optional[str] = None
    ) -> Tooling:
        path_str = str(path)
        path_obj = Path(path)
        tool_id = path_obj.stem

        # Q1=D resolution if kind is None
        resolved_source: Optional[RoleResolutionSource] = None
        role_diag: str = ""

        if kind is None:
            resolved_kind, resolved_source, role_diag = resolve_role(
                fcstd_path=path_obj,
                explicit_target_object_name=target_object_name
            )
            if resolved_kind is None:
                # Unresolved role -> Tooling with MISSING status
                return Tooling(
                    id=tool_id,
                    kind=ToolingKind.PUNCH,  # Dummy fallback kind
                    source_file=path_str,
                    shape=None,
                    local_frame=gp_Trsf() if HAS_OCC else None,
                    geometry_status=GeometryStatus.MISSING,
                    datums={},
                    metadata={},
                    diagnostics=[f"Role resolution failed: {role_diag}"],
                    smbs_role=None,
                    smbs_role_source=None
                )
            kind = resolved_kind
            smbs_role_val = kind.value
            smbs_role_source_val = resolved_source.value if resolved_source else None
        else:
            # Legacy explicit kind path
            smbs_role_val = kind.value
            smbs_role_source_val = None

        # 1. Read geometry
        results, geo_status = read_fcstd(path_str)

        if geo_status == GeometryStatus.MISSING or not results:
            return Tooling(
                id=tool_id,
                kind=kind,
                source_file=path_str,
                shape=None,
                local_frame=gp_Trsf() if HAS_OCC else None,
                geometry_status=GeometryStatus.MISSING,
                datums={},
                metadata={},
                diagnostics=[f"No valid B-Rep shape extracted from {path_str}"],
                smbs_role=smbs_role_val,
                smbs_role_source=smbs_role_source_val
            )

        # Disambiguate shape selection
        if target_object_name:
            # 1. Explicit target — highest priority (unchanged)
            matched = [r for r in results if r[0] == target_object_name]
            if not matched:
                raise AmbiguousShapeError(f"Target object '{target_object_name}' not found in {path_str}")
            obj_name, shape, trsf = matched[0]

        elif len(results) == 1:
            # 2. Single result — take it
            obj_name, shape, trsf = results[0]

        else:
            # 3. Multiple results — filter by volume + heuristic preference

            def _has_volume(shape) -> bool:
                try:
                    from OCC.Core.GProp import GProp_GProps
                    from OCC.Core.BRepGProp import brepgprop
                    props = GProp_GProps()
                    brepgprop.VolumeProperties(shape, props)
                    return props.Mass() > 1e-6
                except Exception:
                    return False

            candidates = [r for r in results if _has_volume(r[1])]
            if not candidates:
                raise AmbiguousShapeError(
                    f"No solid shape with non-zero volume found in {path_str}. "
                    f"Objects: {[r[0] for r in results]}. Specify target_object_name explicitly."
                )

            if len(candidates) == 1:
                obj_name, shape, trsf = candidates[0]
            else:
                # Prefer Body > Pad > ToolShape (PartDesign convention)
                preference_order = ["Body", "Pad", "ToolShape"]
                preferred = None
                for pref_name in preference_order:
                    matches = [r for r in candidates if r[0] == pref_name]
                    if len(matches) == 1:
                        preferred = matches[0]
                        break
                if preferred is not None:
                    obj_name, shape, trsf = preferred
                else:
                    raise AmbiguousShapeError(
                        f"Ambiguous shape selection in {path_str}. "
                        f"Candidates (with volume): {[r[0] for r in candidates]}. "
                        "Specify target_object_name explicitly."
                    )

        world_shape = shape
        if HAS_OCC and trsf:
            world_shape = BRepBuilderAPI_Transform(shape, trsf, False).Shape()

        tool = Tooling(
            id=tool_id,
            kind=kind,
            source_file=path_str,
            shape=world_shape,
            local_frame=trsf,
            geometry_status=GeometryStatus.REAL,
            datums={},
            metadata={},
            diagnostics=[] if not role_diag else [role_diag],
            smbs_role=smbs_role_val,
            smbs_role_source=smbs_role_source_val
        )

        # 2. Check for sidecar JSON
        sidecar_path = path_str.replace(".FCStd", ".tooling.json")
        if os.path.exists(sidecar_path):
            self._load_sidecar(sidecar_path, tool)
        else:
            tool.geometry_status = GeometryStatus.SIMPLIFIED
            tool.diagnostics.append("Sidecar tooling.json missing. Datums inferred heuristically.")
            self._infer_datums(tool)

        return tool

    def _load_sidecar(self, path: str, tool: Tooling):
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        if "id" in data:
            tool.id = data["id"]
        if "metadata" in data:
            tool.metadata = data["metadata"]

        if "datums" in data:
            for role_str, datum_data in data["datums"].items():
                try:
                    role = DatumRole(role_str)
                    tool.datums[role] = Datum.from_json(role, datum_data, source="sidecar")
                except (ValueError, KeyError) as e:
                    tool.diagnostics.append(f"Failed to parse datum {role_str}: {e}")

    def _infer_datums(self, tool: Tooling):
        if not HAS_OCC or tool.shape is None:
            return

        bbox = Bnd_Box()
        brepbndlib.Add(tool.shape, bbox, False)
        xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()

        x_c = (xmin + xmax) / 2.0
        y_c = (ymin + ymax) / 2.0

        if tool.kind == ToolingKind.PUNCH:
            tool.datums[DatumRole.MOUNTING_PLANE] = Datum(
                role=DatumRole.MOUNTING_PLANE, kind="plane",
                geometry=((x_c, y_c, zmax), (0, 0, 1)),
                source="inferred", confirmed=False
            )
            tool.datums[DatumRole.TIP_POINT] = Datum(
                role=DatumRole.TIP_POINT, kind="point",
                geometry=((x_c, y_c, zmin),),
                source="inferred", confirmed=False
            )
            tool.datums[DatumRole.WORKING_AXIS] = Datum(
                role=DatumRole.WORKING_AXIS, kind="axis",
                geometry=((x_c, y_c, zmin), (1, 0, 0)),
                source="inferred", confirmed=False
            )

        elif tool.kind == ToolingKind.DIE:
            tool.datums[DatumRole.MOUNTING_PLANE] = Datum(
                role=DatumRole.MOUNTING_PLANE, kind="plane",
                geometry=((x_c, y_c, zmin), (0, 0, -1)),
                source="inferred", confirmed=False
            )
            tool.datums[DatumRole.V_CENTER] = Datum(
                role=DatumRole.V_CENTER, kind="axis",
                geometry=((x_c, y_c, zmax), (1, 0, 0)),
                source="inferred", confirmed=False
            )
            tool.datums[DatumRole.WORKING_AXIS] = Datum(
                role=DatumRole.WORKING_AXIS, kind="axis",
                geometry=((x_c, y_c, zmax), (1, 0, 0)),
                source="inferred", confirmed=False
            )
