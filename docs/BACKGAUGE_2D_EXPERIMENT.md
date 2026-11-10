# SMBS 2D backgauge experiment

Development branch: `experiment/2d-backgauge-kinematics` (based on `integration/gui-2d-search-screening`).

## Implemented in this branch

- FreeCAD top toolbar/menu command **Задний упор** (`SMBS_Backgauge`)
- Editable, persistent 2D stop dimensions (width along machine `s`, height along machine `z`) and an **explicit target centre (s,z) for every bend**; no inferred default machine position
- Saved settings retain the part label to reject applying coordinates to another model
- Automatic sampled 2D Stage B: per-bend backgauge rectangle checked against sheet/panel section in APPROACH, BENDING, BOTTOM and RETRACT via the existing `classify_polygon_contact` oracle
- Manual-order replay: same per-bend backgauge collision screening and shared operation weights
- Sequence assignment from JSON: rechecks the selected order using current backgauge configuration
- Operation weights: `backgauge_move_fixed` (default 2 points per nontrivial move) and `backgauge_move_per_100_mm` (default 0.5 points per 100 mm Euclidean distance between target `s,z` coordinates), including per-step JSON cost breakdown
- Diagnostic JSON: source coordinates, per-step target, backgauge contact findings and explicit `backgauge_interbend_motion_checked=false`
- Disabled by default until the operator explicitly specifies all coordinates

## How to test in FreeCAD

1. Install/check out the development branch. Restart FreeCAD; select the PressBrakeSim workbench.
2. Load the desired SheetMetal part.
3. Open **Задний упор** from the top toolbar, enable checking, enter physical stop width and height, then fill both `s` and `z` centre coordinates for **every** bend. Save.
4. On the **Настройки** tab set the two backgauge movement cost weights.
5. Run **2D варианты** and inspect candidates' `machine_setup.backgauge_s_mm` / `backgauge_z_mm`, `cost_breakdown[].backgauge_move_cost`, `backgauge_checked`, and `diagnostic_findings` with `tool=BACKGAUGE`.
6. Use the manual-order Stage A/B JSON check on the same part and compare the cost for an identical order.
7. Change one target or move weight; confirm that the backgauge cost changes and that collision findings depend on geometry, **not** on penalty weights.
8. Reopen the GUI to confirm settings persistence; load another part and confirm an old part-specific configuration is not silently accepted.

## Known limitations / not implemented yet

- **Inter-bend stop travel is NOT collision checked**, nor is it animated. Only each bend's configured target is checked at sampled phases. Changing a coordinate changes the hypothetical stop target, but does not prove a collision-free movement from the preceding stop.
- The current 2D rectangle represents **one cutting-plane projection**, not two independent fingers or an actual machine's 3D finger shapes. There is no automated determination of appropriate stop/contact positions from panel edges and flange datum.
- Native machine X/R/Z gauge axis limits, travel speeds, homing, fingers' independent motion, clamping, and transfer poses are not modeled.
- A 2D `CLEAR` or `TOUCH` is not a manufacturing certification; end zones and full 3D require independent verification.
- User-supplied physical tool/gauge dimensions must be checked against the specific machine; no invented default setup becomes production evidence.

## Next qualification gate

Implement explicit backgauge finger assemblies and axis contracts; determine target positions from bend geometry and machine datums, then validate **each interpolated TRANSFER trajectory** with the correct sheet pose and tool state, prior to marking any backgauge motion as checked. Preserve the current search/replay interfaces and do not elevate risk-only 2D checks to 3D approval.
