# Performance optimization: live AI-agent handoff

Updated: 2026-11-13. Canonical development branch: **development/performance-optimization**.
Base branch: **fix/conditional-endzone-volume-tolerance** (keep untouched).
Normative specification: [PERFORMANCE_OPTIMIZATION_MASTER_PLAN.md](PERFORMANCE_OPTIMIZATION_MASTER_PLAN.md).
Agent entry: [../AGENTS.md](../AGENTS.md).
Benchmark procedure: [PERFORMANCE_BENCHMARK_PROTOCOL.md](PERFORMANCE_BENCHMARK_PROTOCOL.md).

## Project intent

The user's prime pain point is **very slow native 3D validation even for an explicit operator sequence**; naïvely searching all possible orders with full OCC would scale disastrously. We are **not** adding punch relief geometry or changing the interface in this branch. Goal is evidence-based native CAD speedups plus staged search, retaining current working behavior.

## Known baseline facts, not speedup claims

- FreeCAD SheetMetal ten-bend reference: `2-1-3-4-(ok).FCStd`. Saved manual order: `B07, B03, B05, B01, B08, B04, B09, B10, B06, B02`; 180° operator orientation; AP1017 and AT3015 V12. Conditional end-zone 10 mm when selected. Exact input file SHA and actual installed FreeCAD module SHA need capture from the next user runtime.
- Historical logs: fast Manual 2D could screen 10/10 with B02 RETRACT warning, but that is not 3D certification. Native D6 sampled-clear B07/B03/B05/B01 on a prior revision; B08 initially reported central micro-sliver. The preceding branch modified Conditional volume classification, **NOT** independently accepted on the full live part after this change.
- Native implementation already has per-validator caches, AABB filtering, adaptive sampling, collision-onset early stop and instrumentation counters; none should be reimplemented blindly.
- Long-term exact native model must keep OCC common/geometry checks. Visual collision inspector and Desktop journal already exist.
- **No measured performance baseline has yet been captured for THIS branch at its HEAD.** Do not invent one.

## Phase progress table

| Gate | Status | Evidence required / outcome |
| --- | --- | --- |
| A0 branch and governance | IMPLEMENTED | `AGENTS.md`, master plan, this handoff, benchmark protocol |
| A1 headless performance log analyzer | HEADLESS_TESTED | `performance_report.py` read-only CLI, multiple/partial runs, missing fields = null; unit tests green |
| A2 per-phase OCC/caching instrumentation | HEADLESS_TESTED | `summarize_native_trajectory` reports existing counters; native Manual D6 wraps `count_native_kernel_calls` and emits `PERF_NATIVE_BEND_KERNEL`; no changes to collisions |
| A3 live reference measurements | HISTORICAL_LOG_ANALYZED / NEW_RUNTIME_NOT_MEASURED | Historical 691.6417 s five-bend log6 documented; new branch needs FreeCAD runs |
| B targeted reuse and duplicate OCC removal | HEADLESS_TESTED / NATIVE_NOT_VERIFIED | Single OCC Common for Conditional; conservative AABB-gap fast rejection, no changed physical tolerances |
| C conservative panel/zone broad phase | ZONE_CACHE_HEADLESS_TESTED / BROAD_PHASE_NOT_STARTED | Bounded keyed native bend-zone cache added; component broad-phase still unimplemented, live FreeCAD comparison required |
| D persistent cross-state caching | FULL_SHEET_LRU_HEADLESS_TESTED / CROSS_STATE_NOT_STARTED | Full-sheet local cache bounded to 64 poses; no shared cache across candidate/worker boundaries yet |
| E process-based CPU parallelism | NOT_STARTED | 1/2/4 worker scaling, bounded RAM, isolation |
| F multi-stage automatic search | NOT_STARTED | Deterministic candidates, conservative pruning |
| G optional GPU/alternate engine | DEFERRED | Only if profiling proves need |

## Next agent's exact action

