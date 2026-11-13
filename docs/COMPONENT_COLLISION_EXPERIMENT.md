# Component collision oracle: experimental machine-frame shadow implementation

Date: **2026-11-13**. Branch: `experiment/component-collision-oracle`
(forked from `development/performance-optimization` at `7de4f51d`).
Status: **HEADLESS_TESTED / NATIVE_FREECAD_NOT_YET_RUN FOR SHADOW**.

## Current canonical call graph

`check_manual_3d_sequence`
→ `check_active_25d_bend_trajectory`
→ `AdaptiveTrajectoryValidator3D.create_state_snapshot`
→ `build_multi_bend_sheet_solid`
→ **shared** `build_multi_bend_sheet_components`
→ **optional diagnostic observer of those exact BRep components**
→ legacy full `multiFuse` + strict one-valid-closed-solid / volume qualification
→ legacy `validate_workpiece_tooling_pose` (exact punch/die/Conditional)
→ **advisory comparison** with the observer result.

No geometry or tool validation is replaced, no end-zone tolerance changed,
no source FCStd/ToolLibrary/GUI/FCStd JSON state changed.

## What the new component path does

- Retains the original **11 panel + 10 bend-zone BRep solids**, IDs and
  `FoldState` transforms. The same solids are fed to the old full-sheet union.
- Computes a conservative machine-space AABB from **all eight corners** of
  each component's Part-coordinate native bound box, the actual placement's
  3×3 rigid rotation and its translation, plus a numerical roundoff pad.
- Discards each component with strictly positive AABB gap from the selected
  native tool. Source BReps are not copied or OCC-transformed for those pairs.
- For candidate BReps, copies the source and rigidly transforms the copy with
  the same FreeCAD `App.Matrix` as the full-sheet pose; evaluates the
  original measured OCC exact `Common` against the **actual** punch or die.
- A single positive component intersection exceeding the original
  `volume_tolerance_mm3` is `PROVEN_PENETRATION`, **advisory only**.
  AABB-separated, small/aggregate overlaps, missing inputs, nonrigid placement,
  invalid bounding boxes, OCC exceptions and candidate budget exhaustion
  all yield `FALLBACK_REQUIRED`. There is no fast `CLEAR` classification.
- Limits optional probes to **0–8 per bend**; with the recommended
  **2-probe setting**, one new BENDING pose is sampled and one slot is
  reserved for the first real (non-Conditional) RETRACT blocker. When that
  blocker reused a previously joined full sheet, the experiment rebuilds
  only isolated panel/zone BReps for its diagnostic screen, without a new
  multiFuse. A bend with no blocking RETRACT may therefore have only
  **one** shadow probe.
- Up to **12 exact AABB candidates per tool** may be tested. Additional
  candidates defer to the full old oracle, never fabricate a CLEAR.
  Shadow mode is an experiment and may be *slower* than standard validation.
- Records per-bend counts, timings, candidate sets and comparison flags in
  `PERF_NATIVE_BEND_KERNEL.data.component_shadow` in the existing Desktop
  `SMBS_debug_log.json`. The read-only `performance_report` CLI also
  exposes `component_shadow_*` totals, distinct from normal OCC counters.
- Native module origin/fingerprint includes `component_collision_oracle.py`
  to detect stale ZIP installs and duplicate source roots.

**Important:** The normal exact OCC kernel call counter includes extra
`Common` calls used by the shadow experiment when enabled. The
`component_shadow.component_common_calls` field identifies this extra
work. Do not interpret a shadow-on wall time as an accelerated production
run, or sum shadow Common twice into exact native query totals.

## How to run the native shadow comparison in FreeCAD

1. Install this experiment branch from GitHub in the single intended
   `Mod/SMBS_BendSeq-2D` directory. Restart FreeCAD to unload older modules;
   confirm no second active SMBS installation.
