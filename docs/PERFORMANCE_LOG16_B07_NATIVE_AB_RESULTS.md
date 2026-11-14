# LOG16 — native Mac B07 A/B: machine-pose reuse has no measured speedup

Date: 2026-11-14. **Actual attached native FreeCAD JSON artifacts**:
`SMBS_B07_pose_baseline.json`, `SMBS_B07_pose_reuse.json`.
Both were inspected as files; this is not inferred from headless mocks.

## Exact common inputs and scope

- FreeCAD 1.1.3 (macOS 12.7.6, Intel x86_64, 4 logical CPUs, Python 3.11.14).
- Same original FCStd part SHA-256:
  `576d3a65b44a8c98cbb5da86cadb0ceb3c486be87b5e33bd5bb9ba2e07a1d37e`.
- Same real punch file SHA-256:
  `91c237e915ba623e5c57fc491e8d498179b3b3af0fffb4f5f2a02955eb5102be`.
- Same real die file SHA-256:
  `b3d3cb5ac5b4fd1d845c940133e3237f9a409647e011f26243e59dff54b33699`.
- Both use `SMBS_BendSeq-experiment-component-collision-oracle-5`;
  same `manual_physical_validation.py` and script paths.
  Git HEAD/code hashes are **not recorded** in the benchmark artifacts,
  so equivalent source checkout is strongly suggested but **NOT proven**.
  OCC version explicitly `NOT_RECORDED`.
- Requested prefix: B07 only, at +90°, reverse axis true, selected punch/die,
  virtual tool length `498.1775817583994 mm`, end-zone policy 20 mm enabled.
  `arc_segments=128`, `volume_tolerance_mm3=1e-4`,
  `max_phase_time_s=300`, `max_evaluated_states_per_phase=64`.
  Component reject/shadow OFF. Same settings except
  `SMBS_MACHINE_POSE_REUSE`: baseline FALSE vs candidate TRUE.
- **Both overall Manual results `UNVERIFIED` intentionally**: only the first
  bend of ten requested; that does not represent a 3D plan acceptance.
- Both B07 D6 sampled states: **47**; B07 status
  `SAMPLED_3D_CLEAR_NOT_CONTINUOUS`,
  `native_status=SAMPLED_INTERVAL_CONVERGED`. RETRACT
  `SAMPLED_CLEAR`, 5 samples, same maximum tiny punch overlap
  `0.000098 mm3` below volume tolerance, die 0.
  No independently certified continuous motion.

## Measurement

| Metric | Base (reuse OFF) | Experimental (reuse ON) | Diff ON–OFF |
|:---|---:|---:|---:|
| Total wall seconds | 85.852764 | 95.007942 | **+9.155178 s** (+10.66%) |
| Manual validation seconds | 81.1164 | 89.9410 | **+8.8246 s** |
| Native B07 step seconds | 81.1146 | 89.9393 | **+8.8247 s** |
| RETRACT phase seconds | 4.0686 | 11.9029 | **+7.8343 s** |
| RETRACT punch evaluation seconds | 3.4703 | 2.7204 | **−0.7499 s** |
| RETRACT solid build seconds | 0.3585 | 0.3956 | **+0.0371 s** |
| RETRACT solid cache hits/misses | 3 / 1 | 3 / 1 | same |
| RETRACT die cache hits/misses | 4 / 0 | 4 / 0 | same |
| RETRACT punch cache hits/misses | 0 / 4 | 0 / 4 | same |

Outcome: **not a speedup**. One A→B sequential test is not an estimate
of stable causal slowdown; machine warm state, load, GC and OCC execution
variance were not controlled. However, no native gain is demonstrated.

In particular RETRACT increased by ~7.83s even though recorded punch
evaluation decreased by ~0.75s and solid-build time changed by only 0.037s.
This suggests the extra elapsed time is in the other timing regions
(`pair_geometry_sanity`, copy/transform, native other operations) or
unaccounted execution variance, but THESE TWO COMPACT JSONS do not provide
OCC-region call/time breakdown or machine-reuse cache hit counts. Do not
claim a root cause yet.

## Action / acceptance

- Keep `SMBS_MACHINE_POSE_REUSE=0` (default) until a replicated native
  test shows stable same-verdict speed improvement.
- Obtain the **already written** Mac Desktop `SMBS_debug_log.json`
  containing both run events; the existing `PERF_NATIVE_BEND_KERNEL`
  and `MANUAL_NATIVE_BEND_TRAJECTORY.native_d6_samples` entries contain
  native Common/distance/union/sanity region timers and
  `total_machine_pose_reuse_hits` / `...misses`.
  Do NOT rerun the full 10-bend 28-minute sequence just for diagnosis.
- Subsequent code change adds opt-in **`include_benchmark_metrics=True`**
  for `run_native_benchmark`, writing full D6 kernel, geometric region,
  reuse hit/miss and compact per-phase counters into each new short
  benchmark JSON without needing the global Desktop log.
  This implementation does **not** retroactively add metrics to the
  already uploaded files; no native comparative rerun has been performed
  after it.
- To prove a speedup, run warm matched OFF→ON→ON→OFF B07 prefix with identical
  part/tool SHA and source/version and compare region counters / results,
  or use a controlled focused native microbenchmark of same-pose BOTTOM/
  RETRACT. Do not infer target 1–3min from this micro-optimization.
- Focus next research on the expensive exact `pair_geometry_sanity`,
  full-sheet qualification and union operations measured in LOG14, and
  Conditional-aware positive-only component rejection when validated
  by independent native replay and full D6.
