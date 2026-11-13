# SMBS: Performance Optimization Master Plan

Version: 1.0 — 2026-11-13. This is the normative plan for branch **development/performance-optimization**.
Owner-facing language: Russian. Engineering code/docs may be English.

## 0. Scope and baseline

Goal: make operator-defined native 3D bend checks substantially faster and make automatic bend-order search practical for 10+ bends **without reducing physical correctness or inventing clearance evidence**. The initial baseline comes from the branch `fix/conditional-endzone-volume-tolerance`; this branch must remain a recoverable reference. Keep the working 2D Manual button, separate native 3D button, collision inspector, FCStd session JSON, Desktop developer log, ToolLibrary, and FreeCAD Tasks UI.

Current reference: 10-bend FreeCAD SheetMetal part `2-1-3-4-(ok).FCStd`; tools AP1017 punch and AT3015 V12 die; Manual prefix B07 → B03 → B05 → B01 → B08 → B04 → B09 → B10 → B06 → B02; 180-degree loading, virtual tool length when explicitly selected, Conditional end zones 10 mm when explicitly selected. **This is test data, not a verified safe production plan.** Prior observed statuses: 2D screened 10/10 with a RETRACT warning; native D6 reached B08 after four sampled-clear bends, initially reported a B08 central numerical sliver. Conditional sliver handling has since been modified, but the resulting full native rerun is NOT yet known.

Important distinction: FreeCAD workbench uses bundled FreeCAD Part/OpenCASCADE directly, not necessarily pythonocc-core.

## 1. Hard safety, correctness, and compatibility invariants (MUST)

1. **No fabricated PASS.** `COLLISION`, `UNVERIFIED`, incomplete interval, absent tool geometry, timeout, invalid OCC, and unknown machine transfer remain distinct. A sampled-clear result is NOT continuously certified. Conditional end-zone allowance stays CONDITIONAL, never physical PASS.
2. **2D is an advisory prefilter.** 2D interference does NOT prove an actual full-span native 3D collision; 2D clearance does not prove 3D clearance. Avoid pruning a physically valid branch solely from a non-conservative 2D approximation.
3. **The exact native oracle is authoritative for confirmed collisions.** Do not suppress a collision by increasing fuzzy tolerances, reducing contact checks, changing solids, or silently discarding UNKNOWN. A fast filter may skip OCC only if conservatism is demonstrated for the claimed decision and tested.
4. **No hidden tool geometry changes.** Preserve selected FCStd punch/die, virtual length, real section assembly, datums, end-zone policy and selected 0°/180° orientation. Virtual tool geometry is not installed-machine certification.
5. **Never mutate the operator's FCStd or FoldState** during tests/search. Restore exactly after aborts and exceptions. All tooling caches must invalidate on source/tool/geometry/config changes. Do not distribute mutable Part.Shape objects across processes.
6. **UI contract stays stable:** Manual Simulate without collision validation; separate quick 2D verification; explicit native 3D; manual inspection; Tasks close/reopen; Desktop `SMBS_debug_log.json`; FCStd embedded session.
7. **Use stable bend/panel IDs and reference frames.** Preserve SheetMetal Unfold sourcing, signs, completed bends, reverse-axis map, and machine-X/Y/Z contracts.
8. **Never merge an unqualified performance change into `main` or installable `smbs`.** All experiments stay on this development branch or short-lived child branches with measurable results.
9. **No stubs, unused fallback solvers, dead branches, duplicate modules, fabricated fixtures** as substitutes for actual geometry. Keep active production call graph explicit.
10. Fail closed on timeouts, task cancellation, cache corruption and worker exceptions. Store enough provenance to reproduce and debug.

These guarantees override benchmark targets. A 10x faster wrong answer is a regression.

## 2. Current actual call graph and observed bottlenecks

Manual:
`pressbrake_2d.py` → `check_manual_3d_sequence` → `check_active_25d_bend_trajectory` → `AdaptiveTrajectoryValidator3D.validate_bend_trajectory` → adaptive APPROACH/CONTACT/BENDING/BOTTOM/RETRACT samples → `create_state_snapshot` → `build_multi_bend_sheet_solid` (panels, bending zones, multiFuse/qualification) → `validate_workpiece_tooling_pose` → `check_pair_clearance` (AABB, native distance, OCC Common, optional end-zone classification).

