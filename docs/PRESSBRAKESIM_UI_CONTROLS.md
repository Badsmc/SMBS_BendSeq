## Part button activation — SheetMetal unfold (2026-11-09)

The **Деталь / Part** command is enabled only when a FreeCAD document is active and the current selection resolves to a single SheetMetal unfold (`*_Unfold` with `baseObject`). Selecting the Unfold itself, its source Bend object, or its related unfold sketch may satisfy this requirement; an ambiguous or absent selection does not. Clicking **Деталь** opens the viewer if necessary and loads the selected part.

The command snapshots document and selection **before** changing focus to the MDI viewer / Task Panel. This prevents loading from an empty selection after focus moves away from the FreeCAD model tree.

**Installation note:** FreeCAD stores command registration for the current application session. After updating GUI branch files, restart FreeCAD before checking command availability, especially if an earlier version of the workbench was loaded.

**Live acceptance:** In a restarted FreeCAD instance, select `Body_Unfold` in the model tree and confirm **Деталь** enables; clear selection to confirm it disables; click it and check that the simulation viewer receives the selected model. Verify behavior again with Task Panel open. If FreeCAD suppresses toolbar commands while an active task dialog is shown, that native modal behavior requires a separate UI handling change rather than weakening the selection check.

---

## Viewer navigation (2026-11-09)

- **2.5D viewer:** mouse wheel zooms in/out around the cursor; left-button drag pans. `Fit` restores fit-to-scene scaling.
- **3D viewer:** mouse wheel zooms around the cursor; left-button drag changes orbit yaw and pitch; `Shift` + left-button drag rotates (rolls) about the viewing axis. `Fit` restores 3D zoom; `reset_3d_view()` restores the default camera orientation and zoom.
- These are viewer-only transforms. No SheetMetal geometry, bend angle, sequence or playback controller is modified by camera interactions.
- One animation timer is maintained. Automatic redraws must preserve user zoom and orientation.
- The 3D view remains a projected preview of the full part, **not** an OpenCascade solid viewport. Polygon depth ordering is approximate; crossing surfaces may have limitations.

**Acceptance test in FreeCAD:** zoom 2.5D, pan, switch to 3D, rotate yaw/pitch/roll, zoom, play full cycle while zoomed/rotated, switch tabs repeatedly, verify Fit, close/reopen and confirm the source FreeCAD document geometry remains unchanged.

---

## GUI layout contract — FreeCAD Task Panel (2026-11-09)

The FreeCAD workbench toolbar and menu contain four commands in this order:
**Симуляция**, **Деталь**, **Оснастка**, **Расчёт**. The fourth is intentionally disabled until the automatic sequence solver is qualified.

**Симуляция** opens one MDI viewer window containing only source/status labels and 2.5D / read-only 3D viewer tabs. **Деталь** loads the FreeCAD selection (opening the simulation window if needed). **Оснастка** switches to the tooling tab in the FreeCAD Task Panel. The viewer does not display a duplicate command row or a right-side controls splitter.

FreeCAD's native Task Panel hosts all three controls tabs: **Симуляция**, **Оснастка**, **Настройки**. One shared session, timer, Qt controls and diagnostics recorder are used; no independent simulation engine is created. Closing the MDI viewer closes its Task Panel; closing the Task Panel closes that MDI viewer (never the source document). A second press of **Симуляция** focuses the existing viewer instead of creating a duplicate.

**Verification:** Python structural and Qt tests exist, but a real FreeCAD GUI acceptance run is still required before merging to `main` or releasing `smbs`.

---

# PressBrakeSim 2.5D — UI Controls Contract (v1.0)

Status: **UI layout consolidated in branch `2D`; pending real FreeCAD acceptance.**
Scope: user-visible controls and enablement only. This is not permission to change
`FoldState`, the press cycle, tooling geometry, or collision logic.

## A. Non-negotiable rules

1. **One user intent = one control.** Do not create an additional button to work
   around an algorithmic defect. Fix the algorithm independently.
2. **One automatic playback entry:** a single `Play All`, controlled by `Mode`.
   **One Qt timer** drives the simulation regardless of mode.
3. **One bend-angle input:** the existing slider. Source target angles are
   read-only, acquired from FreeCAD SheetMetal.
4. **One operation selection:** click the row in the bend table.
5. **Step navigation is distinct** from selecting an operation:
   `Previous Step` / `Next Step` adjust the completed sequential prefix,
   whereas choosing a table row selects an active bend without claiming it was
   performed. Do not conflate these.
6. **No fabricated full-cycle results** for nonparallel axes or unverified tool
   contact. The automatic `Bend Only` preview is an explicitly different mode
   and never claims press-machine collision validation.
7. **Reset** resets the simulated bend state; it does NOT delete source
   SheetMetal or tool-library content. UI values derive from controller state.
8. All warnings must name a concrete reason and expose the recording log path.
9. Any new UI action must be added here first with a documented need, distinct
   effect, enabled/disabled rules, and a regression test. Do not add a second
   button for an existing action.

## B. Main workspace

Toolbar/menu:
- `Simulation`: enabled only with a valid selected SheetMetal part/face per
  existing command preconditions.
- `Tool Library`: enabled independently of a loaded workpiece.

