# Experimental X1: exact component OCC rejection before full-sheet multiFuse

Branch: `experiment/component-occ-broadphase`. Base: `development/performance-optimization` at `7de4f51d186436d405cc992c72c2022309e9cadf`.
Status: **HEADLESS_TESTED / FREECAD_NOT_VERIFIED / SPEEDUP_NOT_MEASURED**.

## Goal

Avoid expensive whole-sheet reconstruction **only when an individual actual
SheetMetal panel or BendZone has a proven positive-volume prohibited
intersection with the actual selected punch/die**. This is *collision-only*
screening, not a mesh/BVH CLEAR certificate, full machine check, or continuous
motion certificate. Benchmarks must use actual FreeCAD 1.1 + SheetMetal and
identical input part/tool hashes.

## Source and semantics

- `native_bend_solid_3d.py`: two reusable stages:
  `build_multi_bend_sheet_components` creates all unchanged exact panel and
  zone OCC solids; `join_multi_bend_sheet_components` performs original
  `multiFuse`, one valid closed solid qualification, and strict material
  volume conservation. `build_multi_bend_sheet_solid` still always does both.
- `native_component_collision_3d.py`: transform **copies** of all components
  into machine coordinates, check input validity, reject AABB-separated pairs,
  and call instrumented OCC Common for potential intersecting pairs. A
  significant individual DIE intersection is a blocker. PUNCH rejection when
  Conditional is enabled requires a verified *central* overlap above the
  existing unchanged volume threshold; an end-only, ambiguous or micro hit
  must fall back. Exceptions also fall back; none are called CLEAR.
- `motion_trajectory_3d.py`: `AdaptiveSamplingConfig.component_collision_rejection`
  controls this path. **Default False**. True allows early return on a
  verified single-component blocker and avoids full-sheet join **only for
  those blocked samples**. All other poses reuse built components in the
  original full-sheet oracle. This path never marks a shot as continuously
  certified and does not change tool or numerical tolerances.
- The early report labels the other, skipped tool `MISSING_EVIDENCE`, not
  `CLEAR`. Component overlap is explicitly a lower bound on full-sheet
  overlap, and the report includes component ID, tool, actual Common volume
  and bound. It is not an end-zone/full-body aggregation result.
- In experimental mode, `PERF_COMPONENT_BROADPHASE` events append to the
  existing `SMBS_debug_log.json`: bend/phase/angle, samples, candidate
  pairs, rejected AABB pairs, Common calls, wall time, hit or fallback reason.
- `tools/run_native_benchmark.py` accepts an explicit
  `component_collision_rejection=True` for local side-by-side testing.

## Actual local FreeCAD acceptance (required before enabling by default)

Use one **single** installed source checkout in the FreeCAD environment.
Ensure no stale duplicates in the Mod directories. Open FreeCAD Python console:

```python
import sys
sys.path.insert(0, r"C:\\path\\to\\SMBS_BendSeq")
from tools.run_native_benchmark import run

# Independent output files; both runs use the same real FCStd reference:
baseline = run(
    component_collision_rejection=False,
    output=r"C:\\Users\\User\\Desktop\\SMBS_X1_baseline.json",
)
experimental = run(
    component_collision_rejection=True,
    output=r"C:\\Users\\User\\Desktop\\SMBS_X1_components.json",
)
```

The benchmark uses the real 10-bend part and selected AP1017/AT3015 V12
tooling, reverse axis 180°, Conditional 10 mm (as configured by the runner).
Capture each Desktop developer journal with distinct saved filenames between
runs; do not overwrite the original source files. Use the existing read-only
`performance_report.py --compare` analyzer, and manually verify identical
actual hashes of part and tools.

Acceptance gates:

1. Both modes load exactly the same runtime modules (source hash), part
   SHA-256, tool files, machine orientation, sampling and tolerances.
2. All previously sampled B07/B03/B05/B01/B09/B10 statuses remain sampled
   clear (not continuous); B08/B04/B06 remain Conditional; **B02 RETRACT
   central PUNCH collision remains rejected**, with actual component OCC
   evidence. If sampling changes, compare first blocker and phase envelopes
   rather than claiming byte-equal sampling paths.
3. For fallback samples, strict full-sheet union/qualification and original
   OCC collision oracle still execute. Any unexpected status change or
   missing/false blocker is a **FAIL**.
4. Check new `PERF_COMPONENT_BROADPHASE` event counts and real reduction in
   `sheet_union` / `sheet_qualification` calls, overall wall time, memory
   usage, peak CPU, and end-zone classification evidence.
5. Repeat cold and warm identical runs if feasible; do not claim speedup
   from unpaired trials. Do not merge into main/smbs until this gate passes.

Expected tradeoff: X1 only avoids multiFuse on *blocked* sampled poses.
A valid mostly nonblocking sequence can still require almost every normal
full-sheet build, and speculative extra Commons can make X1 slower. This
experiment alone does NOT promise 60–180 seconds, let alone 20–40 seconds.

## Headless development checks

```shell
python -m compileall -q Init.py InitGui.py freecad_smbs smbs_core tools
python -m unittest discover -s tests -p 'test_native_component_collision.py' -v
python -m unittest discover -s tests -p 'test_native_component_integration.py' -v
python -m unittest discover -s tests -p 'test_native_collision_fastpath.py' -v
python -m unittest discover -s tests -p 'test_native_sheet_single_qualification.py' -v
```

The CI workflow additionally exercises existing GUI/manual and runtime
contracts on Linux; these are **not** native CAD physics tests.

## Next phases (not implemented here)

- X2: formally establish component union equivalence and prove non-collision
  on candidate poses without repeated full-sheet OCC. Treat insufficient
  integrity evidence as UNKNOWN, never CLEAR.
- X3: evaluate conservative BVH/mesh prefilter vs AABB+OCC on real cases;
  log false-negative bounds and native FreeCAD packaging constraints.
- X4: adaptively reduce pose evaluations (retain onset and conditional
  refinement) and parallelize independent search candidates in isolated
  processes only after benchmarked correctness.
