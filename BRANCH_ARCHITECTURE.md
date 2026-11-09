# Branch architecture and release policy

## Permanent branches

| Branch | Role | Source / status |
|---|---|---|
| `main` | Integration branch: assembled, reviewed features | Current legacy headless planner baseline; 2D/2.5D integration **pending** |
| `2d` | 2D/2.5D sequence planning, simulation, physical checks | Started from `2D` at `4513acc` |
| `3d` | True 3D physical modeling and collision validation | Started from `d6-native-3d` |
| `gui` | FreeCAD UI, task panels and commands | Started from `2D`; UI changes should be backported via PR |
| `core` | Solver, geometry backend and shared contracts | Started from `main` |
| `smbs` | Minimal end-user installation | Runtime-only **preview** snapshot from `2D`; independent acceptance pending |

## Integration process

1. Work on feature branch (`2d`, `3d`, `gui`, `core`).
2. Add tests, migration notes and a real FreeCAD verification plan on that branch.
3. Merge into `main` **only after** import checks, tests, compatibility audit, and FreeCAD smoke test. Do not blindly replace `main` with the 2D tree: the two branches currently have different GUI entry points and solver architecture.
4. Create a deterministic runtime-only release tree from an approved `main` commit and advance `smbs` only after a fresh-install test.
5. Ship only imports and assets required by registered workbench commands. Tests, developer tools, fixtures, demos, logs, caches and generated files must not be in `smbs`.

## Status on 2026-11-09

The initial `smbs` preview is a minimal 2.5D runtime snapshot and is **not** equivalent to an integrated `main` release. The 2D/3D integration remains incomplete. This document describes required gates, not evidence that they have passed.

## Existing historical branch names

The pre-existing `2D`, `d6-*`, `d5-*`, `fix/*`, `experiment/*`, and other named branches were deliberately preserved to prevent losing work or invalidating references. New work should use the semantic permanent branches above. Archive or delete old refs only after verifying their commit tips remain reachable through a backup tag or archive branch, checking open pull requests, and verifying that CI or local clones do not depend on them.

## FreeCAD acceptance checklist for a promotion to `smbs`

- Clean `Mod` directory, exactly one SMBS installation, no duplicate workbench menus
- Startup and command registration without hidden exceptions
- SheetMetal unfold input recognized and signed bend angles correct
- Tested L, U, Z and box models including negative bends
- Play/pause/slider/reset and reopen reliably work
- Tooling library persists and loads models correctly
- 2D/2.5D limits and unsupported 3D collision cases explicitly reported
- No untested solver marked as production-ready
