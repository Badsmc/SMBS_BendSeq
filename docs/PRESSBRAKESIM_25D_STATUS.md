# PressBrakeSim 2.5D — current verified status

Updated: 2026-11-06. Source-of-truth branch: **`2D`**.
Previous experimental phase-by-phase chronicle is recoverable in
`archive/2d-pre-cleanup-20261106`; it is not the current runtime contract.

## Current active stack

- FreeCAD workbench loader: repository-root `Init.py` and `InitGui.py`,
  delegating to `freecad_smbs/`.
- FreeCAD menu / toolbar: **PressBrakeSim 2.5D** and **Библиотека оснастки**.
- 2.5D SheetMetal extraction and finite-radius FoldState, signed angles,
  canonical bend IDs, dynamic press-cycle preview and motion-trace JSONL.
- Two view panels: active 2.5D section, read-only whole-part 3D surface preview.
- A manual bend order editor and automatic *visualization* of that order.
- Tooling: punch/die profiles, source FreeCAD models, backgauge, clamps and
  machine frame catalog; *loaded* does not mean *physically qualified*.
- Optional 2D section-contact oracle; no assumption that a 2D collision-free
  section proves the entire 3D machine clear.

## Evidence

- `2D` GitHub Actions, pure-Python runtime CI:
  10 install/path tests and 147 2.5D regression tests **PASS**,
  6 FreeCAD-dependent tests skipped outside the native application
  (run 42215667048; subsequent commits must pass their own CI).
- Source compilation: all retained Python source, tests and root FreeCAD
  entry points compile successfully on Python 3.11 (Ubuntu).
- Active command surface: exactly `SMBS_PressBrake2D` and `SMBS_Tooling`.
- Live **native D6.3.4 GUI** fixture context switching passed previously on
  the **separate `d6-native-3d` branch**. That result must not be copied
  to 2D as evidence of a passing Full Cycle or automatic sequence search.
- 17 original demo FCStd part files are **not checked into the branch yet**;
  a manifest and a hash-checked local installer are supplied instead.

## Open release blockers

1. Fresh clean-install Windows FreeCAD test of both commands and all
   controls, including close/reopen and active document preservation.
2. Original FCStd model binaries in `demo/parts/` and model-by-model
   acceptance for L, Z, 135, U, boxes, 8/10-bend models and negative cases.
3. Stabilize Full Cycle, support checks and true 3D turn between nonparallel
   bend axes; otherwise explicitly report unsupported/unknown.
4. Confirm trace JSONL and summary write in actual FreeCAD user data directory
   and no source FCStd modification.
5. Manual order physical rejection and a bounded, honest automatic
   manufacturing-order solver if the release advertises such a capability.
6. Cross-platform native FreeCAD smoke tests and complete release documents.

## Release safety contract

A 2.5D **preview is not an industrial safety certificate**.
Machine/collision or sequence statuses must distinguish preview,
sampled-evidence, unsupported, unknown, and independently validated results.
No unresolved blocker may be labelled PRODUCTION_READY.

For the active checklist see `PRESSBRAKESIM_25D_RELEASE_GATES.md`.
For decisions and removed files see `2D_BRANCH_CLEANUP_AUDIT.md`.

## Stage 1 (2026-11-06): Full Cycle failure-state stabilization — native FreeCAD acceptance PASS (8-bend fixture)

Change summary on authoritative `2D` branch:
- A failure during a Full Cycle Qt timer tick now pauses the underlying
  headless press-cycle/sequence controller, stops the single Qt timer,
  records the error in JSONL, finalizes the trace and latches a machine
  pose fault. `PLAYING` cannot continue silently after an exception.
- `Play All` and `Resume` catch both `SequenceError` and
  `PressCycleError`, refusing to start on an unsupported transfer state.
- Transfer construction failures remain fail-closed: no active bend switch,
  no fabricated successful motion; the FoldState and transforms are preserved.
  A dedicated pure Python regression asserts this behavior.
