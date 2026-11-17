# SMBS ManualMode — manual press-brake bending

This branch is based on [smbs](https://github.com/Badsmc/SMBS_BendSeq/tree/smbs) and preserves its FreeCAD runtime. The source branch is unchanged.

## Operator workflow

1. **Деталь** — select a SheetMetal unfold and load the part.
2. **Оснастка** — choose punch, die and machine parameters.
3. **Порядок и проверка** — enter the bend labels, for example `2-1-3-4` or `B02 B01 B03 B04`. You can also click the bend lines on the unfolded view or reorder the operations table. A partial selection checks the chosen prefix.
4. **Проверить и показать** — run the adaptive OCC check for the selected order and see the result in Tasks. The 3D view opens in the checked loading position or at the first proven sheet/tool collision.
5. **Симулировать результат** — replay the same loading orientations. A partial check stops playback at its checked boundary. **Симулировать без проверки** is available before checking. **Быстрая проверка OCC (5 углов)** provides a coarse check.
6. **Последовательности / Настройки** — restore or save the order and diagnostics in FCStd.

## Loading orientation and previously bent flanges

For each operation, the operator can select the row in **Порядок и проверка** and click **Развернуть выбранный гиб на 180°**. This toggles the complete sheet placement around the machine vertical axis without changing the bend angle or selected order. Machine section convention: **operator on the left (-s)**; **press and rear gauge on the right (+s)**. This explicitly overrides the default ergonomic preference (shorter side toward the gauge), which alone cannot decide whether a previously formed flange will hit the punch. The same orientation map is consumed by the simulation and native OCC check. Changing the orientation invalidates prior collision reports; repeat the collision check. A toggle is not proof of access, collision clearance, or rear-gauge support.

**Automatic orientation selection (default on):** The check uses the previously formed flanges at each bend. If the first position collides, is conditional, or is unresolved, it tries the same bend with the sheet turned 180° about the machine's vertical axis. The order and signed angles stay fixed. The check reuses the completed bend history and keeps the best supported position. If both positions collide, the task shows both results. A collision in one position and an unresolved second position produce an unresolved result. Use **Подбирать положение заготовки (0° / 180°)** to control automatic selection.

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

- **Physical tooling display:** the 3D viewer uses the selected physical tool
  lengths. With virtual lengths enabled, both tools use the inspected bend's
  length, including when the blocker belongs to a different bend from the
  current playback step.
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
- **Evidence:** The native 3D result covers the sampled workpiece, punch and die
  geometry. The 2D section diagnostics report discrepancies alongside that
  result. Coarse OCC and incomplete validation remain unverified for the full
  path. The rear gauge, transfers, machine frame and clamps are outside this check.
- **Tests:** `tests/test_manual_native_replay_contract.py` covers matrix
  mismatches and GUI wiring; GitHub Actions job
  `manualmode-replay.yml` runs it alongside syntax and FCStd persistence.
  Final validation still requires a Windows FreeCAD test with the selected
  native punch/die and the sample part.

For manual QA, enter the order and click **Проверить и показать**. On
**Симулировать результат**, compare the loading orientation with the per-bend
result and confirm that a known collision stops playback at the stored pose.
After a tool or orientation change, repeat the OCC check.


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
