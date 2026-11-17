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


## ManualMode: one OCC pose for verification and replay (2026-11-17)

The manual bend order is never changed by this feature. The selected per-bend
loading orientation, FoldState history and native punch/die remain the input.

- **Physical tooling display:** the 3D viewer no longer silently clips the
  punch to the active bend length when the actual OCC run checks the full tool.
  Displayed profile geometry is still a preview; positive overlap is established
  only by native FreeCAD OCC.
- **Manual replay:** a fresh, matching OCC report is retained across the
  Simulate action. The matching check includes selected order, active FreeCAD
  session, punch/die IDs, physical section setup, virtual-length setting,
  conditional end-zone settings and the full per-bend 0/180-degree map.
  Restored historical embedded reports are not accepted as current runtime
  clearance evidence.
- **Physical phase alignment:** when fresh native evidence exists, manual
  playback uses `FULL_CYCLE`, not `BEND_ONLY`. On reaching a native-confirmed
  blocking phase it pauses and displays the exact saved sampled native machine
  placement in the integrated 3D viewer. The exact OCC document remains an
  optional separate inspection command.
- **Fail-closed pose comparison:** the recorded native 3x3 rotation and XYZ
  translation are compared with an independently reconstructed
  `WorkpiecePlacement3D.compute_placement` for the saved FoldState angles,
  support plane and current loading orientation. The visualization reports
  `NATIVE_PREVIEW_POSE_MISMATCH` instead of drawing inconsistent geometry.
  The check tolerances are 1e-8 for matrix entries and 1e-5 mm for XYZ.
- **Evidence:** `COLLISION` is never changed into `CLEAR`; `CONDITIONAL`
  remains conditional end-zone evidence; `UNVERIFIED` and coarse OCC cannot
  certify a safe full path. Operator/rear-gauge, transfer, frame and clamping
  collisions remain unverified unless checked separately.
- **Tests:** `tests/test_manual_native_replay_contract.py` covers matrix
  mismatches and GUI wiring; GitHub Actions job
  `manualmode-replay.yml` runs it alongside syntax and FCStd persistence.
  Final validation still requires a Windows FreeCAD test with the selected
  native punch/die and the sample part.

For manual QA, run **5-angle OCC**, repeat with **Full 3D** as appropriate,
then press **Simulate** without changing tool identity or orientation. On
B02, the integrated 3D viewport must stop on the first native blocker, with
the correct sampled pose and punch height. Changing a loading orientation or
tool setup invalidates the old evidence and requires a new OCC run.


## ManualMode — B02 tooling and retract diagnosis (2026-11-17)

The tested sample bend B02 is 293 mm long. With **virtual tool length**
enabled, the native punch AND die are modelled as 293 mm-long solid tools;
the 100 mm source FCStd punch does not physically cover the 293 mm bend.
Do not reduce the virtual OCC length to 100 mm merely to suppress an overlap.

The selected B02 pose (0 degrees loading orientation) has a **proven
APPROACH punch collision**; its 180-degree retry passes the early phases
but hits the punch during **RETRACT**. Manual auto-orientation now retains
both collision reports, so the original APPROACH blockage no longer hides
the eligible 180-degree retract experiment.

- In **Manual > native check**, the report shows source/effective punch
  lengths and separates central from end-zone-attributed XYZ intersections.
  Bounding-box classification is diagnostic and is NOT a physical proof of
  actual end relief. A central collision is never accepted as CONDITIONAL.
- **Check assisted lift** is enabled if either the original or the separately
  captured 180-degree pose has a native-confirmed punch RETRACT collision.
  This calls the existing exact OCC replay of a hypothetical sheet/punch lift,
  preserving the selected tool geometry and physical COLLISION result.
- When that sampled lift finds no blocking penetration, the existing
  **check lateral release** button can evaluate candidate withdrawal
  directions in native OCC. The program does NOT silently change the
  operator's 0/180 selection, nor start the 180-degree assisted animation
  for an unrelated selected pose.
- A sampled lift or sampled lateral withdrawal is still **UNVERIFIED** as a
  complete real movement: mechanical sheet grip, continuous collision
  freedom, clamps, backgauge, frame and handling remain unproven.
  The standard Manual result remains **COLLISION** until an independently
  verified end-to-end motion is available.

For QA: select the same ten-bend order and actual punch/die,
retain virtual-length mode for a full-length bend, run **5-position OCC**
with auto 180-degree retry enabled, verify log event
`MANUAL_ALTERNATIVE_COLLISION_RETAINED` for B02/RETRACT, then press the
assisted lift probe and (if a sampled candidate is reported) lateral release.
Review `MANUAL_ASSISTED_RETRACT_EXPERIMENT` and
`MANUAL_ASSISTED_RELEASE_EXPERIMENT` in the desktop diagnostic JSON.
