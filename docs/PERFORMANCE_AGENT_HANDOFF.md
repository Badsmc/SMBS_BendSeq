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


## 2026-11-13 — User log9: Common-first observed win; single-pass sheet qualification experiment

- **Native Windows FreeCAD evidence:** user-supplied `SMBS_debug_log(9).json` (original file not committed). Source journal SHA-256 `91a99b8ac3c8cf023f7d9387690e698c882ff405b96ba497cd9dc85edba85734`. Runtime installed native-source composite SHA-256 `e1257326e357081169bc1a8491fcb0925b13d7e2475cf7286e145434dfd26e57` (ZIP without .git). Actual installed Mod root was identified in the log. Compare exact source bytes, not claimed Git commit.
- **Baseline comparison:** log8 last optimized native run 1512.8474s → log9 run **1321.3356s** (22:01.34), observed **191.5118s / 12.659%** time reduction and **1.145×** ratio. Common 623→627; native distance 658→548; measured distance time 365.852→179.738s (186.114s less). Both use same recorded order/settings with all 10 bends checked and identical statuses. However input part/tool SHA-256 absent, repeated controlled runs absent: not fully qualified.
- Physical verdict unchanged: B08/B04/B06 CONDITIONAL, B02 punch COLLISION on RETRACT; B07/B03/B05/B01/B09/B10 sampled-clear but NOT continuous certificate. The first B02 sampled collision was refined from `u=0.25, ram Z≈−0.994513, overlap 1360.04mm³` to `u=0.15625, ram Z≈−1.931328, overlap 1004.605mm³`, with prior clear `u=0.140625`. Central bend_zone:B09 remains ~2.752mm³ positive volume (not within Conditional). An exact OCC inspection scene was successfully opened.
- After this run, identified duplicate `joined.isValid()` and `joined.isClosed()` operations: successful sheet build called both inside strict qualification **and again just to populate its result metadata**. Eliminated ONLY redundant second calls by reusing the already validated state and volume in `native_bend_solid_3d.py`, commit `b87d95d`. Added fail-closed helper tests `1669d73`; CI registration `14749ed`, successful https://github.com/Badsmc/SMBS_BendSeq/actions/runs/43407959920. Strict original one-valid-closed-solid and volume agreement predicates are unchanged. Native FreeCAD speedup from this new code: **NOT MEASURED**.
- Source evidence document: [PERFORMANCE_LOG9_DIAGNOSTIC.md](PERFORMANCE_LOG9_DIAGNOSTIC.md). Structured comparison was generated for the user as `SMBS_log9_analysis.json` in the chat container; DO NOT assume it is available in the GitHub checkout.
- **NEXT ACTION:** deploy newest `development/performance-optimization`, confirm SHA in new Desktop runtime/loader events, rerun identical ten-bend D6, capture `PERF_NATIVE_BEND_KERNEL.geometry_build.elapsed_s.sheet_qualification` and total time vs log9 (1321.3356s), confirm same physical B02 RETRACT result (onset may refine). Only then qualify or revert the redundant-validation optimization. Further costly targets: 297 full-sheet union/qualification passes and 574 bend-zone cache misses; do NOT skip topology checks or assert exact continuous safety.


## 2026-11-13 — GitHub survey for 1–3-minute native checks; begin X0 profiling

