# PressBrakeSim: Manual preview vs. collision validation

## User-facing workflow

1. Open **Деталь** and load the selected SheetMetal unfold in FreeCAD.
2. Choose **Manual** on the workbench toolbar; the 2.5D viewer switches
   to **Развёртка · Порядок** and enables bend-line selection.
3. Click the bend lines in the desired order or reorder the operations with
   **Move Up / Move Down / Restore**.
4. Click **Симулировать** to watch the selected order immediately.
   The main viewer switches to **2.5D · Гибка**, the Tasks dock changes to
   **Симуляция**, and playback starts in **Bend Only** mode. **No collision
   validation or automatic sequence search runs.** This is explicitly
   `UNVERIFIED`.
5. Use **Reset**, **|<<**, **Play/Pause/Resume**, **>>|**, **Fit**, and the angle
   slider to inspect the sequence. The punch stroke follows the coupled
   punch/sheet/die kinematics while the slider changes the bend angle.
6. To check the order use **Проверить**. It first runs sampled Stage A/B
   (2D) and then native D6.3.4 (3D) for each selected bend in manual order.
   Each bend is simulated with the **selected real FCStd punch and die**,
   their chosen real or virtual length, and all previously formed signed
   bends. It also checks punch-vs-die collision at each sampled ram height.
   The backgauge remains visual-only and is not tested.
7. Full automatic physically screened search remains accessible through
   **PressBrakeSim 2.5D > Автопоиск последовательности** in the FreeCAD
   menu, separate from the toolbar **Симуляция** action.

## Partial manual ordering

If only the first `N` bend lines were explicitly selected in Manual,
**Симулировать** plays that prefix and stops. The remaining bends are *not*
silently treated as a valid manually specified suffix. A current order
created through Move Up / Move Down, without an explicit selection prefix,
plays as the entire current order.

## Technical contract

- The six-toolbar-action contract is unchanged except that the position
  formerly occupied by `SMBS_Calculate` now uses existing
  `SMBS_PressBrake2D`, which only opens the simulation.
  The original `SMBS_Calculate` is still registered and accessible from
  the workbench menu. No search solver code is removed.
- `simulate_manual_order_unverified()` reuses the same live session and
  Qt playback timer; it does not call `calculate_sequence()`,
  `evaluate_manual_planar_order()`, or any physical validator.
- `_validate_export_manual_order()` drives the 2D replay plus
  `manual_physical_validation.check_manual_3d_sequence()`.
  A fresh, isolated `FoldState` applies completed signed bends for each
  operation without overwriting live playback state or source FCStd files.
- Only verified selected **FCStd** punch/die solids qualify for native
  checks; an unverified DXF or STEP profile alone cannot claim 3D clearance.
- The **virtual tool length** checkbox constructs in-memory axial tool
  geometry exactly as long as the active bend; when unchecked, the selected
  source tool's real length is enforced. Multi-section real assemblies are
  incompatible with virtual length.
- The **punch end-zone conditional** option remains explicitly opt-in:
  confirmed end-only overlap is `CONDITIONAL`, never a clean PASS. Neither
  die penetration nor punch-vs-die collision can use this exemption.
- Reverse-loaded 3D poses are not yet supported: verification is
  `UNVERIFIED` for those poses. No real backgauge, frame, clamp, force
  or continuous transfer verification is claimed.
- The manual slider invokes
  `PressCycleController.set_manual_preview_angle()`, which preserves
  the older park-at-upper behavior of `set_manual_angle()` for other clients.
- **Sheet and punch now descend together:** the model computes one
  `stroke_for_angle(abs(current_angle))`. The punch tip is positioned at
  `contact_z - stroke`, and the active workpiece bend arc center is placed
  at `BendZone.outer_radius_mm - stroke`. Both 2.5D and 3D consume the same
  machine-space placement; the die is stationary.
- A manual slider scrub is tracked explicitly by
  `_manual_preview_active`. This prevents `Full Cycle / IDLE` from
  returning the sheet to its flat support plane while the punch moves.
  Reset, step navigation and automatic Full Cycle playback clear the flag.
- In `Bend Only` the view uses the *same* shared support computation rather
  than the old constant `BendZone.outer_radius_mm` that caused the bug.
- In `Bend Only` automatic playback the GUI calls
  `preview_punch_at_current_angle()` after advancing the sequencer.
  The ram position is computed from the model's
  `CoupledAirBendingKinematics.stroke_for_angle()`. The die stays fixed.
- The preview, without explicit Verify, always records `UNVERIFIED`.
  The Verify result is one of `COLLISION`, `CONDITIONAL`,
  `UNVERIFIED` and `SAMPLED_3D_CLEAR_NOT_CONTINUOUS`.
  Even the last status does not establish full-cycle manufacturing safety.
- In the 2.5D (and 3D) viewer, middle-mouse drag or Ctrl+left-mouse drag
  pans the scene horizontally and vertically; the wheel zooms and Fit
  restores the camera. Normal 3D left-drag orbit and flat-view line
  selection are unchanged.

## Verification

`tests/test_press_cycle_manual_preview.py` checks coupled stroke updates,
positive/negative bends, unchanged die transform, bounds errors and
the legacy retract-on-manual-set behavior without FreeCAD.
`tests/test_manual_physical_validation.py` tests selected FCStd identity,
signed history, virtual length, conditional end-zone state, native punch-die
pair sampling, missing evidence, reverse-loading and FoldState nonmutation.
`tests/test_manual_tooling_stage_a.py` tests actual tool-length checks.
All are headless with mocked native geometry; they are **not** an OCC
validation on a real workpiece. `.github/workflows/gui-icon-contract.yml`
tests these and GUI contracts and Python compilation.

**Acceptance required on Windows 11 / FreeCAD 1.1.3:**

- Open a real FCStd unfold with multiple bends, select an arbitrary manual
  order and press Simulate without pressing Validate.
- Confirm that simulation plays only the explicit prefix if incomplete and
  that the punch follows both the slider and Bend Only autoplay.
- Confirm the punch returns to the upper position on Reset/step changes,
  the die never travels, and Manual order is preserved.
- Confirm the toolbar Simulation action never starts automatic search;
  the workbench-menu automatic search remains callable.
- Confirm the UI always marks unverified manual preview as UNVERIFIED.

- With two actual FCStd tools, press Manual **Проверить**; verify the
  full manual order is traversed and JSON records the selected CAD paths,
  each bend's D6 evidence and tool-pair ram sample count.
- Use a real tool shorter than the bend. With virtual length OFF, Stage A
  must reject its length. With virtual length ON, the report must label
  the synthetic axial geometry and never claim source-CAD span coverage.
- Intentionally create a punch/die-sheet or punch-vs-die intersection:
  `COLLISION` must identify the affected bend and part of the trajectory.
- Enable conditional punch end-zone mode and verify that end-only overlaps
  remain `CONDITIONAL`; die/tool-tool overlap is never waived.
- Drag the 2.5D view in both directions with middle mouse or Ctrl+LMB,
  then check wheel zoom, Fit, and left-drag 3D orbit.
