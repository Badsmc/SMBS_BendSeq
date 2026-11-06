"""Install the original FCStd demo fixtures from the distributed ZIP archive.

Usage in FreeCAD Python Console (Windows/macOS/Linux):
    from tools.install_demo_models import install_demo_archive
    install_demo_archive()

The archive is user-provided, not downloaded from a network source. File content
is verified against version-controlled demo/MANIFEST.json before being copied.
No original FCStd file is modified or overwritten with different geometry.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import zipfile

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "demo" / "MANIFEST.json"
DESTINATION = REPO_ROOT / "demo" / "parts"
ARCHIVE_BASENAME = "SMBS_demo_FCStd_17_files.zip"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resolve_archive(archive_path: str | Path | None) -> Path:
    if archive_path is not None:
        candidate = Path(archive_path).expanduser()
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(f"Demo archive does not exist: {candidate}")
    for candidate in (Path.home() / "Downloads" / ARCHIVE_BASENAME,
                      REPO_ROOT / ARCHIVE_BASENAME):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"Download {ARCHIVE_BASENAME} to your Downloads folder or supply its path."
    )


def install_demo_archive(archive_path: str | Path | None = None) -> dict:
    """Verify archive and install all referenced original FCStd files.

    Distinct existing files are never overwritten. Validates all archive members
    before writing any files. This does not claim geometry or physical validation.
    """
    archive = _resolve_archive(archive_path)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    required = manifest["files"]
    if len(required) != 17:
        raise ValueError(f"Unexpected demo manifest: expected 17 files, got {len(required)}")

    verified = {}
    with zipfile.ZipFile(archive) as z:
        for entry in required:
            name = entry["name"]
            if (Path(name).name != name or "/" in name or "\\" in name or
                    not name.lower().endswith(".fcstd")):
                raise ValueError(f"Invalid FCStd name in manifest: {name!r}")
            member = f"demo/parts/{name}"
            data = z.read(member)
            if len(data) != int(entry["size_bytes"]):
                raise ValueError(f"FCStd size mismatch: {name}")
            if _sha256(data) != entry["sha256"]:
                raise ValueError(f"FCStd SHA-256 mismatch: {name}")
            if not zipfile.is_zipfile(__import__("io").BytesIO(data)):
                raise ValueError(f"Invalid FCStd document container: {name}")
            verified[name] = data

    # Reject any conflict up front, before copying anything.
    for name, data in verified.items():
        target = DESTINATION / name
        if target.exists() and (not target.is_file() or _sha256(target.read_bytes()) != _sha256(data)):
            raise FileExistsError(
                f"Different demo model already exists: {target}. "
                "Back it up or remove it manually before installing."
            )

    DESTINATION.mkdir(parents=True, exist_ok=True)
    installed = []
    for name, data in verified.items():
        target = DESTINATION / name
        if target.is_file():
            continue
        # Write temporary file in the destination filesystem, then rename.
        handle, tmp = tempfile.mkstemp(prefix=".smbs-demo-", dir=DESTINATION)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
            Path(tmp).replace(target)
        finally:
            Path(tmp).unlink(missing_ok=True)
        installed.append(name)
    print(f"SMBS demo: {len(verified)} verified FCStd, {len(installed)} installed to {DESTINATION}")
    return {"verified": len(verified), "installed": len(installed), "destination": str(DESTINATION)}