Auto:
`sequence_search_2d.py` performs cached Stage A/B prefix filtering, axis grouping, bounded search and replay. Do not equate its 2D candidates with fully physically verified 3D sequences.

Existing safeguards/caches: panel prism cache, complete-solid cache, die/punch clearance caches, pose-result cache; broad-phase AABB; some face-distance pruning; adaptive recursion, state/time budgets and early stop on proven physical collision. **Optimization work MUST profile these existing mechanisms first, not duplicate them.** Existing caches are currently scoped principally to an individual validator.

Known likely costly calls: repeated bend-zone reconstruction, multiFuse, solid validity/volume qualification, distanceToShape, native Part.common for punch/die, and additional Common on Conditional end-zone attribution. Historical tests reported OCC dominating ~97–99%, but no new performance percentage is claimed for current code.

## 3. Phases and acceptance gates

### Phase A — Baseline, observability, reproducible profiling (START NOW)

Deliver: a read-only analyzer of the existing Desktop JSON developer log, an explicit per-bend/per-phase metric schema, and tests. Run reference FCStd via real FreeCAD on at least one target machine (Mac/Windows) with loaded module path and git SHA recorded. Capture 3 cold and 3 warm identical runs where feasible, CPU/RAM, elapsed, OCC Common/distance counts and timings, solid build time/count, cache hit/miss, sampling counts, max phase, first obstruction/status, tool and orientation signatures, FULL/partial check. Headless CI tests the parser; it is NOT the native performance baseline.

Gate A: structured report generated from an actual FreeCAD JSON log; baseline metrics and verified source version; no changes in geometric verdicts. If actual log unavailable mark phase `IMPLEMENTED_NOT_MEASURED`.

### Phase B — Low-risk algorithmic speedups

Reduce redundant exact distance, duplicate Common/geometry validation and repeated reconstruction only where identity/proof allows it. Reuse already computed exact Common for Conditional zoning **without rerunning the boolean**, but only if complete shape provenance/cache lifetime are correct. Investigate OCC API correctness, exact per-pose geometry reuse and tool loading overhead. Use caches with bounded memory and explicit signatures. Avoid increasing time budgets as a substitute for speed.

Gate B: same native statuses, first blocking pose within sampling resolution, exact overlap within declared numerical tolerance; no new UNKNOWN→CLEAR; measurable speedup in matched live runs. Document changed Common count and elapsed percent.

### Phase C — 3D component broad phase / split geometry

Maintain canonical independent panel solids and bend-zone solids with stable IDs, pose transforms and conservative AABB/OBB/BVH. First discard components provably separated from tool's swept candidate region; then run native OCC only on potential colliders. Distinguish collision **rejection** (single proven overlapping material component can reject immediately) from clearance **qualification** (all relevant materials and assembly/topology must be accounted for). For union-to-component equivalence, handle adjacent seams/overlaps and containment; never infer CLEAR from partial component checking. Preserve full independent final verification for promising plans.

Gate C: randomized and real FCStd equivalence across flat and folded shapes, special angles, signs, Conditional zones and real tooling; no false negatives on any benchmark; exact physical verdict preserved or explicitly downgraded to UNVERIFIED. Benchmark building vs checking separately.

### Phase D — Cross-state caching / incremental geometry

Introduce bounded, deterministic, reproducible state signatures. Key by source file hash (and relevant topology), thickness, fold angles, active bend, machine placement, loaded tool file + sections/virtual length, orientation, OCC/version and all tolerances/end-zone options. Cache panels/transforms and conservative-screen outcomes when valid. Evict via LRU+memory budget; metrics hit/miss and retained bytes. Never serialize opaque Part.Shape for cross-machine reuse unless explicitly versioned and independently validated.

Gate D: repeated and reordered shared-prefix checks become faster with identical statuses and no stale-cache reuse after changing tools/FCStd/settings.

### Phase E — Parallel workers / CPU utilisation

