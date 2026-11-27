# Native package migration

## Current status

The namespace migration is integrated into `develop` (0.2.0). The package requires FreeCAD 1.1.3+ and Python 3.11+. Root entrypoints remain as compatibility wrappers pending clean installation and namespace-only startup checks.

The migration baseline was commit `67a9482`; `scripts/check_core_migration.py` preserves the historical AST comparison. Current check commands are documented in [Development and releases](DEVELOPMENT.md).

## Earlier Windows runtime evidence

User-provided Windows FreeCAD 1.1.3 journals from 2026-11-26 recorded successful DXF import with Cyrillic layer names, FCStd restoration, FAST search, staged bend editing, Mesh and PDF operations, and minimize/restore interactions. A later session observed both legacy and namespace entrypoints. These observations predate the full package relocation and do not establish acceptance of the current layout; the checklist below remains open.

## Implemented architecture
- `freecad/bendseq` owns commands, UI, integration, workbench, startup and resources.
- `freecad/bendseq/core` owns all 82 headless core modules. Old top-level packages are removed, with no import aliases or duplicate implementations.
- `paths.py` resolves installation/resources and user directories. All installation-relative resources use it. Paths derived from a selected CAD file remain relative to that CAD library.
- Lowercase `init.py` and `init_gui.py` perform native startup. Both temporary root entrypoints import the same canonical modules; workbench/command registration remains idempotent.
- Preserve `SMBSWorkbench`, all 16 command IDs, Qt/ParamGet settings, language selection and light/dark assets.
- Icons, panels, templates and translations reside in `resources/`. Existing bundled machine/backgauge/clamp assets moved unchanged into `resources/default_toollibrary`; no local user tools were added.
- `package.xml` requires FreeCAD 1.1.3 and Python 3.11, retains version 0.2.0 and existing dependency/license declarations.

## Startup evidence and remaining decision
Reviewed official Addon Academy source and FreeCAD tag 1.1.0:
- `src/App/FreeCADInit.py`: pkgutil scanning imports `freecad.<name>.init`.
- `src/Gui/FreeCADGuiInit.py`: pkgutil scanning imports `freecad.<name>.init_gui`.
- Scanner checks metadata/stopfiles using an assumed installation name. BendSeq additionally checks its actual installation root, `ADDON_DISABLED`, `ALL_ADDONS_DISABLED`, DisabledAddons and metadata.
- Keep root entrypoints for this preview because actual 1.1.3 namespace-only GUI loading and Addon Manager installation have not yet been confirmed. They contain no implementation or path injection. Remove only after those checks succeed.
- Available host reports FreeCAD 1.0.0 (freecadcmd); this is below the target and does not prove 1.1.3 acceptance. Initial GUI binary probe without a display aborted; no product GUI acceptance is claimed.

## Data and compatibility audit
- `SMBS_Data` is an App::FeaturePython container with PropertyFileIncluded JSON and scalar hashes/schema. No Proxy assignment or serialized BendSeq Python class was found in current session writer or parametric tool writer. No speculative legacy package adapters were added.
- Schema, decode/serialize and geometry validation ASTs are unchanged. Matching legacy tooling fingerprints additionally permits verified pre-migration source paths; no schema or saved payload rewriting.
- Migration never opens or saves FCStd documents. Existing auto-resume/persist behavior stays unchanged.
- Historical collision reports remain historical evidence; migration never certifies/rechecks them.
- Legacy absolute model references are resolved at point of use via the verified migration journal, preserving profile serialization.
- Existing external sequence report paths in ParamGet and existing `~/.smbs/bend_templates.v1.json` recipes stay accessible without moving them.

## Copy-only data migration
Triggered when the default tool library is first requested. Target is `FreeCAD.getUserAppDataDir()/BendSeq` (platform fallback for standalone Python).
Sources: this installation's `toollibrary`, `config`, `templates`, `sequences`, `saved_sequences`; known predecessor SMBS/SMBS_BendSeq folders in FreeCAD user/Mod locations. External libraries passed explicitly remain at their chosen location.
- All file extensions and relative names are preserved, including FCStd, DXF, JSON, segment quantities and dimensions, machine/config files and saved orders.
- Sources are never deleted. Existing destination files are never replaced.
- Copies use temporary files, fsync, SHA-256 source/destination verification and atomic non-overwriting link publication.
- Conflicts retain a complete source-group snapshot under `migration-conflicts/<source-id>/<inventory-hash>/`, keeping CAD models and their sidecars together. Primary user versions win; conflicting versions can be selected/imported explicitly from that snapshot.
- `migration-v1.json` records paths, hashes, conflicts and errors. Complete is set only after all files verify. Retry after interruption safely; existing successful copies are checked and reused. Historical mappings survive removal of the old installation.
- On copy/hash errors migration raises visibly rather than presenting an incomplete library as successful.
- Do not delete conflict snapshots or the journal while documents still refer to old paths.

