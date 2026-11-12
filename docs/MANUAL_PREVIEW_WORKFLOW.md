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
6. To check the order instead, use the separate **Проверить** button in Manual.
   Full automatic physically screened sequence search remains accessible
   from **PressBrakeSim 2.5D > Автопоиск последовательности** in FreeCAD's
   main menu. This is distinct from the toolbar's **Симуляция** action.

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
- The manual slider invokes
  `PressCycleController.set_manual_preview_angle()`, which preserves
  the older park-at-upper behavior of `set_manual_angle()` for other clients.
- In `Bend Only` automatic playback the GUI calls
  `preview_punch_at_current_angle()` after advancing the sequencer.
  The ram position is computed from the model's
  `CoupledAirBendingKinematics.stroke_for_angle()`. The die stays fixed.
- The viewer and trace record `UNVERIFIED`; this does **not** check
  puncture, collision, force, tool clearance, clamp/backgauge contact,
  workpiece transfer, or producibility. A visually smooth preview cannot
  be relied on as an approved press-brake program.

## Verification

`tests/test_press_cycle_manual_preview.py` checks coupled stroke updates,
positive/negative bends, unchanged die transform, bounds errors and
the legacy retract-on-manual-set behavior without FreeCAD.
`.github/workflows/gui-icon-contract.yml` tests GUI command/route
contracts and Python compilation.

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
