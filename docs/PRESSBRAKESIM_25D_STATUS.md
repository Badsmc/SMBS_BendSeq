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
