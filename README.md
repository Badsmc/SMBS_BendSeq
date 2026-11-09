# SMBS Core — Headless algorithms

## Branch responsibility

| Branch | Role |
|---|---|
| [main](../main) | Integrated development |
| [2d](../2d) | 2D/2.5D planning and simulation |
| [3d](../3d) | Native physical 3D validation |
| [gui](../gui) | FreeCAD UI and task panels |
| [core](../core) | Headless core, models and solvers |
| [smbs](../smbs) | Minimal install-only 2.5D preview |

This branch owns reusable headless calculation and geometry modules. It is **not** a FreeCAD Workbench and must not be installed into FreeCAD's Mod directory as an add-on.

## Included source

- `smbs_core/Core/`: bend/panel graph, fold state and orchestrator
- `smbs_core/Geometry/`: geometry extraction and fold/unfold logic
- `smbs_core/Solvers/`: backward DFS, A*, greedy search
- `smbs_core/Physics/`: collision physics and validation
- `smbs_core/contracts/`, `smbs_core/IO/`, `smbs_core/geometry_backend/`: model contracts and adapters
- Tooling data required by calculation components

FreeCAD GUI entry points and `freecad_smbs/` are excluded. The historical macro files within `smbs_core/addon/` are retained for migration analysis; they are not imported as a FreeCAD workbench. Some backends require configured FreeCAD or PythonOCC even in headless operation.

## Integration and testing

Work on shared APIs here, keep deterministic tests and strict tolerance/collision contracts, and merge qualified changes into `main` through a reviewable pull request. Passing unit tests alone is not proof of safe manufacturing sequences. `smbs` is a separately validated runtime artifact, not an automatic mirror.

See [main architecture](https://github.com/Badsmc/SMBS_BendSeq/blob/main/BRANCH_ARCHITECTURE.md).
