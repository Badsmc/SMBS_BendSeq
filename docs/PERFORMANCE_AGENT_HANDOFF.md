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
| A1 headless performance log analyzer | PLANNED | Parse existing Desktop journal into run/bend/phase totals and honest missing-value fields |
| A2 per-phase OCC/caching instrumentation | PLANNED | Extend existing summary only; no physical behavior changes |
| A3 live reference measurements | NOT_STARTED | Real FreeCAD, source SHA, cold/warm 3+3, matched settings, report |
| B targeted reuse and duplicate OCC removal | NOT_STARTED | Only after baseline |
| C conservative panel/zone broad phase | NOT_STARTED | Proof + independent native validation |
| D persistent cross-state caching | NOT_STARTED | Bounded RAM and invalidation tests |
| E process-based CPU parallelism | NOT_STARTED | 1/2/4 worker scaling, bounded RAM, isolation |
| F multi-stage automatic search | NOT_STARTED | Deterministic candidates, conservative pruning |
| G optional GPU/alternate engine | DEFERRED | Only if profiling proves need |

## Next agent's exact action

1. Verify branch HEAD, actual FreeCAD module source, CI green and new docs.
2. Finish Phase A1/A2 by adding a **read-only** parser of `SMBS_debug_log.json` and instrumented native per-phase counts, without invoking OCC or changing search.
3. Run `python -m compileall -q Init.py InitGui.py freecad_smbs smbs_core` and relevant unit tests, then GitHub Actions. Note versions/commit.
4. On local FreeCAD (Windows or macOS), run the exact manual sequence twice (ideally 3 cold + 3 warm) and export a performance JSON report, keeping the original journal intact.
5. Decide the FIRST optimization based on measured distribution of time across sheet construction, Part.common, distanceToShape and cache misses. If Common dominates, determine whether end-zone classification repeats it and profile before changing anything.
6. After each implementation and native benchmark, UPDATE THIS FILE and master plan status, always show measured before/after with same settings.

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