1. **Start at Phase A3 native validation:** this branch contains already committed Phase B/C fastpaths and geometry caching; do not make further geometry changes until a real FreeCAD regression run verifies them against baseline. Check out branch, verify HEAD, loaded `Mod` module, and no duplicate installed SMBS addon.
2. Run the reference 10-bend manual native 3D sequence in FreeCAD at identical settings. One first run is sufficient to begin if it is prohibitively slow; collect further cold/warm runs when feasible.
3. From repo root, run `python -m smbs_core.kinematics2d.performance_report "~/Desktop/SMBS_debug_log.json" --json --output "~/Desktop/SMBS_perf_baseline.json"` (adjust Windows Desktop path).
4. Verify `PERF_NATIVE_BEND_KERNEL` and existing `MANUAL_NATIVE_BEND_TRAJECTORY` appear for each checked bend. If missing, report loaded runtime path/SHA instead of assuming new source was installed.
5. Collect per-bend wall time, Common/distance calls and elapsed, full-solid reconstructions, phase samples, cache hits, and CPU/peak RAM. Record real FreeCAD/OCC version and hashes for model and tools. Never invent missing counters.
6. Compare those metrics and physical statuses with the previous unchanged baseline where available; select the **first** optimisation experiment only after identifying the dominant cost (e.g. duplicated Common, multiFuse, or cross-state cache misses).
7. Update this handoff with evidence and exact CI + native test information. Do not launch Phase C or multiprocess changes before Phase A3 is reported.

## Benchmark/report vocabulary

- `elapsed_s`: wall time of a run or bend.
- `solid_reconstructions`: complete native sheet reconstruction count.
- `common_calls`, `common_elapsed_s`: instrumented OCC Part.common; do not mistake query count for unique booleans.
- `distance_calls`, `distance_elapsed_s`: instrumented OCC distToShape.
- `sample_count`: per-phase unique sampled poses; no guarantee of continuous clearance.
- `cache_hits` / `cache_misses`: exact counts by cache type where present.
- `verdict`: COLLISION / CONDITIONAL / UNVERIFIED / sampled clear, with same physical scope and notes.
- `runtime_commit`, `runtime_module_file`, `file_hashes`, `settings`: prevent accidental old-module comparisons.

## Last code outcomes and known caveats

- The restored quick 2D verifier must remain separate from native 3D; DO NOT change its default semantics.
- Conditional still means permission under an artificial end-zone rule, not a physically cut real punch.
- Transfer, backgauge, clamps, frame and continuously swept motion are still not independently certified by current native per-bend checks.
- An exact native visualization may invoke additional expensive OCC separately; baseline validation must distinguish validation cost from **post-collision forensic inspection** cost.
- Potentially stale README in base branch refers to an older handmode distribution; prioritize branch code + this handoff. Updating unrelated installer docs is out of scope.

## Completion protocol

At the end of any work, edit this document to append a timestamped entry with: branch & commit, changed paths, hypothesis, unit/CI checks, real FreeCAD evidence (or explicitly unavailable), speedup ratio if measured (or NOT_MEASURED), safety regression result, next task. Leave no vague "everything works" claims.


## 2026-11-13 — Phase A0/A1/A2 headless implementation

