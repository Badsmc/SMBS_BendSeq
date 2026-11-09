# SMBS 3D — physical validation development

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

- `smbs_core/` physical geometry, collision validation and machine tool assembly relevant to exact 3D.
- Native FreeCAD/OpenCASCADE 3D bend solids and collision routines, and PythonOCC / optional accelerated experiments where retained.
- `tests/test_d63*`, `tests/test_d634*`, `tests/test_freecad_native_collision.py`, physical validation and sequence search tests.
- `tools/run_d63*`, `tools/run_d634*`, `docs/D63_NATIVE_3D.md`, `docs/D634_INSTALL_FREECAD.md` and related reports.

## Isolation policy

This branch started from historical `d6-native-3d`. It still includes shared/legacy 2D and GUI code because native 3D validation relies on the combined FreeCAD model and tooling adapters. These shared files are **dependencies**, not independent ownership or a completed 3D product. Preserve existing diagnostics and tests until a dependency audit proves they can be removed. Do not register experimental features for end users without verification. Promote well-scoped changes by PR to `main`.

## Acceptance

Check exact solid validity, signed bend angle, self-intersections, punch/die/backgauge/clamps/frame collisions, tolerances, and full intermediate swept motion. No 2.5D preview substitutes for these checks.
