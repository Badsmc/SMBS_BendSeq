# D7 — Press-brake operation penalty / changeover-cost model

**Branch:** `d7-operation-weights` (from `d6-freecad-native`).

**Status:** core and optional weighted search **IMPLEMENTED + pure-Python TESTED**; **not production-qualified and not enabled in the FreeCAD UI**. The present D6 preview oracle still cannot certify all physical setup/transfer scopes, and it does not yet emit actual discrete machine setup alternatives.

## Purpose

Once collision-safe orders are known, prefer the order requiring fewer manual reorientations, sheet turnovers and tool-station/bend-length adjustments. Costs **never** overrule physical feasibility. The core cost model is designed to accept new penalty types later without introducing another geometry engine or changing SheetMetal ownership of the bend data.

## Initial weights (dimensionless penalty points, configurable)

| Operation | Initial penalty | Trigger |
|---|---:|---|
| Turn the workpiece in the machine plane | 2 points per turn + 1 point per 90 degrees | Difference between verified **machine-frame** longitudinal setup headings > 1 degree |
| Flip / overturn the workpiece | 8 points | Workpiece reference side changes between consecutive *actual setups* |
| Change required bending-line length | 3 points + 0.5 point per 100 mm difference | Bend-line length changes by > 0.5 mm |
| Initial setup/loading | 0 | Not modeled yet; first bend has no **changeover** penalty |

These values are **heuristics**, not real elapsed seconds or labor costs. Real machine calibration may change all weights. `BendSetup.line_length_mm` is the **length of the bend axis**, not flange length, bend angle, sheet width or punch catalog length.

**Important:** varying bend line lengths do not necessarily require changing machine tooling or usable punch length. This is the explicitly requested temporary proxy penalty; replace with true segmented-tool change and effective tooling length when the setup model exists.

## Strict data contract

- `BendSetup(bend_id, rotation_deg, flipped, line_length_mm)` must come from actual chosen machine setup and SheetMetal data.
- `rotation_deg` describes the physical setup orientation in the **machine frame**; it cannot be inferred solely from 2D bend axis direction or the difference between bend angles.
- `flipped` describes overturning the sheet relative to a reference face; an opposite signed bend does not automatically imply a flip.
- No missing rotation/flip data are substituted with zero. The weighted search rejects incomplete setup mappings.
- Initial penalties are nonnegative and deterministic. New costs should be added as separately documented, individually testable terms.
- All costs are **secondary** to the StepOracle's collision and full-scope validation. A physically rejected step is **never** made legal by being cheap.

## Modules

- `smbs_core/kinematics2d/operation_cost.py` — immutable `OperationWeights`, `BendSetup`, `StepPenalty` and `SequencePenalty`; `bend_length_mm(bend)` extracts physical length from FreeCAD's authoritative SheetMetal bend axis, without a FreeCAD runtime dependency.
- `smbs_core/kinematics2d/weighted_sequence_search.py` — optional `plan_weighted_forward` branch-and-bound optimizer. Uses D6's `_fresh_state`, `StepEvidence` and `REQUIRED_SCOPES`. Fully certified and partial-preview candidates are kept separate.
- `tests/test_operation_weights_d7.py` — input validation, cost ranking, safety gates, budget exhaustion and determinism contracts.
- `.github/workflows/d7-operation-weights.yml` — tests D7 plus unmodified D6 sequence contracts.

## Selection example

When B01/B02/B03 are individually physically acceptable (synthetic test oracle only):

- Setup B01: heading 0°, front face, 100 mm bend line.
- Setup B02: heading 180°, reversed face, 120 mm bend line.
- Setup B03: heading 0°, front face, 100 mm bend line.

A naïve order B01 → B02 → B03 incurs two turn/flip/length changes. B01 → B03 → B02 incurs one, and receives a lower penalty. In real operation, the solver may pick that order **only if full physical checks certify every step**.

The user can add further weights later, e.g. change of die opening, punch segment, backgauge setup, sheet support, fixture use, regrip count, machine travel time, operator handling time, or preference for consecutive parallel bends.

## Planned integration after actual setup extraction

1. FreeCAD/SheetMetal already supplies bend axes and lengths. Adapt the real per-bend workpiece/machine placement output to compute **explicit machine-frame headings** and **face orientation**, including legitimate alternative placements.
2. A step candidate is not solely `bend_id`; eventually it must include setup identifier, tooling configuration, and machine pose. Extend DFS state key to include actual chosen setup when alternatives exist.
3. Call existing physical oracle to **certify** each `(bend, setup)` candidate for approach, bending, retract, transfer and all machine interactions.
4. Rank and branch-and-bound verified routes by D7 score. Do not upgrade the existing 2.5D preview's `UNVERIFIED` status.
5. Expose weights in the GUI and include a cost breakdown, chosen setups, turn/flip/length-change counts in JSON under the unified addon `reports/` folder.
6. Later calibrate weights against machine operation time and add new penalty types without modifying physical collision logic.

## Caveats

The optimizer currently assumes **one explicitly supplied setup per bend**, because the D6 tool/placement pipeline has not yet enumerated physical alternatives. It is an independent optional path, not a replacement for D6's current GUI calculation. Under state/check budget exhaustion, `optimality_proven` is false. No order is certified solely by the D7 cost layer.

**No external Python libraries and no FCL.**
