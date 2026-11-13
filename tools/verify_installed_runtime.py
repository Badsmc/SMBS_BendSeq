"""Read-only check that FreeCAD's installed SMBS code equals THIS checkout.

Git-based branch detection is unreliable for ZIP installs. Compare the actual
important Python source bytes, not directory names, and never modify Mod.
Usage (system Python is sufficient; FreeCAD need not be running):
    python -m tools.verify_installed_runtime --installed "C:\\...\\Mod\\SMBS_BendSeq-2D"
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from smbs_core.kinematics2d.runtime_build_identity import (
    NATIVE_SOURCE_PATHS, runtime_source_fingerprint,
)


def compare_source_trees(reference: str | Path,
                         installed: str | Path) -> dict[str, Any]:
    """Detect mismatched installed bytes, partial installs and mixed modules."""
    reference_root = Path(reference).expanduser().resolve()
    installed_root = Path(installed).expanduser().resolve()
    expected = runtime_source_fingerprint(reference_root)
    actual = runtime_source_fingerprint(installed_root)
    mismatched = [
        rel for rel in NATIVE_SOURCE_PATHS
        if expected["installed_source_sha256"][rel]
        != actual["installed_source_sha256"][rel]
    ]
    comparable = (
        expected["required_source_files_present"]
        and actual["required_source_files_present"]
    )
    return {
        "schema_version": "smbs.installed_source_comparison.v1",
        "reference_root": str(reference_root),
        "installed_root": str(installed_root),
        "match": bool(comparable and not mismatched),
        "reference_sha256": expected["composite_source_sha256"],
        "installed_sha256": actual["composite_source_sha256"],
        "changed_or_missing_files": mismatched,
        "reference_missing_files": expected["missing_required_files"],
        "installed_missing_files": actual["missing_required_files"],
        "warning": (
            "A matched source tree is code identity only, NOT physical "
            "FreeCAD native test acceptance or production safety."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify actual FreeCAD Mod code equals checked-out SMBS"
    )
    parser.add_argument("--installed", required=True,
                        help="Full path to actual FreeCAD Mod/SMBS directory")
    args = parser.parse_args(argv)
    here = Path(__file__).resolve().parents[1]
    report = compare_source_trees(here, args.installed)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
