"""
tooling_library.py - Persistent tooling library manager (Phase B, S.22e-kod).

Wraps ToolingCatalog with add/remove operations.
Requires .tooling.json sidecar for every added tool (no fabricated datums).
Headless. No UI.

Library layout:
  <library_root>/
    punch/
      <tool_id>.FCStd
      <tool_id>.tooling.json
    die/
      <tool_id>.FCStd
      <tool_id>.tooling.json
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from smbs_core.geometry_backend.io.tooling_catalog import (
    ToolingCatalog,
    ToolDescriptor,
    ToolingCatalogError,
    scan_library,
    KIND_PUNCH,
    KIND_DIE,
    VALID_KINDS,
)


class ToolingLibraryError(Exception):
    """Raised on add/remove failures: missing sidecar, kind conflict, path traversal, etc."""
    pass


class ToolingLibrary:
    def __init__(self, library_root: Path | str) -> None:
        """
        library_root must exist and contain punch/ + die/ subdirs.
        Raises ToolingLibraryError if structure invalid.
        """
        if not isinstance(library_root, Path):
            library_root = Path(library_root)

        self.library_root = library_root.resolve()
        if not self.library_root.exists() or not self.library_root.is_dir():
            raise ToolingLibraryError(f"Tooling library root does not exist or is not a directory: {self.library_root}")

        punch_dir = self.library_root / KIND_PUNCH
        die_dir = self.library_root / KIND_DIE

        if not punch_dir.exists() or not punch_dir.is_dir():
            raise ToolingLibraryError(f"Missing required punch subdirectory: {punch_dir}")
        if not die_dir.exists() or not die_dir.is_dir():
            raise ToolingLibraryError(f"Missing required die subdirectory: {die_dir}")

    def add_tool(self, source_fcstd: Path | str, kind: Optional[str] = None) -> ToolDescriptor:
        """
        Copy source FCStd + sidecar to library.

        - Requires <stem>.tooling.json next to source_fcstd (honest: no fabricated datums).
          Missing sidecar -> ToolingLibraryError with actionable message.
        - If kind is None: resolve via Q1=D (property -> name -> sidecar).
          If unresolved -> ToolingLibraryError.
        - If kind is specified: must be 'punch' or 'die'. If sidecar/resolution differs -> ToolingLibraryError.
        - If destination tool_id already exists in target kind -> ToolingLibraryError (no overwrite).
        - Copies both FCStd and sidecar.
        - Returns ToolDescriptor for the newly added tool.
        """
        if not isinstance(source_fcstd, Path):
            source_fcstd = Path(source_fcstd)

        source_fcstd = source_fcstd.resolve()
        if not source_fcstd.exists() or not source_fcstd.is_file():
            raise ToolingLibraryError(f"Source FCStd file does not exist or is not a file: {source_fcstd}")

        tool_id = source_fcstd.stem
        # Path traversal / validity check
        if Path(tool_id).name != tool_id or "/" in tool_id or "\\" in tool_id or ".." in tool_id:
            raise ToolingLibraryError(f"Invalid tool_id '{tool_id}': contains path separators or traversal characters")

        if kind is not None and kind not in VALID_KINDS:
            raise ToolingLibraryError(f"Invalid kind '{kind}'. Expected one of: {VALID_KINDS}")

        # Sidecar check
        sidecar_path = source_fcstd.parent / f"{tool_id}.tooling.json"
        if not sidecar_path.exists() or not sidecar_path.is_file():
            raise ToolingLibraryError(
                f"Tool '{tool_id}' has no sidecar at {sidecar_path}. "
                f"Create one manually with datums (mounting_plane, working_axis, ...) before adding. "
                f"See docs/ROADMAP.md Phase B."
            )

        # Q1=D resolution
        from smbs_core.geometry_backend.backends.fcstd_loader import resolve_role
        resolved_kind_enum, resolved_source, diag = resolve_role(source_fcstd)

        if resolved_kind_enum is None:
            raise ToolingLibraryError(f"Cannot resolve role for '{tool_id}': {diag}")

        resolved_kind_str = resolved_kind_enum.value
        if resolved_kind_str not in VALID_KINDS:
            raise ToolingLibraryError(
                f"Resolved role '{resolved_kind_str}' for tool '{tool_id}' is not a valid library kind ({VALID_KINDS})"
            )

        if kind is not None and kind != resolved_kind_str:
            raise ToolingLibraryError(
                f"Kind conflict: caller specified '{kind}' but resolution gives '{resolved_kind_str}' (via {resolved_source})"
            )

        target_kind = kind if kind is not None else resolved_kind_str
        target_dir = self.library_root / target_kind

        # Duplicate check in destination
        dest_fcstd = target_dir / source_fcstd.name
        dest_sidecar = target_dir / sidecar_path.name

        if dest_fcstd.exists():
            raise ToolingLibraryError(
                f"Tool '{tool_id}' already exists in {target_kind}/. Remove it first or use a different tool_id."
            )

        # Copy files
        try:
            shutil.copy2(source_fcstd, dest_fcstd)
            shutil.copy2(sidecar_path, dest_sidecar)
        except Exception as err:
            # Clean up partial copy if needed
            if dest_fcstd.exists():
                dest_fcstd.unlink()
            if dest_sidecar.exists():
                dest_sidecar.unlink()
            raise ToolingLibraryError(f"Failed to copy tool files to {target_dir}: {err}") from err

        catalog = self.list_tools()
        return catalog.get_tool(tool_id)

    def remove_tool(self, tool_id: str, kind: str) -> None:
        """
        Remove <library>/<kind>/<tool_id>.FCStd and optional .tooling.json.
        Raises ToolingLibraryError if tool not found or kind invalid.
        """
        if kind not in VALID_KINDS:
            raise ToolingLibraryError(f"Invalid kind '{kind}'. Expected one of: {VALID_KINDS}")

        if Path(tool_id).name != tool_id or "/" in tool_id or "\\" in tool_id or ".." in tool_id:
            raise ToolingLibraryError(f"Invalid tool_id '{tool_id}': contains path separators or traversal characters")

        target_dir = self.library_root / kind
        fcstd_file = target_dir / f"{tool_id}.FCStd"
        sidecar_file = target_dir / f"{tool_id}.tooling.json"

        if not fcstd_file.exists():
            raise ToolingLibraryError(f"Tool '{tool_id}' not found in {kind}/ at {self.library_root}")

        try:
            fcstd_file.unlink()
        except Exception as err:
            raise ToolingLibraryError(f"Failed to delete {fcstd_file}: {err}") from err

        if sidecar_file.exists():
            try:
                sidecar_file.unlink()
            except Exception as err:
                raise ToolingLibraryError(f"Failed to delete sidecar {sidecar_file}: {err}") from err

    def list_tools(self) -> ToolingCatalog:
        """Return ToolingCatalog by delegating to scan_library(self.library_root)."""
        return scan_library(self.library_root)

    def get_tool(self, tool_id: str) -> ToolDescriptor:
        """Return ToolDescriptor by delegating to catalog.get_tool (raises on not found or ambiguous)."""
        return self.list_tools().get_tool(tool_id)

    def has_tool(self, tool_id: str) -> bool:
        """True if tool_id exists in either kind. Ambiguous (in both) -> True."""
        try:
            self.get_tool(tool_id)
            return True
        except ToolingCatalogError as err:
            err_msg = str(err)
            if "Ambiguous tool_id" in err_msg:
                return True
            return False
