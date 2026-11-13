# Historical native D6 workload: SMBS_debug_log(6).json

Recorded input: user-provided Desktop developer log from the **older**
`fix/restore-legacy-manual-2d-check` runtime. Analyzed on 2026-11-13
with read-only Python aggregation. The raw user log remains outside GitHub
and has NOT been modified or published. This is useful hotspot evidence
but **NOT a matched before/after benchmark for the performance branch**.

## Scope, controls and evidence boundaries

- Reference part: `2-1-3-4-(ok).FCStd`, 10 folds.
- Selected physical tool source identifiers: AP1017 punch, AT3015 V12 die,
  with virtual active-bend-length geometry as configured by the user.
- Manual order: B07 → B03 → B05 → B01 → B08 → B04 → B09 → B10 → B06 → B02.
- Second observed native run: 180-degree operator loading; Conditional end
  zones enabled (10 mm).
- Result: `COLLISION` reported at B08 on older implementation, stopped
  after B08 (5 checked), NOT a completed or safe sequence. The subsequent
  branch `fix/conditional-endzone-volume-tolerance` changed how tiny
  central numerical OCC slivers are classified; this older B08 verdict
  is not the outcome of that later fix.
- Total wall time reported by the native Manual run: **691.6417 s**.
- Parsed event types: `MANUAL_NATIVE_CHECK_STARTED`,
  `MANUAL_NATIVE_BEND_TRAJECTORY`, `MANUAL_NATIVE_CHECK_FINISHED`.
- Per-phase `TIMINGS` strings provide timings for sheet rebuild,
  actual punch evaluation, actual die evaluation and Python cache lookup;
  they do not individually separate exact distance from Common.

## Second run: per-bend sampled D6

| Bend | D6 elapsed (s) | BENDING elapsed (s) | BENDING sampled states | D6 status |
| --- | ---: | ---: | ---: | --- |
| B07 | 90.2 | 84.69 | 33 | SAMPLED_3D_CLEAR_NOT_CONTINUOUS |
| B03 | 126.5 | 119.77 | 33 | SAMPLED_3D_CLEAR_NOT_CONTINUOUS |
| B05 | 171.0 | 156.05 | 33 | SAMPLED_3D_CLEAR_NOT_CONTINUOUS |
| B01 | 200.5 | 188.11 | 33 | SAMPLED_3D_CLEAR_NOT_CONTINUOUS |
| B08 | 72.4 | 58.63 | 6 | COLLISION (historical classifier) |

Elapsed entries are rounded independently. Other phases include APPROACH,
CONTACT, BOTTOM and RETRACT when reached.

## All five checked bends: summed instrumented timing fields

| Instrumented category | Sum (s) | Share of total Manual wall time |
| --- | ---: | ---: |
| Full sheet solid reconstruction | 259.047 | 37.5% |
| Punch collision evaluation | 218.441 | 31.6% |
| Die collision evaluation | 158.904 | 23.0% |
| Dictionary cache lookups | 0.011 | ~0.002% |
| Other run time and unclassified overhead | ~55.239 | ~8.0% |

The categories above reflect the existing `TIMINGS` attribution, NOT
a whole-program CPU profiler. The `BENDING` phases alone sum to about
**607.259 s (87.8%)**, showing the dominant hotspot. At B01, full-sheet
reconstruction in BENDING took **86.625 s**, punch evaluation 50.940 s,
die evaluation 47.219 s. One 90° bend therefore cost several minutes
largely due to 32 unique intermediate 3D poses.

## Optimization experiments justified by these metrics

1. Reuse the punch's first OCC Common for Conditional end-zone classification
   instead of recomputing it for the same pose. Preserve actual sampled
   collisions and conditional policy.
2. Avoid exact distance for AABB-separated pairs beyond near/contact
   thresholds; avoid Common when positive AABB separation proves no
   shared material even if shapes are near.
3. Reuse exact immutable bend-zone shapes whose signed angle and fixed-panel
   rigid transform are identical across intermediate samples; still
   multiFuse and qualify the full sheet at every unique pose.
4. Bound full-sheet pose cache memory to limit RAM use during long checks.
5. Instrument individual `sheet_union` and `bend_zone_build` times on
   the optimized branch before changing 3D topology implementation.

## Status

**Historical hotspot analysis only.** CPU acceleration ratio, OCC count
reduction on real FCStd, new Conditional B08 status, precise peak RAM and
multi-process scaling after the performance commits are **NOT_MEASURED**.
A verified local FreeCAD runtime is required for that acceptance.