- User asked whether reviewing GitHub implementations could cut the **1321.3356s** ten-bend manual native check down to **60–180s**. This is **7.34×–22.02× faster**, an experimental target with no measured guarantee.
- Examined `MoheyElbaz/FreeCAD-ClashCheck` (component solid AABB/face-BBox → exact Common), `open-mrp/dfm/brake` (flange trees and grid sampling), `flexible-collision-library/fcl`, `BerkeleyAutomation/python-fcl`, `coal-library/coal`, FreeCAD `TopoShape.pyi` (proximity, distToShape, Common) and native Boolean parallel wrapper, plus `KanadeK/bendseq` (bounded 2D planner). The surveyed GitHub links and design decisions are recorded in [PERFORMANCE_GITHUB_RESEARCH_COMPONENT_ORACLE.md](PERFORMANCE_GITHUB_RESEARCH_COMPONENT_ORACLE.md).
- **Chosen architecture**: optional *component-wise native OCC oracle* (all 11 source panels and 10 bend zones with exact transforms), fast per-part bounding rejection, exact Common on only candidate component/tool pairs; full-sheet OCC topology/material consistency and independent final replay remain. Do NOT implement mesh/GPU-only PASS or suppress Conditional and self-collisions. For exact union identity, `(⋃Si)∩T=⋃(Si∩T)` if all component geometry truly constitutes the same physical sheet. Handling of summed micro-overlaps, sheet self-collisions and incomplete coverage requires fail-closed differential tests.
- Log9 timed work: native distance ~179.7s, Common ~153.9s, joined-sheet qualification ~139.2s, union ~88.1s, zone build ~82.8s, panel transforms ~7.0s. **~670.5s remaining** are not explained by these measured nonoverlapping categories. Instrument them before claiming the 1–3 min target.
- Implemented Phase X0 deeper opt-in timing instrumentation only (no geometry, collision verdict or sampling change): `native_geometry_build_metrics.py` new categories `pair_geometry_sanity`, `machine_workpiece_copy`, `machine_workpiece_transform`, `native_punch_pose`; `native_collision_3d.py` logs BRep validity before pair filtering; `motion_trajectory_3d.py` logs machine transforms and punch movement. Commits `f0a13b9`, `84f2d2e`, `a97eb0f`.
- Added headless pure-Python regression tests `tests/test_native_unaccounted_timing.py` (`7f797e7`), CI registration (`7a520c4`), [GitHub Actions SUCCESS](https://github.com/Badsmc/SMBS_BendSeq/actions/runs/43410478115).
- **FreeCAD runtime measurement of X0: NOT RUN.** Next exact task: install this development HEAD, check source fingerprint, repeat same manual ten-bend reference with real selected FCStd, and inspect `PERF_NATIVE_BEND_KERNEL.geometry_build.elapsed_s` new categories to explain missing time. Only then implement non-production `COMPONENT_SCREEN` differential prototype and count AABB candidate parts / exact pair booleans. Retain old full native oracle as control; no release merge until matched physical results, including B02 RETRACT and Conditional.


## 2026-11-13 — Child experiment: conservative component collision primitive (NOT IN PRODUCTION)

- Branch `experiment/component-collision-oracle`, forked from `development/performance-optimization` at `7de4f51d`; parent branch not modified.
- New `smbs_core/kinematics2d/component_collision_oracle.py` exposes an experimental AABB / exact native OCC Common screen for already-positioned authoritative panel/bend-zone components. It only produces `PROVEN_PENETRATION` for a component overlap strictly greater than the existing volume tolerance, or `FALLBACK_REQUIRED` otherwise. No CLEAR/Conditional claim is permitted. Exceptions fail closed.
- New `tests/test_component_collision_oracle.py`: deterministic headless unit tests for AABB skipping, real hit, summed small overlaps, bad tolerance, bad inputs and OCC failure. Registered in `.github/workflows/gui-icon-contract.yml`.
- `docs/COMPONENT_COLLISION_EXPERIMENT.md` records assumptions, missing production integration and next verification.
- Production `create_state_snapshot`, full-sheet `multiFuse`, sampling, GUI, existing collision/Conditional handling **not changed**. Real speedup: **NOT MEASURED**; FreeCAD CAD equivalence: **NOT VERIFIED**.
- Next: extract existing exact panel/zone builder so the screen sees exactly the same BRep solids in machine frame; compare all 21 components with full sheet on real FCStd; safely integrate rejection path only with correct Conditional and report semantics. Do not switch default until live differential FreeCAD test.

- Subsequent child-branch source change `f60890b`: extracted shared `NativeSheetComponents` and `build_multi_bend_sheet_components` from existing `build_multi_bend_sheet_solid`. The production full union now consumes these exact component solids; original strict `multiFuse`, topology and volume checks remain. `tests/test_native_sheet_component_extraction.py` checks IDs, extraction reuse, invalid joined shapes and inconsistent volumes with headless mocks; CI workflow includes it.
- Component tool comparison is still **not connected** to the production motion trajectory. This refactor is not a demonstrated performance improvement and needs native FreeCAD reference regression before merging.

## 2026-11-13 — Supplied debug log (10), per-pose shape validity reuse

- Sanitized native evidence: `docs/PERFORMANCE_LOG10_DIAGNOSTIC.md`; original user Desktop JSON **not committed**. Four complete 10-bend runs: 1321.3356, 1185.8569, 1182.6764, 1180.5304 seconds; different installed-source hashes, unchanged physical statuses, 297 full unions per run.
- Latest enhanced X0 sample recorded 1322 `pair_geometry_sanity` invocations totaling 347.9817 s. It is the largest *measured* individual category, and missing timed work reduced to ~186.9 s. Existing 627 Common/548 distance operations are unchanged.
- Child branch `5d65c1a` scopes one workpiece validity check to a single machine pose when both punch/die queries uncached, reusing the result only for the identical object and preserving per-tool validation; no cache across poses. `tests/test_native_shared_workpiece_sanity.py` and CI cover valid, invalid, direct-call, wrong-identity and partial-cache behavior. GitHub Actions run 43424795573: SUCCESS.
- **Live FreeCAD acceptance after this code is NOT DONE; no measured speedup attributable to this optimization.** Component-only screen remains standalone and does NOT affect production native verdict yet.
- Next: strict source-SHA-matched Windows native run, preserve B02 RETRACT PUNCH collision, Conditional, per-bend results; measure reduction in per-pose sanity and wall time, then integrate the component tool screen with a safe independent oracle comparison.


## 2026-11-13 — Native log (20261113-130304): exact shared workpiece sanity verified

- Reference evidence and quantified deltas: `docs/PERFORMANCE_LOG11_NATIVE_SHARED_SANITY.md`.
- User's exact installed ZIP source for three key modules has SHA-256 byte-for-byte match against this child branch: `native_collision_3d.py`, `native_bend_solid_3d.py`, `motion_trajectory_3d.py`. Composite ZIP runtime hash `1be2bbb85511...`.
- Manual native wall `1180.5304s → 1046.8586s` **(-133.6718s, -11.323%)** on matching recorded reference order, settings and physical statuses. Source model/tool binary hashes and additional matched trials not yet collected.
- `pair_geometry_sanity` drops `1322→1024` calls and `347.9817→197.4414s`. Common `627→627`, distances `548→548`, full unions `297→297`. Exactly the expected target of duplicate per-pose workpiece validity checks.
- B02 still blocks on RETRACT with exact positive central bend-zone B09 intersection ~2.752 mm³. B08/B04/B06 stay Conditional; no false CLEAR and continuous motion remains unverified.
- Stage: **FREECAD_VERIFIED for one part, PROVISIONAL_BENCHMARK**; GUI/addon and full production union still operate. Next stage: non-authoritative component oracle shadow comparison with machine-frame placement and complete Conditional fallback, then gated integration after native evidence.


## 2026-11-13 — Machine-frame component pre-fuse shadow comparison, child branch

- Working branch: `experiment/component-collision-oracle`, based on native
  workpiece-validity benchmark `docs/PERFORMANCE_LOG11_NATIVE_SHARED_SANITY.md`.
  This does **NOT** change `main`, installable `smbs`, or the
  `development/performance-optimization` parent.
- Hypothesis: native machine-space AABB over canonical panel/zone BReps
  may discard most pairs without a `Part.common`; later exact component
  intersections may prove blockers early and reduce `multiFuse` use after
  physical/topology acceptance.
- Commits:
  - `fcec4c9` adds `screen_machine_component_overlap`: rigid-frame 8-corner
    AABB, fail-closed candidate limit (default 4), copied BRep and original
    measured exact OCC Common for candidates, bounded contextvar shadow stats.
  - `2d23d74` offers a synchronous read-only `component_observer`
    on the exact solids about to be fused by original native builder.
  - `425970c` wires optional pre-fuse probes into
    `AdaptiveTrajectoryValidator3D.create_state_snapshot`, disabled by
    default, max 8 samples per bend, no authoritative return-path changes.
    `64ca1e9` isolates even observer bookkeeping exceptions.
  - `9e4bd73` includes `component_shadow` evidence in
    `PERF_NATIVE_BEND_KERNEL` / existing Desktop JSON; `4c8442b`
    extends read-only `performance_report` with disjoint advisory totals.
  - `09122a8` adds `component_shadow_samples_per_bend` to native benchmark,
    `030ca39` adds component source to actual installed/loaded module SHA.
  - `787e623`, `92ea123`, `aff400d`, `e7eac54`, and `6031291`:
    headless tests, component observer equivalence and trajectory integration
    (including observer failure not replacing original oracle).
  - `63bbda3`: update canonical experiment documentation with run steps.
- Exact CI latest for doc commit `63bbda3`:
  https://github.com/Badsmc/SMBS_BendSeq/actions/runs/43435206092
  (SUCCESS: compile and available headless tests).
- Safety invariant: full native `multiFuse`, exact validity, closure/volume,
  contact and end-zone classifications still run on every previously
  uncached sample. No new `CLEAR`, no Conditional waiver, no skipped native
  geometry certification, no continuous sweep claim.
- **FreeCAD comparison of SHADOW MODE: NOT RUN** in this session.
  Last independently measured native reference remains **1046.8586s**
  without shadow; 297 full unions. Component speedup: **NOT MEASURED**.
  Enabling shadow adds extra work and is NOT an acceleration benchmark.
- Next REQUIRED action: in installed real FreeCAD Python console set
  `os.environ["SMBS_COMPONENT_SHADOW_SAMPLES"]="2"` before an otherwise
  unchanged Manual 3D check. Upload the saved Desktop JSON, inspect
  `component_shadow` candidates/hits/disagreements, plus B02 RETRACT and
  B08/B04/B06 Conditional. Disable env flag for base performance comparison.
  See `docs/COMPONENT_COLLISION_EXPERIMENT.md` for exact commands and
  fail-closed acceptance gates.
- Next code gate after actual evidence: make a gated **proven-blocker-only**
  no-fuse search filter, with old full native oracle as mandatory fallback
  on ambiguous, Conditional, contact or topology-dependent cases. Do not
  declare component-only CLEAR without independently proven full-sheet
  integrity and exact all-components contact/distance semantics.


## 2026-11-13 — Log 20261113-141036 proves broadphase works but shadow hit coverage inadequate

- User native Windows log analyzed from the actual ZIP-installed `component_collision_oracle.py` (runtime fingerprint `a875c255ec2c3da07eea6b05b2b13b6cdc9f6da7342d58cd562b02a37aa8f93d`).
- Ten-bend original manual D6 finished **1047.1784s**, compared with 1046.8586s without shadow (+0.3198s, effectively unchanged). All native statuses preserved: B02 PUNCH collision at RETRACT; B08/B04/B06 Conditional; remaining 6 sampled-clear-not-continuous. Full unions **297** again.
- Native shadow 20 probes / 40 tool screens / 840 component-tool pairs. Conservative AABB rejected **603 (71.79%)**, candidates **237**; 29/40 screens had `candidate_budget_fallback` from four-candidate ceiling; other outcomes 10 uncertain-nonpenetrating and one separated. **0 proven hits / 0 disagreements**: NOT sufficient for equivalence because only BENDING samples were probed, while B02 true first blocker is RETRACT. Extra 30 exact measured Commons (native kernel 657 vs previous 627), 548 distance unchanged. Detailed source evidence: `docs/PERFORMANCE_LOG12_COMPONENT_SHADOW.md`.
- Corrective commits on this branch: `cce4353` BENDING-first + reserve one for first non-Conditional RETRACT PENETRATION, even for a cached full-sheet pose, with diagnostic-only rebuild of isolated components and candidate cap **12**; `fb6c3ea` new regression; `0bc4f93` fix mock result to match real punch/retract/die outcome (CI success #43441205080); `2db4bae`, `d9d9078`, `33dc6d4`, `23dfb5b` add separate component rebuild timing and assertions.
- **Critical:** before accepting a new proven-reject shortcut, rerun in real FreeCAD with `SMBS_COMPONENT_SHADOW_SAMPLES=2` and verify a B02 RETRACT component-positive exact punch hit. If none is proven, debug candidate placement/edge cases, not false-CLEAR the whole shape. Shadow mode may be slower, still makes 297 full-sheet unions, is NOT a production speedup.
- Future test files: `tests/test_component_shadow_trajectory_integration.py`, `tests/test_component_machine_shadow.py`, `tests/test_performance_report.py`. All changes confined to child experiment, no main/smbs updates.


## 2026-11-13 — New native log (20261113-144507) proves 5 exact component hits; earliest-blocker replay added

- Actual user-provided Windows journal source `SMBS_debug_log(20261113-144507).json`: `SMBS_RUNTIME_READY` composite ZIP SHA `cc6c178b8d0aea1322e7207a3ebbe1d65962ab3d7bfc9cc4f6992d0321ef146e`. Source files/tool/part binary SHA **not present in log**; see `docs/PERFORMANCE_LOG13_COMPONENT_PROOF_AND_REPLAY.md`.
- Native Manual 10/10 wall **1051.5764s** vs previous shadow-OFF **1046.8586s** (+4.7178s, +0.45%; shadow does not speed up). 297 original multiFuse/qualifications unchanged. Native Common now **728**, exactly the former **627** plus **101** shadow component Commons; distances **548** unchanged.
- Component screens: 11 probes / 22 tool screens / 462 component-tool pairs; **330 (71.43%) AABB disjoint**, 132 candidates, 105 BRep transformations, 101 exact Common. **5 real exact positive punch overlaps, 0 positive/full-oracle mismatches, 0 incomplete screens**. Important: zero mismatches across five positive comparisons does NOT prove safe clearance on other states.
- Three positive B08/B04/B06 BENDING hits are `CONDITIONAL` punch end-zone exceptions, not automatic proof that the sequence should be rejected. B02 BENDING punch P09 positive. B02 RETRACT shadow u=1.0 punch P01 positive. The earliest native B02 RETRACT blocker is **u=0.15625**, ram Z **−1.931327582 mm**; independent component attribution at this different pose proves P09/P11 ~500.926mm³ each and central bend_zone:B09 **2.751760mm³**. Preserve this distinction.
- New FreeCAD-only tool `tools/replay_native_component_blocker.py` at `a4c3c94` plus strict provenance fixes `b2e078b`, `cfe0ecd`, and runtime provenance `fe73b74`: read recorded first blocker, rebuild exact isolated components at that *specific* pose and compare recomputed native machine frame, source bend IDs/axes/signs, actual FCStd punch/die, exact per-component OCC Common volumes. **No full-sheet multiFuse or 10-bend trajectory rerun**. Only a recorded positive blocker can be replayed; errors -> UNVERIFIED; no output can declare CLEAR. HEADLESS_TESTED ONLY; actual replay not yet run in FreeCAD. Source/tool SHA now recorded in replay report; original source SHA still unavailable for matched provenance.
- Additive diagnostic commit `1c16f8a`: log component `hit_volume_mm3`, `full_oracle_tool_status`, `full_oracle_conditional` in bounded shadow examples, preserving original physical oracle. Two intermediate GitHub Actions failed because mock positive-hit fixtures omitted `volume_mm3`; fixed by optional-field hardening `b8d7e31` and realistic fixtures `bed171d`. **CI SUCCESS:** https://github.com/Badsmc/SMBS_BendSeq/actions/runs/43448366715.
- Next native test is **one-pose replay, not another 17-minute manual run**: in FreeCAD Python console `from tools.replay_native_component_blocker import run; result=run(r"C:/Users/User/Desktop/SMBS_debug_log.json",bend_id="B02")`. Obtain `SMBS_component_blocker_replay.json`. Compare P09, P11 and B09 exact positive volumes, especially central 2.75mm³; inspect precise source geometry/placement mismatch if any. See full steps in LOG13.
- Do NOT enable production no-fuse `CLEAR` or `CONDITIONAL` rejection. A validated positive physical central blocker may eventually prune a candidate after exact Conditional zoning; uncertain, topology-invalid, contact-only and unverified results require original native oracle. No user acceptance or measured whole-run component speedup yet.
