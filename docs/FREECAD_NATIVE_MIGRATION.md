# FreeCAD native integration migration

Base: feature/pdf-task-export; do not merge to main until the acceptance checks pass.

## Implemented in this branch
- Let FreeCAD control toolbar icon size, text style, visibility and placement.
- Register the workbench icon directory using Gui.addIconPath.
- Ignore a saved-document resume callback if the document was closed before it ran.
- Retire native task event filters and disconnect owner callbacks when the task detaches; keep Hide/HideToParent distinct from actual task destruction.
- Add a native Preferences > BendSeq > Mesh 3D page. Migrate legacy Mesh time budget and penetration volume into BaseApp/Preferences/Mod/BendSeq/Mesh without deleting old QSettings. Task controls and the Mesh check read/write the same parameters. Grouped view and other preferences remain in their existing stores for now.

## Remaining work
- Complete native TaskPanel ownership audit, including DXF, Tooling, FAST, PDF and Mesh; use native Qt layouts and avoid altering unrelated FreeCAD tasks. Review reparenting of persistent widgets and document switching.
- Separate global preferences, tooling inventory and document-specific settings; migrate remaining QSettings values with compatibility tests.
- Localize all user-facing strings with Qt .ts/.qm and FreeCAD's language selection; retain existing catalog until parity. The new Mesh preferences form currently has English source strings.
- Audit fixed widget sizes, custom style sheets, SVG resources, HiDPI and native units. Keep intentional geometry/collision visualization colors.
- Move to the modern freecad/<namespace>/ layout only with compatibility shims and FCStd restore tests; retain command IDs and old serialization import paths.
- Review GUI imports and workbench initialization, threaded work, timers and document observers. Do not change bend orientation, FAST/Mesh geometry, tool inventory or PDF output semantics as part of UI refactoring.
- Verify manifest, licenses, asset provenance, dependencies and Addon Index preflight. See ADDON_INDEX_SUBMISSION.md.

## Acceptance checklist (not yet executed)
1. FreeCAD 1.1.x Windows, macOS and Linux: clean install, activate all commands, switch workbenches, check no unrelated toolbar or theme changes.
2. Minimize/restore FreeCAD 10 times with an active SMBS task, including FAST/Mesh and simulation; controls and session remain intact. Close/reopen Tasks 20 times; no duplicate callbacks or stale Qt wrappers.
3. Close or switch documents during queued restore; no traceback, no task opened for the wrong document. Switch to Sketcher/PartDesign with an active task; never close an unrelated native edit.
4. Preferences: migrate existing mesh defaults; edit values in native Preferences, open Mesh Tasks, confirm values; run with changed values, reopen Preferences, restart FreeCAD and confirm persistence. Test invalid manually edited parameters and Cancel.
5. Exercise DXF, SheetMetal unfold, manual and staged bends, FAST, exact sequence orientation and Mesh grouping, PDF export and saved FCStd restore against baseline. Compare sequence IDs, orientations, collision results and project data; do not infer physical certification.
6. Test light/dark and multiple DPI/icon-size preferences, English/Russian; record failures and FreeCAD version.
7. Run syntax checks, headless regression suite and real GUI acceptance before merging.

References:
- https://freecad.github.io/Addon-Academy/Guides/Code/Qt/
- https://freecad.github.io/Addon-Academy/Topics/Addon-Index/Index/Qualities
- https://freecad.github.io/Addon-Academy/Demos/Preferences-Page/
- https://freecad.github.io/Addon-Academy/Guides/Code/Translations/
