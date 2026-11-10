# SMBS 2D automatic backgauge — development status

Branch: `experiment/2d-backgauge-kinematics`, forked from `integration/gui-2d-search-screening`.

## Machine coordinate contract — operator left, interior right (2026-11-10)

All machine-section calculations use the same physical frame:

- Screen/machine `s = -Y_m`; the **bend line, punch working axis and die V centre** are at `s = 0`.
- **Left (`s < 0`)** is the operator side / free portion of the workpiece.
- **Right (`s > 0`)** is the inside of the press / rear-gauge area.
- The active bend's fixed/support panel is physically placed to the rear/right. `WorkpiecePlacement3D.compute_placement(..., rear_panel_vertices_xy=...)` uses a 180-degree rigid rotation about machine Z where needed. No screen-only mirroring; tooling is not flipped or moved off the bend axis.
- The automatic gauge edge is chosen at positive `s` only, and the whole stop rectangle must remain in `s > 0`. Negative manual targets are rejected.
- If geometry leaves both potential rear edges equally plausible, this is `UNVERIFIED` / ambiguous, not a reason to fabricate a side.
- Search Stage B, manual replay, operation setup scoring and live 2.5D press-cycle placements use the same rear-right orientation when backgauge is enabled. The previous negative stop positions and all-first-step collisions in the diagnostic JSON should not recur from the old *side-selection* bug; real physical/projection collisions may remain.
- The punch and die are *stationary machine tooling*: the reference working tip and V groove remain at `s=0`; they are not simply reflected horizontally because their own profile shapes, datum registration and handedness need machine-specific evidence.

**Regression note:** new sampled 2D results are not directly comparable with earlier reports that used ambiguous machine orientation; recalculate from the current version. No claim of production safety or swept-path clearance.

## Real CAD BRep mode — implementation for FreeCAD runtime (2026-11-10)

**Implemented in the experimental branch**:

- In **Задний упор** choose **Использовать реальную 3D-модель Backgauge.FCStd при поиске (экспериментально)**. Specify the position of **one physical CAD gauge unit** along X_m (mm). The setting is opt-in; the old 2D rectangle remains available only as a separate explicitly selected legacy mode.
- The `BackgaugeCAD3D` adapter reads `toollibrary/Backgauge/Backgauge.FCStd` as an actual OCC solid (not metadata BBox), checks the corrected Face3 stop datum and rotates its `(-X)` normal into machine `(+Y_m)` (toward the operator). It positions that contact face on the positive rear edge `s > 0`; no silent source-face substitution.
- Stage B reuses the same rigid machine pose as punch/die, constructs sampled 3D solids for all panels and finite-radius bend zones from `FoldState`, and uses OCC solid Boolean intersections against the actual CAD stop. It reports `BACKGAUGE_CAD_3D` with a panel/zone ID and measured positive overlap volume. Empty sampled intersections do not count as a manufacturing safety certification.
- Initial `LOAD_TO_GAUGE` with explicit positive feed distance is screened at five sampled positions against the CAD stop **on the FIRST operation only**. This is NOT a continuous swept-path collision check and does not test other machine parts during loading.
- Automatic search, full-density candidate replay and manual-order replay use the same CAD Stage B gate. Diagnostics have `geometry_evidence=REAL_BACKGAUGE_FCSTD_SAMPLED_BREP_3D_PANELS_ZONES_NOT_SWEPT` only after the adapter actually loads.
- The 2.5D viewer uses a **slice of the actual positioned CAD BRep** at its configured machine X station. It must not show a synthetic rectangle if the CAD model cannot be loaded.

**Remaining blockers / restrictions**:
1. One CAD stop body at one **explicitly chosen** X station; this does not invent two separate rear-gauge fingers, their mounting offset, limit switches, or machine travel limits. Two independently driven fingers and their placements need a separate model/configuration.
2. CAD sheet solids are sampled from the current BendZone/FoldState geometric model. No proof of swept motion between samples, forming-tool contact dynamics, machine frame, clamps, or actual mechanical finger movement. Do not use results as a production go/no-go without independent verification.
3. FreeCAD runtime tests (`tests/test_backgauge_cad_3d.py`) are only runnable with FreeCAD/Part installed. The authoring environment did not execute FreeCAD or OCC; a local test on the benchmark 10-bend part is still required.
4. FreeCAD GUI settings must explicitly opt into CAD mode; existing saved settings remain in legacy 2D mode until changed.

**Local test**: update the Git checkout, restart FreeCAD, open **Задний упор**, enable the real CAD model, set the desired X station, Save; rerun search and export a fresh JSON. Check that the reported `geometry_evidence` changed and that collisions mention `BACKGAUGE_CAD_3D` rather than `BACKGAUGE`. A malformed source or invalid sheet solid must yield `UNVERIFIED`/an error, never an invented `CLEAR`.

