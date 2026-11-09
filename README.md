# SMBS — FreeCAD user installation (preview)

This branch contains the **runtime-only** FreeCAD PressBrakeSim 2.5D workbench.
Development and integration take place on other branches. The branch is **not yet production-qualified**.

## Installation

Requires FreeCAD 1.1.x with SheetMetal Workbench installed.

1. Download this branch as ZIP, unpack it.
2. Copy its contents into one folder `SMBS` in FreeCAD's active user `Mod` directory.
3. Ensure only one SMBS / PressBrakeSim workbench copy is installed.
4. Restart FreeCAD and choose **PressBrakeSim 2.5D**.

Required top-level files and directories:

```text
SMBS/
  Init.py
  InitGui.py
  freecad_smbs/
  smbs_core/
  toollibrary/
```

## Included capabilities

- PressBrakeSim 2.5D simulation UI
- Tooling library UI
- Required model, kinematics, integration and geometry support

## Limitations / release gate

This installation is a **preview candidate**. A fresh FreeCAD GUI installation, real model loading, animation controls, bend orientation, tooling contacts and full physical 3D collisions have not been independently accepted in this branch. Do not assume that a preview sequence is a validated manufacturing bending plan.

No tests, fixtures, demo parts, CI workflows or developer scripts are shipped in this branch. Those remain on `2d` and other development branches.

Repository: https://github.com/Badsmc/SMBS_BendSeq
Development: `2d`; integration: `main`; 3D: `3d`; UI: `gui`; kernel: `core`.
