# SMBS — FreeCAD installation (experimental)

This branch contains the installable FreeCAD runtime, real tooling models and reference parts. Its runtime was updated from `experiment/fast-search-lift-retract` at `0c6c0df` by explicit request. It remains an experimental preview, not a physically qualified production release.

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

An animated sequence, 2D search result, or sparse 3D check does **not** prove a physically feasible sequence. Continuous motion, backgauge, clamps, frame and transfer are not certified by the fast check. The observed B02 RETRACT punch overlap requires separate physical interpretation of the lift behavior; the program currently reports a collision. Fresh installation and native acceptance of this `smbs` snapshot remain outstanding. Do not use the preview as a certified press-brake plan.

See [integration and release policy](https://github.com/Badsmc/SMBS_BendSeq/blob/main/BRANCH_ARCHITECTURE.md).

## Automatic machine orientation (experiment/auto-short-side-orientation)

The Manual 180° override checkbox is removed. For a hand-selected bend
order, one deterministic per-bend map places the shorter projected side
toward the rear gauge, using the formed state after preceding bends. A
selected complete 2D search candidate retains the orientations actually
evaluated by that search. Both 2D and 3D previews use the same machine
placement; the Manual quick/full OCC checks and the single-bend native
trajectory receive the same per-bend map. The previous second rear-frame
auto-flip in the viewer is disabled.
Machine section convention: `+s` is right/rear gauge, `-s` is left/operator.
The short-side choice and visual gauge envelope now use the unmodified
playback placement (including its explicit 180° choice); neither applies
the older fixed-panel-to-rear rotation a second time.

An unavailable geometric orientation is displayed as UNVERIFIED and blocks
native checking of that bend instead of defaulting to 0°. Old FCStd snapshots
with the manual checkbox are recalculated when loaded. New snapshots label
whether the orientation came from an explicit 2D candidate or the short-side
heuristic. This installation choice does not prove rear-gauge contact,
collision clearance, or a safe bend sequence.

The 3D viewer draws the selected punch and die from the same machine-space
profiles, transforms, and configured lengths used by the 2D tooling setup.
Their transverse station is fixed at the machine bend axis (`s=0`), and the
die remains at `z=0`; only the punch follows the ram's Z position. The sheet
placement carries each bend to this stationary tooling. Multiple configured
tool sections keep their machine-X offsets. These extruded profiles are a
visual preview; native OCC collision checks still use the selected CAD solids.

## Experimental punch-assisted RETRACT (base branch)

`experiment/punch-assisted-retract` starts from the `smbs` installation
snapshot. Following a **proven native PUNCH collision during RETRACT**, Manual
offers `Проверить подъём OCC и показать движение`. The diagnostic replays the
original exact FCStd collision, then tests nine OCC poses from BOTTOM to the
configured upper RAM Z assuming the entire formed sheet follows the punch
vertically one-to-one. It checks the entire sheet against both selected tools
at each pose and appends a separate result to the Desktop developer journal.
The same selected Conditional end-zone policy is applied to this alternate
path at every pose. An exact end-zone-only punch overlap remains labelled
`SAMPLED_LIFT_CONDITIONAL_UNVERIFIED`; a central punch overlap or die
penetration blocks the trial. A missing zone classification is UNVERIFIED.
The button stays visible beside the Manual fast/full check actions; it is
disabled with an explanation until an eligible native RETRACT punch hit has
been observed in the current session. Opening an OCC inspection document is
separate from running the lift probe.

The existing Manual collision verdict is never replaced. A sampled path with
no penetration is **UNVERIFIED**: the diagnostic cannot prove punch-to-sheet
coupling or force, continuous motion between samples, release, lateral
withdrawal, machine frame, clamps or backgauge clearance. A positive die or
punch overlap on this hypothetical path is reported as a blocked sample.
After a sampled path completes without a new blocker, Manual enables
`Показать совместный подъём в симуляции`. This resets and plays the selected
order in Full Cycle. For the probed bend only, RETRACT adds exactly the
punch's rise from BOTTOM to the whole sheet rigid placement used by both
2D and 3D previews; STEP_COMPLETE and the final frame retain that height.
The next-bend transfer starts from the lifted pose. The simulation is an
assumed kinematic illustration and retains the original COLLISION verdict.
The 2D die kinematics now derives the effective opening from the same shoulder
fillet centers measured by native OCC; for the selected AT3015 die the two
paths both reach RAM BOTTOM near -3.492685 mm. Before enabling the animation,
Manual also compares both RAM endpoints against the native replay within
0.05 mm. A mismatch disables the preview and reports it instead of animating
a different path.
Changing inputs or starting another validation disables the experimental
playback until the exact OCC probe is repeated.
After a successful lift probe, `Проверить боковой вывод детали OCC` rebuilds
the same native collision and explores straight release in both machine-Y
directions at upper RAM. Each route ends beyond the punch and die AABBs and
checks five exact OCC positions using the selected Conditional end-zone policy.
Central punch or die penetration blocks that route. A possible route is
`SAMPLED_RELEASE_ROUTE_UNVERIFIED`, never CLEAR: gaps between positions,
backgauge/clamps/frame, operator grip, and physical end relief still require
real evidence. This extra probe never changes the original Manual COLLISION.
Testing the alternative path on the real B02 FCStd in FreeCAD remains necessary
before any physical interpretation or integration into sequence search.
The diagnostic has a 120-second per-path budget and never increases collision
tolerances. The Linux FreeCADCmd smoke test uses simple OCC box solids; it does
not substitute for B02's actual source part, selected tools and Mac runtime.
The first B02 macOS probe (before this Conditional fix) stopped at its BOTTOM
pose on a 943.035 mm³ punch overlap, which the main validator had classified
as Conditional end-zone contact. That first result did not test any rising
pose. Repeat the probe with the updated runtime before interpreting B02 lift.
If the original FCStd contains the saved B02 Manual report, reloading that
document can enable just the experimental lift button without a new ten-bend
run. Order, tool IDs, virtual length and end-zone settings must match the saved
report. The diagnostic then rebuilds the current native sheet and tools and
compares their exact collision Common to the saved B02 sample; any mismatch
returns UNVERIFIED. Historical validation remains historical and cannot be
promoted to a current full-check result by this replay.