## Correct source model: toollibrary/Backgauge (2026-11-10)

The actual rear stop is **`toollibrary/Backgauge/Backgauge.FCStd`**, accompanied by **`Backgauge.tooling.json`**. It is a different asset from the `Clamp` library entry. Verified sidecar metadata:

- `kind: backgauge`, `id: Backgauge`, `source: Backgauge.FCStd`
- `stop_face`: plane origin `(25,137.5,7)` mm, outward normal `(-1,0,0)`, tolerance `0.05` mm (corrected from invalid source origin `(0,100,7)` after FreeCAD Face3 diagnostics)
- `working_axis`: origin `(0,100,7)` mm, direction `(0,1,0)`, tolerance `0.02` mm
- `mounting_plane`: origin `(0,0,0)`, normal `(0,0,-1)`
- metadata dimensions: length `100` mm, width `200` mm, thickness `14` mm

**Datum fix from FreeCAD diagnostic:** source `Body` has 1 solid and 10 faces (8 planar). The original JSON datum point `(0,100,7)` was 25 mm from Face3. Face3 has outward normal dot +1 relative to `(-1,0,0)`, area 1750 mm² and centre `(25,137.5,7)`. Consequently, only the `stop_face.origin` metadata was corrected to the observed Face3 centre. `working_axis` and source CAD were left unchanged pending separate axis verification. Re-run CAD preflight locally before treating this as a validated contact face. The design may contain multiple rearward-facing faces, so actual use as a gauge finger remains unqualified.

**Sequence-search diagnostic `22.json`:** its `backgauge_configuration.geometry_evidence` explicitly says `2D_RECTANGLE_ONLY_NOT_3D_VERIFIED`, and all 10 root bends still fail Stage B at APPROACH. This metadata fix does NOT alter that algorithm and cannot fix those reported 2D projections. The real CAD model must be connected through a separate 3D stop/finger collision contract; never discard intersections by fiat.

**Source-contract implications:** its contact face is at source-local X=0 and faces -X. In machine section (+s toward rear/right), the physical contact face must face the operator (-s), and the gauge body should remain to the right (+s) of that face. Do NOT treat the source metadata's 200 mm width as the 2D gauge depth or invent a 10x10 mm square; the exact BRep orientation and which solids constitute the actual fingers must be loaded and inspected in FreeCAD.

**Current implementation status (updated):** the actual `Backgauge.FCStd` BRep is available as an **opt-in sampled CAD 3D mode** in Stage B; the legacy rectangle is still available separately. Replacing the rectangle safely requires a FreeCAD geometry adapter that preserves the source object's placement, validates the datum plane against actual BRep faces, registers the stop-face-to-target transform and the X-axis stations of separate fingers, and performs collision checks in 3D (including LOAD_TO_GAUGE and TRANSFER). Until this is implemented, diagnostic evidence must not claim CAD backgauge clearance.

The older `Clamp` notes below are retained for clarity only: **Clamp is not the selected rear-gauge tool**.

## Clamp.FCStd audit and loading approach (2026-11-10)

`toollibrary/Clamp/Clamp.FCStd` is a real FreeCAD model with an associated
`Clamp.tooling.json` sidecar. The checked sidecar declares:

- `kind: clamp` (not yet a certified rear-gauge finger)
- `width_mm=150`, `length_mm=150`, `height_mm=40`
- mounting plane origin `(75,75,40)`, normal `(0,0,1)`
- working axis origin `(75,75,0)`, direction `(1,0,0)`
- working plane origin `(75,75,0)`, normal `(0,0,-1)`

**Do not substitute the bounding box of this part for a rear-gauge finger**.
It may be a workholding clamp rather than a backstop. Its actual BRep shape,
contact face orientation, mounting/stroke constraints and usable finger
positions need verification in FreeCAD. No existing metadata designates a
rear-stop contact face, X/R/Z travel datum or left/right finger stations.

Before using the CAD file as collision geometry, a FreeCAD adapter must:
load the source shape, verify solid validity, derive an explicit backstop
contact face/normal and mounting transform, position independent physical
fingers at machine X stations, and intersect each 3D gauge solid with all
rigid folded panels during both SETUP and the swept first-feed/TRANSFER path.
Only after the datum/geometry check should it replace the provisional
2D rectangle in the physical collision oracle.

## Initial introduction of the workpiece (opt-in preview)

The first operation can now start with `LOAD_TO_GAUGE`, before
`APPROACH`. In the **Задний упор** GUI choose an explicit
`initial_feed_preview_mm` value (0 disables it). During this phase the
sheet moves rigidly from the **operator/left** side (`s<0`) to the
first gauge target (`s>0`); punch and die remain stationary and the
ram is held at its upper position. This is visual only: the default is
**disabled** until the operator chooses a preview feed distance, and the
path has no production-safe collision qualification. The distance is a
preview start offset, **not** an inferred real loading starting position.

