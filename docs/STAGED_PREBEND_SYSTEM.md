# SMBS: staged pre-bending and finishing (experimental)

Original development branch: `manualmod.ui`. The pre-bend system was ported
from `experiment/prebend-rebend`.

Status: **operation-level kinematics, GUI, and FCStd persistence are
implemented; geometric and manufacturing validation of staged bends is not
certified**.

## Purpose

Some parts cannot be formed in a single pass along the selected bend line.
For physical line `B03` with a final angle of `+90°`, an intermediate operation
`B03.1 → +35°` can be programmed, followed by `B01`, `B02`, and `B04`, before
returning to the same line for `B03.2: +35° → +90°`.

Operations `B03.1` and `B03.2` **are not two different bend lines**: the geometry,
axis, BendZone, and identifier in `FoldState` remain `B03`. The final angle comes
from the original unfold and does not change. The intermediate angle is
**absolute**, not an increment added to the current angle.

## FreeCAD GUI usage

1. Load a SheetMetal unfold or a prepared DXF session.
2. In the manual bend order table, click `+` in the Pre-bend column for the
   relevant row.
3. Choose an intermediate angle in the dialog (for example, `+35°` for a final
   angle of `+90°`, or `-30°` for a final angle of `-90°`).
4. In **the same dialog**, drag the only active row, `B03.1 · pre-bend`,
   among the other fixed operations. `B03.2 · finish` remains in place; the
   pre-bend cannot follow the finishing operation.
5. Confirm. The main table shows both operations and the chosen order.
   Dragging and the ↑/↓ buttons are also available in the main table,
   subject to the same pre-bend-before-finish constraint.
6. Click Reset, then Play or Next to review the sequence. The intermediate
   angle of the physical bend line is retained between operations.
7. To remove an incorrectly defined pre-bend, click **×** next to **Edit**
   in row `B03.1`, or open **Edit** and click the separate button to remove
   the pre-bend and restore a normal bend. Confirm the removal.
   Operation `B03.1` disappears and `B03.2` becomes an ordinary `B03`
   at the finishing operation's position, with the original final angle.
   Other operations and their positions remain unchanged.
   Obsolete checks are cleared; a new pre-bend can be added with **+**.
   If playback has already run, click **Reset** before editing.
8. Save the FreeCAD FCStd. Embedded SMBS JSON contains
   `order.operation_steps`. Operations are restored on reopening if the
   geometry matches.

Physical bends remain unambiguous: `order.bend_ids` contains unique IDs,
whereas `order.operation_steps` is a separate list of playback operations.

## States and transitions

- `SINGLE`: an ordinary single-pass bend (for example, `B02 → +60°`).
- `PREBEND`: a preliminary bend to any permitted intermediate absolute angle
  (for example, `B03.1 → +35°`).
- `FINISH`: the finishing operation on the same line, from a known intermediate
  angle to the original final angle (for example, `B03.2 → +90°`).

The controller maintains a separate cursor for completed operations and uses
the original `FoldState` as the single source of angles. Next commits the
**operation's** target angle; Previous restores the corresponding operation
prefix. In the machine cycle, an operation is committed after `RETRACT`,
not immediately upon reaching the target angle.

The finishing increment is `Δangle = |final| - |intermediate|`.
Tool changes and scene placement use the physical bend line; no new geometric
edge is created.

## Independent 0°/180° orientation for each operation

- `B03.1` and `B03.2` have **separate** checkboxes. For example,
  `B03.1 = 180°` and `B03.2 = 0°`, or vice versa.
- Values are stored by **operation_id**. Physical line `B03` remains shared
  in `FoldState`, but workpiece orientation relative to the tooling is
  applied independently for each pass.
- `PressCycleController` and `TRANSFER` use the current or next
  operation_id, retaining backward compatibility with ordinary single bends.
- Editing a row, reordering operations, reopening the panel, and restoring
  an FCStd must not merge the checkboxes.
- Embedded JSON stores `order.operation_orientation_overrides` separately
  from the older `order.manual_orientation_overrides`. Older saves without
  the new field remain loadable.
- Legacy D6/OCC verification cannot distinguish operations on one physical
  bend with different orientations and must not report PASS for these
  sequences: the result is `UNVERIFIED`.

## Invariants

- `0 < |intermediate| < |final|`, and both angles have the same sign.
- Each physical bend has exactly one finishing operation.
- Currently, **one** preliminary operation per physical bend line is supported.
  Multiple physical bends may have their own pre-bends.
- `PREBEND` must precede its corresponding `FINISH`.
- Reordering is allowed only with a flat workpiece and stopped playback.
- Reordering original bend lines through the old text field is prohibited
  when staged operations exist; use the operation table.
- The old single-pass `order.bend_ids` is not a reliable operation sequence.

## Safety and first-version limitations

- This is a **kinematic simulation** based on the existing `FoldState` and
  `PressCycleController`. It displays accumulated angles and existing
  press/transfer phases.
- The legacy OCC validator expects each physical bend **once**. Full
  validation for pre-bend plans is **disabled** with an explicit
  `UNVERIFIED` result; previous check results are cleared.
- Current-frame checks do not establish feasibility of the entire multi-stage
  sequence. A new operation-level replay is required, including all
  `APPROACH/CONTACT/BENDING/RETRACT/TRANSFER` phases and intermediate geometry
  before the next pass.
- 0°/180° orientation is now specified **per operation**. Some backgauge
  settings still apply to the physical bend line rather than individual
  operations; contact computation for separate passes needs further work.
- Continuous real-world collisions, workpiece retention, sufficient force,
  and feasibility of the partial bend itself are NOT verified.
  The unloaded final shape does not include springback.
- Historically, this feature was developed in `manualmod.ui`; the original
  changes did not directly modify `ManualMode` or `main`.

## Regression and manual verification

Headless: `python -m unittest discover -s tests -v`.
The original development workflow ran these tests when pushing to `manualmod.ui`.

Manually verify in FreeCAD 1.1.x:

1. The `+` button, positive and negative angles, editing, and removal.
2. Dragging B03.1 across B01/B02; rejecting a move of B03.2 before B03.1,
   including restoring rows after an error.
3. Shape and continuous animation from 0° to 35°, followed by B01/B02,
   then B03 from 35° to 90°; separate Next/Previous/Reset/Play actions.
4. Real selected punch and die, backgauge placement, Coin3D, fixed camera,
   and no false PASS.
5. Closing and reopening the panel, saving and restoring FCStd.
6. Loading an ordinary part without staged bends: unchanged manual behavior.

Key files:
- `freecad/bendseq/core/kinematics2d/staged_bends.py`
- `freecad/bendseq/core/kinematics2d/press_cycle.py`
- `freecad/bendseq/commands/pressbrake_2d.py`
- `freecad/bendseq/integration/fcstd_session_store.py`
- Historical tests: `tests/test_staged_bends.py` and `tests/test_fcstd_staged_operations.py`.
