# FreeCAD native integration migration

Base: feature/pdf-task-export. Do not merge into main until acceptance checks pass.

## Implemented
- Native FreeCAD toolbar sizing, labels and icon path. Qt layout size hints replace most fixed playback and Task button sizes.
- Native Gui.Control TaskDialog, explicit transaction/deleted-document lifetime policy, detach guard disposal, owner disconnect, and no action on temporary Hide/HideToParent.
- Native Preferences > BendSeq > Mesh 3D (time budget and penetration volume), including legacy migration. Task controls and Mesh check share these parameters.
- Typed ParamGet adapter for diagnostics, grouped Mesh view, backgauge configuration, operation weight defaults, sequence catalog paths and PDF export settings. Existing QSettings values are migrated once; old storage is not deleted. Restored FCStd operation weights no longer overwrite global defaults.
- Native Qt theme for tooling status and tabs.
- Register Qt language path and preferences at FreeCAD startup, following Addon Academy. Generate BendSeq_ru.ts from all English/Russian catalog keys and ship the compiled BendSeq_ru.qm from a successful Qt 6 CI build. Qt translations are used with JSON fallback. Qt 6 lrelease is development-only.
- New headless tests for preference migration and task lifecycle, including repeated opening, window state changes, ownership checks and source document association.
- FAST shortlist and standalone virtual clamps now use the shared native Tasks surface, without independent floating windows. Reject the old FAST chooser before loading a new part. A native Tasks dialog is bound to the actual source FCStd, not the temporary animation view.
- Preferences migration uses one-way markers, including Mesh values already stored natively; deleting a migrated value no longer resurrects obsolete Qt settings. CI checks that compiled QM matches its TS source.

## Remaining release gates
- QM resource is compiled and shipped. Verify both Qt Designer preference translations and Python UI translation in a running FreeCAD. Do not remove JSON fallback until parity is verified.
- Native preference page currently exposes the Mesh numeric defaults; other migrated settings are still edited through their existing contextual controls.
- Verify additional dialogs, especially DXF graphical selection, the migrated pre-bend page and tool editors. Retain native Qt modal forms for interactions that genuinely require them; never close another workbench's task. Verify FAST's wide table in narrow Tasks and under overlay modes.
- The legacy Init.py/InitGui.py layout is permitted by Addon Index policy but modern freecad/<namespace>/ is preferred. Retain existing paths until clean installation and FCStd compatibility can be validated; no sys.path manipulation.
- Review remaining hard-coded units and sizes, threading, timers, observers and document ownership. Keep command IDs and old serialization import paths.
- Verify manifest, licenses, asset provenance and Addon Index preflight; see ADDON_INDEX_SUBMISSION.md.

## Windows runtime evidence (2026-11-26)

A user-provided debug log from the experimental branch records:
- Two minimize/restore cycles with Mesh and PDF Tasks active. The root and stack receive Hide/Show events; neither cycle triggers TASKS_CLOSE_DETACH.
- FAST produces five candidates in 3.535 s; additional screening takes 5.682 s. All five have preliminary obstructions. The applied order and flat drawing order are identical.
- Two ten-operation Mesh runs complete without pending operations (24.450 s and 23.149 s), with 9 and 6 findings respectively, plus 8 conditional findings each. These are not clearance verdicts.
- PDF export completes in 3.299 s, producing 796136 bytes; an overwrite request is recorded separately.
- Three Task openings and detachments are recorded; no unhandled Python exception appears and Qt reports normal application shutdown.

This verifies the recorded Windows interactions only, not all acceptance cases. Three detachments occur outside the recorded minimize cycles; the prior journal does not identify their causes. New code adds explicit detach attribution.

## Subsequent changes
- Pre-bend/finish-bend editor now uses a non-modal contextual Tasks page. Its captured controller and operation snapshot must still match at Apply/Delete; the editor is retired when another part loads.
- Part-specific Tasks are retired after successful loads, without raising the hidden native panel; global tooling pages remain intact.
- PDF logs start/completion/error and validates the temporary PDF header before replacing the destination.
- Native lifecycle diagnostics record actual window state, Tasks visibility and close reason.

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
