"""Resumable copy-only migration. Existing files and source files never change.

Conflicting legacy files are retained under migration-conflicts/<source hash>/.
The journal records every verified source and destination digest, including
conflicts. A completion marker is written only after all copies verify.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile

from . import paths

DIRECTORIES = ("toollibrary", "config", "templates", "sequences", "saved_sequences")


def digest(path):
    result = sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".migration-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _verified_copy(source, target, expected):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".copy-", dir=target.parent)
    try:
        with source.open("rb") as reader, os.fdopen(fd, "wb") as writer:
            for block in iter(lambda: reader.read(1024 * 1024), b""):
                writer.write(block)
            writer.flush()
            os.fsync(writer.fileno())
        if digest(temporary) != expected or digest(source) != expected:
            raise OSError("Source changed or copy verification failed: " + str(source))
        # Atomic create without replacing a concurrent writer's destination.
        try:
            os.link(temporary, target)
        except FileExistsError:
            if digest(target) != expected:
                raise OSError("Destination changed during migration: " + str(target))
        if digest(target) != expected:
            raise OSError("Destination checksum mismatch: " + str(target))
    finally:
        Path(temporary).unlink(missing_ok=True)


def legacy_roots():
    """Only this checkout and known predecessor user storage; no broad scanning."""
    return (paths.REPOSITORY, paths.freecad_user_directory() / "SMBS",
            paths.freecad_user_directory() / "SMBS_BendSeq")


def migrate_user_data(sources=None, destination=None, defaults=None):
    """Verify a complete inventory on each call; interruption is safe to retry.

    Explicit sources are installation/data roots containing the supported
    directories. No imported CAD model is opened or rewritten by migration.
    """
    roots = tuple(Path(p).resolve() for p in (legacy_roots() if sources is None else sources))
    destination = Path(destination) if destination is not None else paths.user_data()
    destination = destination.resolve()
    defaults = paths.DEFAULT_TOOL_LIBRARY if defaults is None else Path(defaults)
    journal_path = destination / "migration-v1.json"
    # Preserve mappings when an old installation was removed after a successful run.
    previous = []
    if journal_path.is_file():
        existing = json.loads(journal_path.read_text(encoding="utf-8"))
        previous = existing.get("files", [])
    journal = {"schema": 1, "complete": False, "files": previous, "errors": []}
    _atomic_json(journal_path, journal)
    inventory = []
    for root in roots:
        if root == destination:
            continue
        for directory in DIRECTORIES:
            source_dir = root / directory
            if source_dir.is_dir():
                inventory.extend((source, Path(directory) / source.relative_to(source_dir), root)
                                 for source in sorted(source_dir.rglob("*")) if source.is_file())
    # Bundled defaults seed missing files only; user versions always win.
    if defaults.is_dir():
        inventory.extend((source, Path("toollibrary") / source.relative_to(defaults), defaults)
                         for source in sorted(defaults.rglob("*"))
                         if source.is_file() and source.name not in (".gitkeep", "README.md"))
    try:
        for source, relative, origin in inventory:
            expected = digest(source)
            target = destination / relative
            conflict = target.exists() and digest(target) != expected
            if conflict:
                identity = sha256(str(origin).encode("utf-8")).hexdigest()[:16]
                target = destination / "migration-conflicts" / identity / expected / relative
            _verified_copy(source, target, expected)
            record = {"source": str(source), "destination": str(target),
                      "sha256": expected, "conflict": conflict}
            if record not in journal["files"]:
                journal["files"].append(record)
            _atomic_json(journal_path, journal)
        for directory in DIRECTORIES:
            (destination / directory).mkdir(parents=True, exist_ok=True)
        for directory in ("punch", "die", "Backgauge", "Clamp", "machine_frame", "presses"):
            (destination / "toollibrary" / directory).mkdir(parents=True, exist_ok=True)
        journal["complete"] = True
        _atomic_json(journal_path, journal)
    except Exception as exc:
        journal["errors"].append(str(exc))
        _atomic_json(journal_path, journal)
        raise
    return journal


def relocated_path(value):
    """Resolve old saved absolute library references without changing documents.

    Prefer the exact migrated source (including conflict copies); keep external
    paths as-is. Historic collision findings are not recomputed here.
    """
    original = Path(value)
    try:
        journal = json.loads((paths.user_data() / "migration-v1.json").read_text(encoding="utf-8"))
        for record in journal["files"]:
            if Path(record["source"]) == original:
                candidate = Path(record["destination"])
                if candidate.is_file() and digest(candidate) == record["sha256"]:
                    return str(candidate)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return str(value)


def previous_paths(value):
    """Verified historical aliases for a relocated file, for session matching."""
    result = []
    try:
        journal = json.loads((paths.user_data() / "migration-v1.json").read_text(encoding="utf-8"))
        for record in journal["files"]:
            candidate = Path(record["destination"])
            if (candidate == Path(value) and candidate.is_file()
                    and digest(candidate) == record["sha256"]):
                result.append(record["source"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return tuple(result)