- Removed an unreachable duplicate transfer exception statement; no second
  simulation engine or independent timer was introduced.
- Made the existing native FreeCAD 8-bend GUI suite find the original box
  model in `demo/parts/` on all OSes instead of a hardcoded Mac Desktop.
- Added `tools/run_25d_stage1_gui_acceptance.py`, a real FreeCAD GUI test
  runner that rejects skipped tests and verifies SHA-256 of the source FCStd
  before/after the test.

**Evidence (user-run native FreeCAD on Windows, 2026-11-06):**
`tools.run_25d_stage1_gui_acceptance.run()` finished with
`STAGE1_GUI_ACCEPTANCE_PASS: 6 tests`, `errors=0`, `failures=0`,
`skipped=0` in 26.982 s on original
`demo/parts/box.100out-50in-(ok).FCStd`. Tests covered nonparallel
B01→B02 transfer without halt, full B01–B08 cycle reaching COMPLETED,
Play/Pause/Resume/Reset, injected Full Cycle tick error, manual JSONL+summary
creation and single-timer workspace layout. The acceptance script also checked
that the test fixture SHA-256 was unchanged.

The user separately confirmed by visual inspection that the previously observed
workpiece position jumps **do not occur** in this tested scenario.

**Scope:** Stage 1 Full Cycle integration is VERIFIED **for the tested Windows
FreeCAD runtime and 8-bend fixture**. This does not certify all 17 models,
real-world machine collision safety, continuous swept-volume clearance,
automatic manufacturing sequence search or cross-platform GUI qualification.

In real FreeCAD Python Console, after checkout `2D` and installing the demo
fixture models:

```python
from tools.run_25d_stage1_gui_acceptance import run
run()
```

Any new failure, skip, missing model or changed source hash in future
regressions reopens the gate. Extend fixture coverage before release.


## Stage 2 — P2 geometry and tooling completeness gate (2026-11-09, pending native acceptance)

First bounded correction in the existing `smbs_core/kinematics2d/coupled_kinematics_2d.py` (not in the experimental 2D search branch): `from_tooling_and_fold_state` previously marked coupling `VERIFIED` whenever punch and die datum objects existed, despite missing/nonpositive/nonfinite individual numeric values silently retaining estimated defaults. A nonexistent active bend similarly inherited sheet defaults. This was inconsistent with P2 release requirements and could promote incomplete geometry to a physical result.

Now a `VERIFIED` coupling requires:
- a real requested `FoldState` bend and its finite, positive thickness and inner radius;
- complete finite, positive die opening, shoulder radius, die angle, and V depth, plus finite sheet-bottom plane datum;
- complete finite, positive punch tip radius and included angle.

Missing/invalid fields still have preview estimate values but **cannot** carry the `VERIFIED` status. This is a stricter evidence contract, not an assertion that supplied tool shapes are physically qualified or that a press cycle is collision-free. The changes retain the existing controller and formula, without adding independent solvers or handlers.

Regressions added to `tests/test_coupled_kinematics_2d.py`: positive complete data, missing V depth, NaN punch radius and nonexistent bend. Actual FreeCAD/CI results for these new commits are pending. If the strengthened check marks existing catalog datums incomplete, investigate the exact source fields; do not re-enable `VERIFIED` by falling back to values.

Native acceptance in FreeCAD on `2D`:

```python
import unittest
r=unittest.TextTestRunner(verbosity=2).run(
    unittest.defaultTestLoader.loadTestsFromName(
        "tests.test_coupled_kinematics_2d"))
print("STAGE2_COUPLING_TESTS_PASS:", r.wasSuccessful())
```

Then run `from tools.run_25d_stage1_gui_acceptance import run; run()` using the existing original 8-bend source fixture. Any degradation reopens Stage 1 and must be diagnosed before expanding P2. Existing P2 release gates remain **unchecked** until all original-sheet geometry/tooling/negative fixtures and native integration checks are accepted.


