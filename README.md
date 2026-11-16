> **Experimental branch only (2026-11-16):** This checkout includes an opt-in axis-staged 2D + 3D-preview-BBox search. In FreeCAD's «Поиск 2D вариантов» task page, use «Эксперимент: оси A/B → 3D BBox → топ-5». The original 2D and OCC buttons remain unchanged. The top-five results are preliminary candidates, not physically qualified bend plans. See [the experiment specification](docs/AXIS_STAGED_2D_EXPERIMENT.md) and its precise limits. The stable `smbs` branch is unchanged.

# SMBS — FreeCAD installation (experimental)

This branch contains the installable FreeCAD runtime, real tooling models and reference parts. It incorporates the tested runtime from `experiment/auto-short-side-orientation`, including the punch-assisted retract preview, shared 2D/3D loading orientation, and stationary 3D tooling. It remains an experimental preview, not a physically qualified production release.

## Branch roles

| Branch | Purpose |
|---|---|
| [main](https://github.com/Badsmc/SMBS_BendSeq/tree/main) | Integration of tested 2D, 3D, GUI and core development |
| [2d](https://github.com/Badsmc/SMBS_BendSeq/tree/2d) | 2D/2.5D kinematics, sequence planning and tests |
| [3d](https://github.com/Badsmc/SMBS_BendSeq/tree/3d) | Native 3D physics and collision research |
| [gui](https://github.com/Badsmc/SMBS_BendSeq/tree/gui) | FreeCAD controls and interaction |
| [core](https://github.com/Badsmc/SMBS_BendSeq/tree/core) | Headless calculation, solver contracts |
| [smbs](https://github.com/Badsmc/SMBS_BendSeq/tree/smbs) | Runtime-only downloadable user installation |

Shared code can appear in more than one development branch when required to keep its imports valid. This `smbs` snapshot incorporates the requested experimental runtime directly; development tests, benchmark runners and agent handoff documents remain on the development branch.

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

Shared `toollibrary/` and `demo/SMBendTests/` plus `demo/parts/` reference assets are intentionally included. These are inert on-disk resources: FreeCAD must not automatically register or execute test runners. CI workflows and unrelated development scripts remain excluded.

## Sequence and collision workflow

Run 2D search to list complete candidate orders. Select a candidate to carry its bend order and per-bend loading orientation into Manual and the 3D preview. Manual offers two separate checks: **Fast OCC (five bend angles)** samples the punch and die at five bend angles plus sparse approach/retract positions; **Full 3D** runs adaptive sampled D6. The fast result can prove a sampled collision, but a run without a proven collision is **UNVERIFIED**, not CLEAR. A detected collision can be opened in the exact 3D intersection inspector. The two modes retain the selected virtual tool length and Conditional end-zone settings.

The fast mode currently checks the full native sheet and is not yet the proposed component-level AABB-before-union screening. In a recorded macOS run of the ten-bend reference part, a ten-step fast check took about 225 seconds. This measurement is for the development runtime on one machine; no matched performance acceptance of the installable snapshot has been recorded.

## Current limitations

An animated sequence, 2D search result, or sparse 3D check does **not** prove a physically feasible sequence. Continuous motion, backgauge, clamps, frame and transfer are not certified by the fast check. The observed B02 RETRACT punch overlap remains a collision. Manual can inspect a sampled punch-assisted lift and display its assumed motion after the separate OCC probe, without changing that verdict. The 2D/3D viewer and stationary tooling have been checked in the experimental runtime on macOS; a full native acceptance run of this installable snapshot is still outstanding. Do not use the preview as a certified press-brake plan.

## Shared machine preview

The Manual 180° checkbox has been removed. Hand-selected orders automatically place the shorter projected side toward the rear gauge. A selected complete 2D search candidate retains the orientation actually evaluated by the search. Both 2D and 3D views and the Manual OCC checks use the same per-bend orientation. In the machine section, `+s` is the rear gauge on the right and `-s` is the operator on the left. Older FCStd snapshots with the checkbox recalculate their orientation when loaded.

The 3D viewer displays the selected punch and die from the same machine-space profiles, positions, and configured lengths as the 2D tooling setup. The die stays on the machine bend axis at `s=0`, while the punch moves only vertically. The sheet placement brings each active bend to the tools. Multiple selected tool sections retain their machine-X offsets. This is a profile-extrusion preview; native collision checking uses the selected CAD solids.

See [integration and release policy](https://github.com/Badsmc/SMBS_BendSeq/blob/main/BRANCH_ARCHITECTURE.md).
