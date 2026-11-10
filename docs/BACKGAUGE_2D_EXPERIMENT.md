# SMBS 2D automatic backgauge — development status

Branch: `experiment/2d-backgauge-kinematics`, forked from `integration/gui-2d-search-screening`.

## Target behavior

For every bend, the planner computes the machine-space distance from the active bend line to a supporting **fixed-panel edge** using the current folded state. The backgauge stop is positioned at the selected edge, automatically repositions when the next bend is selected, and moves as an interpolated 2D preview in the `TRANSFER` phase.

The operator **does not need to type an individual target for every bend**. In the **Задний упор** toolbar dialog, enable checks and leave the default **Автоматически вычислять положение по краю опорной панели** checked. Enter the physical stop rectangle dimensions and optional clearance. Manual target entry remains available as an override.

## Geometry and coordinates

- Uses `FoldState` and `WorkpiecePlacement3D` to align the active bend in machine coordinates.
- Uses `compute_machine_cutting_plane_section(...)` to obtain **the active fixed panel's** real cross-section at the bend midpoint.
- Derives the far edge from that fixed-panel cut polygon only (not overall part bounding box and never the moving flange).
- The measured bend-to-edge depth is `abs(edge_s)` in mm; gauge rectangle centre `s` is offset outside that edge by half the stop width plus optional clearance; `z` is aligned to the edge's thickness centre.
- If no fixed-panel edge can be resolved at the machine cutting plane, the candidate is **UNVERIFIED**; no made-up length.
- Geometry mode is `AUTO_FIXED_PANEL_EDGE_2D_UNVERIFIED`; geometry/station orientation still requires qualification against the actual machine.

## Collision and weight integration

- Automatic and manual 2D search generate a target separately for **each active bend in the already-folded sequence prefix**.
- The existing Stage B 2D polygon-contact oracle checks the stop's *target position* in sampled APPROACH, BENDING, BOTTOM, RETRACT phases.
- `backgauge_move_fixed` and `backgauge_move_per_100_mm` score changeover between successive target (s,z) positions; they do not change collision outcomes.
- Automatic cost and manually entered order cost use the same penalty model, and JSON contains per-step targets, source and penalties.
- The sequence catalog's simulation assignment reruns sampled 2D checks with the current backgauge configuration.

## Visualization

- Gauge outline is drawn in the main 2.5D viewer when configured.
- During the existing `TRANSFER` phase it linearly interpolates stop centre from the previous bend target to the next bend target.
- This **is a visual planning interpolation only**, not physically constrained axis motion and not a validated collision-free transfer.

## Important limitations still open

1. The selected far edge is a 2D geometric candidate, **not yet proof of an accessible physical rear-gauge contact face**; direction of the rear machine side, finger reach, and support stability need explicit machine-specific contracts.
2. Stop represented by one 2D rectangular cutting section, not independent left/right fingers, X/R/Z axes or their 3D geometry.
3. Inter-bend motion is **not collision-checked**, even though its visual interpolation is shown. It can pass through the sheet or tooling.
4. No motor motion constraints, axis stroke limits, coordinated sheet transfer, safety interlocks, finger retraction, staging, or real-time machine control.
5. No automatic recognition of alternate supporting edges or reliable backstop placements for flange-less, heavily folded, tapered or unsupported part profiles.
6. No production certification from any sampled 2D result.

## FreeCAD test protocol

1. Checkout this branch and restart FreeCAD. Load test part `2134`.
2. Open top toolbar **Задний упор**. Enable checks, keep **Автоматически вычислять положение** active, enter stop width/height and save. **Do not manually enter per-bend positions.**
3. In **2D варианты**, check JSON `backgauge_configuration.automatic=true`, `machine_setup.backgauge_s_mm`, `backgauge_z_mm`, `machine_setup.note` containing measured depth and `cost_breakdown[].backgauge_move_cost`.
4. Run manual-order replay with the same sequence and weights; compare scores and backgauge findings.
5. Select a result for simulation. During **TRANSFER**, observe the orange gauge stop moving smoothly. If it moves across the workpiece or into tooling, that is a **known unsupported hazard in the current stage** and must be logged for future motion checking.
6. Confirm that missing fixed-panel edge produces a visible unverified diagnostic, not a nominal valid target. Repeat with a different bend sequence and confirm targets are recalculated based on preceding bends.

The next qualification milestone is machine-axis-calibrated dual-finger contact selection, with swept path collision checks for both the stop and the workpiece during TRANSFER.
