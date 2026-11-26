# FreeCAD native integration migration

Base: feature/pdf-task-export. Do not merge into main until acceptance checks pass.

## Implemented
- Native FreeCAD toolbar sizing, labels and icon path. Qt layout size hints replace most fixed playback and Task button sizes.
- Native Gui.Control TaskDialog, explicit transaction/deleted-document lifetime policy, detach guard disposal, owner disconnect, and no action on temporary Hide/HideToParent.
- Native Preferences > BendSeq > Mesh 3D (time budget and penetration volume), including legacy migration. Task controls and Mesh check share these parameters.
- Typed ParamGet adapter for diagnostics, grouped Mesh view, backgauge configuration, operation weight defaults, sequence catalog paths and PDF export settings. Existing QSettings values are migrated once; old storage is not deleted. Restored FCStd operation weights no longer overwrite global defaults.
- Native Qt theme for tooling status and tabs.
- Register Qt language path, generate BendSeq_ru.ts from all English/Russian catalog keys, and use Qt translations if available with JSON fallback. Qt 6 lrelease is a development tool, not an addon runtime dependency.
- New headless tests for preference migration and task lifecycle, including repeated opening, window state changes, and ownership checks.

## Remaining release gates
- Compile and ship the QM resource; verify both Qt Designer preference translations and Python UI translation in FreeCAD. Do not remove JSON fallback until parity is verified.
- Native preference page currently exposes the Mesh numeric defaults; other migrated settings are still edited through their existing contextual controls.
- Audit additional dialogs, especially DXF graphical selection and specialist forms. Retain modal forms for interactions that genuinely require them; never close another workbench's task.
- The legacy Init.py/InitGui.py layout is permitted by Addon Index policy but modern freecad/<namespace>/ is preferred. Retain existing paths until clean installation and FCStd compatibility can be validated; no sys.path manipulation.
- Review remaining hard-coded units and sizes, threading, timers, observers and document ownership. Keep command IDs and old serialization import paths.
- Verify manifest, licenses, asset provenance and Addon Index preflight; see ADDON_INDEX_SUBMISSION.md.

## Acceptance checklist (real FreeCAD run still required)
1. Clean install and activate in FreeCAD 1.1.x on Windows, macOS and Linux; switch workbenches, verify no unrelated toolbar or theme changes.
2. Minimize/restore FreeCAD 10 times with Tasks active, including FAST/Mesh and simulation; controls and session remain intact. Close/reopen Tasks 20 times; no duplicate callbacks or stale Qt wrappers.
3. Close/switch documents during queued restore; no traceback or wrong document. Switch to Sketcher/PartDesign; never close unrelated native edits.
4. Verify migration and restart persistence for Mesh, PDF, diagnostics, weights, backgauge and catalog. Edit Mesh defaults in FreeCAD Preferences, then open Mesh Tasks and verify both values; confirm Cancel leaves values unchanged.
5. Exercise DXF, SheetMetal unfold, manual/staged bends, FAST, exact sequence orientation, Mesh groups, PDF and FCStd reopen against baseline; compare operation IDs, orientation, collisions and project data. No claim of continuous collision certification.
6. Verify light/dark theme, several DPI/icon sizes, English/Russian. Check all UI text and compiled native translations.
7. Run syntax checks, headless regressions, Addon preflight and real GUI acceptance before merging.

References:
- https://freecad.github.io/Addon-Academy/Guides/Code/Qt/
- https://freecad.github.io/Addon-Academy/Topics/Addon-Index/Index/Qualities
- https://freecad.github.io/Addon-Academy/Demos/Preferences-Page/
- https://freecad.github.io/Addon-Academy/Guides/Code/Translations/
