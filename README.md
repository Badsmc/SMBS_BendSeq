# SMBS 2D / 2.5D development

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

## Owned features

- `smbs_core/kinematics2d/`: signed bend kinematics, planar sequence checks, press cycle, tooling sections, transfer and animated 2.5D preview.
- `freecad_smbs/integration/`: SheetMetal extraction, directions and panel construction.
- `freecad_smbs/commands/pressbrake_2d.py`: current workbench client used for real-world developer testing (GUI improvements belong in `gui`).
- `tests/`, `demo/`, `fixtures/`, `scripts/` and `tools/`: development-only verification and acceptance aids.

## Status and rules

2D/2.5D is a **development line**. A playable sequence or planar collision check is not proof of 3D manufacturability. Real FreeCAD installation, tool contacts and automatic physical sequence qualification are not yet fully accepted. Changes to UI should originate in `gui`; 3D geometry validation originates in `3d`. Preserve needed shared runtime dependencies, propose integration through pull requests to `main`, and never directly promote unverified search results into `smbs`.

See `docs/PRESSBRAKESIM_25D_RELEASE_GATES.md` for acceptance requirements.
