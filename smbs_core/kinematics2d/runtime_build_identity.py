"""Deterministic SMBS runtime identity for ZIP-installed FreeCAD workbenches.

Git metadata is commonly absent from FreeCAD Mod ZIP installs. Never pretend
that GitHub HEAD was installed; fingerprint the actual bytes imported by
the running program. This module imports no FreeCAD or Qt and creates no files.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path

RUNTIME_FINGERPRINT_SCHEMA = "smbs.runtime_source_sha256.v1"
NATIVE_SOURCE_PATHS = (
    "freecad_smbs/commands/pressbrake_2d.py",
    "smbs_core/kinematics2d/manual_physical_validation.py",
    "smbs_core/kinematics2d/native_collision_3d.py",
    "smbs_core/kinematics2d/motion_trajectory_3d.py",
    "smbs_core/kinematics2d/native_bend_solid_3d.py",
    "smbs_core/kinematics2d/native_sequence_geometry_cache.py",
)


NATIVE_MODULE_NAMES = (
    "smbs_core.kinematics2d.manual_physical_validation",
    "smbs_core.kinematics2d.physical_validation_25d",
    "smbs_core.kinematics2d.motion_trajectory_3d",
    "smbs_core.kinematics2d.native_collision_3d",
    "smbs_core.kinematics2d.native_bend_solid_3d",
)


def loaded_native_module_sources() -> dict[str, dict | None]:
    """Report which actual modules Python imported, even with duplicate Mods.

    Inspects sys.modules without importing or modifying anything. Read
    actual loaded files instead of assuming they came from the GUI's Mod.
    """
    import sys

    origins = {}
    for name in NATIVE_MODULE_NAMES:
        module = sys.modules.get(name)
        module_file = getattr(module, "__file__", None)
        if not module_file:
            origins[name] = None
            continue
        source = Path(module_file).resolve()
        try:
            digest = sha256(source.read_bytes()).hexdigest()
            origins[name] = {"path": str(source), "sha256": digest}
        except OSError as exc:
            origins[name] = {
                "path": str(source), "sha256": None,
                "read_error_type": type(exc).__name__,
            }
    return origins


def runtime_source_fingerprint(repo_root: str | Path) -> dict:
    """Hash installed files without relying on Git checkout metadata.

    Incomplete/modified ZIP installations remain explicitly unverified.
    The composite digest is stable across Windows/macOS path separators.
    No file content, user path, or source CAD is stored in the journal.
    """
    root = Path(repo_root).resolve()
    files: dict[str, str | None] = {}
    missing = []
    errors = []
    for rel in NATIVE_SOURCE_PATHS:
        candidate = root / rel
        try:
            digest = sha256(candidate.read_bytes()).hexdigest()
            files[rel] = digest
        except FileNotFoundError:
            files[rel] = None
            missing.append(rel)
        except OSError as exc:
            files[rel] = None
            errors.append({
                "file": rel,
                "error_type": type(exc).__name__,
            })
    composite = None
    if not missing and not errors:
        canonical = "\n".join(
            "{}={}".format(name, files[name])
            for name in NATIVE_SOURCE_PATHS
        )
        composite = sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "schema_version": RUNTIME_FINGERPRINT_SCHEMA,
        "algorithm": "sha256",
        "installed_source_sha256": files,
        "composite_source_sha256": composite,
        "required_source_files_present": not missing and not errors,
        "missing_required_files": missing,
        "read_errors": errors,
        "meaning": (
            "IDENTITY_OF_INSTALLED_BYTES_ONLY; NOT_A_SAFETY_CERTIFICATE;"
            "compare_against_target_checkout_before_BENCHMARKING"
        ),
    }
