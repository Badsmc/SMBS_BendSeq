# SMBS BendSeq — integrated development

## Repository branch map

| Branch | Purpose | Installation |
|---|---|---|
| [main](https://github.com/Badsmc/SMBS_BendSeq/tree/main) | Integrates compatible, reviewed 2D / 3D / GUI / solver work | Development only until validated |
| [2d](https://github.com/Badsmc/SMBS_BendSeq/tree/2d) | 2D and 2.5D section geometry, sequences, press cycle, previews, tests | Development |
| [3d](https://github.com/Badsmc/SMBS_BendSeq/tree/3d) | 3D OCC / FreeCAD physical collisions, machine geometry and 3D validation | Development |
| [gui](https://github.com/Badsmc/SMBS_BendSeq/tree/gui) | FreeCAD workbench interface, commands, task panels and UX acceptance tests | Development |
| [core](https://github.com/Badsmc/SMBS_BendSeq/tree/core) | Headless solvers, contracts, graph model, geometry and physics interfaces | Development |
| [smbs](https://github.com/Badsmc/SMBS_BendSeq/tree/smbs) | Minimal runtime-only user install (currently 2.5D preview) | Installable preview, **not qualified for production** |

**Branch names describe development ownership, not a claim that code is exclusively present there.** Shared imports remain where required for runnable development environments. Never install multiple checkouts simultaneously in FreeCAD's `Mod` directory.

A feature is merged into `main` via tested integration; `smbs` is generated from an accepted runtime snapshot only after real FreeCAD GUI acceptance. These are different stages. Historic branches remain available as references.

## Current integration status

The current `main` still contains the legacy headless bend-sequence pipeline and original FreeCAD command set. It **does not yet integrate** the newer standalone `2d` / `3d` implementations. The interface, geometry and solver APIs must be reconciled before merge. Do **not** assume that all branches have been combined or physically verified.

See [BRANCH_ARCHITECTURE.md](BRANCH_ARCHITECTURE.md) for merge gates, release rules and FreeCAD verification checklist.

## Integration criteria

- Preserve a single FreeCAD entry point and unique command IDs.
- Preserve SheetMetal as the authoritative unfold/angle source.
- Keep 2D clearance distinct from verified 3D collision claims.
- Run deterministic unit and regression tests plus real GUI smoke/acceptance tests.
- Reject silent fallback to physically unvalidated sequences.
- Prepare runtime-only artifact, verify in a clean FreeCAD installation, then promote to `smbs`.