The intended full cycle is:
`IDLE -> LOAD_TO_GAUGE -> APPROACH -> CONTACT -> BENDING ->
BOTTOM -> RETRACT -> TRANSFER -> ...`.
The post-bend `TRANSFER` remains separate from the initial sheet feed.

## Target behavior

For every bend, the planner computes the machine-space distance from the active bend line to a supporting **fixed-panel edge** using the current folded state. The backgauge stop is positioned at the selected edge, automatically repositions when the next bend is selected, and moves as an interpolated 2D preview in the `TRANSFER` phase.

The operator **does not need to type an individual target for every bend**. In the **Задний упор** toolbar dialog, enable checks and leave the default **Автоматически вычислять положение по краю опорной панели** checked. Enter the physical stop rectangle dimensions and optional clearance. Manual target entry remains available as an override.

## Geometry and coordinates

- Uses `FoldState` and `WorkpiecePlacement3D` to align the active bend in machine coordinates.
- Uses `compute_machine_cutting_plane_section(...)` to obtain **the active fixed panel's** real cross-section at the bend midpoint.
- Derives a **strictly positive rear/right** edge from that fixed-panel cut polygon only (not overall part bounding box and never the moving flange), after the physical machine orientation has been normalized.
- The measured bend-to-edge depth is `abs(edge_s)` in mm; gauge rectangle centre `s` is offset outside that edge by half the stop width plus optional clearance; `z` is aligned to the edge's thickness centre.
- If no fixed-panel edge can be resolved at the machine cutting plane, the candidate is **UNVERIFIED**; no made-up length.
- Geometry mode is `AUTO_FIXED_PANEL_EDGE_2D_UNVERIFIED`; geometry/station orientation still requires qualification against the actual machine.

## Collision and weight integration

- Automatic and manual 2D search generate a target separately for **each active bend in the already-folded sequence prefix**.
- The existing Stage B 2D polygon-contact oracle checks the stop's *target position* in sampled APPROACH, BENDING, BOTTOM, RETRACT phases.
- `backgauge_move_fixed` and `backgauge_move_per_100_mm` score changeover between successive target (s,z) positions; they do not change collision outcomes.
- Automatic cost and manually entered order cost use the same penalty model, and JSON contains per-step targets, source and penalties.
- The sequence catalog's simulation assignment reruns sampled 2D checks with the current backgauge configuration.

## Visualization

- Gauge outline is drawn in the main 2.5D viewer when configured.
- During the existing `TRANSFER` phase it linearly interpolates stop centre from the previous bend target to the next bend target.
- This **is a visual planning interpolation only**, not physically constrained axis motion and not a validated collision-free transfer.

## Important limitations still open

1. The selected far edge is a 2D geometric candidate, **not yet proof of an accessible physical rear-gauge contact face**; direction of the rear machine side, finger reach, and support stability need explicit machine-specific contracts.
2. Stop represented by one 2D rectangular cutting section, not independent left/right fingers, X/R/Z axes or their 3D geometry.
3. Inter-bend motion is **not collision-checked**, even though its visual interpolation is shown. It can pass through the sheet or tooling.
4. No motor motion constraints, axis stroke limits, coordinated sheet transfer, safety interlocks, finger retraction, staging, or real-time machine control.
5. No automatic recognition of alternate supporting edges or reliable backstop placements for flange-less, heavily folded, tapered or unsupported part profiles.
6. No production certification from any sampled 2D result.

## FreeCAD test protocol

1. Checkout this branch and restart FreeCAD. Load test part `2134`.
2. Open top toolbar **Задний упор**. Enable checks, keep **Автоматически вычислять положение** active, enter stop width/height and save. **Do not manually enter per-bend positions.**
3. In **2D варианты**, check JSON `backgauge_configuration.automatic=true`, `machine_setup.backgauge_s_mm`, `backgauge_z_mm`, `machine_setup.note` containing measured depth and `cost_breakdown[].backgauge_move_cost`.
4. Run manual-order replay with the same sequence and weights; compare scores and backgauge findings.
5. Select a result for simulation. During **TRANSFER**, observe the orange gauge stop moving smoothly. If it moves across the workpiece or into tooling, that is a **known unsupported hazard in the current stage** and must be logged for future motion checking.
6. Confirm that missing fixed-panel edge produces a visible unverified diagnostic, not a nominal valid target. Repeat with a different bend sequence and confirm targets are recalculated based on preceding bends.

The next qualification milestone is machine-axis-calibrated dual-finger contact selection, with swept path collision checks for both the stop and the workpiece during TRANSFER.
