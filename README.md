# PressBrakeSim 2.5D — FreeCAD SheetMetal workbench

**Authoritative development branch: [`2D`](https://github.com/Badsmc/SMBS_BendSeq/tree/2D).**
Always install, test and continue 2.5D development from this branch.
`main`, `d6-native-3d` and archived branches are NOT substitutes for this source tree.

**Status: release candidate under verification — not a certified machine planner.**
The Python test suite passes on GitHub Actions, but a fresh real FreeCAD GUI
installation, the full 2.5D workflow on original FCStd models, physical
tooling/collision validation and automated manufacturing sequence search
still require acceptance.

## Runtime architecture

- `freecad_smbs/InitGui.py` — one active workbench, two commands: **PressBrakeSim 2.5D**
  (simulation workspace) and **Библиотека оснастки** (tooling dialog).
- `freecad_smbs/commands/pressbrake_2d.py` — the one 2.5D UI/workspace.
  Includes angle slider, bend order table, Play All, Pause/Resume, Reset,
  tooling controls, 2.5D machine-section view and read-only 3D preview.
- `freecad_smbs/integration/` — authoritative SheetMetal unfold reader,
  panel builder, direction validator and optional diagnostics.
- `smbs_core/kinematics2d/` — finite-radius bend zones, signed FoldState,
  workpiece pose, press cycle, 2D collision oracle, 3D surface preview,
  motion JSONL recorder and trace analyzer.
- `freecad_smbs/ui/tooling_panel.py`,
  `smbs_core/geometry_backend/io/` and `toollibrary/` — tooling catalog,
  punch, die, clamps, backgauge and machine-frame source models.
- `demo/parts/` — original CAD reference workpieces (see below).
- `tests/` and `.github/workflows/pressbrake-preview-2d.yml` —
  source-level regression tests.

The obsolete HandMod/SMBS OCC pipeline, duplicate FreeCAD commands, old GUI
dialogs, macros and unrelated CLI have been removed from `2D`. Their history
remains on [`archive/2d-pre-cleanup-20261106`](https://github.com/Badsmc/SMBS_BendSeq/tree/archive/2d-pre-cleanup-20261106).
The experimental native D6.3.4 evaluator remains isolated in `d6-native-3d`
and is **not** a required 2.5D runtime dependency.

## Install directly in FreeCAD

Requirements: **FreeCAD 1.1.x**, the FreeCAD **SheetMetal Workbench**, and
FreeCAD's Python 3.11 / PySide6 environment. The runtime uses FreeCAD's
built-in `Part` geometry; **installing `pythonocc-core` is not required**.

Download the `2D` branch using GitHub **Code → Download ZIP**, or clone
`git clone --branch 2D https://github.com/Badsmc/SMBS_BendSeq.git`.

Put the **checkout root** directly under the *active FreeCAD user Mod
directory* in a single folder, for example on Windows:

```text
%APPDATA%/FreeCAD/v1-1/Mod/SMBS_BendSeq-2D/
  Init.py
  InitGui.py
  freecad_smbs/
  smbs_core/
  demo/
  toollibrary/
  tools/
```

Do **not** install a second copy of the old SMBS workbench in the same Mod
directory. Restart FreeCAD after installing or updating code. In the Workbench
selector choose **PressBrakeSim 2.5D**.

On macOS or Linux, place the same checkout under the FreeCAD user Mod directory.
Paths inside the project are always resolved from module files, not from
Windows-style paths, the user's Desktop or current working directory.

Direct checkout installation is the **only supported install route** for
the cleaned `2D` branch: it keeps the Workbench, geometry, models and
demo files under one root and avoids duplicate FreeCAD registrations.

## Original demo CAD models

The 17 user-supplied `.FCStd` models are described in
`demo/MANIFEST.json`, but **their original binary content has not yet been
committed to GitHub**. Do not claim that downloading `2D` alone includes
L, Z, U and box reference workpieces.

Download `SMBS_demo_FCStd_17_files.zip` separately and put it in your
Downloads folder. In FreeCAD's Python Console, adjust `root` to match
your installed checkout:

```python
from pathlib import Path
import sys
root = Path("C:/Users/User/AppData/Roaming/FreeCAD/v1-1/Mod/SMBS_BendSeq-2D")
sys.path.insert(0, str(root))
from tools.install_demo_models import install_demo_archive
print(install_demo_archive())
from smbs_core.demo_paths import all_demo_parts
print("Demo FCStd:", len(all_demo_parts()))
```

The installer checks all original file sizes and SHA-256 hashes, checks FCStd
archive format, and refuses to overwrite different files. When all original
files are later committed, a separate ZIP will no longer be necessary.

## Use and limitations

1. Open a valid FreeCAD SheetMetal document with an Unfold feature.
2. Select the relevant part/face in the tree, then launch the simulator.
3. Choose **Bend Only** for automatic **kinematic visualization** with
   one `Play All` control.
4. Choose **Full Cycle** for press-motion preview when the machine pose and
   tooling geometry are supported. An unsupported nonparallel-axis or
   unverified contact case must stop with an explicit warning, not fake safety.
5. Use the bend table, angle slider, manual order, controls and optional JSONL
   motion recorder to inspect the sequence.
6. Do not equate a playable/manual order with a physically feasible
   **manufacturing sequence**. Automatic valid sequence planning remains work
   to complete before that capability can be promised.

2.5D section clearance cannot guarantee full 3D collision freedom. Clamps,
backgauge, machine frame and out-of-section interference require independent
qualification; collision and transfer results must state their evidence limits.
This product does not compute tonnage, springback or FEM behavior.

## Tests and release criteria

GitHub Actions runs all retained **pure-Python** tests, source compilation,
install-path checks and scope assertions. Tests requiring real FreeCAD/Qt
and original FCStd fixtures need a separate Windows/macOS/Linux validation
environment.

Current release gates:
[`docs/PRESSBRAKESIM_25D_RELEASE_GATES.md`](docs/PRESSBRAKESIM_25D_RELEASE_GATES.md).

Traceability and legacy decisions:
[`docs/2D_BRANCH_CLEANUP_AUDIT.md`](docs/2D_BRANCH_CLEANUP_AUDIT.md).

**The branch is the single source of development truth, but must not be tagged
as a production-ready release until all release-blocking tests and real GUI
acceptance steps pass.**
