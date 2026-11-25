# Branch architecture and release policy

## Permanent branches

| Branch | Role |
|---|---|
| `main` | Stable public release destination after acceptance |
| `develop` | Active development and integration of the FreeCAD addon (formerly `smbs`) |

Temporary branches use `feature/*`, `fix/*`, or `experiment/*` and start from the current `develop` baseline.

## Integration and release process

1. Start changes from `develop`, with a focused test and verification plan.
2. Preserve the FreeCAD entry point, unique command IDs, geometry contracts and tooling compatibility.
3. Run import checks and deterministic tests, then verify changed GUI or physical behavior in FreeCAD.
4. Integrate reviewed changes into `develop`. Remove temporary branches after their useful history is reachable from a retained ref.
5. Before promoting to `main`, reconcile the historical baseline and reference assets with the current development tree. Do not blindly overwrite `main` or merge incompatible solver architectures.
6. Promote an accepted snapshot to `main` and publish a release only after clean-install and regression acceptance.

## Current status — 2026-11-25

The latest addon implementation is on `develop`. The current `main` remains a historical baseline containing legacy code and reference assets. The intended stable release role is a policy, not a claim that this baseline has passed acceptance.

Older algorithm experiments and alternative implementations are retained under `archive/2026-11-25/` Git tags. See [GitHub tags](https://github.com/Badsmc/SMBS_BendSeq/tags). The former permanent branches are historical references, not active merge targets.

## FreeCAD acceptance checklist before release

- Clean Mod directory with exactly one SMBS installation and no duplicate workbench menus.
- Startup and command registration without hidden exceptions.
- Recognize SheetMetal unfold input and signed bend angles.
- Exercise L, U, Z and box reference models, including negative bends.
- Verify manual ordering, staged bends, play/pause, slider, reset and reopening.
- Verify DXF input, session persistence, tooling selection and language switching.
- Check FAST and Mesh behavior, and distinguish sampled checks from continuous physical validation.
- Never label an unverified sequence as production-ready.