2. In **FreeCAD Python console**, immediately before clicking **Проверить 3D**:

   ```python
   import os
   os.environ["SMBS_COMPONENT_SHADOW_SAMPLES"] = "2"
   ```

   This enables up to one BENDING pre-fuse probe and, if present, one real
   blocking RETRACT probe **per bend**. A cached RETRACT sheet may require a
   diagnostic-only component reconstruction. The original collision result
   remains authoritative; this may run slower than normal.
3. Use the real reference FCStd with exact selected AP1017 punch, AT3015 V12
   die, Manual order `B07 B03 B05 B01 B08 B04 B09 B10 B06 B02`,
   180° orientation, virtual active-bend length and explicitly selected
   Conditional end-zone 10 mm.
4. Save a copy of the resulting Desktop `SMBS_debug_log.json`, including
   `SMBS_RUNTIME_READY`, all `PERF_NATIVE_BEND_KERNEL`,
   `MANUAL_NATIVE_BEND_TRAJECTORY` and `MANUAL_NATIVE_CHECK_FINISHED`
   events. **Do not overwrite the original source FCStd or tooling.**
5. Disable with:
   ```python
   os.environ["SMBS_COMPONENT_SHADOW_SAMPLES"] = "0"
   ```
   Restart for a strictly isolated comparative baseline if desired.

Alternative separate benchmark (the same real FreeCAD Python console):
```python
from tools.run_native_benchmark import run
result = run(
    part=r"C:\absolute\path\to\2-1-3-4-(ok).FCStd",
    component_shadow_samples_per_bend=2,
    output=r"C:\Users\User\Desktop\SMBS_component_shadow_benchmark.json",
)
```
This native runner also records SHA-256 of the real part and tool FCStd
files; the separate Desktop developer journal contains the shadow metrics.
The environment variable, if set, takes precedence over the runner argument.

Report reader after the run:
```powershell
python -m smbs_core.kinematics2d.performance_report "C:\Users\User\Desktop\SMBS_debug_log.json" --json
```

## Native acceptance gates before ANY no-fuse production verdict

1. Prove from installed-source SHA and FreeCAD/OCC/tool/part hashes that the
   exact branch and source BReps were loaded. `UNKNOWN` ZIP Git branch alone
   is not evidence.
2. For **every sampled state**, inspect component count, candidate counts,
   exact Common results, numerical error/uncertainty and incomplete budget.
   Two probes per bend are a sampling diagnostic, NOT full equivalence.
3. Refined B02 **RETRACT** must still report PUNCH COLLISION, including its
   actual central bend-zone B09 overlap. B08/B04/B06 must remain
   `CONDITIONAL`; the end-zone-only hit is not cleared by component AABB.
4. Differential tests on flat, 45° and 90°, inverse signs, adjacent and
   mirrored bends, nested/enclosed components, partial contact, exact
   near-contact and split tool sections. No wrong `CLEAR`, no
   `UNKNOWN/INVALID` silently promoted, no incomplete → qualified.
5. **Sheet topology is independent of tool intersection**: even if all
   individual components are separated from the tool, the full piece may
   contain disconnected seams, self intersections, micro-overlap sums or an
   invalid union. An independent verified topology/safety proof is required
   before eliminating multiFuse on a previously unseen pose.
6. Conditional is a **virtual tool-end-zone exemption**, not a physical
   punch relief cut. End-zone classification must use exact shared geometry
   and preserve real central tool penetration.
7. After these gates, add an explicitly scoped proven-reject fast path for
   suitable search states, with full fallback and an independent final
   full-sheet replay. Only then consider a proven-clear component-only fast
   path, which requires stronger sheet integrity and contact/distance proofs.

## Tests and remaining limits

- `tests/test_component_collision_oracle.py`
- `tests/test_component_machine_shadow.py`
- `tests/test_native_sheet_component_extraction.py`
- `tests/test_component_shadow_trajectory_integration.py`
- `tests/test_native_shared_workpiece_sanity.py`
- `tests/test_performance_report.py`

These are **headless mock regression tests** plus GitHub Actions syntax/
compile/unit tests. They are **not native OCCT certification**.

