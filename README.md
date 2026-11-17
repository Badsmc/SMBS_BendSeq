# SMBS ManualMode — manual press-brake bending

This branch is based on [smbs](https://github.com/Badsmc/SMBS_BendSeq/tree/smbs) and preserves its FreeCAD runtime. The source branch is unchanged.

## Operator workflow

1. **Деталь** — select a SheetMetal unfold and load the part.
2. **Оснастка** — choose punch, die and machine parameters.
3. **Порядок и проверка** — select bends manually on the unfolded view, reorder or undo steps.
4. **Симуляция** — preview the chosen order without claiming that collisions have been checked.
5. **Порядок и проверка** — explicitly run fast sampled OCC or full sampled 3D collision checks when needed.
6. **Последовательности / Настройки** — restore and save orders and FCStd JSON; manage diagnostics.

The FreeCAD toolbar has five commands: Деталь, Оснастка, Симуляция, Порядок и проверка, Настройки. The menu also provides saved sequences, backgauge, and the separate tooling library.

## Automatic search is disabled in ManualMode

The workbench **does not register** `SMBS_Calculate` or `SMBS_PlanarCandidates`. The 2D candidate-search task page is removed from the visible task stack; search-only weights and RETRACT search tolerance are not shown. They cannot be launched from the ManualMode GUI. Existing auto-search classes/solvers are kept as internal dependencies for now, not exposed as operator actions. This is intentional to avoid breaking imports and manual simulation/validation paths during the manual-only split. No claim is made that search internals have been physically deleted from this branch.

## Installation

Requires FreeCAD 1.1.x and SheetMetal Workbench. Download the **ManualMode** branch ZIP and extract its contents into a single folder `SMBS` in the active FreeCAD user `Mod` directory. Remove other SMBS/PressBrakeSim installations and **restart FreeCAD** to clear previously registered commands.

Manual preview is not a collision check. Fast OCC and full 3D checks sample the geometry and cannot certify continuous motion, frame/clamp or complete backgauge safety. Verify real machining feasibility independently.

## Development status

GUI search entry points and search-only controls removed from operator interface. Manual behavior inherited from `smbs`; physical FreeCAD smoke test is still required.