## Stage 2 native acceptance: geometry PASS, Stage 1 GUI regression found (2026-11-09)

User tested the Stage 2 coupling contract in Windows FreeCAD 1.1 / Python 3.11.14: `tests.test_coupled_kinematics_2d` **14/14 PASS**, repeated twice. The same acceptance session ran `tools.run_25d_stage1_gui_acceptance.run()`; **5/6 passed, 1 failed**, specifically `test_b01_to_b02_nonparallel_transfer_no_halt` at step 203:

```
AUTO PLAY ERROR: Internal C++ object (PySide6.QtWidgets.QGraphicsPolygonItem) already deleted.
STAGE1_GUI_ACCEPTANCE_FAILED: ran=6 failures=1 errors=0 skipped=0
```

The entire 8-bend Full Cycle completed in the separate `test_full_sequence_b01_to_b08_completion`, and controls, trace creation and layout tests also passed. This is an intermittent Qt graphics-lifetime failure, not a demonstrated kinematic collision. **Stage 1 acceptance reopened; do not label currently PASS until native rerun.**

A narrow GUI fix in `freecad_smbs/commands/pressbrake_2d.py` now prevents reentrant `draw()` while `self.scene.clear()` and new QGraphicsScene-owned items are being created. Nested draws are dropped, not queued. A targeted native GUI regression `test_nested_draw_keeps_qgraphics_scene_items_alive` intentionally calls `draw()` again during tooling rendering and checks the draw guard plus scene validity. This fixes one plausible lifetime invalidation mechanism, **not yet proven** to be the sole root cause of the intermittent failure. No controller, FoldState or solver math is changed.

After checking out the latest `2D` branch, restarting FreeCAD to reload Python modules and ensuring the original `box.100out-50in-(ok).FCStd` fixture is available, run:

```python
from tools.run_25d_stage1_gui_acceptance import run
run()
```

The acceptance suite now has **7** tests. If the original failure recurs, collect the complete `draw()` traceback from FreeCAD Report View / addon diagnostic log and the stage1 test's fault step. Preserve failure status and investigate Qt scene ownership rather than suppressing `RuntimeError` or changing its `FAIL` semantics. The 14/14 successful Stage 2 coupling tests remain valid evidence for the headless coupling subcomponent only.


## Native follow-up acceptance (2026-11-09): Stage 1 GUI regression requalified

After the Qt QGraphicsScene nested-redraw guard and its regression test, user reran the actual native Windows FreeCAD 1.1.3 acceptance:

- `STAGE1_GUI_ACCEPTANCE_PASS: 7 tests`, `errors=0`, `failures=0`, runtime 45.385 seconds.
- Original `box.100out-50in-(ok).FCStd` fixture confirmed in local `demo/parts/`.
- B01→B02 nonparallel-axis Full Cycle, full B01→B08 completion, playback controls, nested draw object lifetime, latched tick errors, recording artifacts and single-timer workspace all passed.
- Source fixture SHA-256 before/after remained unchanged (enforced by the runner).
- Stage 2 coupled geometry parameter validation separately passed 14/14 in the same Windows FreeCAD environment (previous user run).

This **restores Stage 1 native acceptance for the tested fixture**. It is not evidence of general physical collision clearance or automatic sequence solving. Next required P2 gate: compare real FoldState bend count, signed angles, thickness and radii against the originating SheetMetal objects/Unfold across the original model fixture matrix. In particular reconcile the observed 1.793-mm planar-section Z-span vs authoritative 1.500-mm sheet thickness before claiming dimensional fidelity. Do not construct synthetic positive test geometry in place of original FCStd files.


## Stage 2 native Z geometry checkpoint (2026-11-09)

