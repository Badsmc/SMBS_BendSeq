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
- Limits optional probes to **0–8 new full-sheet poses per bend**, and up to
  **4 exact candidates per tool per pose**. Excess candidates defer to the
  reference oracle; there is no forced unsafe decision.
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

   This enables up to two pre-fuse shadow probes **per bend**, without changing
   the original collision results. It adds work and may run slower than normal.
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

Current reference manual run without shadow was **1046.8586s** for ten bends
(log 20261113-130304). It had 297 full-sheet fusions. No shadow-run wall
time or candidate histogram has been provided yet; **no component-speedup
percentage can be asserted**. The previous shared-pose workpiece validity
optimization is separately verified on the single reference part.

## Immediate next action

Run the two-probe-per-bend native shadow comparison above and inspect
`PERF_NATIVE_BEND_KERNEL.component_shadow` for candidate counts,
fallbacks and disagreements. Use that measured evidence to determine the
proven-reject gating and topology validation strategy. Do **not** turn
`FALLBACK_REQUIRED` into `CLEAR` to meet a speed target.
