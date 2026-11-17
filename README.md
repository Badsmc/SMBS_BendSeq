# SMBS ManualMode — manual press-brake bending

This branch is based on [smbs](https://github.com/Badsmc/SMBS_BendSeq/tree/smbs) and preserves its FreeCAD runtime. The source branch is unchanged.

## Operator workflow

1. **Деталь** — select a SheetMetal unfold and load the part.
2. **Оснастка** — choose punch, die and machine parameters.
3. **Порядок и проверка** — select bends manually on the unfolded view, reorder or undo steps.
4. **Симуляция** — preview the chosen order without claiming that collisions have been checked.
5. **Порядок и проверка** — explicitly run fast sampled OCC or full sampled 3D collision checks when needed.
6. **Последовательности / Настройки** — restore and save orders and FCStd JSON; manage diagnostics.

## Loading orientation and previously bent flanges

For each operation, the operator can select the row in **Порядок и проверка** and click **Развернуть выбранный гиб на 180°**. This toggles the complete sheet placement around the machine vertical axis without changing the bend angle or selected order. Machine section convention: **operator on the left (-s)**; **press and rear gauge on the right (+s)**. This explicitly overrides the default ergonomic preference (shorter side toward the gauge), which alone cannot decide whether a previously formed flange will hit the punch. The same orientation map is consumed by the simulation and native OCC check. Changing the orientation invalidates prior collision reports; repeat the collision check. A toggle is not proof of access, collision clearance, or rear-gauge support.

**Automatic orientation retry (default ON):** On either Manual OCC validation action, the program preserves the hand-selected bend order, tests the current orientation, and, if a **proven native OCC collision** stops the check, retries that bend with the whole part rotated 180° around the vertical machine axis. It repeats on subsequent blocked bends. If both orientations collide, it restores the original orientation and its collision verdict; if the result is UNVERIFIED (including timeout/exception), it does not automatically claim a clear result or flip. The two passes can substantially increase runtime because currently the validated sequence prefix is replayed on retry. The task summary and Desktop JSON journal include the orientation trials. The user can disable this with **Автоматически проверять разворот 180° при коллизии**, or override any bend manually. The chosen orientation is not a verified rear-gauge contact and does not certify continuous machine clearance.

The FreeCAD toolbar has five commands: Деталь, Оснастка, Симуляция, Порядок и проверка, Настройки. The menu also provides saved sequences, backgauge, and the separate tooling library.

## Automatic search is disabled in ManualMode

The workbench **does not register** `SMBS_Calculate` or `SMBS_PlanarCandidates`. The 2D candidate-search task page is removed from the visible task stack; search-only weights and RETRACT search tolerance are not shown. They cannot be launched from the ManualMode GUI. Existing auto-search classes/solvers are kept as internal dependencies for now, not exposed as operator actions. This is intentional to avoid breaking imports and manual simulation/validation paths during the manual-only split. No claim is made that search internals have been physically deleted from this branch.

## Installation

Requires FreeCAD 1.1.x and SheetMetal Workbench. Download the **ManualMode** branch ZIP and extract its contents into a single folder `SMBS` in the active FreeCAD user `Mod` directory. Remove other SMBS/PressBrakeSim installations and **restart FreeCAD** to clear previously registered commands.

Manual preview is not a collision check. Fast OCC and full 3D checks sample the geometry and cannot certify continuous motion, frame/clamp or complete backgauge safety. Verify real machining feasibility independently.

## Development status

GUI search entry points and search-only controls removed from operator interface. Manual behavior inherited from `smbs`; physical FreeCAD smoke test is still required.
## Fixed viewport during Manual simulation

The 2.5D and 3D QGraphicsView cameras retain the operator's chosen zoom, pan and orbit while rendering, moving between bends, and replaying the manual sequence. Scene bounds no longer cause a per-frame `centerOn()` call after the user has set the camera. **Fit** explicitly sets a new framing; wheel/mouse navigation changes the camera only on user input. The sheet and ram still move physically in machine coordinates. This change does not freeze the workpiece or disable collision detection.
