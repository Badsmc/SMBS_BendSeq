# GUI module boundaries (branch `gui`)

## Goal

Keep FreeCAD command registration, presentation, user interaction, diagnostics and 2.5D simulation calculations separate. Preserve the existing runtime behavior during extraction. This branch is for GUI development, **not** for a production installer; merge validated work through `main`, then qualify `smbs`.

## Ownership map

| Area | Current owner | Next refactor target |
| --- | --- | --- |
| FreeCAD workbench/toolbar | `InitGui.py`, `freecad_smbs/InitGui.py` | One registration path only |
| User entry commands | `freecad_smbs/commands/` | Commands must only validate and open GUI/controllers |
| Interactive 2.5D panel | `freecad_smbs/commands/pressbrake_2d.py` | Move `_PreviewPanel` to `ui/pressbrake_panel.py`; preserve import shim during migration |
| Part session / SheetMetal selection | `pressbrake_2d.py` and `integration/` | Move `LoadedPartSession` and selection/build to `integration/part_session.py` after contract tests |
| Presentation / Qt widgets | `freecad_smbs/ui/` | Break panel into controls, canvas and playback coordinator |
| File diagnostics | `freecad_smbs/ui/diagnostics_logging.py` | Extracted; independent of FreeCAD/Qt |
| 2D/2.5D kinematics and tooling | `smbs_core/kinematics2d/` | Keep in `2d` ownership; GUI consumes interfaces only |
| Geometry, collision, search | `smbs_core/` | Keep in `core` / `3d` ownership; no duplicated GUI solver |

## Safe migration sequence

1. (Started) Document current command names, menu order, Qt object names, interaction behavior and signal contracts.
2. (Completed for diagnostics and Qt constants) Extract stateless helpers first with headless unit tests; keep existing import names for callers.
3. (Extracted; validation pending) Extract session data/selection logic with deterministic model fixtures and session preservation tests.
4. Split `_PreviewPanel` into canvas, controls, and playback coordinator; keep exactly one timer and one angle controller.
5. Remove obsolete commands only after checking dynamic imports and recorded FreeCAD GUI acceptance; do **not** delete a file solely because its command is not shown in the toolbar.
6. Run `python -m unittest tests.test_gui_diagnostics` and the existing GUI suite in an actual FreeCAD Qt session. Explicitly verify start/restart, tool library, load part, Play/Pause/Reset, slider, 2D/3D tabs, JSONL trace creation, and no duplicate workbench.
7. After FreeCAD verification, propose a reviewed GUI-to-`main` PR. The `smbs` install branch stays untouched until release gates pass.

## Acceptance rules

- One workbench and one command registration per name; no legacy toolbar resurrection.
- No GUI-created independent geometry or solver paths.
- No exception swallowed where the user requires visible error; diagnostic fallback must not crash FreeCAD.
- No auto-switch of sheet bend sign, angle or part pose when a physical state is invalid.
- No additions to `smbs` until live FreeCAD checks pass.

## Current limitation

The large 2.5D panel has been moved into \`freecad_smbs/ui/pressbrake_panel.py\` without splitting its internal Qt controller/rendering methods yet. Its original import path stays supported. Headless structural checks are added in \`tests/test_gui_module_boundaries.py\`. **FreeCAD GUI behavioral verification is not yet complete.** The next stage is to decompose the panel internally, keeping exactly one timer and preserving all viewer object names.
