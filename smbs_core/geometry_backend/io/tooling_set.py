"""
tooling_set.py - ToolingSet model for named tooling sets (Stage 7, S.25a).

A ToolingSet is a named bundle: punch_id + die_id + clamp_positions_mm.
Stored as <library>/sets/<set_id>.json.

Pure Python. No OCC, no FreeCAD, no pipeline integration (storage only —
enforcement/substitution is requirement 4).
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

DEFAULT_SET_SCHEMA_VERSION = "1.0"


class ToolingSetError(Exception):
    """Raised on invalid ToolingSet data or malformed JSON."""


@dataclass
class ToolingSet:
    """Named tooling set. Mutable dataclass (metadata is dict)."""
    set_id: str
    punch_id: str
    die_id: str
    clamp_positions_mm: tuple[float, ...]
    description: str = ""
    metadata: dict = field(default_factory=dict)
    schema_version: str = DEFAULT_SET_SCHEMA_VERSION

    def __post_init__(self):
        if not self.set_id or not isinstance(self.set_id, str):
            raise ToolingSetError(f"set_id must be non-empty string, got {self.set_id!r}")
        if not self.punch_id or not isinstance(self.punch_id, str):
            raise ToolingSetError(f"punch_id must be non-empty string, got {self.punch_id!r}")
        if not self.die_id or not isinstance(self.die_id, str):
            raise ToolingSetError(f"die_id must be non-empty string, got {self.die_id!r}")
        # Normalize positions to sorted tuple of floats
        if not isinstance(self.clamp_positions_mm, tuple):
            object.__setattr__(self, "clamp_positions_mm", tuple(float(p) for p in self.clamp_positions_mm))
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})

    @classmethod
    def from_dict(cls, data: dict) -> "ToolingSet":
        """Build from JSON dict. Strict reading (no .get(default) for required fields)."""
        try:
            set_id = data["set_id"]
            punch_id = data["punch_id"]
            die_id = data["die_id"]
            clamp_positions_raw = data["clamp_positions_mm"]
            schema_version = data.get("schema_version", DEFAULT_SET_SCHEMA_VERSION)
            description = data.get("description", "")
            metadata = data.get("metadata", {})
        except (KeyError, TypeError) as e:
            raise ToolingSetError(f"ToolingSet.from_dict missing required field: {e}") from e

        if schema_version != DEFAULT_SET_SCHEMA_VERSION:
            raise ToolingSetError(
                f"Unsupported schema_version '{schema_version}'. Expected '{DEFAULT_SET_SCHEMA_VERSION}'."
            )

        try:
            positions = tuple(sorted(float(p) for p in clamp_positions_raw))
        except (TypeError, ValueError) as e:
            raise ToolingSetError(f"Invalid clamp_positions_mm: {e}") from e

        return cls(
            set_id=set_id,
            punch_id=punch_id,
            die_id=die_id,
            clamp_positions_mm=positions,
            description=description,
            metadata=dict(metadata) if metadata else {},
            schema_version=schema_version,
        )

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict matching tooling_set.schema.json."""
        result = {
            "schema_version": self.schema_version,
            "set_id": self.set_id,
            "punch_id": self.punch_id,
            "die_id": self.die_id,
            "clamp_positions_mm": list(self.clamp_positions_mm),
        }
        if self.description:
            result["description"] = self.description
        if self.metadata:
            result["metadata"] = dict(self.metadata)
        return result


def _validate_data_against_schema(data: dict) -> None:
    """Internal. Validates data dict against tooling_set schema. Raises ToolingSetError.

    Used by both validate_set_dict (load path) and save_set_file (write path) —
    same guarantee: no silent skip if schema missing, no uncaught exceptions.
    """
    try:
        import jsonschema
    except ImportError as e:
        raise ToolingSetError(
            "jsonschema is required for tooling_set validation. "
            "FreeCAD python has it preinstalled; for other environments install jsonschema."
        ) from e

    schema_path = Path(__file__).resolve().parent.parent.parent / "contracts" / "schemas" / "tooling_set.schema.json"
    if not schema_path.exists():
        raise ToolingSetError(f"ToolingSet schema not found: {schema_path}")

    try:
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)
        jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as e:
        raise ToolingSetError(f"ToolingSet schema validation failed: {e.message}") from e
    except Exception as e:
        raise ToolingSetError(f"Unexpected error during schema validation: {e}") from e


def validate_set_dict(data: dict) -> ToolingSet:
    """Validate data dict against schema + build ToolingSet.

    Public API for loaders (ToolingLibrary, CLI).
    Raises ToolingSetError on schema violation or invalid fields.
    """
    _validate_data_against_schema(data)
    return ToolingSet.from_dict(data)


def load_set_file(path: Path) -> ToolingSet:
    """Load ToolingSet from JSON file. Raises ToolingSetError on missing/malformed."""
    path = Path(path)
    if not path.exists() or not path.is_file():
        raise ToolingSetError(f"ToolingSet file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ToolingSetError(f"Malformed JSON in {path}: {e}") from e
    return validate_set_dict(data)


def save_set_file(path: Path, set_obj: ToolingSet) -> None:
    """Atomic write ToolingSet to JSON file. Creates parent dirs if needed."""
    import os
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    data = set_obj.to_dict()

    # Validate before write via same helper as load path — no silent skip if schema missing.
    _validate_data_against_schema(data)

    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, path)
    except Exception as e:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        raise ToolingSetError(f"Failed to save ToolingSet to {path}: {e}") from e
