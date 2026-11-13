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
| A3 live reference measurements | NOT_STARTED | Real FreeCAD, source SHA, cold/warm 3+3, matched settings, report |
| B targeted reuse and duplicate OCC removal | NOT_STARTED | Only after baseline |
| C conservative panel/zone broad phase | NOT_STARTED | Proof + independent native validation |
| D persistent cross-state caching | NOT_STARTED | Bounded RAM and invalidation tests |
| E process-based CPU parallelism | NOT_STARTED | 1/2/4 worker scaling, bounded RAM, isolation |
| F multi-stage automatic search | NOT_STARTED | Deterministic candidates, conservative pruning |
| G optional GPU/alternate engine | DEFERRED | Only if profiling proves need |

## Next agent's exact action

1. **Start at Phase A3:** check out this branch, verify its HEAD and that the active FreeCAD Mod module actually points at it; confirm no duplicate installed SMBS addon.
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