Use independent **FreeCADCmd or equivalently supported FreeCAD Python** worker processes (NOT Python threads sharing FreeCAD/CAD state). Queue immutable serialized jobs; each worker loads its own source CAD and tool data; return JSON verdict with provenance, timing and full error. Probe 1, 2, 4 workers first; cap CPU processes and RAM; graceful cancellation/timeouts; avoid breaking main GUI process. Auto mode parallelises independent search branches/candidates; Manual can parallelise independent exact samples only after profiling overhead and preserving phase sequencing.

Gate E: reproducible exact/UNKNOWN statuses; worker failures return UNVERIFIED; no GUI freeze or duplicate module loading; measured throughput vs processes and peak RAM.

### Phase F — Search architecture

Use cheapest sound constraints and Stage A/B first; rank candidates, but retain uncertain candidates instead of unsound pruning. DFS/A* with backtracking, stable state keys, transposition tables and branch-and-bound priorities (tool switch, flip/turn, backgauge movement, handling penalty). Run bounded component-level 3D on a narrow frontier and full independent native D6 only on top ranked complete candidates (target 7–15 configurable, NOT a guarantee). Report partial prefixes honestly with unverified remaining bends. Use reverse planning only where fold/unfold and physical predicates are proven equivalent.

Gate F: find/reject candidates with same physical semantics and deterministic output; collect depth, branches, nodes, pruning causes and time; measure time-to-first physically verified candidate and candidates/second, not just total permutations.

### Phase G — Optional specialist engine / GPU

Only after A–F: benchmark BVH/GJK/triangle mesh/voxel/GPU as conservative preliminary filters. Fast approximate CLEAR is not a native OCC certificate. Any mesh tolerance must be controlled and verified. Direct GPU acceleration of existing `Part.Shape.common` is NOT assumed available.

## 4. Standard task loop (MUST follow for EVERY change)

1. Read `AGENTS.md`, this plan and `docs/PERFORMANCE_AGENT_HANDOFF.md`. Inspect repository/head and current native runtime before editing.
2. Define an explicit hypothesis and baseline metric for one change. Identify exact functions/caches affected and risks to verdict or operator workflow.
3. Add a failing regression/benchmark first whenever possible. Prove equivalence/uncertainty handling before calling a shortcut sound.
4. Make the minimum isolated source change, without removing working features or tolerances.
5. Run `python -m compileall -q Init.py InitGui.py freecad_smbs smbs_core` and relevant unittest files plus GitHub Actions. Use FreeCAD local native tests for CAD-specific claims.
6. Measure matched before/after data: same part/tool/check mode, branch SHA, FreeCAD/OS/hardware, cold/warm, states and phase counts. Store run summary under ignored local reports or artifact, NEVER replace authoritative original log.
7. Update `docs/PERFORMANCE_AGENT_HANDOFF.md` with exact commit SHA, files, tests, status, first blocker, numerical metrics and next experiment; keep all claims grounded.
8. Commit to development branch only. Merge to `main`/`smbs` requires separate explicit acceptance. If speedup fails or correctness differs, revert the experiment and document why.

## 5. Progress states / semantics

`PROPOSED` → `IMPLEMENTED` → `HEADLESS_TESTED` → `FREECAD_VERIFIED` → `BENCHMARKED` → `REGRESSION_QUALIFIED`.
No file/class is a performance success without `BENCHMARKED` and correctness-qualified live run. Record both the source SHA and the **actual installed FreeCAD module path**.

Do NOT describe: 
- `SAMPLED_3D_CLEAR_NOT_CONTINUOUS` as a fully collision-free continuous process;
- `CONDITIONAL` as proof of real material removed from punch;
- `UNVERIFIED`/timeout as `COLLISION`;
- a 2D result as equivalent to exact native 3D;
- green Linux headless CI as real FreeCAD OCC acceptance.

## 6. Excluded from this optimization branch

No new punch-relief geometry editor, no visual redesign, no unrelated CAD features or tool library renaming, no mass core rewrite, no replacing SheetMetal with an inaccurate proxy. Handle only profiling, performance correctness and search improvements.

## 7. Handoff contract

An incoming agent starts at `docs/PERFORMANCE_AGENT_HANDOFF.md`, checks its HEAD against GitHub, and executes its first outstanding task. Every agent updates that file after work, including failures and uncertainty. If multiple agents work concurrently, use isolated child branches, never conflicting parallel writes to the same GitHub path. Reviews and experiments are merged into this development branch only with tests.