Reference manual run without shadow was **1046.8586s** for ten bends
(log 20261113-130304). The first, older shadow run
`20261113-141036` took **1047.1784s**, still 297 full-sheet fusions:
20 probes, 603/840 disjoint AABB pairs, 29/40 candidate-budget fallbacks,
zero proven component hits and zero meaningful positive comparisons.
See `PERFORMANCE_LOG12_COMPONENT_SHADOW.md`. The new targeted RETRACT
probing code has **NOT** been live-tested yet, and it is an experimental
comparison, not a runtime speedup. The prior shared-pose workpiece validity
optimization is independently verified on one reference part.

## Immediate next action

Run the **updated** two-slot-per-bend shadow comparison above and confirm
B02's real RETRACT blocker is among the newly examined poses. Inspect
`PERF_NATIVE_BEND_KERNEL.component_shadow` for candidate counts,
fallbacks, proven central B09/P09/P11 punch overlaps and disagreements.
Use measured evidence to determine the proven-reject gating and topology
validation strategy. Do **not** turn `FALLBACK_REQUIRED` into `CLEAR`
to meet a speed target.

## Reject-only D6 integration (2026-11-13)

Status: **IMPLEMENTED / HEADLESS_TESTED / NOT_BENCHMARKED**.
The opt-in path now runs inside `AdaptiveTrajectoryValidator3D.create_state_snapshot`,
not just a standalone replay. It is used by native checking of a proposed sequence.
The section-only DFS in `sequence_search_2d.py` remains 2D; this change does not
implement a new automatic 3D search or a candidate-ranking loop.

Enable before starting native validation in the FreeCAD Python console:

```python
import os
os.environ["SMBS_COMPONENT_REJECT"] = "1"
os.environ["SMBS_COMPONENT_SHADOW_SAMPLES"] = "0"
```

Disable for the independent full oracle (also the default):

```python
os.environ["SMBS_COMPONENT_REJECT"] = "0"
```

The benchmark runner also accepts `run(component_reject_enabled=True)`.
An explicitly set environment flag takes precedence. No new full run is needed
merely to inspect the existing replay JSON. Future performance qualification
requires matched native runs, with the same OS, source hashes and end-zone policy.

Mechanism:

- Transform all eight corners of each authoritative component AABB into the
  exact machine frame. Only overlapping pairs reach exact OCC Common.
- Validate all component and tool solids before accepting a positive subset
  proof. Invalid input, uncertain Common and missing classification fall back.
- Reuse the same Common for the existing punch-zone classifier. With end-zone
  relief enabled, only central/mixed non-waivable volume above the unchanged
  tolerance can reject; end-only hits continue screening and ultimately defer
  to the full validator. No sum of potentially overlapping component volumes.
- A proven collision exits before full union and full-sheet tool checks.
  Unchecked other tooling is `MISSING_EVIDENCE`, never fabricated CLEAR.
  The hit volume is ONE COMPONENT's volume, not the full intersection volume.
- All nonproofs invoke the old full validator. Thus no component-only CLEAR,
  no topology certification and no continuous-motion claim are introduced.
- Reuse up to eight component sets for the same folded geometry at changing ram
  positions (including RETRACT). Invalidate with source/config/tool caches.
  Component proofs do not populate full-sheet or tool-clearance caches.
- Default budgets: 12 candidate pairs and 2 seconds per screen, configurable
  through `AdaptiveSamplingConfig`. A budget expiry abandons the shortcut;
  it cannot cancel an OCC call already in flight. Shadow probes are suppressed
  while reject mode is enabled to avoid duplicate diagnostic work.
- `NATIVE_COMPONENT_REJECT_SCREEN` in the existing desktop JSON records counts,
  elapsed time, fallback reason, component identity and overlap bbox/volume.
  Original native kernel instrumentation counts the actual OCC calls.

Native speedup is **not measured**. Extra screening may slow clear candidates;
use measured fallback/hit rates before changing defaults. Final independent
acceptance must use `SMBS_COMPONENT_REJECT=0`.
