# SMBS GUI — FreeCAD user interface development

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

- `Init.py`, `InitGui.py` and `freecad_smbs/InitGui.py`: single FreeCAD Workbench registration.
- `freecad_smbs/commands/` and `freecad_smbs/ui/`: menus, toolbars, task panels, tooling dialogue, playback controls and visuals.
- `freecad_smbs/resources/`: required icons and UI assets.
- `tests/test_pressbrake_workspace_gui.py`, `tests/test_direct_install_25d.py`, GUI acceptance tooling and `docs/PRESSBRAKESIM_UI_CONTROLS.md`.

## Dependencies and constraints

The UI currently imports 2D kinematics and geometry and therefore the branch **retains supporting `smbs_core/` and FreeCAD integration modules**. Do not delete imports merely to make the tree look smaller. Solver behavior belongs to `2d`/`3d`/`core`; change the UI by narrowing adapters/interfaces where possible. Keep command registration strict, exactly one workbench installation, and make unsupported actions disabled or visibly rejected. Send verified GUI changes via PR to `main`.

See [GUI module boundaries](docs/GUI_ARCHITECTURE.md) for ownership, safe refactor stages, verification gates and pending decomposition of the 2.5D panel.

## GUI checks

Open/reopen the task panel; choose a document/face; show part; run Play/Pause/Reset/angle slider and order controls; load tooling; confirm that commands do not duplicate and errors are not swallowed.