User confirmed through a read-only FreeCAD Python Console `build_part_session` call on `Z-10-20-10-(ok)`:
- 2 real bends: `B01 +90.00000000000003 deg`, `B02 -90.00000000000003 deg`.
- Both have thickness 1.5 mm, inner radius 1.0 mm, outer radius 2.5 mm, `moving_left=False`.
- The SheetMetal Unfold reports `KFactor=0.4`.
- `getattr(unfold, "Base", None)` returned `None`. **Not a missing-source finding:** canonical `sheetmetal_unfold_reader.read_unfold` accesses `unfold.baseObject` and has already successfully read the actual reference via `build_part_session`. Check `unfold.baseObject` or `session.unfold_data.source_object/source_face`, not `Base`.

This establishes consistency for bend count, signed fold angles and thickness/radius values from the existing session, **not independent validation against the original source BRep**. Await direct comparison against `session.unfold_data.bends` (SheetMetal BendInfo) and source face, and later model geometry reconstruction / thickness-plane discrepancy investigation, before checking off P2 geometry gates.


## P3 axis-by-axis sequence search scaffold (2026-11-09; NOT yet an operational physical solver)

User's required order: **2D only; optimize feasible bends on one axis family FIRST, then the other family**. Parallel and antiparallel axes belong to the same family. No automatic generic 3D trajectory or transfer optimizer is in scope. Implementation lives in the **existing** `smbs_core/kinematics2d/fold_sequence.py` as `search_axis_grouped_order` and `AxisGroupedSearchResult`, not a second FoldState or sequencing engine. Bounded search tries all bend permutations within one group, then the next; it can also reverse which axis family is first. Prefixes are replayed from flat state for every oracle decision; state restored on all returns/exceptions. Oracle results are strictly `CLEAR`, `BLOCKED`, or `UNKNOWN`; unknown and resource limits never qualify a candidate. The no-order result applies ONLY to axis-grouped permutations, not arbitrary interleaving.

The new `tests/test_axis_grouped_search.py` covers axis grouping (including antiparallel), prefix-state replay, order choice, UNKNOWN, search budget and exception restoration.

**Critical limitation:** This is the deterministic *search orchestration* only. The FreeCAD `Расчёт` button remains disabled until the existing 2D tooling/contact oracle can be supplied for EACH completed-prefix + next-bend check, in machine coordinates, across setup/stroke/retract and supported section components. A callback that always returns CLEAR is NOT an acceptable physical validator. A geometric 3D projection or user-supplied fabricated `CLEAR` cannot be promoted to production search. The above code/tests have not yet been executed in FreeCAD or CI after these commits.

Next action required before claiming physical automatic search: wire the existing physical 2D contact verification to the new bounded order loop (no new collision engine), add real negative fixture tests where a complete-prefix stroke fails, ensure status `UNKNOWN` for unsupported pose or missing hardware, and expose `Расчёт` only for the supported 2D use case after native acceptance.


## P3 native axis grouping smoke test (2026-11-09)

User ran `tests.test_axis_grouped_search` inside Windows FreeCAD: **6/6 PASS** (0.003 s). On original `Z-10-20-10-(ok)`, `build_part_session` plus `search_axis_grouped_order` with a deliberately UNKNOWN-only oracle returned:
- `axis_groups=(('B01', 'B02'),)` (two parallel bend axes; signed bend directions do not split axis families);
- `status=UNKNOWN_UNVERIFIED`, `evaluated_steps=2`, `order=()`.

This proves the grouping/contract smoke test, **not** a physical solver. Existing `check_sheet_tooling_preview` is a section classifier; no direct connected step oracle currently validates the entire completed-prefix setup/stroke/retract cycle. The UI 'Расчёт' button remains disabled. Do not mark P3 automatic search verified until each oracle CLEAR is backed by real 2D evidence on the existing FoldState and tooling. Also the current bounded function finds the first feasible order, not a proven optimum; objective criteria and cost comparison remain pending.