Viewer tabs:
- `2.5D · Гибка`: part and punch/die visualization.
- `3D · Просмотр`: fixed isometric whole-part fold preview, no punch or die.

Loaded-part action:
- `Деталь` (Load Part): always accessible in the opened workspace; opens a
  fresh session from the selected valid part. Shows a diagnostic if unavailable.

### Simulation tab: KEEP

| Control | Enabled | Action |
|---|---|---|
| Mode: `Full Cycle` / `Bend Only` | when panel is open | Select simulation semantics; not a separate playback trigger |
| Speed (°/s) | when panel is open | Configure bend animation rate; not sheet geometry |
| Cycle Config | when panel is open | Advanced press stroke, timing and kinematic parameters |
| `Play All` | valid part loaded; blocked after latched invalid Full Cycle until reset/mode change | Start automatic playback in selected mode |
| `Pause / Resume` | valid part in PLAYING / PAUSED | Freeze/resume current simulation state |
| `Reset` | valid part loaded | Return all bends to 0 and controller to IDLE |
| `Fit` | visual scene exists | Fit current scene; no automatic camera changes during active playback |
| Angle slider | valid part loaded, manual operation | Adjust current signed bend from 0 to target angle |
| `Previous Step` / `Next Step` | valid part loaded, valid sequential state | Navigate completed bend prefix without changing operation order |
| Operations table | valid part loaded | Select active bend; display target/current/completion |
| `Move Up` / `Move Down` | valid part with >1 bend; controller allows editing | Edit sequence order, preserving bend IDs |
| `Restore Order` | order differs from canonical, controller allows editing | Restore original order |

**Full Cycle** runs the punch/die cycle but stops on an unsupported workpiece
pose. **Bend Only** animates FoldState angles automatically without verifying
machine placement. Both use exactly the same `Play All` and Qt timer.

### Simulation tab: REMOVE (duplicate controls)

- `<` / `>` 5° angle buttons: replaced by continuous angle slider.
- `>| ` set current angle to target: redundant with slider and `Next Step`.
- `◀` / `▶` next/previous **bend selection**: redundant with selecting a table row.
- `Авто гибка (без пресса)`: redundant with `Mode=Bend Only` + `Play All`.
- Do not reintroduce a second automatic-play or another parallel angle writer.

### Tooling tab: KEEP

- `Import Punch`, `Import Die`, `Tool Library`: existing library operations
  (a future cleanup may consolidate the import actions into Tool Library, but
  do not modify proven import flow without acceptance).
- Punch/Die selectors and explicit Select actions: retain pending verification
  of existing selection flow.
- `Align Tooling`, `Reset Tooling Position`, `Hide/Show Tooling`: distinct
  positioning/visibility actions; keep in Tooling, not simulation playback.
- Tooling status text: always show actual selected profiles and validity.

### Settings tab: KEEP diagnostic-only tools

- Interface check and diagnostic output.
- JSONL checkbox (automatic record on Play All).
- `Начать запись JSONL`, `Завершить и создать JSON`.
- `Открыть папку записей`, `Выбрать папку`.
- Recording is *not* a prerequisite for animation; recording failures must not
  silently disable Play All.

## C. Interaction and states

| State | Play All | Pause/Resume | Slider | Reorder |
|---|---|---|---|---|
| No part | disabled | disabled | disabled | disabled |
| IDLE, part loaded | enabled | disabled | enabled | enabled if >1 bend |
| PLAYING | running | Pause | manual edit stops playback | disabled by controller |
| PAUSED | can restart/resume valid state | Resume | edit is manual | controller-specific |
| COMPLETED | enabled (controller resets and replays) | disabled | enabled | controller-specific |
| FULL_CYCLE_UNSUPPORTED | disallow Full Cycle continuation | do not resume broken pose | inspect manually or reset | controller-specific |

A `NONPARALLEL_BEND_AXES` or `DIE_SUPPORT_MISSING` guard is a geometric
blocker for Full Cycle, not a reason to hide or disable Bend Only.

## D. Acceptance tests

1. Open workspace, exactly two viewer tabs and three control tabs.
2. **One** Play All button; **one** Qt timer; no duplicate Auto Geometry button.
3. With `Bend Only` selected, one Play All click advances angles on successive
   Qt timer ticks without user clicking Next Step or modifying the slider.
4. With `Full Cycle` selected, one Play All click begins APPROACH and respects
   explicit unsupported-pose stop; no false IDENTITY continuation.
5. Pause/Resume must freeze/continue the exact state; Reset returns to IDLE.
6. Slider changes angle in manual mode, preserving sign; table row selection
   does not perform a bend.
7. Verify no unexpected `fitInView` or camera recenter on each playing tick.
8. Tool Library/import functions remain available and unchanged.
9. JSONL test trace should contain multiple FRAME rows when Play All runs;
   a trace with one IDLE frame and RESET only does not qualify as an autoplay
   test.
10. Run the GUI test `tests/test_pressbrake_workspace_gui.py` inside FreeCAD
    and verify the current controls manually before marking this UI contract
    as VERIFIED.

## E. Change control

Any additional button requires a specific use case not already covered by the
table, a distinct state transition, tests, and a document revision. Otherwise
fix existing code rather than adding controls.

