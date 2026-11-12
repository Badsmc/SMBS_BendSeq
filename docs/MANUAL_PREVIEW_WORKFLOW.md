# PressBrakeSim: Manual preview vs. collision validation

## FreeCAD-native Tasks integration

All SMBS controls now open in the **native FreeCAD Задачи / Tasks tab**,
via `FreeCADGui.Control.showDialog(...)`, rather than a separate
`PressBrakeSim TaskPanel` QDockWidget. The independent MDI scene
continues to display the 2.5D, 3D and flat-pattern views.

The native Tasks page holds Деталь, Оснастка, Симуляция, 2D варианты,
Manual and Настройки, plus embedded specialist forms and progress/results.
The normal toolbar actions switch pages in that same Task panel; there
is no second addon dock, modal tool dialog or duplicated playback timer.

**Закрыть** or Esc closes only the native Task controls, not the selected
SheetMetal model or SMBS viewing session. To reopen the controls, click
**Показать панель управления** in the viewer or an SMBS toolbar action.
The same in-memory tool selections, order and Manual verification results
remain attached to the viewer. Closing the viewer itself cleans up the
Tasks page and document observer. SMBS never forcibly closes an unrelated
Sketcher or PartDesign task; finish it first.

**Acceptance in FreeCAD 1.1.x:** restart FreeCAD after installing this
branch; click Симуляция and confirm the controls appear in the *existing*
Задачи tab, not in a new PressBrakeSim TaskPanel dock. Switch to Оснастка,
Manual, Настройки and Задний упор; close/reopen Tasks twice and confirm
the selected punch/die, sequence, slider, results and mouse pan still
work. Check for Qt widget deletion errors in Report View.

The automated tests model FreeCAD's task lifecycle without Qt; actual
visual rendering in FreeCAD still requires an interactive test.

## User-facing workflow

1. Open **Деталь** and load the selected SheetMetal unfold in FreeCAD.
2. Choose **Manual** on the workbench toolbar; the 2.5D viewer switches
   to **Развёртка · Порядок** and enables bend-line selection.
3. Click the bend lines in the desired order or reorder the operations with
   **Move Up / Move Down / Restore**.
4. Click **Симулировать** to watch the selected order immediately.
   The main viewer switches to **2.5D · Гибка**, the native FreeCAD Tasks view changes to
   **Симуляция**, and playback starts in **Bend Only** mode. **No collision
   validation or automatic sequence search runs.** This is explicitly
   `UNVERIFIED`.
5. Use **Reset**, **|<<**, **Play/Pause/Resume**, **>>|**, **Fit**, and the angle
   slider to inspect the sequence. The punch stroke follows the coupled
   punch/sheet/die kinematics while the slider changes the bend angle.
6. To check the order use **Проверить**. It first runs sampled Stage A/B
   (2D) and then native D6.3.4 (3D) for each selected bend in manual order.
   Each bend uses the **selected real FCStd punch and die**, their chosen
   real or virtual length and previous signed bends. Punch-vs-die collision
   is also checked at every sampled ram height. The backgauge remains
   visual-only and is not checked.
   **The result appears directly in Manual**: overall verdict plus every
   tested bend's status, native reason and tool-pair check. **There is no
   standalone JSON download dialog.** Manual results are embedded in
   `SMBS_Data / SessionJSON` in the *same* FreeCAD document.
   - If the FreeCAD document already has an FCStd filename, Verify calls
     native `document.save()`, committing the included report in-place.
   - If the document is still unnamed, Verify stages the report inside the
     document. Use FreeCAD **Ctrl+S** to name/save the first FCStd file.
   - Reopening an FCStd restores stored checks as **historical only**:
     `HISTORICAL / UNVERIFIED`, never a live collision clearance.
   - Every bend now also shows a **RETRACT** column (`ПРОЙДЕН`,
     `КОЛЛИЗИЯ`, `УСЛОВНО`, `НЕ ПРОВЕРЕН`, or `НЕ ВЫПОЛНЕН`).
     The Manual results below the table show the beginning and final
     **Machine Z (mm)** of the punch retraction, the sampled phase
     evidence and the reason for any non-clear result.
     Green sampled clearance is allowed only when the RETRACT phase
     sampled **both** its planned bottom and upper endpoints.

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
- **Shared RETRACT geometry:** `ram_cycle_geometry.ram_cycle_positions()`
  is the one FreeCAD-independent reference for the simulation
  `PressCycleController` and native D6.3.4 `AdaptiveTrajectoryValidator3D`.
  Instead of former fixed native endpoints +50 (APPROACH) and +40 mm
  (RETRACT), both now calculate `upper_z = contact_z +
  cycle_config.initial_clearance_mm`, and `retract_start_z = bottom_z`.
  Manual Verify passes the same `CycleConfig.initial_clearance_mm`
  into `AdaptiveSamplingConfig.initial_clearance_mm`.
