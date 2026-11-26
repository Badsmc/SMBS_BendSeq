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

## Further Windows runtime evidence (2026-11-26)

The next user-provided journal records a new part (Omega), restores its existing six staged operations, cancels editing B02, and successfully adds B04.1 at -45 degrees and B04.2 at -90 degrees. The resulting seven operations are reordered successfully. Closing the native Tasks surface while editing B04 retains the editor and it can be reopened and applied. One minimize/restore cycle produces no detach. The journal ends with normal Qt shutdown. It does not demonstrate DXF import, tooling generation, or physical collision clearance.

## Graphical editor lifecycle
- Retain the full-size native Qt drawing surfaces for DXF and parametric tooling: narrowing them to a task sidebar would impair geometric selection and dimension editing.
- Use QDialog.open() and finished callbacks instead of nested exec() loops.
- DXF protects against duplicate queued calculations, cancelled-dialog callbacks, and closing during a calculation.
- Parametric tool creation refreshes the calling library only after acceptance.
- Verify both editors in FreeCAD on Windows and macOS, including cancel, retry, successful save/import and window close. Headless tests cover DXF lifecycle guards but not the actual graphical interaction.
- Keep existing package paths and FCStd compatibility until a separately tested namespace migration is ready.

## DXF and generator runtime acceptance (2026-11-26)

A subsequent Windows FreeCAD 1.1.3 journal records one successful DXF import:
52 entities, seven circular holes, five panels, four bends, one folded solid,
3.586 s total. The generated punch and die were both saved and used by FAST.
FAST returned five preliminary-clear candidates in 2.424 s including screening,
but the sampled Mesh run (19.785 s) found one punch/P02 collision at B04, 67.5
degrees (205.611 mm³ overlap). PDF export completed in 1.322 s (320592 bytes).
The journal ends in normal Qt shutdown, with native Tasks detach explicitly
attributed to two rejections and deletion of the source document.

The journal contained replacement characters in DXF layer names. The previous
reader used UTF-8 with errors="replace"; it could collapse distinct names.
The new reader uses the DXF version and declared DWGCODEPAGE, reads modern
AC1021+ DXF as UTF-8, and reports undecodable declared data rather than
silently replacing names. Undeclared legacy encoding cannot be inferred
reliably; the fallback is UTF-8 then CP1252. The original DXF bytes were not
supplied, so the particular file still needs a fresh import test.

## Namespace startup, compatibility stage

FreeCAD 1.1.3 discovers namespace packages in `freecad.__path__` and imports
lowercase `init` and `init_gui`. This checkout now provides
`freecad/bendseq/{__init__,init,init_gui}.py`, without a shared
`freecad/__init__.py`. Both modern and legacy entrypoints import the same
canonical `freecad_smbs.Init` / `freecad_smbs.InitGui` modules. Namespace
startup checks the actual checkout's metadata, ADDON_DISABLED flag and
origin of both existing packages. No sys.path modification or duplicate
workbench registration is introduced. The preflight includes the new files.

This is an entrypoint migration, not a full relocation of existing modules.
Keep root Init.py / InitGui.py and serialized Python module names during this
compatibility stage. Test native startup in a clean FreeCAD installation,
both direct-Mod ZIP and standard addon installation, with a saved FCStd.
A complete package relocation is not required to validate these entrypoints.

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
