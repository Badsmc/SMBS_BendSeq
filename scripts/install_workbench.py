#!/usr/bin/env python3
"""
install_workbench.py - Cross-platform installer for FreeCAD SMBS Workbench.

Automates workbench registration in FreeCAD user Mod directory across:
- Windows (Directory Junction - works without Developer Mode, or copy fallback)
- macOS (~/Library/Application Support/FreeCAD/Mod)
- Linux (~/.local/share/FreeCAD/Mod or ~/.FreeCAD/Mod)

Guarantees:
1. Links / junctions / copies freecad_smbs into user FreeCAD Mod directory as 'SMBS' (and 'freecad_smbs').
2. Guarantees 'import smbs_core' succeeds in FreeCAD without manual PYTHONPATH.
3. Guarantees 'toollibrary/' is automatically discovered without manual path entry.
4. Supports Windows unprivileged users via Directory Junctions (mklink /J or _winapi).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple


REPO_ROOT = Path(__file__).resolve().parent.parent
WB_SOURCE = REPO_ROOT / "freecad_smbs"
TOOL_LIBRARY = REPO_ROOT / "toollibrary"


def get_default_mod_dirs() -> List[Path]:
    """Returns candidate user Mod directories in priority order."""
    candidates: List[Path] = []

    # 1. Environment variable override
    env_mod = os.environ.get("FREECAD_USER_MOD")
    if env_mod:
        candidates.append(Path(env_mod).expanduser().resolve())

    home = Path.home()

    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            ap_path = Path(appdata)
            candidates.extend([
                ap_path / "FreeCAD" / "Mod",
                ap_path / "FreeCAD" / "v1-1" / "Mod",
                ap_path / "FreeCAD" / "v1-0" / "Mod",
            ])
        userprofile = os.environ.get("USERPROFILE")
        if userprofile:
            candidates.append(Path(userprofile) / "AppData" / "Roaming" / "FreeCAD" / "Mod")

    elif sys.platform == "darwin":
        app_support = home / "Library" / "Application Support" / "FreeCAD"
        candidates.extend([
            app_support / "Mod",
            app_support / "v1-1" / "Mod",
            app_support / "v1-0" / "Mod",
        ])

    else:
        # Linux / FreeBSD / other POSIX
        xdg_data = os.environ.get("XDG_DATA_HOME")
        if xdg_data:
            candidates.append(Path(xdg_data) / "FreeCAD" / "Mod")
        candidates.extend([
            home / ".local" / "share" / "FreeCAD" / "Mod",
            home / ".FreeCAD" / "Mod",
        ])

    return candidates


def find_or_create_mod_dir(preferred_dir: Optional[str] = None) -> Path:
    """Finds existing FreeCAD Mod directory or creates the canonical default one."""
    if preferred_dir:
        mod_path = Path(preferred_dir).expanduser().resolve()
        mod_path.mkdir(parents=True, exist_ok=True)
        return mod_path

    candidates = get_default_mod_dirs()
    # Check if any candidate already exists
    for cand in candidates:
        if cand.is_dir():
            return cand

    # If none exists yet, pick the canonical primary candidate and create it
    primary = candidates[0] if candidates else (Path.home() / ".local" / "share" / "FreeCAD" / "Mod")
    primary.mkdir(parents=True, exist_ok=True)
    return primary


def create_junction(src: Path, dst: Path) -> bool:
    """Creates a Windows directory junction (requires no Developer Mode and no admin rights)."""
    if sys.platform != "win32":
        return False

    # Try native Windows API in Python 3.8+
    try:
        import _winapi
        _winapi.CreateJunction(str(src), str(dst))
        return True
    except Exception:
        pass

    # Fallback to cmd /c mklink /J
    try:
        res = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(dst), str(src)],
            capture_output=True,
            text=True,
            check=False
        )
        return res.returncode == 0
    except Exception:
        return False


def link_or_copy(src: Path, dst: Path, mode: str = "auto") -> str:
    """
    Installs src directory to dst path using requested mode or best available fallback.
    Returns method used: 'symlink', 'junction', or 'copy'.
    """
    if dst.is_symlink() or dst.is_file():
        dst.unlink()
    elif dst.is_dir():
        # Could be an existing junction on Windows (check if junction) or directory
        if sys.platform == "win32":
            try:
                # Remove junction via rmdir
                subprocess.run(["cmd", "/c", "rmdir", str(dst)], capture_output=True, check=False)
            except Exception:
                pass
        if dst.exists():
            shutil.rmtree(str(dst))

    # 1. Try Windows junction
    if sys.platform == "win32" and mode in ("auto", "junction"):
        if create_junction(src, dst):
            return "junction"
        if mode == "junction":
            raise RuntimeError(f"Failed to create directory junction from {src} to {dst}")

    # 2. Try symlink
    if mode in ("auto", "symlink"):
        try:
            os.symlink(str(src), str(dst), target_is_directory=True)
            return "symlink"
        except OSError:
            if mode == "symlink":
                raise

    # 3. Fallback to copy
    shutil.copytree(str(src), str(dst))
    return "copy"


def write_marker_files(target_dir: Path) -> None:
    """Writes .smbs_repo_path marker into workbench source and target for zero-config path discovery."""
    repo_str = str(REPO_ROOT)
    # Write into source
    try:
        (WB_SOURCE / ".smbs_repo_path").write_text(repo_str, encoding="utf-8")
    except Exception:
        pass
    # Write into target (especially vital if copied)
    try:
        if target_dir.exists():
            (target_dir / ".smbs_repo_path").write_text(repo_str, encoding="utf-8")
    except Exception:
        pass


def verify_installation(mod_dir: Path, target_names: List[str]) -> Tuple[bool, List[str]]:
    """Validates that workbench files, smbs_core and toollibrary are resolved correctly."""
    report: List[str] = []
    all_ok = True

    for name in target_names:
        target = mod_dir / name
        if not target.exists():
            report.append(f"❌ Target '{target}' does not exist.")
            all_ok = False
            continue

        init_gui = target / "InitGui.py"
        init_py = target / "Init.py"
        if not init_gui.exists() or not init_py.exists():
            report.append(f"❌ Missing InitGui.py / Init.py in '{target}'.")
            all_ok = False
            continue

        report.append(f"✅ Workbench '{name}' installed in: {target}")

    # Test import smbs_core and toollibrary discovery
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    try:
        import smbs_core  # type: ignore[import-not-found]
        report.append(f"✅ smbs_core imported successfully: {smbs_core.__file__}")
    except Exception as e:
        report.append(f"❌ Failed to import smbs_core: {e}")
        all_ok = False

    try:
        from freecad_smbs.resources.tooling_paths import TOOL_LIBRARY_ROOT, is_tool_available
        if TOOL_LIBRARY_ROOT.is_dir():
            report.append(f"✅ toollibrary located: {TOOL_LIBRARY_ROOT}")
            report.append(f"   • Punch available: {is_tool_available('punch')}")
            report.append(f"   • Die available: {is_tool_available('die')}")
            report.append(f"   • Machine frame available: {is_tool_available('machine_frame')}")
        else:
            report.append(f"❌ toollibrary directory missing at: {TOOL_LIBRARY_ROOT}")
            all_ok = False
    except Exception as e:
        report.append(f"❌ toollibrary discovery failed: {e}")
        all_ok = False

    return all_ok, report


def install(mode: str = "auto", preferred_dir: Optional[str] = None, names: Optional[List[str]] = None) -> int:
    """Executes installation into FreeCAD user Mod directory."""
    if not WB_SOURCE.is_dir():
        print(f"Error: Workbench source directory not found: {WB_SOURCE}", file=sys.stderr)
        return 1

    target_names = names or ["SMBS", "freecad_smbs"]
    mod_dir = find_or_create_mod_dir(preferred_dir)
    print(f"Installing SMBS Workbench...")
    print(f"Source repository: {REPO_ROOT}")
    print(f"Workbench source:  {WB_SOURCE}")
    print(f"FreeCAD user Mod:  {mod_dir}")
    print(f"Installation mode: {mode}")

    methods_used = {}
    for name in target_names:
        dst = mod_dir / name
        method = link_or_copy(WB_SOURCE, dst, mode=mode)
        methods_used[name] = method
        write_marker_files(dst)
        print(f" -> Installed '{name}' via [{method.upper()}] to: {dst}")

    # Ensure marker in source as well
    write_marker_files(WB_SOURCE)

    # Verify
    ok, report_lines = verify_installation(mod_dir, target_names)
    print("\n--- Installation Verification ---")
    for line in report_lines:
        print(line)

    if ok:
        print("\n✨ SMBS Workbench installed successfully!")
        print("Start FreeCAD, and select 'SMBS' in the Workbench dropdown menu.")
        return 0
    else:
        print("\n⚠️ Installation completed with warnings/errors.", file=sys.stderr)
        return 1


def uninstall(preferred_dir: Optional[str] = None, names: Optional[List[str]] = None) -> int:
    """Removes SMBS workbench links/copies from FreeCAD user Mod directory."""
    target_names = names or ["SMBS", "freecad_smbs"]
    mod_dir = find_or_create_mod_dir(preferred_dir)
    print(f"Uninstalling SMBS Workbench from: {mod_dir}")

    for name in target_names:
        dst = mod_dir / name
        if dst.is_symlink() or dst.is_file():
            dst.unlink()
            print(f" -> Removed symlink: {dst}")
        elif dst.is_dir():
            if sys.platform == "win32":
                subprocess.run(["cmd", "/c", "rmdir", str(dst)], capture_output=True, check=False)
            if dst.exists():
                shutil.rmtree(str(dst))
            print(f" -> Removed directory/junction: {dst}")
        else:
            print(f" -> '{name}' not found in {mod_dir}")

    print("Uninstallation complete.")
    return 0


def status(preferred_dir: Optional[str] = None, names: Optional[List[str]] = None) -> int:
    """Inspects and reports current installation status."""
    target_names = names or ["SMBS", "freecad_smbs"]
    mod_dir = find_or_create_mod_dir(preferred_dir)
    print(f"=== FreeCAD SMBS Workbench Status ===")
    print(f"Repository Root: {REPO_ROOT}")
    print(f"Workbench Source: {WB_SOURCE} ({'exists' if WB_SOURCE.is_dir() else 'MISSING'})")
    print(f"User Mod Directory: {mod_dir} ({'exists' if mod_dir.is_dir() else 'not created yet'})")

    ok, report_lines = verify_installation(mod_dir, target_names)
    print("\n--- Diagnostic Details ---")
    for line in report_lines:
        print(line)

    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install and configure FreeCAD SMBS Workbench in user Mod directory."
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "junction", "symlink", "copy"],
        default="auto",
        help="Installation method. 'auto' uses junction on Windows, symlink on POSIX, copy as fallback. (default: auto)"
    )
    parser.add_argument(
        "--mod-dir",
        type=str,
        default=None,
        help="Explicit FreeCAD user Mod directory path (optional)"
    )
    parser.add_argument(
        "--name",
        type=str,
        default="both",
        choices=["SMBS", "freecad_smbs", "both"],
        help="Target folder name in Mod/ (default: both SMBS and freecad_smbs)"
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Uninstall workbench from user Mod directory"
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show current installation status"
    )

    args = parser.parse_args()

    names = ["SMBS", "freecad_smbs"] if args.name == "both" else [args.name]

    if args.uninstall:
        return uninstall(preferred_dir=args.mod_dir, names=names)
    elif args.status:
        return status(preferred_dir=args.mod_dir, names=names)
    else:
        return install(mode=args.mode, preferred_dir=args.mod_dir, names=names)


if __name__ == "__main__":
    sys.exit(main())