- Branch: `development/performance-optimization`; source used: `fix/conditional-endzone-volume-tolerance` (working baseline retained).
- Initial plan: `bc03d3c`; agent rules: `e8e9e80`; handoff: `08f44fc`; benchmark protocol: `31980ca`.
- Native journal field extension: `cd70000` in `smbs_core/kinematics2d/developer_debug_log.py`, new counters only.
- Native Part call timing: `a1569ab` in `manual_physical_validation.py` — context-scoped `common/distToShape` counts and durations, including failed D6 calls, excludes later collision forensics.
- Read-only parser: `e944150` in `performance_report.py`; tests: `1b0fc15` in `test_performance_report.py`.
- CI entry: `a0c49fd`, [successful GitHub Actions run](https://github.com/Badsmc/SMBS_BendSeq/actions/runs/43381662295), compile and test workflow (20 steps, all green).
- Actual FreeCAD machine validation: **NOT RUN from this conversation**; numerical baseline and speedup: **NOT MEASURED**. No claim of acceleration or geometrical qualification.
- Next step: Phase A3 real FreeCAD benchmark with confirmed runtime module and journal; then narrow Phase B to the measured hotspot.


## 2026-11-13 — Historical hotspot analysis and native algorithm optimization (CURRENT)

**Historical evidence:** `docs/PERFORMANCE_HISTORICAL_LOG6_BASELINE.md`.
One prior manual native 3D run on an older branch recorded 691.6417s for 5 checked bends before B08 stopped (NOT a safe complete 10-bend sequence). Approximate subtotal breakdown: 259.047s full-sheet builds, 218.441s punch checks, 158.904s die checks; 607.259s in BENDING. Original user debug log was read-only and was not committed. **This is NOT a matched benchmark of the current optimization branch.**

**Completed source changes on `development/performance-optimization`:**

- `1cf1e84` — `native_collision_3d.py`: classifier reuses the **same exact OCC Common solid** from punch pair check; no second boolean for a normal penetrating/Conditional pose. Only existing volume tolerance; classifier still returns UNCLASSIFIED when evidence is missing.
- `6239ca1` — safe AABB lower-bound reject above the actual near/contact threshold, replacing the arbitrary previous 5mm margin.
- `19584eb` — additional conservative proof: when two AABBs have a strict positive gap but are close, evaluate their exact distance, **skip Common** (no shared volume possible).
- `0187863`, `13ff5e8` — dedicated `test_native_collision_fastpath.py` regression suite, including one-Common counting, central blocker, enclosure, near/touching and unknown. An intermediate CI run failed due to test expecting the OLD redundant Common at the exact AABB threshold; test was corrected to assert the new proof condition, final headless CI green. No runtime collision tolerance relaxed.
- `f525dba` — `manual_physical_validation.py` records exact breakdown of native build regions `panel_extrude`, `panel_transform`, `bend_zone_build`, `sheet_union`, `sheet_qualification` next to Part.common and distance; excludes post-collision inspection cost.
- `f008142`, `dce369a`, `acfe27b` — bounded cache of `NativeBendSolid` keyed by immutable BendZone, angle, fixed-panel rigid transform, moving_left and segments. Each cached Part.Shape is copied before OCC use. Source geometry/config changes invalidate cache. **Full native union/topology/volume qualification is not skipped.**
- `db1539b`, `9a85d8d` — tests and CI for zone cache hit/miss, invalidation, LRU, independent shapes.
- `b44c375`, `87edc28` — performance JSON analyzer reports actual build-region timings and zone-cache statistics, preserving null for fields absent in older logs.
- `a895a1e`, `cc22f46`, `64ab3cc` — per-validator full-sheet OCC cache bounded to **64 most recently used solids** to prevent unbounded RAM retention, tested with eviction semantics.
- The last successful source test pipeline for these changes includes [CI at `64ab3cc`](https://github.com/Badsmc/SMBS_BendSeq/actions/runs/43386136252). Later documentation-only commits also run CI.

**Current validation state:** HEADLESS_TESTED; actual optimized FreeCAD timing, B08 outcome, full 10-bend regression, peak RAM, and performance ratio **NOT_MEASURED**. No claim of 2× or 10× acceleration yet.

**Exactly next:** install this branch in FreeCAD (single Mod copy), run manual native 3D on reference model with identical tool/Conditional settings, record log and analyzer output. Verify per-bend physical verdicts and inspect `geometry_build.elapsed_s.sheet_union` vs `bend_zone_build`, `kernel.common_calls` vs `distance_calls`. Test that first four sampled clear bends stay clear and B08 new Conditional classification behaves as on the corrected baseline. Investigate exceptions/UNVERIFIED immediately. Then benchmark before/after with baseline branch under matched source and model; only after success implement component broad phase or process workers.
