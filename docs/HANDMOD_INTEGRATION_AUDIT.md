# HandMod integration audit — 2026-11-02

Status: REVIEWED SOURCE, NOT YET MERGED. Target: development/working-addon.

## Verified repository findings

1. Current branch has freecad_smbs/commands and ui but **no** freecad_smbs/manual directory. HandMod-V.0.0.1 has six manual modules, manual_sequence command and manual_sequence_panel.
2. Existing shared worker subtrees baseline/export/geometry/sheetmetal/topology/validator/verifier have **identical Git tree SHA** between working branch and HandMod. Do not duplicate.
3. HandMod's manual_worker.py imports BendSelectionItem and RawBendLineSnapshot from freecad_smbs.ui.manual_sequence_panel. This is a dependency inversion: worker/model imports UI.
4. Those two types are currently defined as ordinary mutable Python classes in UI, despite RawBendLineSnapshot's docstring claiming immutability. Their to_dict methods serialize raw edge selection data. A headless worker should not depend on importing Qt-capable UI modules for these models.
5. manual_sequence_panel.py imports manual_worker and manual_runner inside worker code and uses subprocess/temporary files. Validate cancellation, process cleanup, artifact ownership and thread safety before connecting it to production.
6. oracle_bridge.py creates its own PhysicalValidator and geometry transforms; audit against the automatic solver's oracle to avoid diverging geometry/physical checks.
7. HandMod's CI file targeted main/stage-1-fix-correctness; it was copied and adjusted for development/working-addon. CI success has NOT been confirmed.
8. Working branch currently includes selected headless tests and loader, but not the HandMod manual-specific tests, because importing them without their implementation would create misleading failures.
9. There are broad exception handlers in manual files. Each must be classified as UI cleanup vs suppressed critical failure before migration.

## Merge plan (strict ordering)

- M1. Introduce a single neutral manual selection data contract in an existing suitable shared module (or one new small models module if none exists); update UI and worker to import it. Test roundtrip serialization and snapshot immutability.
- M2. Compare canonical bend matching in manual/geometry_extract.py with existing SheetMetal feature extraction; enforce one authoritative bend ID mapping and signed angle.
- M3. Replace or adapt manual/oracle_bridge.py so Manual and Auto call the same PhysicalValidator + KinematicEngine. No duplicate geometry engine or permissive fallback.
- M4. Connect manual worker to existing coordinator stages and verifier; avoid an unverified parallel mini-pipeline. Require identical artifact schemas and production eligibility.
- M5. Import GUI command/panel only after M1–M4; register once in InitGui; verify IsActive, selection observer lifecycle, cancellation, Undo/Redo, geometry changes and actual folded-step display.
- M6. Import manual-specific tests and real fixtures selectively; run unit + integration + FreeCAD smoke tests; remove obsolete paths only after reference checks.

## Explicit no-copy list

Do not bulk-copy all of HandMod's freecad_smbs or smbs_core, old scratch_*.py scripts, generated restored_*.brep outputs, tests/archive, tests/artifacts, or alternative orchestration paths. Do not copy a manual GUI that merely appears functional without verifying its oracle and worker dependencies.

## Evidence required before declaring Manual working

- Canonical bend IDs and +45/-45 geometry match between manual and auto.
- No import dependency from core/worker into GUI.
- Same candidate order produces equivalent FoldState and collision result in manual and auto.
- Fail-closed on missing tooling, geometry ambiguity, unknown collision.
- Real FCStd L/U/C smoke, GUI screenshots and persisted logs.