## P3 physical collision oracle bridge: initial read-only setup screen (2026-11-09, native test pending)

`smbs_core/kinematics2d/contact_oracle_2d.py` now includes `make_axis_step_oracle_2d(panel_vertices_xy, tooling, evidence=list)`. It connects the existing `search_axis_grouped_order` iteration to existing `WorkpiecePlacement3D.compute_placement` (machine coordinate placement only), `compute_machine_cutting_plane_section`, `ToolingSetup2D.align_to_cross_section` on a private deep copy, and `check_sheet_tooling_preview` with actual stored PUNCH/DIE polygons. No separate collision solver or fictitious mock CLEAR is introduced in runtime.

For each candidate, the search restores the completed bend prefix, computes the next active bend's central 2D machine setup, checks all extracted section components against punch and die. Confirmed cross-sectional material overlap returns `BLOCKED` with evidence. Missing tooling, unsupported/incomplete machine cutting plane, TOUCH/UNVERIFIED, or geometrically CLEAR but *unsampled* bend stroke returns `UNKNOWN`. The adapter deliberately does not return `CLEAR` from an initial parked setup alone; hence it cannot yet produce `FOUND_2D_ORACLE`. This is first-step **collision-based pruning**, not completed automatic physical sequence finding.

`tests/test_axis_grouped_search.py` includes extra bridge tests for missing tools, confirmed interference, and refusal to certify parked setup as a full stroke. No current evidence of tests run on these commits; pending Windows FreeCAD suite and real model screening.

Native diagnostic in FreeCAD, with a loaded `session` and actual verified ToolingSetup2D named `tooling`:
```python
from smbs_core.kinematics2d.fold_sequence import search_axis_grouped_order
from smbs_core.kinematics2d.contact_oracle_2d import make_axis_step_oracle_2d
evidence=[]
oracle=make_axis_step_oracle_2d(session.panel_vertices_xy,tooling,evidence=evidence)
result=search_axis_grouped_order(session.fold_state,oracle,max_evaluations=2000)
print(result.status,result.axis_groups,result.evaluated_steps,result.order)
for item in evidence: print(item)
```

Do not activate the production `Расчёт` UI button or report a valid sequence until the complete 2D setup/stroke/retract contract is implemented. The contact screen's `BLOCKED` evidence covers only the checked central 2D machine section, not every longitudinal section or external hardware.


## P3 native 2D-bridge regression fix (2026-11-09)

User ran the nine `tests.test_axis_grouped_search` in Windows FreeCAD: 7 passed, 2 failed. Both new mocked bridge tests returned `UNKNOWN` because a mocked pose was the plain Python `object()`, yielding `unsupported_setup: 'object' object has no attribute 'apply'`. The test replaced the module's `compute_machine_cutting_plane_section`, but the bridge factory had captured its original function reference before the patch. This is a **test injection/late-binding problem**, not evidence of a physical sheet interference calculation failure.

Fixed in `contact_oracle_2d.py`: resolve `compute_machine_cutting_plane_section` and `WorkpiecePlacement3D` inside `evaluate_step` after input checks, so the two existing regression patches now select the intended test double. The runtime still invokes the same underlying section adapter and same contact oracle; this patch does not promote any `UNKNOWN` to `CLEAR`.

Separate FreeCAD console error: the user's `if punch is not None:` block was followed by another `if` at an incomplete console indentation boundary, causing `SyntaxError`. Rerun selections as simple single-line statements or complete each interactive block with a blank line.

The library's first verified die selection in the native session was `DIE_AM_2089_60°_V80 _H115` (80-mm V opening), which is **not** necessarily the selected 12-mm V die previously used for the Z-part. Never silently auto-select the first catalog entry as the intended real tooling. Require exact identification before interpreting geometry results. The fix and nine-test native acceptance are pending re-run.


## 2D Z fixture collision evidence and zero-angle polygon fix (2026-11-09)

