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
| D persistent cross-state caching | MANUAL_ORDER_SHARED_CACHE_HEADLESS_TESTED / CROSS_CANDIDATE_NOT_STARTED | Manual D6 now reuses bounded exact sheet/panel/zone geometry across successive bends; tool verdict caches remain local; needs real FreeCAD acceptance |
| E process-based CPU parallelism | NOT_STARTED | 1/2/4 worker scaling, bounded RAM, isolation |
| F multi-stage automatic search | NOT_STARTED | Deterministic candidates, conservative pruning |
| G optional GPU/alternate engine | DEFERRED | Only if profiling proves need |

## Next agent's exact action

1. **First fix version mismatch before native benchmarking:** inspect [PERFORMANCE_LOG7_DIAGNOSTIC.md](PERFORMANCE_LOG7_DIAGNOSTIC.md). Last user log still ran a ZIP of unknown SHA with 117 duplicate punch end-zone Common operations and lacked `geometry_build` / `shared_geometry_cache`. Check out this branch, compare its exact code against the installed `Mod` directory using `python -m tools.verify_installed_runtime --installed "<installed SMBS Mod root>"`; require `match:true`. No duplicate loaded SMBS addons. The newer `SMBS_RUNTIME_READY` must log the composite source SHA. Do NOT claim any matched speedup on the unverified ZIP.
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


## 2026-11-13 — Additional per-Manual-run geometric reuse checkpoint

Source commits after the initial optimization series:

- `d6beea2`: new `native_sequence_geometry_cache.py`, an explicit lifetime-bounded cache with source-binding invalidation, no global shapes/worker sharing, max 16 joined native sheet shapes per Manual check.
- `e8de728`: `AdaptiveTrajectoryValidator3D` accepts this shared geometry cache, while per-bend punch/die collision verdict caches remain independent.
- `61417f3`: optional cache flows through `check_active_25d_bend_trajectory`, preserving default call behavior.
- `ed8bcd3`: `check_manual_3d_sequence` owns one cache for the entire checked order and records its use in `PERF_NATIVE_BEND_KERNEL` developer events.
- `fbf0d88` and `8adfc66`: headless tests and CI contract for bounded shared cache and source/tool isolation. [CI run successful](https://github.com/Badsmc/SMBS_BendSeq/actions/runs/43386827759).

These are implemented improvements to exact-geometry reuse, **not** a measured speedup. The local FreeCAD 1.1 native regression run is still missing, so Phase A3 and production qualification remain open. The next priority is to install this branch, confirm `module_file`, obtain an up-to-date debug log and compare exact native results/statuses with the reference `fix/conditional-endzone-volume-tolerance`. No CPU multiprocess or GPU code has been enabled.


## 2026-11-13 — Additional Phase B/D implementation and conservative comparison

- Direct response to request to continue development in GitHub. Based on the earlier real historical user log, the native run to B08 required **691.6417 s** across five bends, with **607.259 s** in BENDING; these measurements are from a previous runtime, not this optimized branch.
- New exact per-Manual-run `NativeSequenceGeometryCache` (commit `d6beea2`) holds **at most 16 joined sheet shapes**, immutable source-panel prisms and per-bend zone shapes during one Manual selected-order check. No persistent global cache, no cross-process shape sharing.
- Native validators accept this explicitly scoped cache (`e8de728`); physical service passes through `sequence_geometry_cache` (`61417f3`); Manual orchestration instantiates once per selected sequence and logs occupancy/source invalidations (`ed8bcd3`).
- Cache clearing rejects stale source identities, preserves tool-dependent collision verdicts per validator, still recomputes the exact full-sheet boolean and topology checks for new poses. Tests `fbf0d88`, workflow `8adfc66` passed in headless CI.
- `performance_report.py` records per-bend shared cache snapshots (`3848aaf`; assertion `fdb9d5b`).
- Added `--compare` CLI for **two untouched Desktop logs** (`1bf3012`), rejecting numeric speedup if order, selected tooling/sampling parameters, native evidence, first hit or statuses disagree. Tests `27d8578`, signed-angle fix `0683e0f` and protocol docs `993569d`. Even matching logs remain PROVISIONAL until identical input FCStd/tool hashes and real native FreeCAD are independently verified.
- **Native FreeCAD availability in this tool environment:** unavailable (FreeCAD Python module not installed; direct access to the user's macOS/Windows GUI is not provided). No production geometry qualification or numerical speedup is claimed.
- **Next exact action:** install THIS branch into a single FreeCAD Mod directory, execute `B07,B03,B05,B01,B08,...` at original 180°/tool/Conditional settings, inspect `PERF_NATIVE_BEND_KERNEL.geometry_build` and `shared_geometry_cache`, run comparison against matched baseline. If mismatch occurs, revert the geometry-sharing experiment; if matching and faster, qualify then proceed to component broad phase and separate-process CPU scheduling. Never merge into `main`/`smbs` until this native acceptance.


## 2026-11-13 — Automated local FreeCAD native benchmark entry point

- Added working source-level FreeCAD-only launcher `tools/run_native_benchmark.py` (`fd28d32`) for the repository's existing `demo/parts/2-1-3-4-(ok).FCStd`, real FCStd tools AP1017/AT3015, selected ten-bend order, 180° orientation, virtual tool length and 10-mm Conditional. It calls the **actual production `check_manual_3d_sequence`** in FreeCAD Python, not mocks.
- Reuses current D6 native log and writes a separate `Desktop/SMBS_native_benchmark.json` with input part/tool SHA-256 and runtime information. Does not save/modify input FCStd. Pass alternative `unfold_name` or `part` explicitly if needed; unsupported/missing input returns UNVERIFIED with reason.
- Entrypoint test `tests/test_native_benchmark_runner.py` (`03444ef`); workflow now compiles `tools/` and runs test (`d5a617c`). Documentation `docs/PERFORMANCE_BENCHMARK_PROTOCOL.md` includes FreeCAD Python-console example (`5f344cf`).
- Error-safe serialisation now uses existing developer journal converter (`d086e8c`).
- Benchmark report Desktop discovery now reuses the same Windows OneDrive/redirected Desktop resolver as the debug journal (`76ff10c`, `ca5efa5`).
- Latest headless CI: [success for `d086e8c`](https://github.com/Badsmc/SMBS_BendSeq/actions/runs/43388717184). This covers compilation and unit contracts; it did **not** run CAD geometries.
- **Native finalisation still outstanding**: this environment does not contain the actual installed FreeCAD or user's local CAD app. Do not call the optimization production-ready or claim a measured speedup before FreeCAD/SheetMetal native comparison.
- First local run: open FreeCAD Python console, put checked-out branch on sys.path, run `from tools.run_native_benchmark import run; result = run()`. Compare generated Desktop JSON against the actual debug journal and prior baseline, then implement further CPU multiprocess optimization only if verified safe. Retest after each change.


## 2026-11-13 — New supplied SMBS_debug_log(7).json, current priority is source identity

- Historical Windows FreeCAD log had three Manual native checks. The 0° run stopped at sampled B03 punch collision (20.627748 mm³ first sample, 78.1174s wall). Two 180° complete-order checks were 1829.2286s and 1790.2005s and ended at B02 RETRACT proven punch collision; first blocked sample **1360.039981 mm³**.
- Latest 180° statuses: B07 B03 B05 B01 B09 B10 sampled clear (NOT continuous certification); B08 B04 B06 CONDITIONAL; B02 COLLISION at RETRACT. Physical component attribution: P09, P11, B09, P03, P01; do NOT suppress this real overlap as numeric noise.
- Last run spent **1595.5834s in BENDING** and rebuilt the complete sheet **298 times (288 BENDING)**, measured **740 Part.common** (207.157s), **670 native distances** (360.972s), and **117 `punch_end_zone_common` extra booleans**. Source installed from `SMBS_BendSeq-2D` reported git branch/commit UNKNOWN, and new `geometry_build`/`shared_geometry_cache` fields were missing in ALL kernel events. **Therefore the current performance HEAD was not verified as the installed runtime and no benchmarked speedup is claimed.**
- Sanitized evidence and exact interpretation: [PERFORMANCE_LOG7_DIAGNOSTIC.md](PERFORMANCE_LOG7_DIAGNOSTIC.md); original user log kept private and not uploaded into GitHub.
- Added `runtime_build_identity.py` (`c4f3d02`, `a4bb022`) to SHA-256 fingerprint actual installed source and actual imported native module paths even in ZIPs; GUI startup `SMBS_RUNTIME_READY` now includes this provenance (`e004b70`); each native per-bend metric event logs actual module origins (`22beda7`). The existing analyzer preserves them (`194c4c8`).
- Added standalone read-only `tools/verify_installed_runtime.py` (`d33156b`) and tests `tests/test_verify_installed_runtime.py` (`0168b49`), runtime SHA tests `tests/test_runtime_build_identity.py` (`5ecc7f6`, `09232c5`), analyzer test `0a6abbe`, CI registrations `847c802`, `c0b049e`. No geometric collision code was modified in this round.
- Next real FreeCAD test: install current branch (one Mod directory, no old active duplicate), verify exact source fingerprints, repeat identical ten-bend 180° run and collect `PERF_NATIVE_BEND_KERNEL.geometry_build`, `shared_geometry_cache`, actual imported `loaded_native_modules`, and source hashes. Compare physical statuses, conditional zones, B02 hit and measured time with an identically configured baseline. **Do not implement further risky 3D shortcuts or multiprocess OCC until that identity and regression gate is satisfied.**


## 2026-11-13 — Log8 native benchmark and Common-first continuation

- User supplied `SMBS_debug_log(8).json`, containing 3 FreeCAD Windows sessions and **four** Manual native checks. Latest source fingerprint was recorded, and actual per-bend imported native modules resolved to the same single installed Mod root.
- Two 180° checks in the same journal have identical recorded start settings and same per-bend physical status, including B08/B04/B06 CONDITIONAL and B02 RETRACT PUNCH COLLISION (sampled first Common 1360.039981 mm³). Earlier 1790.2005 s; optimized 1512.8474 s; observed **277.3531 s / 15.493%** wall time reduction. Strict input file hashes and cold/warm standardization not available; no qualified speedup yet. Sample counts in APPROACH of B07/B09/B10 decreased 5→3, although phase evidence statuses remained equal.
- Confirmed removal of duplicate Conditional Common: **740→623 Part.common**, `punch_end_zone_common` 117→0. Native distances 670→658. Full sheets still reconstructed/qualified **297** times. Geometry build detailed times: distance 365.852s; native Common 151.356s; sheet qualification 139.553s; sheet union 87.971s; bend-zone handling 82.868s. Cache recorded 574 actual bend-zone misses and 2396 hits. Full detail: [PERFORMANCE_LOG8_DIAGNOSTIC.md](PERFORMANCE_LOG8_DIAGNOSTIC.md).
- Based on measured repeated punch overlap, added **Common-first opt-in** for a new punch sample only if the previous measured punch result had a positive-volume penetration. Exact positive Common skips duplicate distance; otherwise old exact distance fallback preserves CLEAR/NEAR/TOUCHING and detects invalid geometry. Code `1a305ae`, `8460797`; regression tests `a3b2c5d`; CI registration `efaa346`. GitHub Actions **SUCCESS**: https://github.com/Badsmc/SMBS_BendSeq/actions/runs/43402767881.
- Real FreeCAD test of Common-first patch: **NOT RUN**. The supplied log8 preceded the patch and therefore provides only the previous optimized baseline, not proof of the new fastpath. NEXT: deploy newest branch, verify SHA, rerun B07→...→B02 with same FCStd/tools, compare statuses/sample intervals and record speed. Revert patch if physics or reliability regresses. Do not extend to GPU/parallel work before this gate.
