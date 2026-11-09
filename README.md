# SMBS — minimal FreeCAD installation (preview)

This branch contains only the FreeCAD runtime code and tooling models for the 2D/2.5D preview. **It is not yet a physically qualified production release and has not been merged from an accepted integrated `main` build.**

## Branch roles

| Branch | Purpose |
|---|---|
| [main](https://github.com/Badsmc/SMBS_BendSeq/tree/main) | Integration of tested 2D, 3D, GUI and core development |
| [2d](https://github.com/Badsmc/SMBS_BendSeq/tree/2d) | 2D/2.5D kinematics, sequence planning and tests |
| [3d](https://github.com/Badsmc/SMBS_BendSeq/tree/3d) | Native 3D physics and collision research |
| [gui](https://github.com/Badsmc/SMBS_BendSeq/tree/gui) | FreeCAD controls and interaction |
| [core](https://github.com/Badsmc/SMBS_BendSeq/tree/core) | Headless calculation, solver contracts |
| [smbs](https://github.com/Badsmc/SMBS_BendSeq/tree/smbs) | Runtime-only downloadable user installation |

Shared code can appear in more than one development branch when required to keep its imports valid; branch ownership denotes where modifications should originate. Feature changes enter `main` by PR after tests and real FreeCAD checks; the `smbs` snapshot should be refreshed only from an approved `main` state.

## Installation

Requires FreeCAD 1.1.x and SheetMetal Workbench. Download this branch as a ZIP, extract the contents into one folder `SMBS` under the active FreeCAD user `Mod` directory. Remove other installed SMBS/PressBrakeSim copies, restart FreeCAD and select **PressBrakeSim 2.5D**.

```text
SMBS/
  Init.py
  InitGui.py
  freecad_smbs/
  smbs_core/
  toollibrary/
  README.md
```

Only runtime and required supporting code is included; test fixtures, demos, CI workflows and development scripts are excluded.

## Current limitations

The workbench contains the 2.5D simulation and tooling UI. An animated sequence or section-level clearance does **not** prove a physically feasible sequence. Fresh FreeCAD GUI installation, real-part acceptance and full 3D machine/tool collision qualification remain outstanding. Do not use the preview as a certified press-brake plan.

See [integration and release policy](https://github.com/Badsmc/SMBS_BendSeq/blob/main/BRANCH_ARCHITECTURE.md).