Native Windows FreeCAD, original `Z-10-20-10-(ok)`, two parallel axis-family bends, verified catalog profiles `PUNCH_AP_1017_88°_H104,65_R0,8` and `T-DIE_AT_3015_88°_V12_H80`: user observed two setup probes, both `UNKNOWN`, no suggested order. The die contact classifier found `TOUCH` for `active_fixed` and `active_moving`; it reported `UNVERIFIED: Degenerate polygon edge` for the active zero-angle bend zone and inactive bend zone. Therefore this is **not** a finding of physical interference or proof of machine feasibility.

Root cause localized to `BendZone.section_profile(angle_deg=0, segments=32)`: at zero angle, each of the arc samples is an identical vertex, and the polygon classifier `_proper_cross` rejected adjacent identical points. In `contact_oracle_2d._validate_ring` remove only duplicate consecutive coordinates within **1e-9 mm** (not all edges below 0.02-mm physical contact tolerance), including a repeated closing point. Keep truly short finite edges unaltered; `Degenerate polygon edge` still yields UNVERIFIED, not false CLEAR. No FoldState, geometry generation or contact tolerances changed.

Regression tests added to `tests/test_contact_oracle_2d.py` for zero-angle repeated vertices under both CLEAR and INTERFERENCE cases, and a nonzero sub-tolerance feature that must still fail closed. These are awaiting native FreeCAD rerun. Run `tests.test_contact_oracle_2d` plus `tests.test_axis_grouped_search`; then repeat actual Z-catalog-tools diagnostic. Expect a change from `Degenerate polygon edge` to a concrete setup contact status/reason, but **do not expect** a qualified automatic sequence until continuous stroke verification is implemented and accepted.


## P3 native Z collision regression acceptance (2026-11-09)

User reran `tests.test_contact_oracle_2d` and `tests.test_axis_grouped_search` in real Windows FreeCAD: **27/27 PASS** (0.145 seconds). On `Z-10-20-10-(ok)` with `PUNCH_AP_1017_88°_H104,65_R0,8` and `T-DIE_AT_3015_88°_V12_H80`, both B01 and B02 yielded `UNKNOWN setup_boundary_contact_unverified`, with exactly two supported findings per candidate: `DIE/active_fixed=TOUCH boundaries_within_tolerance` and `DIE/active_moving=TOUCH boundaries_within_tolerance`. No `Degenerate polygon edge` or observed `INTERFERENCE` remains at that initial setup. `UNKNOWN_UNVERIFIED` is expected; do not infer full bend feasibility.

Existing `CoupledAirBendingKinematics` already provides `stroke_for_angle`, `ram_z_for_angle`, `support_plane_zm`, and `validate_geometric_constraints`; these must be reused when developing continuous 2D stroke checks, with all prior folds retained and all supported sheet components inspected. The selected tool's physical datums and ram placement must be reconciled in common machine coordinates at each stage. Do not consider finite samples alone a continuous-path collision proof, and do not promote `TOUCH` or `UNKNOWN` to success without evidence. GUI calculation remains unqualified.


## Stage 2: phase-sampled 2D collision diagnostic (2026-11-09; acceptance pending)

Added `sample_bend_stroke_2d(fold_state, panel_vertices_xy, tooling, bend_id, samples=12)` to the **existing** `smbs_core/kinematics2d/contact_oracle_2d.py` (no separate solver). Uses actual verified ToolingSetup2D, existing `CoupledAirBendingKinematics.from_tooling_and_fold_state`, machine-space pose/cutting-section adapter, and `check_sheet_tooling_preview`. Inspects discrete `APPROACH`, `CONTACT`, intermediate `BENDING`, `BOTTOM`, and `RETRACT` frames. Earlier bend angles remain applied; tooling/deformation use local copies, and the caller FoldState is restored. It stores per-frame phase, angle, ram height, geometry findings and kinematic constraint decisions.

