# SMBS JSON in a FreeCAD document (.FCStd)

Experimental branch: `feature/fcstd-embedded-smbs-json`  
Base: `GUI/v0.0.1-context-panel` (unchanged).

## How it works

A FreeCAD file is a native ZIP-based document. The SMBS session is stored as
a **real JSON file**, not in a string preference and not alongside the document.
An `App::FeaturePython` object named `SMBS_Data` owns a dynamic
`App::PropertyFileIncluded` property named `SessionJSON`. FreeCAD copies
its source into the document's transient storage and includes it in the next
`.FCStd` save.

The workbench does **not** write directly into FreeCAD's internal transient
directory and does not manipulate the ZIP archive itself. It uses
`App::PropertyFileIncluded`, respecting FreeCAD's Undo/Redo file lifecycle.

## User workflow

1. Open the correct `.FCStd` and enable the SMBS workbench.
2. Select the SheetMetal detail/unfold and use **Деталь → Загрузить**.
3. Define a manual bend sequence, select Punch/Die, configure simulation and
   optionally run a verification. No verification is required to save.
4. Press **Ctrl+S** in FreeCAD. SMBS refreshes `SMBS_Data/SessionJSON`
   immediately before that document's native save (via
   `slotStartSaveDocument`). Or choose **Настройки → Данные SMBS в
   документе FCStd → Сохранить в FCStd** to trigger a full document save.
   For an untitled document, a Save As dialog appears.
5. Close FreeCAD and reopen the **same** `.FCStd`; select the unfold and use
   **Деталь → Загрузить**. SMBS reads the included JSON and restores the
   saved bend order, manually numbered prefix, supported simulation settings,
   and matching Punch/Die selections. There is also a manual
   **Восстановить из FCStd** button in Settings.

### What is stored

`smbs_session.json` (schema version 1) includes:

- The exact full bend order and manually selected prefix.
- Immutable SheetMetal bend geometry and a SHA-256 of the original source
  Part BRep; a second SHA-256 seals the canonical geometry manifest.
- The selected Punch/Die **IDs and full profile fingerprints** (profiles
  remain in the existing tool library, no duplicate tool CAD is created).
- CycleConfig, execution mode, operation weights, RETRACT warning and tooling
  tolerances.
- Compact summary of manual, automatic and preliminary search evidence,
  with the saved status clearly marked **historical only**.
- Schema version, UTC timestamp and SHA-256 of the attachment.

Do not include massive motion traces, JSONL frames or source CAD solids in
this JSON. The source CAD is already in the document. Full JSONL traces remain
external to keep the CAD file manageable.

### Safety invariants

1. **No old validation is trusted on restore.** Even when the model and tool
   identifiers match, restored reports are marked
   `UNVERIFIED_RECHECK_REQUIRED`. A new physical validation is required.
2. **Changed geometry is rejected.** The whole source BRep plus canonical
   bend/panel manifest must match. A mismatch never applies a stale order.
3. **Missing or changed tools are not silently substituted.** The saved tool
   profile SHA must match the current library profile. The user sees a warning
   if a saved tool cannot be restored; a restored sequence is still unverified.
4. A corrupt, missing, incompatible or stale existing attachment disables
   **automatic replacement**. Saving the CAD document then preserves the
   old attachment. Press **Сохранить в FCStd** explicitly if you intend to
   replace it.
5. A 4 MiB payload limit, JSON schema validation and checksum protect against
   bad or excessively large JSON; no Python pickle is executed.
6. The observer is bound to the document that loaded the SMBS session. It
   cannot inject the session into a different active FreeCAD document tab.
7. The workbench does not automatically save the CAD document when the
   geometry/order changes; the operator controls the native Ctrl+S save.

## Tests

- `python -m unittest discover -s tests -p 'test_fcstd_embedded_session.py' -v`
  — headless checks of the included-file adapter with a FreeCAD property
  mock, SHA-256 integrity, stale geometry, bad schema and overwrite behavior.
- `tests/manual/test_fcstd_included_json.FCMacro` — run in FreeCAD 1.1.x.
  It creates a temporary FCStd, includes a JSON property, saves the archive,
  verifies a real JSON ZIP entry, closes and reopens the FCStd and verifies
  the restored content. It does **not** modify a user's CAD files.
- Physical user acceptance: open one real 10-bend FCStd, save a noncanonical
  manual order, close/reopen, reload the unfold and verify order, prefix,
  Punch/Die selection, cycle settings and historical-validation warnings.
  Alter a bend or hole; the previous state must be refused as stale.

**The GitHub tests cannot prove the FreeCAD C++ archive property and
`slotStartSaveDocument` lifecycle. The real FreeCAD macro and an interactive
save/reopen test are mandatory before merging this feature.**
