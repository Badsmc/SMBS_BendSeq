# SMBS Cascade — operator/rear loading orientation in actual playback

Date: 2026-11-11
Branch: `experiment/2d-backgauge-kinematics-cascade`

## Reproduced mismatch

Previously, 2D search could accept `REVERSE_MACHINE_Z_180` on a bend,
but some simulations still displayed the long side pointing right/rear.
There were multiple competing data sources:

1. Search chose a rigid 180° machine-Z orientation and stored it per bend.
2. GUI Bend Only rendered a *source-local* `FoldState.cross_section_2d`
   without applying that machine pose.
3. Whole-part 3D tab used `build_sheet_preview_faces` with the source
   model frame and canonical first bend, rather than the current
   `PressCycleController` machine pose.
4. Manual/canonical playback had no search-selected orientation and
   silently returned to a rear-facing fixed panel.
5. The 3D orbit camera re-centered on the moving sheet centroid every
   frame, obscuring workpiece motion relative to the stationary die.
6. A partial search result could stop at BOTTOM rather than after the
   punch had finished RETRACT.

## Implemented

- All 2D/3D machine projections now consume one placement:
  `_current_preview_machine_placement`, which delegates to the
  `PressCycleController` for Full Cycle and constructs an equivalent
  machine-frame pose for Bend Only.
- `build_sheet_preview_faces(..., machine_placement=...)` accepts
  that same machine transform. The 3D full-part mesh projects all
  authoritative FoldState panels/bend zones to machine
  `s=-Y_m, depth=X_m, z=Z_m`, without applying an extra local pose.
- The 3D orbit anchor is the fixed machine die datum (origin), not the
  sheet centroid.
- A selected DFS candidate supplies checked sampled loading
  orientations and S/Z positions for its entire prefix.
- For a manual/canonical order with an enabled gauge but no selected
  candidate, `loading_orientation_preview.py` computes deterministic
  operator-first 180° suggestions using an *isolated* FoldState.
  These suggestions are strictly `ERGONOMIC_ONLY_NOT_CAD_VALIDATED`:
  **they are not approved gauge contacts or collision checks**.
- The active GUI label states which side is operator (-s, left) and
  gauge (+s, right), whether the sheet is reversed, and the evidence.
- Motion trace `RECORDING_STARTED` includes selected order, per-bend
  orientation and evidence provenance. Each `MACHINE_SECTION_3D`
  event also records the active bend orientation and source.
- The partial-prefix Full Cycle stops after the last verified sampled
  bend has fully retracted its punch, before transferring into
  unchecked remaining bends.

## Regression tests

- `tests.test_loading_orientation_preview`: 800 mm operator side,
  isolation of live FoldState, explicit unverified evidence.
- `tests.test_preview_mesh_3d`: 3D surfaces actually rotate in machine
  space by 180°, ignoring an extra legacy local pose.
- `tests.test_gui_experiment_integration`: machine-pose usage for both
  views and selected sequence source.

## Test in FreeCAD (required)

1. Install this experiment branch cleanly. Restart FreeCAD to clear
   previously imported Python modules; confirm the loaded source/build
   fingerprint using the motion trace.
2. Load the 10-bend reference part with actual tooling and enabled
   Backgauge.FCStd.
3. In **2D варианты**, select the candidate using
   `REVERSE_MACHINE_Z_180` on B01. In the GUI verify that the operator
   (-s) side is long and rear gauge (+s) side short.
4. Change tabs 2.5D ↔ 3D and switch Bend Only ↔ Full Cycle: the part
   must remain in the *same actual machine-facing orientation*.
5. In Full Cycle, check the transfer B01 → B03 and B04 → B10. A flip
   may be physically necessary; it must remain a continuous rigid
   transport, not a mirrored drawing or instantaneous jump.
6. Play only the partial candidate: playback must stop after final
   `RETRACT` instead of performing unverified remaining bends.
7. Export motion trace JSONL and search diagnostics JSON. Compare
   `loading_orientation_by_bend`, per-frame `reverse_axis`,
   `selected_planar_prefix`, physical continuity and collisions.

## Safety and remaining work

- The manually suggested orientation is *ergonomic only*.
  If the rear stop cannot physically contact the shorter side, or
  the punch/die collides, the setup must remain UNVERIFIED.
- Search-selected reverse orientations still require independent native
  3D bend trajectory validation and swept-path checks.
- Existing D6.3.4 3D trajectory checker does **not** certify reverse
  orientations; the GUI explicitly reports UNVERIFIED for that mode.
- Kinematic preview does not demonstrate real backgauge feed/retract,
  finger support or physical feasibility of all handling manoeuvres.
- Performance on the 10-bend model must be measured from an actual
  FreeCAD run; headless CI tests only establish contracts.