## Automated verification

On 2026-11-27, the repository preflight and all 105 headless tests passed. CI also checks translations, syntax and the historical migration guard. Mocked startup and headless tests do not verify GUI behavior or physical validity. Run the [documented checks](DEVELOPMENT.md) against each release candidate.

## Manual acceptance required before 0.3.0
Use copies of documents and tooling for acceptance. Do not overwrite original projects.

| Check in FreeCAD 1.1.3+ | Expected | Status |
|---|---|---|
| Manual ZIP installation and restart | One workbench, no import errors | Pending |
| Addon Manager custom repository installation/update | Metadata, icon, dependencies accepted | Pending |
| Namespace-only startup (temporarily remove root entrypoints in test copy) | Same workbench; disabled addons stay disabled | Pending |
| Tasks minimize/reopen, DXF editor close/reopen | Workspace stays intact | Pending |
| SheetMetal unfold; DXF contours/holes/arcs/bend lines | Same geometry and bend IDs | Pending |
| Manual order, prebend/rebend, independent flips | Same order, angles and operation IDs | Pending |
| FAST on baseline examples | Identical candidates, weights and placement | Pending |
| Mesh/OCC collision checks | Identical verdicts and collision geometry | Pending |
| Coin3D, backgauge, clamps, punch reversal | Same scene and motion as baseline | Pending |
| Tooling import/edit/save and segments | Quantities/dimensions persist outside installation | Pending |
| PDF normal/2D section/collision overlays | Same layout and geometry | Pending |
| Open copies of 0.1.0 and 0.2.0 FCStd sessions | All saved session data restored; historical results marked as before | Pending; real session fixtures unavailable |
| Save/reopen test document | Embedded session restores | Pending |
| English/Russian and light/dark theme | FreeCAD language and readable resources | Pending |
| Windows/macOS/Linux, spaces/Unicode install and user paths | Startup/resources/migration succeed | Pending; temporary filesystem tests pass |

## Installation and rollback
1. Close FreeCAD. Copy old installation, user data and original FCStd documents to a safe backup.
2. Extract the migration branch ZIP into a single `Mod/BendSeq` folder. Disable/remove other active BendSeq copies; do not overlay an old checkout with leftover Python files.
3. If old tooling is in a differently named external installation, retain that folder until migration completes. Known predecessor folder names are discovered automatically; otherwise copy its data into the new user data folder without overwriting existing files or call `migrate_user_data(sources=[old_root])` from the FreeCAD console.
4. Start FreeCAD 1.1.3+, open tooling, inspect `BendSeq/migration-v1.json` under FreeCAD user data; resolve conflicts explicitly if needed. Check copied hashes and segment counts against the source.
5. Execute the manual acceptance table using copied documents. Record platform, FreeCAD version and outcomes in this file.
6. Roll back by closing FreeCAD and restoring the backed-up 0.2.0 installation. Original installation data/documents are untouched by migration. New edits in the separate BendSeq user directory remain available; back them up before choosing any older tool version.
7. After manual acceptance, remove root entrypoints if verified safe, update core/package/README/CHANGELOG together to 0.3.0 and rerun all checks. Promote the tested release using the [release workflow](DEVELOPMENT.md).

## Known limits / continuation
The branch is an architecture migration preview, not a validated 0.3.0 manufacturing release. Actual UI, host geometry and old-session acceptance remain pending. No real customer session fixtures were available; AST equivalence and headless tests do not establish complete FCStd compatibility or physical validity. Existing documents and user configuration are never rewritten by the migration itself. On filesystems without hard-link support the migration fails safely and leaves completion false; test the actual user data filesystem. No new runtime dependencies or pip installs were introduced.
