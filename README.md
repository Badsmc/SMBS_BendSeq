# SMBS ManualMode — manual press-brake bending

This branch is based on [smbs](https://github.com/Badsmc/SMBS_BendSeq/tree/smbs) and preserves its FreeCAD runtime.

## Operator workflow

1. **Деталь** — select a SheetMetal unfold and load the part. For an ASCII DXF, open **Развёртка DXF**, enable the relevant layers, assign bend angles in order, and click **Рассчитать**. **Назад** undoes the last assignment or edit. The imported order appears numbered on the simulator's flat pattern.
2. **Оснастка** — choose punch, die and machine parameters.
3. **Симуляция** — enter the bend labels, for example `2-1-3-4` or `B02 B01 B03 B04`. You can also click the bend lines on the unfolded view or reorder the operations table. A partial selection checks the chosen prefix.
4. **Проверить и показать** — run the adaptive OCC check for the selected order and see the result in Tasks. Coin3D opens in the checked loading position or at the first proven sheet/tool collision.
5. **Симулировать результат** — replay the same loading orientations. A partial check stops playback at its checked boundary. **Симулировать без проверки** is available before checking. **Быстрая проверка OCC (5 углов)** provides a coarse check.
6. **Последовательности / Настройки** — restore or save the order and diagnostics in FCStd.

## Loading orientation and previously bent flanges

The **180°** column in **Симуляция** shows the loading position of each bend. A checked box means the complete sheet is rotated 180° around the machine’s vertical axis. An unchecked box means 0°. Clicking the box fixes that bend’s position for Coin3D and the OCC check. The choice follows the bend when you reorder the steps and is saved with the SMBS settings in FCStd. Run the collision check again after editing a position.

**Подбирать положение заготовки (0° / 180°)** enables automatic selection for bends without a manual choice. If the first position collides, is conditional, or is unresolved, OCC tries the opposite position with the same previously formed flanges. To let OCC choose the position for a manually set bend again, right-click its checkbox and select **Подбирать положение автоматически**.

The FreeCAD toolbar has five commands: Деталь, Развёртка DXF, Оснастка, Симуляция, Настройки. The menu also provides saved sequences, backgauge, and the separate tooling library.

## Automatic search is disabled in ManualMode

ManualMode exposes manual order selection, replay, and collision checks. The sequence catalog can still open existing reports and replay imported orders. Shared search and validation modules support those workflows.

## Installation

Requires FreeCAD 1.1.x with `pivy.coin` and SheetMetal Workbench. Download the **ManualMode** branch ZIP and extract its contents into a single folder `SMBS` in the active FreeCAD user `Mod` directory. Remove other SMBS/PressBrakeSim installations and **restart FreeCAD** to clear previously registered commands.

Manual preview is not a collision check. Fast OCC and full 3D checks sample the geometry and cannot certify continuous motion, frame/clamp or complete backgauge safety. Verify real machining feasibility independently.

## Coin3D simulation

ManualMode uses the renderer from [development/coin3d-animation](https://github.com/Badsmc/SMBS_BendSeq/tree/development/coin3d-animation). The flat pattern remains available for picking bends. With Python Quarter, Coin3D opens in the SMBS workspace. With `pivy.coin` alone, it opens in a temporary native FreeCAD document named `SMBS_Animation_NN`. Press **Симуляция** to open it and **Fit** to frame the scene.

- **Показывать станок** displays `machine_frame.FCStd`. Its registered lower mounting face sits under the die and the upper slide follows the punch height. The CAD mounting faces are recorded in `machine_frame.tooling.json`.
- **Задние упоры** enables one or two `Backgauge.FCStd` fingers. Existing manual or automatic S/Z settings and configured X stations drive the CAD visualisation. The fingers move between bend setups during transfer.
- The frame and fingers are display geometry. The current OCC check evaluates the selected punch, die and sheet at sampled positions.


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


## Segment inventory and clamp-aware assembly (ManualMode)

1. **Библиотека оснастки** → choose an individual punch or die →
   **Сегменты и количество**. Enter each section length in mm and the
   number actually available. Inventory is stored under the selected
   tool's `.tooling.json` `metadata.segments` as
   `[{"length_mm": 150.0, "quantity": 2}, ...]`.
   The original datums, metadata and FCStd shape are preserved.
2. Under **Лапки**, configure the actual length along the machine X axis
   (`width_mm` in the existing schema), minimum edge-to-edge separation
   (`gap_between_mm`), clamp center positions (`positions_mm`), minimum
   clamp count, joint-support and end-overhang rules. Use **Число зажимов**
   to create a centred row with pitch equal to clamp length plus gap.
   Explicit X centres take precedence. X = 0 is the press centre.
3. Open a part, choose the punch and die, set the manual order, then press
   **Набрать оснастку — расчёт сегментов** in **Симуляция**
   or **Рассчитать раскладку** in the tooling library. Pick a bend in
   the report to see its sections in Coin3D. A supported punch can be
   previewed while the die stock is still missing. The report shows for each bend: nominal bend length, actual assembled
   punch and die totals, every segment's length and machine-X start/end,
   and the clamp centres, width and minimum gap. It also shows the punch
   centre and how many millimetres of each segment sit under each clamp.
   The inventory is reused between successive bends.
4. Use **Применить общий набор** inside the report to use one fixed
   layout for the selected bends. It covers the longest selected bend.
   Coin3D shows adjacent sections with alternating shades and displays
   the full configured row of CAD clamps with the ram, independent of
   the active punch and its length. For an unconfigured library, Coin3D
   displays a nominal row based on the CAD ram width. Coin3D and OCC use the same
   segment intervals from verified FCStd tool models. **Снять общий набор** returns to the original tools.
   Opening a saved FCStd recalculates the active layout using the current
   inventory and clamp configuration.

The planner prefers an exact punch length and then a punch up to 15 mm
shorter. The existing overlength option is also available when these
combinations do not fit. The die covers the full bend line. The report
shows the punch shortfall and OCC retains its full-coverage requirement.
The planner tries different section orders and punch offsets along X. Each punch segment needs positive overlap with a
clamp. For segments short enough to fit in a clamp gap, the planner
prefers full support under a clamp. The planner uses the configured
clamp positions. If no positions are specified
and clamps are movable, calculated clamp centers are explicitly marked
`PROPOSED_REPOSITION_UNVERIFIED` and require operator confirmation.

The layout report checks stock, tool coverage and punch support on the
clamps. Run OCC after applying the common layout to check sampled
punch and die collisions. Clamp and frame clearance remain outside
that collision check.
