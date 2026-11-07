# PressBrakeSim 2.5D — selective D6 physical validation integration

Working branch: `feature/25d-physical-validation`
Base: `2D` at `5c089f427c7ed69261df969e1d741363b5463277`.
**Do not merge into 2D until native FreeCAD acceptance passes.**

## Implemented

The existing `freecad_smbs/commands/pressbrake_2d.py` UI now has two
**on-demand** controls under the existing Tooling tab. No replacement
animation engine, new Qt timer, or duplicate FoldState has been added.

1. **Проверить текущую позу: 2D + 3D**
   - Computes the same FreeCAD FoldState machine-plane section used by
     the GUI and runs `contact_oracle_2d.check_sheet_tooling_preview`.
   - If source `.FCStd` tooling models, datums and physical length are
     available, constructs the native FreeCAD Part solid from the real
     `panel_build`, loads the selected punch/die CAD models and calls
     the original D6.3.3 `validate_workpiece_tooling_pose`.
   - Returns `COLLISION`, `UNVERIFIED`,
     `SECTION_CLEAR_3D_UNVERIFIED`, or
     `SAMPLED_3D_CLEAR_NOT_CONTINUOUS`.
   - `SAMPLED_3D_CLEAR_NOT_CONTINUOUS` is **only one pose**, not
     evidence for the entire operation. Evidence becomes stale as soon as
     bend angles, ram position, tool selection or setup changes.

2. **Проверить весь гиб: D6.3.4**
   - Uses the original `AdaptiveTrajectoryValidator3D` directly.
   - Samples APPROACH/CONTACT/BENDING/BOTTOM/RETRACT of the selected bend
     from the existing `FoldState` and actual native tool solids.
   - Explicitly distinguishes sampled convergence, positive-volume
     collision, unresolved intervals, and unsupported geometry.
   - Restores FoldState angles and transforms and refuses any successful
     verdict if restoration fails.
   - Can be resource-intensive: invoked **only on user command** while
     the simulation is paused, never automatically from the 40 Hz timer.

Native source modules reused from `d6-native-3d`:
`native_collision_3d.py`, `native_tool_loader.py`,
`native_bend_solid_3d.py`, `motion_trajectory_3d.py`.

Unified thin adapter:
`smbs_core/kinematics2d/physical_validation_25d.py`.

## Evidence limits & Verified Scope (Stage 2 Partial)

> [!WARNING]
> Stage 2 remains **OPEN** and is NOT yet fully certified.
> A passing headless or sampled run is NOT a full machine safety certificate.

### 1. Verified Model and Tooling Scope
- **Part Model**: Exclusively `demo/parts/box.100out-50in-(ok).FCStd` (8-bend box, SHA256: `6510d62cdf6427cfac18d936243c39f8aafa4e31718248124105642ad9e52db3`).
- **Tooling Models**:
  - Die: `toollibrary/die/matrica.FCStd` (SHA256: `a7f5e8640835fc9d7c92ca46d91384a6a04ba0be5e9a690539fd88c01f8bcb22`).
  - Punch: `toollibrary/punch/puancon.FCStd` (SHA256: `4093cf55ca1804cf013e65d15bc19e8f6334ced63cd92a741228058ac5704cc0`).
- **Verified Phases**:
  - Initial contact pose validation: 2D section (`TOUCH`) and 3D native (`TOUCHING`), machine-space delta `(0.0, 0.0, 0.0)`.
  - Motion trajectory validation: D6.3.4 adaptive sampling (APPROACH, CONTACT, BENDING, BOTTOM, RETRACT) converged (`SAMPLED_INTERVAL_CONVERGED`, 75 adaptive discrete states).
- **Exact Collision Checks**:
  - Native collision checks candidate face pruning with boundary distance proof.
  - Full volume enclosure check (`_evaluate_common_volume` via OCC `common()`) prevents false positives for wholly enclosed solids.
  - Fail-closed contract on non-finite (NaN/inf), negative or missing volume results.

### 2. Unverified Items (Explicit Exclusions)
- **Other Fixture Models**: L, Z, U, 135, and labeled-negative fixtures (`box.100in-50out-(false)`, `U-10-10-20-(false)`) have not completed Stage 2 acceptance.
- **Machine Surroundings & Transfer Motion**:
  - Swept volume during workpiece transfer and rotation between nonparallel bends is **UNVERIFIED**.
  - Backgauge fingers (`Backgauge.FCStd`), machine frame uprights/throat (`machine_frame.FCStd`), hydraulic clamps (`Clamp.FCStd`), and front sheet supports remain **UNVERIFIED** (not part of the punch/die pair check).
- **Physical Metallurgy & Mechanics**:
  - Machine tonnage limits, springback compensation, sheet thinning, and tool deflection are **UNVERIFIED**.
