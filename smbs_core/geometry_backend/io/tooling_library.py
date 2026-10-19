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
from smbs_core.geometry_backend.io.clamp_validator import (
    ClampConfig,
    ClampValidatorError,
    load_clamp_config,
    save_clamp_config,
)
from smbs_core.geometry_backend.io.tooling_set import (
    ToolingSet,
    ToolingSetError,
    load_set_file,
    save_set_file,
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

    def get_clamp_config(self) -> ClampConfig:
        """
        Load <library_root>/config.json via load_clamp_config.

        If config.json exists -> return loaded config (validated by schema).
        If config.json missing -> return DEFAULT config with empty positions
            (ClampConfig.from_defaults(positions_mm=())).

        Does NOT auto-create config.json — use ensure_clamp_config() for that.

        Raises ClampValidatorError on malformed JSON, invalid schema, or invalid fields.
        """
        return load_clamp_config(self.library_root)

    def set_clamp_config(self, config: ClampConfig) -> None:
        """
        Save config to <library_root>/config.json via save_clamp_config.

        Atomic write (already handled inside save_clamp_config).
        Overwrites existing file.

        Raises ClampValidatorError on validation failure or write error.
        """
        save_clamp_config(self.library_root, config)

    def ensure_clamp_config(self) -> ClampConfig:
        """
        Ensure <library_root>/config.json exists.

        If exists -> return loaded config (same as get_clamp_config()).
        If missing -> create DEFAULT config (empty positions) via set_clamp_config,
                     then return it.

        Idempotent: repeated calls do not overwrite existing config.

        Raises ClampValidatorError on malformed existing config or write error.
        """
        config_file = self.library_root / "config.json"
        if config_file.exists():
            return self.get_clamp_config()
        default = ClampConfig.from_defaults(positions_mm=())
        self.set_clamp_config(default)
        return default

    def _sets_dir(self) -> Path:
        """Return <library_root>/sets/. Does NOT create the directory."""
        return self.library_root / "sets"

    def _set_path(self, set_id: str) -> Path:
        """Return <library_root>/sets/<set_id>.json. Path-traversal-safe check."""
        if not set_id or not isinstance(set_id, str):
            raise ToolingLibraryError(f"Invalid set_id: {set_id!r}")
        if Path(set_id).name != set_id or "/" in set_id or "\\" in set_id or ".." in set_id:
            raise ToolingLibraryError(
                f"Invalid set_id '{set_id}': contains path separators or traversal characters"
            )
        return self._sets_dir() / f"{set_id}.json"

    def add_set(self, set_obj: ToolingSet, check_refs: bool = True) -> None:
        """
        Save ToolingSet to <library>/sets/<set_id>.json.

        - If set already exists -> ToolingLibraryError (no overwrite; use remove_set + add_set to update).
        - If check_refs=True: verify punch_id and die_id exist in library via has_tool().
          Missing reference -> ToolingLibraryError.
        - Creates sets/ subdirectory lazily.
        - Atomic write via save_set_file.

        Raises ToolingLibraryError on validation, duplicate, missing refs, or write error.
        """
        set_path = self._set_path(set_obj.set_id)

        if set_path.exists():
            raise ToolingLibraryError(
                f"ToolingSet '{set_obj.set_id}' already exists. Remove it first or use a different set_id."
            )

        if check_refs:
            try:
                punch_desc = self.get_tool(set_obj.punch_id)
            except ToolingCatalogError as e:
                raise ToolingLibraryError(
                    f"ToolingSet '{set_obj.set_id}' references punch_id '{set_obj.punch_id}' "
                    f"which is not present in library at {self.library_root}: {e}"
                ) from e
            if punch_desc.kind != "punch":
                raise ToolingLibraryError(
                    f"ToolingSet '{set_obj.set_id}' punch_id '{set_obj.punch_id}' "
                    f"resolved to kind '{punch_desc.kind}', expected 'punch'"
                )

            try:
                die_desc = self.get_tool(set_obj.die_id)
            except ToolingCatalogError as e:
                raise ToolingLibraryError(
                    f"ToolingSet '{set_obj.set_id}' references die_id '{set_obj.die_id}' "
                    f"which is not present in library at {self.library_root}: {e}"
                ) from e
            if die_desc.kind != "die":
                raise ToolingLibraryError(
                    f"ToolingSet '{set_obj.set_id}' die_id '{set_obj.die_id}' "
                    f"resolved to kind '{die_desc.kind}', expected 'die'"
                )

        try:
            save_set_file(set_path, set_obj)
        except ToolingSetError as e:
            raise ToolingLibraryError(f"Failed to save ToolingSet '{set_obj.set_id}': {e}") from e

    def get_set(self, set_id: str) -> ToolingSet:
        """
        Load ToolingSet by set_id. Raises ToolingLibraryError if not found or malformed.
        """
        set_path = self._set_path(set_id)
        if not set_path.exists():
            raise ToolingLibraryError(f"ToolingSet '{set_id}' not found at {set_path}")
        try:
            return load_set_file(set_path)
        except ToolingSetError as e:
            raise ToolingLibraryError(f"Failed to load ToolingSet '{set_id}': {e}") from e

    def list_sets(self) -> tuple[str, ...]:
        """
        Return sorted tuple of set_ids present in <library>/sets/.
        Missing sets/ directory -> empty tuple (not an error).
        """
        sets_dir = self._sets_dir()
        if not sets_dir.exists() or not sets_dir.is_dir():
            return ()
        ids = []
        for item in sorted(sets_dir.iterdir()):
            if item.is_file() and item.suffix == ".json" and not item.name.endswith(".json.tmp"):
                ids.append(item.stem)
        return tuple(sorted(ids))

    def has_set(self, set_id: str) -> bool:
        """True if <library>/sets/<set_id>.json exists."""
        try:
            return self._set_path(set_id).exists()
        except ToolingLibraryError:
            return False

    def remove_set(self, set_id: str) -> None:
        """
        Remove <library>/sets/<set_id>.json.
        Raises ToolingLibraryError if set_id invalid or set not found.
        """
        set_path = self._set_path(set_id)
        if not set_path.exists():
            raise ToolingLibraryError(f"ToolingSet '{set_id}' not found at {set_path}")
        try:
            set_path.unlink()
        except Exception as e:
            raise ToolingLibraryError(f"Failed to delete ToolingSet '{set_id}': {e}") from e