- Each native `BendTrajectoryResult` includes `ram_cycle_positions_mm`
  (planned machine Z positions). The Manual checker stores a compact
  `retract` object in **each** `manual_3d_validation.steps[]` item:
  `start_z_mm`, `end_z_mm`, `contact_z_mm`, `bottom_z_mm`,
  `initial_clearance_mm`, `sampled_start_z_mm`,
  `sampled_end_z_mm`, `end_position_sampled`, `sample_count`,
  `status`, `phase_evidence`, max overlaps and a concise reason.
  If the trajectory aborts during an earlier phase, the unexecuted
  RETRACT reports `NOT_EVALUATED`; **planned coordinates are not
  presented as measured clearance**. Endpoint mismatch, missing
  samples or native uncertainty remain `UNVERIFIED`.
- The FCStd `SMBS_Data/SessionJSON` attachment includes these per-bend
  retract records with the rest of the bounded native 3D manual report.
  Reopened evidence remains `HISTORICAL / UNVERIFIED`: no automatic
  replay of an old safe state.

- The result is first shown in `manual_check_summary` and
  `manual_check_details`, then staged by
  `_persist_manual_check_to_fcstd()` through
  `fcstd_session_store.build_snapshot()` and `write_included_json()`.
  The snapshot preserves selected tools, per-bend D6 phase evidence,
  native punch-die pair diagnostics and Stage A/B findings.
- Native FCStd save serializes the existing `App::PropertyFileIncluded`
  attachment. The Manual button never opens a JSON file chooser.
  Storage failure is explicitly reported as **НЕ СОХРАНЕНО В FCStd**;
  unsaved documents report that Ctrl+S is required.
- Manual precondition failures are `UNVERIFIED` and are included in the
  same document, rather than being silently skipped. Stored diagnostics
  remain `HISTORICAL_ONLY_RECHECK_REQUIRED` on reopen, and a later
  Ctrl+S preserves their per-bend details unless overwritten by a new check.
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
validation on a real workpiece.
`tests/test_fcstd_embedded_session.py` covers embedded per-bend native
diagnostics, historical re-save, checksums and size limits.
`tests/test_manual_check_embedded_ui_contract.py` guards against
a standalone JSON download reappearing on Manual Verify.
`.github/workflows/gui-icon-contract.yml` runs these, GUI contracts
and Python compilation.

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
  task panel visibly displays progress, final status and per-bend reasons.
  **No JSON save dialog must appear.**
- Reopen the original FCStd and inspect `SMBS_Data / SessionJSON`:
  `diagnostics.manual.manual_3d_validation.steps` must contain
  the actual statuses, selected tool IDs and D6/punch-die diagnostics.
  The UI must label restored evidence historical, not live.
- Repeat with an unsaved FreeCAD document: Manual Verify should stage
  the attachment without asking for a JSON filename; Ctrl+S should
  save the entire document as FCStd.
- Set `initial_clearance_mm` to a value other than the default, run
  Manual Verify on a valid native FCStd setup, then run Full Cycle.
  For each bend, compare native RETRACT `end_z_mm` against the GUI
  `upper_z_mm`; compare native bottom/start Z against the simulated
  `bottom_z_mm`. They must agree for the same selected tools.
- Create a physical collision during BENDING: the step must be
  `COLLISION` and its RETRACT column `НЕ ВЫПОЛНЕН`, not green.
  Test a RETRACT-only collision: the step must be `COLLISION`, with
  RETRACT `КОЛЛИЗИЯ`. Test a conditional end-zone-only RETRACT:
  `УСЛОВНО` must never turn into a green unconditional PASS.
- Save and reopen the FCStd, inspect
  `diagnostics.manual.manual_3d_validation.steps[*].retract`.
  Actual end Z, sample count and phase status must survive unchanged.

- Use a real tool shorter than the bend. With virtual length OFF, Stage A
  must reject its length. With virtual length ON, the report must label
  the synthetic axial geometry and never claim source-CAD span coverage.
- Intentionally create a punch/die-sheet or punch-vs-die intersection:
  `COLLISION` must identify the affected bend and part of the trajectory.
- Enable conditional punch end-zone mode and verify that end-only overlaps
  remain `CONDITIONAL`; die/tool-tool overlap is never waived.
- Drag the 2.5D view in both directions with middle mouse or Ctrl+LMB,
  then check wheel zoom, Fit, and left-drag 3D orbit.
