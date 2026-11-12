# SMBS GUI/v0.0.1 — FreeCAD interface layout

This document describes the actual layout implemented on `GUI/v0.0.1`
after the manual/tooling/settings refactor. It applies to the **interface**,
not the experimental search or collision algorithms.

## FreeCAD main toolbar / menu

- **Симуляция**: opens the singleton workspace and Task Panel.
- **Деталь**: loads the selected SheetMetal unfold.
- **Оснастка**: selects the **Оснастка** page of the existing Task Panel.
- **Расчёт**: main 2.5D / native sequence search.
- **2D варианты**: sampled 2D sequence candidates.
- **Последовательности**: sequence catalog.
- **Задний упор**: actual backgauge geometry/settings.
- **Manual**: enabled only when a model/unfold is loaded; switches viewer to
  `Развёртка · Порядок`, activates line-by-line bend selection, and
  selects the **Manual** Task Panel tab.
- **Настройки**: selects the **Настройки** page of the existing Task Panel.
- **Библиотека оснастки**: displays the full tooling catalog in a temporary Task Panel page.

## Task Panel: one dock, four permanent pages

**Симуляция** has a single horizontal row:

```text
[Reset] [|<<] [Play <-> Pause] [>>|] [Fit]
[---- current bend angle slider ----]
```

The Play/Pause button shows Play when stopped, Pause while running,
Resume when paused, and uses the existing controller methods. Previous
and Next operate on the same active bend sequence. No duplicate pause
button, cycle-mode block, active bend/angle heading or manual-order controls
remain in this tab.

**Manual** contains:

```text
[Move Up] [Move Down] [Restore Order]
[Отменить линию] [Очистить]
┌──────────────────────────────────────┐
│ Step | Bend | Target | Current | Status │
│              ordered bends           │
└──────────────────────────────────────┘
[Проверить]
```

`Проверить` runs the existing manual Stage A/B validation and JSON export;
its brief label does not reduce scope. No obsolete instruction paragraph
is shown. Re-opening Manual preserves the current manual selection.

## Task Panel page: Оснастка

Tabs:

- **Основные:** Punch combo, Die combo, current tooling status. Changing a
  combo already invokes the associated `currentIndexChanged` selection
  handler — redundant `Select Punch`/`Select Die` buttons are removed.
- **Импорт:** Import Punch, Import Die, Tool Library.

Removed from the UI (but underlying needed alignment functions are retained):
Align Tooling, Reset Tooling Position, Hide/Show Tooling.

## Task Panel page: Настройки

Contains the **entire former Settings Task Panel page**, plus:

- `Режим симуляции`: Bend Only / Full Cycle, Speed, Cycle Config,
  machine-phase and loading orientation status.
- RETRACT conditional search warning.
- Virtual punch/die length equal to bend length (development option).
- Punch end-zone length and optional CONDITIONAL end-zone policy.
- Check current physical pose and check whole bend D6.3.4 (with result).
- Operation weights, backgauge settings and diagnostics.
- JSONL automatic/manual recording and output-folder settings.

## Temporary Task Panel pages

Top-toolbar and in-panel actions reuse the single Task Panel for detailed tools:

- **Задний упор**: CAD backgauge geometry and stop positions.
- **Последовательности**: JSON sequence catalog.
- **2D варианты**: result shortlist and partial sequences.
- **Cycle Config**: detailed kinematic parameters.
- **Tool Library / Библиотека оснастки**: profile list and full tooling inventory.

The toolkit's native file/directory pickers and warning/confirmation
message boxes remain separate system dialogs: they are not persistent
PressBrakeSim application windows.

## Preservation and constraints

- The same QTimer, loaded part, FoldState, tooling selectors, cycle
  controller, collision checks, search and JSON log callbacks are used.
- Settings and tooling widgets are contained in the same persistent Task Panel.
- The Backgauge, Sequence Catalog, 2D search results, Cycle Config and both
  tooling catalogs mount as temporary Task Panel pages. They never call
  `QDialog.exec()` for the main workflow.
- Automatic search checks remain **sampled / experimental** and do not
  imply full machine safety. No collision logic was changed in this GUI
  refactor.
- Only `GUI/v0.0.1` has this layout. The installation branch `handmode`
  remains unchanged.

## Manual FreeCAD 1.1.x acceptance (not covered by headless CI)

1. Open workbench; expect 10 commands; no Qt/Python exception.
2. Without a part: Manual disabled. With a selected Unfold loaded via
   Деталь: Manual enabled.
3. Press Manual: viewer switches to flat unfold; clicking each bend line
   numbers it and updates the Manual tab sequence table. Undo/clear works.
4. Open Manual again: previous selection is preserved.
5. In Simulation: Play toggles Pause/Resume correctly; Reset, Previous,
   Next, Fit and slider work. There is no separate Pause button.
6. Open Оснастка: change punch/die directly from combos in Основные;
   import buttons and library work from Импорт.
7. Open Настройки: all former settings, including mode/speed, RETRACT,
   virtual tool length, end zone, physical tests and JSONL recording work.
8. Open/close every Task Panel page repeatedly without losing the part,
   creating duplicate timers, or triggering deleted-widget errors.
9. From the toolbar, open Backgauge, Sequence Catalog, Tool Library and
   Cycle Config: verify no extra top-level editor window appears.
10. Close temporary pages with their existing Done/Cancel/Close buttons;
    reopening must create a fresh populated page in the same dock.

GitHub Actions validates Python compilation, SVG resources, and the
structural command/layout contract, **not** interactive PySide6/FreeCAD
behavior. Complete those manual acceptance steps before designating the
interface as GUI-verified.