**Important**: this is diagnostic Stage 2 progress, not automatic collision certification. It checks central 2D section at finite angles only, not the swept volume between frames, noncentral sheet/tool spans, handling/transfer or machine backgauge/clamps/frame. Only physically verified tooling inputs avoid early UNKNOWN; the returned top-level status is always `SAMPLED_2D_NOT_CONTINUOUSLY_VERIFIED` rather than a qualified CLEAR. Actual setup or punch/ram geometries may require coordinate reconciliation if frame reports show contradictions. Do NOT enable the automatic sequence button from this result or mistake finite-frame checks for complete feasibility.

Additional regression tests in `tests/test_axis_grouped_search.py`: 1) invalid sample count, 2) missing tool datums/verified geometry, 3) phase progression and restoration of an already folded bend. **These latest changes have not been rerun in native FreeCAD/CI.**

Recommended user acceptance on original Z model with verified punch AP1017 and V12 die:
```python
from smbs_core.kinematics2d.contact_oracle_2d import sample_bend_stroke_2d
for bid in ("B01","B02"):
    report = sample_bend_stroke_2d(session.fold_state, session.panel_vertices_xy,
                                   tooling, bid, samples=12)
    print(bid, report["status"], report["reason"])
    for frame in report["frames"]:
        print(frame["phase"], round(frame["angle_deg"],2),
              frame.get("contact"), frame.get("kinematics"),
              frame.get("status"), frame.get("findings"))
```


## Stage 2 native Z stroke logs — absolute versus relative support-plane coordinates (2026-11-09)

User native Windows FreeCAD ran `tests.test_contact_oracle_2d` and `tests.test_axis_grouped_search`: **30/30 PASS (0.206 s)**. The new `sample_bend_stroke_2d` diagnostic on `Z-10-20-10-(ok)` using `PUNCH_AP_1017_88°_H104,65_R0,8` / `T-DIE_AT_3015_88°_V12_H80` returned:
- **B01**: `DIE/active_fixed` and `DIE/active_moving` `INTERFERENCE/proper_material_boundary_crossing` already in `APPROACH 0°` and `CONTACT 0°`; mid-bend repeatedly interferes with die; at BOTTOM and RETRACT `CLEAR`.
- **B02**: initial `TOUCH` (valid sheet/die bearing contact), mid-bend `UNVERIFIED Degenerate polygon edge` at -7.5..-30°; `PUNCH` `INTERFERENCE` starting approximately -37.5° and continuing to -90°; RETRACT `CLEAR`.
- `GEOMETRIC_COUPLING_VERIFIED` in the log refers to the coupled analytical constraint predicate only, *not* the contact report; it does not disprove or qualify tool collision findings.

Found concrete coordinate inconsistency in the diagnostic: the initial tooling is aligned against `compute_placement(...support_plane_zm=0)` for the flat workpiece, but each phase recalculated the placement using `kin.support_plane_zm(angle)`, which includes **absolute `die_top_z_mm`**. That shifted the sheet by die-top offset a second time (in particular at 0°), potentially creating false die interference before the ram touches the sheet.

Fixed diagnostic to **reuse exact initial pose** for APPROACH and CONTACT and apply only `kin.support_plane_zm(angle)-kin.support_plane_zm(0)` as displacement for BENDING/BOTTOM/RETRACT. Added `support_delta_z_mm` frame evidence and a regression assertion with a deliberately nonzero die datum (`-7.25 mm`). This is a targeted machine-reference correction, **not evidence that the native Z-part collision anomalies are solved**. The user must rerun native Stage 2 diagnostic to verify the B01 initial die classification returns to the original `TOUCH`. Negative B02's later punch overlaps may require separate orientation/flip and physical-pose fixes; short finite bend-zone edges at intermediate angles still need a numerical tolerance strategy. Do not suppress real overlap classifications or artificially widen clearances. Stage 2 remains open.
