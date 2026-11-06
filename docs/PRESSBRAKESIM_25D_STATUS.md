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

## Stage 1 (2026-11-06): Full Cycle failure-state stabilization — implemented, native GUI pending

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

**Evidence:** GitHub Actions validates pure Python behavior and compiles native
test code. The native FreeCAD GUI acceptance suite has **not yet been executed
in this development session**. Animation transitions, press contact, nonparallel
bend handling and the complete eight-bend Full Cycle therefore remain open.

In real FreeCAD Python Console, after checkout `2D` and installing the demo
fixture models:

```python
from tools.run_25d_stage1_gui_acceptance import run
run()
```

Any exception, skip, missing model, source hash change or failed test means
the Stage 1 acceptance gate remains open. Record logs/screenshots and fix
the actual cause before marking Phase 5B verified.