- **Continuous Swept Motion**:
  - Trajectory verdict is `SAMPLED_3D_CLEAR_NOT_CONTINUOUS`: discrete poses certified, continuous swept volume is NOT mathematically certified.

## CI status and limits

The feature branch's
`.github/workflows/25d-physical-validation.yml` compiles the integrated
modules and runs the isolated physics + D6 collision tests together with
the entire retained 2.5D pure-Python suite. FreeCAD-only GUI and BRep
tests are skipped outside native FreeCAD.

Green GitHub Actions **is not native FreeCAD GUI acceptance**.

## Required manual acceptance in Windows FreeCAD 1.1.x

1. Back up your current `2D` installation *outside* FreeCAD's `Mod`
   directory. Only one copy of PressBrakeSim may be installed under `Mod`.
2. Download branch
   `feature/25d-physical-validation` and install its complete source
   checkout into
   `%APPDATA%/FreeCAD/v1-1/Mod/SMBS_BendSeq-2D/`.
3. Ensure `demo/parts/box.100out-50in-(ok).FCStd` and the original
   `toollibrary/punch/puancon.FCStd` and
   `toollibrary/die/matrica.FCStd` are present.
4. Restart FreeCAD. Open the box fixture, select its `Body_Unfold`,
   choose PressBrakeSim 2.5D, load the part, choose native tool profiles
   and use **Align Tooling**.
5. With the animation paused, click each physical-check button.
   Inspect the full status text, including `native_status`, reason
   and checked scopes. An ordinary 2.5D clear status is not 3D safe.
6. Move the angle slider or switch bend; previously sampled evidence
   must be visibly marked `УСТАРЕЛО`. Full Cycle playback and its
   controls must remain unchanged.
7. Execute these commands in **FreeCAD Python Console**, after installing
   the working branch:
 
```python
from tools.run_25d_physical_gui_acceptance import run
result = run()
```

The runner opens the **original box FCStd** read-only, checks the
current-pose result and D6.3.4 sampled bend result, and verifies no
FoldState angle changes and unchanged source file SHA-256. It rejects
missing native evidence and `UNVERIFIED` statuses. The runner may reveal
geometry, profile, or setup incompatibilities; treat these as real
integration blockers and **do not weaken the acceptance criteria**.

8. Repeat on L, Z, 135, U and the available labelled-negative fixtures
   before completing the general Stage 2 release gate. Confirm actual
   3D intersections independently in FreeCAD, including the expected
   failure cases.
9. Only after functional GUI acceptance and green CI, merge the tested
   feature changes into `2D`; rerun the `2D` CI. Then remove the
   temporary development branch.

## Report every real test

Record the branch commit SHA, FreeCAD/SheetMetal versions, fixture,
selected punch and die profile IDs and lengths, current bend,
check status, first collision (if any), native D6 sampling status,
and whether source FCStd checksum and GUI context were preserved.

Do not mark Stage 2 `VERIFIED` or `PRODUCTION_READY` from a pure
Python mock test.

## Real FreeCAD regression finding — 2026-11-06, box.100out-50in-(ok)

Observed in Windows FreeCAD 1.1.x during first physical acceptance:
- Local zone bounds `Z=[-1.5, 0.0]`.
- Displayed machine section zone bounds `Z=[-3.0, -1.5]`.
- Selected die datum `Z=-1.5` and punch tip `Z=5.0`.
- Native current-pose collision result `PENETRATION` and section
  `INTERFERENCE`, specifically `DIE/active_fixed`.
- D6.3.4 initially failed before any sampling because
  `WorkpiecePlacement3D.compute_placement` in 2D did not accept
  `align_bend_arc_center=True`, which D6.3.4 requires.

Corrections on feature branch:
- Ported ONLY the D6 optional arc-center placement parameter and
  translation logic; the default 2D placement remains unchanged.
- Added regression for the opt-in placement API / default invariant.
- Added initial sheet vs die support-datum difference in collision
  diagnostic text. A 1.5 mm difference is recorded in this fixture
  but **not silently corrected** by moving solids after the fact.
- Native sampling and current 2D/native pose checks remain independent.
  `COLLISION` on actual positive-volume overlap continues to reject.
  No geometry safety check is disabled.

Open physical blocker: identify the correct shared machine-frame
setup for the initial pose (local section offset, die V support datum,
and D6 native machine zero). Only change runtime placement after
validating it against true tool CAD, the eight-bend Full Cycle test and
a negative collision fixture. Correcting the D6 method signature is
not proof that existing penetration is resolved.

Run `tools.run_25d_physical_gui_acceptance.run()` again after updating
the branch and provide all `PHYSICAL_FRAME_DIAGNOSTIC`,
`PHYSICAL_GUI_RESULT`, and `D634_ADAPTIVE_RESULT` lines.
If collision persists, do not mark the stage completed or merge into 2D.
