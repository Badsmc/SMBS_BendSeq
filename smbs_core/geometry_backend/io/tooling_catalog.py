"""
tooling_catalog.py - Directory scan + index for tooling library (Phase B, S.22b-kod).

Scan <library_root>/punch/*.FCStd and <library_root>/die/*.FCStd.
For each, look for <stem>.tooling.json sidecar.
Build ToolingCatalog with structured ToolDescriptor list.

Pure Python + JSON. Does NOT parse FCStd contents. Does NOT touch role resolution (Q1=D) — 
that is S.22c. Does NOT manage add/delete — that is S.22d.
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

KIND_PUNCH = "punch"
KIND_DIE = "die"
VALID_KINDS = (KIND_PUNCH, KIND_DIE)


class ToolingCatalogError(Exception):
    """Raised on malformed library structure, sidecar, or duplicate tool_id."""
    pass


@dataclass(frozen=True)
class ToolDescriptor:
    tool_id: str                    # stem of FCStd filename (e.g. "puancon")
    kind: str                       # "punch" | "die"
    fcstd_path: Path                # absolute resolved Path
    sidecar_path: Optional[Path]    # None if no .tooling.json present
    has_sidecar: bool
    metadata: dict                  # from sidecar.metadata; {} if no sidecar


@dataclass(frozen=True)
class ToolingCatalog:
    library_root: Path
    punches: tuple[ToolDescriptor, ...]
    dies: tuple[ToolDescriptor, ...]

    def list_tools(self, kind: str) -> tuple[ToolDescriptor, ...]:
        """Return punches or dies. Raise ToolingCatalogError on unknown kind."""
        if kind == KIND_PUNCH:
            return self.punches
        elif kind == KIND_DIE:
            return self.dies
        raise ToolingCatalogError(f"Unknown tool kind: '{kind}'. Expected one of {VALID_KINDS}")

    def get_tool(self, tool_id: str) -> ToolDescriptor:
        """
        Return descriptor by tool_id (across both kinds).
        Raise ToolingCatalogError if not found or ambiguous (same id in both kinds).
        """
        matched_punches = [t for t in self.punches if t.tool_id == tool_id]
        matched_dies = [t for t in self.dies if t.tool_id == tool_id]

        total_matches = len(matched_punches) + len(matched_dies)
        if total_matches == 0:
            raise ToolingCatalogError(f"tool_id '{tool_id}' not found in catalog at {self.library_root}")
        if total_matches > 1:
            raise ToolingCatalogError(f"Ambiguous tool_id '{tool_id}': found as both punch and die in {self.library_root}")

        return matched_punches[0] if matched_punches else matched_dies[0]

    def all_tools(self) -> tuple[ToolDescriptor, ...]:
        """Concatenation: punches + dies, sorted by (kind, tool_id)."""
        combined = list(self.punches) + list(self.dies)
        combined.sort(key=lambda t: (t.kind, t.tool_id))
        return tuple(combined)


def scan_library(library_root: Path) -> ToolingCatalog:
    """
    Scan the tooling library directory.

    Structure expected:
      <library_root>/
        punch/
          foo.FCStd
          foo.tooling.json  (optional)
        die/
          bar.FCStd
          bar.tooling.json  (optional)

    Missing <library_root> -> ToolingCatalogError.
    Missing punch/ or die/ subdirectory -> ToolingCatalogError (explicit, not silent).
    Empty punch/ or die/ subdirectory -> OK (empty tuple).
    *.FCStd without sidecar -> ToolDescriptor(has_sidecar=False, metadata={}).
    Malformed sidecar JSON -> ToolingCatalogError with file path.
    Duplicate tool_id within same kind -> ToolingCatalogError.
    Same tool_id in punch AND die -> valid (kind disambiguates); get_tool() must raise.
    """
    if not isinstance(library_root, Path):
        library_root = Path(library_root)

    resolved_root = library_root.resolve()
    if not resolved_root.exists() or not resolved_root.is_dir():
        raise ToolingCatalogError(f"Tooling library root does not exist or is not a directory: {resolved_root}")

    punch_dir = resolved_root / KIND_PUNCH
    die_dir = resolved_root / KIND_DIE

    if not punch_dir.exists() or not punch_dir.is_dir():
        raise ToolingCatalogError(f"Missing required punch subdirectory: {punch_dir}")
    if not die_dir.exists() or not die_dir.is_dir():
        raise ToolingCatalogError(f"Missing required die subdirectory: {die_dir}")

    punches = _scan_kind_directory(punch_dir, KIND_PUNCH)
    dies = _scan_kind_directory(die_dir, KIND_DIE)

    return ToolingCatalog(
        library_root=resolved_root,
        punches=punches,
        dies=dies
    )


def _scan_kind_directory(kind_dir: Path, expected_kind: str) -> tuple[ToolDescriptor, ...]:
    descriptors: list[ToolDescriptor] = []
    seen_ids: set[str] = set()

    for item in sorted(kind_dir.iterdir()):
        if item.is_dir():
            continue

        if item.suffix.lower() == ".fcstd":
            tool_id = item.stem
            if tool_id in seen_ids:
                raise ToolingCatalogError(f"Duplicate tool_id '{tool_id}' in {expected_kind}/: {item}")
            seen_ids.add(tool_id)

            sidecar_path = kind_dir / f"{tool_id}.tooling.json"
            has_sidecar = False
            metadata: dict = {}

            if sidecar_path.exists() and sidecar_path.is_file():
                has_sidecar = True
                try:
                    with open(sidecar_path, "r", encoding="utf-8") as f:
                        sidecar_data = json.load(f)
                except Exception as err:
                    raise ToolingCatalogError(f"Malformed sidecar JSON at {sidecar_path}: {err}") from err

                sidecar_kind = sidecar_data.get("kind")
                if sidecar_kind != expected_kind:
                    raise ToolingCatalogError(
                        f"Sidecar kind mismatch at {sidecar_path}: sidecar specifies kind='{sidecar_kind}', "
                        f"but file is in '{expected_kind}/' directory."
                    )

                raw_meta = sidecar_data.get("metadata")
                if isinstance(raw_meta, dict):
                    metadata = raw_meta

            descriptors.append(
                ToolDescriptor(
                    tool_id=tool_id,
                    kind=expected_kind,
                    fcstd_path=item.resolve(),
                    sidecar_path=sidecar_path.resolve() if has_sidecar else None,
                    has_sidecar=has_sidecar,
                    metadata=metadata
                )
            )
        else:
            sys.stderr.write(f"ToolingCatalog: skipping non-FCStd file: {item}\n")

    return tuple(descriptors)
