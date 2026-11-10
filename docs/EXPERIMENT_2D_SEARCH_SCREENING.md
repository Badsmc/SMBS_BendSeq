# 2D/2.5D search-screening experiment

Only develop on `experiment/2d-search-screening`, baseline `9ab0c6b2345f14e961b3df1b4661810458fc67fa`. Never alter source branches. All 2D and 3D computations must derive from the same authoritative FoldState, geometry, machine setup and tool datums.

## Source inventory and decisions

| Component | Source | Decision | Limit |
|---|---|---|---|
| `preliminary_screening.py` | baseline | ADAPT | Stage A/B exist; Stage B samples central `X_m=0`, cannot certify whole tool span |
| `contact_oracle_2d.py` | baseline | REUSE | Polygon-interior contact; preview only, not a 3D proof |
| `machine_section_3d.py` | baseline | ADAPT | Current machine section assumes `X_m=0`; requires local-axis multi-station support |
| `coupled_kinematics_2d.py` | baseline | REUSE WITH VERIFICATION | Common punch/sheet/die air-bending kinematics; validate real setup |
| `sequence_search.py` | baseline | REUSE LATER | Forward/backward DFS and transition cache; do not duplicate |
| `operation_cost.py` | baseline | REUSE LATER | Existing configurable operation penalties |
| `PreviewStepOracle25D` | `d6-integrated-physical-planner` | AUDIT BEFORE ADOPTION | No port yet, contract/integration still unverified |
| `sequence_planning.py` | `d6-integrated-physical-planner` | AUDIT BEFORE ADOPTION | No port yet; avoid second solver |
| SheetMetal adapters, panel geometry | `2D` and baseline | REUSE | Common fold and unfolded geometry |
| `axis_screening.py` | experiment | NEW | Pure axis grouping + longitudinal sample position suggestions |

## Implementation status

**Stage 1: PARTIAL.** New geometry-only `axis_screening.py` groups axes by parallelism (including endpoint reversal), generates active-bend local transverse/longitudinal frames and suggests longitudinal probe positions for bend/tool ends and panel vertices. It does not assume exactly two global axes; diagonal groups remain independent. The tested functions are not yet wired to the sequence search.

**Stage 2: NOT VERIFIED.** Existing `WorkpiecePlacement3D` is the shared pose source, but a full approach/transfer/retract swept path has not been verified by this experiment.

**Stage 3: PARTIAL PLANNING ONLY.** Local positions for candidate longitudinal sections are generated, but sampling the actual machine-section polygons at those locations with identical tool and workpiece placements is pending. Probe stations are suggestions, not coverage proofs.

**Stage 4: NOT INTEGRATED.** The existing polygon contact oracle remains unchanged; it must eventually evaluate each section/phase. Do not translate 2D INTERFERENCE directly into PROVEN_BLOCKING until the physical mapping and blocking proof are established.

Safety rule: A finite set of clear sections never implies a collision-free full trajectory. Missing source CAD, end zones, setup, backgauge/frame geometry, unsupported bend geometry, or uncertain intersection must remain `UNVERIFIED` or `CONTINUE_TO_3D`. Only proven impossible transitions can be pruned. An OCCT-confirmed collision can be cached only for matching fold state, tooling, placement, tolerances and policy.

## Tests and next steps

Headless check: `python -m unittest tests.test_axis_screening -v`. Seven geometry tests were run locally using an isolated copy of the module and passed; CI and native FreeCAD/OCCT were **not** run for these commits.

Next: parameterize machine cutting-plane station; verify same physical pose/tool datum and cross sections around tool ends and flanges; add conservative stage B multi-station evaluations; record Z-(ok) allowed cases and eight box collision comparisons under baseline AP1017 + AT3015. Report JSON must include disagreements, preliminary time, complete candidates and actual OCCT call counts. No speedup or confirmed full plan is claimed yet.


## Multi-station geometry adapter (second increment)

- `machine_section_3d.compute_machine_cutting_plane_section(..., target_xm=0.0)` now accepts an explicit machine-X station. Default zero preserves existing callers and UI. Sheet panels are cut at the requested machine coordinate; bend zones still use the pre-existing local midpoint profile. That approximation is NOT a proof for non-parallel or varying-section bend zones.
- `multi_station_screening.screen_machine_stations_2d` evaluates the existing contact oracle across requested stations using one external FoldState, placement, and tooling object. Its API accepts separately verified punch/die longitudinal spans. Without both spans, or outside either span, stations are UNVERIFIED and no extrapolated tool polygon is evaluated.
- Return statuses are `2D_INTERFERENCE_REQUIRES_3D`, `UNVERIFIED_NEEDS_3D`, or `SAMPLED_SECTIONS_CLEAR_OR_TOUCH_3D_REQUIRED`. The implementation NEVER emits `PROVEN_BLOCKING` or certifies the full swept trajectory. No integrations into DFS, 3D validation, or caching yet.
- `tests/test_multi_station_screening.py` adds seven unit tests using mocked section/oracle dependencies, including changed station X, source-spans missing, tool end, fail-safe exceptions, and collision classification. These are synthetic contract tests, not actual CAD physical measurements. Existing FreeCAD/OCCT integration tests remain required.
- The experiment does not yet establish longitudinal spans from verified real AP1017/AT3015 CAD geometry. It must not pass nominal/virtual spans as verified source spans. Longitudinal offsets for tool ends in `axis_screening` are measured relative to the active bend midpoint; convert them through the SAME machine placement before passing to the multi-station adapter.

### Required next steps

1. Derive station selection from real aligned source-tool spans and panel vertices transformed into machine coordinates (not a guessed global XY axis).
2. Derive or cross-check the local bend-zone section at each station for non-parallel bends; test zero and nonzero targets against known 3D sliced solids.
3. Integrate the multi-station report into preliminary screening as risk-only diagnostics; do not prune from sampled sections.
4. Compare with fixed AP1017/AT3015 setups on Z-(ok) and eight recorded box 3D collision cases. Record explicit counters and timings, including unknowns and missed 2D contacts.


## Machine-frame station planning (third increment)

- Added `machine_station_planner.py` using the **same FoldState panel transforms and WorkpiecePlacement3D pose** as the existing machine section evaluator. Active axis endpoints and each panel vertex are transformed into machine space before stations are selected. The machine X coordinates are absolute and tool-end stations are converted to active-midpoint-relative offsets exactly once.
- Station positions include active bend ends, real machine-space panel vertices and, ONLY when independently provided, punch/die source-CAD end positions with small symmetric offsets. This is a geometric risk sampler, not an exhaustive interval collision proof.
- Rejects tilted/nonaligned active axes, invalid tool spans, nonfinite vertices and excessive probe counts rather than silently discarding geometry.
- New synthetic unit tests: `tests/test_machine_station_planner.py` (six test methods), covering nonzero machine placement, verified tool ends, invalid alignment and bounds, overflow and JSON export.
- No use of nominal or virtual AP1017/AT3015 spans as verified source-CAD spans is allowed. FreeCAD/OCCT integration, full CAD source-tool extraction, sampled bend-zone accuracy, transport path checks, and actual timing/collision evidence are still pending.
- **Status:** planner and tests committed, but not yet wired into Stage B/DFS. The tests are not confirmed executed by CI and do not establish physical correctness.


## Stage B / DFS integration (fourth increment)

- `screen_candidate_stage_b(..., multi_station_enabled=False, verified_punch_span_xm=None, verified_die_span_xm=None)` can now sample machine-space stations at each existing Stage B phase using the same phase pose and temporary tooling alignment; diagnostics appear in `PreliminaryScreeningReport.station_reports` and its JSON export. Existing default callers are unchanged.
- `search_bend_sequence_25d` and `search_bend_sequence_backward_25d` expose `experimental_multi_station_2d=False` and two independently verified source-CAD longitudinal machine spans; the forward dispatcher passes the options through to backward DFS. This is an **explicit opt-in diagnostic**, not a new rejection rule.
- No preview classification triggers `PROVEN_BLOCKING`, even if it detects apparent overlap. Geometry/placement failures remain `UNVERIFIED_NEEDS_3D` in the station report. Stage B still hands every non-proven-blocking transition to 3D.
- Use `experimental_multi_station_2d=True` only with machine-coordinate longitudinal spans verified from the *same selected real tool CAD and placement*, not configured virtual lengths. Without confirmed spans, the station preview is marked UNVERIFIED and must not be used for physical claims.
- Regression tests: `tests/test_stage_b_multistation_integration.py` verifies opt-in, state restoration, synthetic apparent collision, fail-safe exception and both DFS public signatures. The tests have been committed but their execution in native FreeCAD/OCCT and CI is **not confirmed**.
- **Performance note:** extra 2D sampling can increase elapsed time; no savings in OCCT are claimed until safe pruning or candidate ranking is established on Z-(ok) and the box, with exact baseline setups. Diagnostics alone cannot reduce calls.


## Ranking-only 2D screening experiment (fifth increment)

- New `screening_risk.py` computes **search-order-only** risk from the Stage B `station_reports` (one count per phase/station): apparent INTERFERENCE=10, UNVERIFIED=3, TOUCH=1 by default. Missing samples receive uncertainty; nonfinite/negative weights are rejected.
- Both forward and backward DFS expose `experimental_2d_risk_ranking=False` and optional `screening_risk_weights`. Ranking is only active when **both** `experimental_2d_risk_ranking=True` and `experimental_multi_station_2d=True`. Otherwise the original technological cost order is unchanged.
- For ranking only: `priority = technological_transition_cost + screening_risk_total`. The **original technological transition cost is retained** in the candidate tuple for objective evaluation, lower bounds, cost pruning and final reporting; no heuristic sampled collision is treated as a forbidden transition. Values are experimental and not manufacturing-qualified.
- New `tests/test_screening_risk.py` exercises deterministic ordering, synthetic apparent overlap, uncertainty, missing reports, custom weights, error handling and per-station rather than per-polygon counts. Added branch-only GitHub Actions workflow `.github/workflows/experiment-2d-screening.yml` for compilation, tests and original contact-oracle regressions.
- **Testing evidence:** GitHub accepted commits, but at the time of documentation no workflow run or status was available for the last checked commit. Local real FreeCAD/OCCT testing was unavailable; no pass claim is made for the new workflow.
- **Caution:** ranking on more sampled sections can COST more time than it saves; risk weights require calibration against fixed AP1017+AT3015 Z and box cases. Without verified physical tool spans, multi-station outputs are UNVERIFIED and the heuristic can bias ordering without useful collision evidence. Keep ranking disabled in production.
- The planner remains a source of *suggested* stations, not proof of complete geometry. False negatives from unsampled tool-end contacts, nonparallel bend-zone approximations and transfer motion still need 3D comparison.

### Experimental invocation

Pass `experimental_multi_station_2d=True, experimental_2d_risk_ranking=True` to `search_bend_sequence_25d(...)`, together with `verified_punch_span_xm` and `verified_die_span_xm` **only when extracted from the selected real CAD setup** in machine coordinates. The same options work with `search_direction="BACKWARD"`. Leave both flags off for baseline measurements.


## Measurement schema: Stage C is not OCCT (sixth increment)

- Added `screening_experiment_metrics.py`, a strict comparison/JSON-export schema for paired baseline vs risk-ranked runs. Read existing `SequenceSearchReport` via `from_search_report` and compare with `compare_measurements`.
- Enforce identical `model_id`, `tooling_setup_id`, `placement_id` and `tolerance_signature`. Refuse missing or divergent identities. Record status, order, full candidate and verified-plan times, total elapsed, Stage B evaluations, Stage C validation *attempts* and expensive unique step evaluations.
- `occt_boolean_calls` and `occt_boolean_elapsed_s` are **optional measured fields** and remain `null` unless separately instrumented in the native collision backend. One Stage C candidate can execute multiple OCCT booleans; Stage C is NOT a measurement of OCCT calls. Never infer actual saved booleans from DFS counters.
- If there is no verified plan on both sides, time-to-first-plan savings is `null`, not zero. Negative deltas mean a regression; no speedup is asserted without measured data.
- Added `tests/test_screening_experiment_metrics.py` and CI registration. These are schema tests, not real CAD benchmarks.
- Outstanding: instrument native boolean operations around OCCT intersections without changing collision tolerances; run the same AP1017 + AT3015 baseline setup on Z-(ok) and on the eight fixed box cases; record per-run JSON with source model/tool hashes and 2D false-negative disagreements. A useful first-plan comparison is permitted only where the same valid plan can be physically confirmed.


## Native 3D collision-kernel counters (seventh increment)

- Added `native_kernel_metrics.py` with an explicit `count_native_kernel_calls()` context manager, independently scoped via `ContextVar`. Counter snapshots include `common_calls`, `distance_calls`, elapsed wall time of those operations, failures and operation labels.
- Wrapped existing `shape.common(...)` sites in `native_collision_3d.py` (pair overlap and punch end-zone analysis) and `distToShape(...)` sites (direct, pruned-face, fallback) through zero-logic-change `measured_common`/`measured_distance`. Without an active counter scope the wrappers simply call the original methods and preserve behavior/return values/exceptions.
- These counters capture **instrumented native collision operations only**, NOT all OCCT calls in FreeCAD, shape construction, tool loading, solid fusing or external backends. Do not label these numbers as exhaustive OCCT totals. They can nevertheless measure the dominant directly observed native collision operations when the same search run is wrapped with the counter context.
- Example for an **actual local** run after creating independent, equivalent session/press-cycle/tooling environments:

```python
from smbs_core.kinematics2d.native_kernel_metrics import count_native_kernel_calls
from smbs_core.kinematics2d.screening_experiment_metrics import from_search_report
with count_native_kernel_calls() as counter:
    result = search_bend_sequence_25d(
        session, press_cycle, tooling,
        experimental_multi_station_2d=True,
        experimental_2d_risk_ranking=True,
        # verified_*_span_xm only from selected physical tool CAD in machine coordinates.
    )
snapshot = counter.to_dict()
measurement = from_search_report(
    result, "2D_RISK",
    occt_boolean_calls=snapshot["common_calls"],
    occt_boolean_elapsed_s=snapshot["common_elapsed_s"],
    model_id="MODEL_AND_FILE_HASH", tooling_setup_id="TOOL_CAD_AND_SETUP_HASH",
    placement_id="POSE_AND_SHIFT_HASH", tolerance_signature="TOLERANCE_AND_POLICY_HASH",
)
```

- In this schema `occt_boolean_calls` represents instrumented `common()` calls only; distances are independently captured via `counter.to_dict()`. Persist that complete snapshot alongside the experiment report to avoid claiming exhaustive backend coverage.
- Added synthetic headless tests in `tests/test_native_kernel_metrics.py` and extended the branch-only CI workflow. CI outcomes and native CAD benchmark measurements remain unverified until executed.


## Opt-in source panel-prism reuse (eighth increment; requires physical benchmark)

Measured baseline on Windows FreeCAD 1.1.3 / Z-(ok) first B01 physical step:
- `max_phase_time_s=90`, `max_recursion_depth=6`, `max_evaluated_states_per_phase=48`, search max checks 1; same virtual-length tooling setup.
- One instrumented run: total search wall 56.45 s; BENDING 41.47 s. Earlier 30-second phase budget stopped BENDING with two unresolved intervals, so do not compare its 30.93 s to a fully converged trajectory.
- Reported across BENDING: ~24 source **sheet assembly** reconstructions, ~9.895 s solid-build time; punch 10.503 s, die 9.750 s. These are observed baseline timings, not improved results.
- `native_bend_solid_3d.build_multi_bend_sheet_solid(..., panel_prism_cache=None)` is backward-compatible. Optional per-validator cache reuses only *untransformed source panel prisms* (original face extruded along -thickness); it never caches or reuses transformed panels, moved tools, bend-zone solids, or full joined solids.
- The `MotionTrajectoryValidator` tracks source panel geometry signature, original face identity, generation token and thickness. It clears cached source prisms if these change and clears with `clear_caches()` and tool changes. Every new angle still applies a fresh rigid placement to a `shape.copy()`, constructs new bend zones, recomputes OCC union, and checks full validity and volume. Source face validity is checked even on a cache hit. No collision tolerances, sampling thresholds, evidence semantics or 2D risk ranking were changed.
- This specifically eliminates repeated `face.extrude()` for fixed panel primitives at different angles; it cannot eliminate expensive complete-sheet OCC fusion and intersection tests. The *speed benefit is unknown* until physical FreeCAD regression comparison.
- `tests/test_panel_prism_reuse.py` covers cache hit, original uncached behavior, invalid source rejection on hit, invalid cached solid rejection and independent validator caches. CI includes this headless contract test, but it does not prove FreeCAD-native numerical equivalence.

Before treating this optimization as accepted, compare at **identical** settings and fresh process/cache:
1. Baseline commit `b3dd944d26fe5213a301ba7053456138246c5cce` vs experimental head, with Z-(ok).FCStd and exact same virtual tooling/mode initially; run B01 once for each.
2. Check per-phase status, `unresolved_intervals`, every collision class, distances/volumes within tolerance, bend pose, and zero failures; measure total wall, `phase_solid_reconstruction_ms`, measured common/distance calls. Both must remain `SAMPLED_3D_CLEAR_NOT_CONTINUOUS` without unresolved intervals, or investigate.
3. Repeat with **real full-length verified** AP1017 + AT3015 tool solids separately; virtual-length result is not physical production qualification.
4. Treat changes in outcome or loss of shape validity as regression, not performance improvement. Do not claim a speedup without actual paired runs.


## Native sheet-construction fine-grained profiling (ninth increment)

Windows FreeCAD 1.1.3 B01, with adaptive phase time 90s, recursion 6, max 48 sampled states: before source-prism cache, 56.45s total wall, 41.47s BENDING; after the cache change, 57.93s total wall, 41.07s BENDING, 32 full sheet reconstructions and 13.27s total BENDING build time. Both returned SAMPLED_3D_CLEAR_NOT_CONTINUOUS without unresolved intervals. This **does not demonstrate a speedup**. An earlier 24-reconstruction run was time-limited and is not equivalent.

Added `native_geometry_build_metrics.py` with explicit opt-in `count_native_geometry_builds()` context manager. It measures these disjoint regions inside the existing full-sheet construction code:

- `panel_extrude`: creation of original, untransformed source-panel prisms; on a valid source cache hit no extrusion occurs.
- `panel_transform`: rigid transformation of an independent copy of a source panel prism.
- `bend_zone_build`: entire per-bend zone generation and validation, including the underlying zone extrusion.
- `sheet_union`: OCC `multiFuse` / `fuse` on assembled panel and zone solids.
- `sheet_qualification`: validity, closed/single-solid and volume-integrity checks.

Each region includes wall time, invocation count, and failures. Times do not encompass all geometric work or all OCCT kernel operations. Instrumentation preserves original geometry calculations, exception paths, tolerance values and evidence. It is disabled unless the context manager is used.

Run from a freshly restarted FreeCAD Python console, with the experimental branch updated:

```python
from smbs_core.kinematics2d.native_geometry_build_metrics import count_native_geometry_builds
from smbs_core.kinematics2d.native_kernel_metrics import count_native_kernel_calls
with count_native_geometry_builds() as geometry_metrics:
    with count_native_kernel_calls() as kernel_metrics:
        report, cache, wall, bends = run_single_search_in_clean_session(
            direction="FORWARD", fixture_name="Z-(ok).FCStd",
            punch_name="PUNCH_AP_1017_88°_H104,65_R0,8",
            die_name="T-DIE_AT_3015_88°_V12_H80",
            sampling_cfg=cfg4,
            weights=OperationWeights(flip=20.0, turn_per_90_deg=10.0, shift_fixed=5.0),
            max_search_time_s=180.0, max_states=2, max_checks=1,
        )
print(geometry_metrics.to_dict())
print(kernel_metrics.to_dict())
```

The 5-case headless test `tests.test_native_geometry_build_metrics` validates label counting, nested isolation and exception propagation. It is not a substitute for FreeCAD-native B01 geometric/physical equivalence tests. Next step: quantify whether sheet union or zones dominate, and A/B-test any change that modifies OCC construction methods.


## Experimental standalone 2D-only sequence planner (tenth increment)

Implemented a **new, separate experimental mode** at `smbs_core/kinematics2d/sequence_search_2d.py`, entry point `search_bend_sequences_2d(session, tooling, ...)`. This mode never invokes `search_bend_sequence_25d`, `evaluate_candidate_bend_step`, `validate_workpiece_tooling_pose`, `check_active_25d_bend_trajectory` or the native 3D OCCT collision detector.

- Uses authoritative `FoldState`, `panel_vertices_xy`, `WorkpiecePlacement3D` **mathematical coordinate placement only**, `compute_machine_cutting_plane_section` (analytic panel section), `CoupledAirBendingKinematics`, aligned 2D tool profiles and `check_sheet_tooling_preview` polygon contact tests. Despite historical module names including `3d`, these steps do not construct or intersect FreeCAD Part solids.
- Stage A checks geometric inputs/tool coverage, Stage B now records `contact_statuses` alongside sampled phases so the standalone planner can distinguish `CLEAR`/`TOUCH`, `INTERFERENCE`, and `UNVERIFIED`. Existing Stage B -> Stage C behavior is unchanged for the original physical planner.
- By default samples BENDING at 17 angle fractions (0..1) and APPROACH/RETRACT at 9 ram positions (0..1), plus BOTTOM. These are **finite samples**, not proof of continuous-motion clearance. Stage B's new `stroke_fractions` argument is opt-in and defaults to legacy single-endpoint behavior for the physical solver. Signed negative bend angles now retain sign in FoldState but pass the **absolute** angle to ram/stroke functions.
- Deterministic forward DFS explores candidate bends with completed earlier bends at target signed angles; each Stage B candidate has its own machine alignment. Tracks visited states, checks, reasons, limits, and up to 10 full sequences. Full sequences containing uncertain sections carry `UNVERIFIED_2D`; those with only sampled clear/contact receive `SAMPLED_2D_CLEAR_OR_TOUCH`. No result is labeled 3D safe or production ready. Section interference prunes branches **in the planar preview only**, not the physical 3D planner.
- Scope: only active machine cross-section by default, no verified longitudinal tool-end, clamp/backgauge/frame, fixture or self-collision coverage. Optional multi-station mode requires externally grounded source tool spans; it must not synthesize spans or treat virtual tool extrusion as physical evidence. Virtual length matching remains active for the first demo; the reports explicitly disclose it.
- `tools/run_planar_sequence_search.py` exposes `run_planar_search(App.ActiveDocument, output_path=...)` from FreeCAD Python Console for a document with an existing `*_Unfold` object, the default AP1017 + AT3015 2D tool library profiles, and optional JSON export. No 3D collision oracle is invoked. The runner closes its preview widget but does not change the user's open document.
- New headless tests in `tests/test_sequence_search_2d.py` and Stage B opt-in stroke/signed-angle tests. These are synthetic; actual FreeCAD model results are **not yet tested**.

### Windows 11 / FreeCAD Console — first test

Update ONLY `experiment/2d-search-screening` in your installed workbench directory and restart FreeCAD. Open `Z-(ok).FCStd` containing an existing SheetMetal `*_Unfold`. In Python Console:

```python
import FreeCAD as App
from tools.run_planar_sequence_search import run_planar_search
from pathlib import Path
p = Path(r"C:\Users\User\AppData\Roaming\FreeCAD\v1-1\Mod\SMBS_BendSeq-2D")
result = run_planar_search(
    App.ActiveDocument, max_solutions=10, max_search_time_s=60.0,
    output_path=str(p / "reports" / "z_planar_only.json"),
)
print(result["report"]["status"], result["report"]["elapsed_s"])
print([c["sequence"] for c in result["report"]["candidates"]])
```

This uses only the 2D section collision model. If all sampled sections appear clear, **do not infer that the plan is safe in 3D**. Do not merge into the main GUI or the physical solver before real FreeCAD tests of signed bends, tool placement, approach/retract path and false negatives. Historical `B01` sampled 3D result is not a validation of this new 2D-only model.


## Axis-aware ranked 2D-only planning (eleventh increment)

The standalone planar DFS now integrates the existing `axes_from_fold_state()` and `group_parallel_bends()`: axis groups are derived from authoritative flat-model bend lines; all remaining bends are still eligible at every DFS depth. It prioritizes finishing the previous axis group to reduce handling, **but does not force contiguous group execution**. Cross-group interleavings remain possible and are evaluated against the complete FoldState with all completed bends at target signed angles. This prevents unsafe "optimize each group in isolation and concatenate" assumptions.

Each evaluated step records the actual analytical `compute_workpiece_machine_setup()` result; every complete candidate is scored by existing `score_sequence()` (turns, flips, longitudinal shifts, active length changes). Missing or failed placement calculations set `cost_complete=False` so incomplete cost evidence cannot outrank a complete candidate. No fake cost is treated as verified. Top K plans are sorted by sampled-clear status, complete cost evidence, measured score, axis-group transitions and deterministic bend IDs. Each includes numbered bends, group IDs, step evidence and machine setup data. Search evaluates up to `max_complete_candidates=1000` full sequences within node/evaluation/time limits; **top K is optimal only among examined candidates unless search exhausts all branches**. `SEARCH_LIMIT_REACHED` explicitly discloses truncated searches. This is bounded best-of-examined ranking, not certified global optimum.

Optional `backgauge_polygon_machine` (2D closed solid material polygon in machine (s,z) coordinates) now checks every sampled component in the phase section with the existing area-contact oracle. The geometry MUST come from measured/verified machine inputs: no default gauge is fabricated. If absent, result warnings and per-step `backgauge_checked=False` report the missing coverage. Other frame, clamps, transfers and longitudinal end collisions remain out of the selected 2D plane scope. The physics solver remains unchanged. Tool length matching remains **virtual** and is disclosed in runner JSON.

`tools/run_planar_sequence_search.py` now launches without constructing an _PreviewPanel Qt widget; only a read-only SheetMetal session and the library's two 2D tool profiles are created. `print_planar_summary(payload)` prints grouped, numbered, cost-ranked candidates from FreeCAD's Python Console.

### First FreeCAD acceptance command

Update branch and restart FreeCAD; open `Z-(ok).FCStd` and ensure SheetMetal `*_Unfold` exists:

```python
from pathlib import Path
from tools.run_planar_sequence_search import run_planar_search, print_planar_summary
p = Path(r"C:\Users\User\AppData\Roaming\FreeCAD\v1-1\Mod\SMBS_BendSeq-2D")
payload = run_planar_search(
    App.ActiveDocument,
    max_solutions=10,
    max_complete_candidates=1000,
    max_search_time_s=60.0,
    output_path=str(p / "reports" / "z_planar_ranked.json"),
)
print_planar_summary(payload)
```

Synthetic tests validate axis grouping, cross-group ordering, ranked costs and preservation of state; Stage B tests cover optional backgauge overlap and clearance. Native FreeCAD physical acceptance is **outstanding**. This is not a 3D safety check, nor yet a final tested production-grade implementation of all nine original stages.


## Real FreeCAD planar benchmark regression and corrective work (Nov 9 2026)

**Observed Windows FreeCAD result**, document `Z_10_20_10__ok_` / existing `Body_Unfold`: `SEARCH_LIMIT_REACHED`, **73.696 s wall**, **2 individual step evaluations**, **2 DFS nodes**, **0 completed candidate sequences** and `MAX_SEARCH_TIME_REACHED` with a configured 60-second search time. This is a performance failure, not a successful 2D search.

This increment addresses two distinct issues:

1. The pure-Python `contact_oracle_2d.classify_polygon_contact` previously called costly exact segment-crossing and segment-distance functions for every sheet/tool edge pair, including edges with obviously disjoint bounding rectangles. It now computes the tool edge rectangles once per polygon and skips exact edge operations unless expanded edge AABBs intersect. This is a **necessary-condition-only exclusion**: for segments with AABB gap > tolerance, neither a proper intersection nor a within-tolerance distance is possible. Interior witnesses for large tool rings outside the sheet polygon AABB are also skipped before point-in-ring evaluation. New tests cover dense 256-sided tool contours, contained sheet and empty cavity.
2. `machine_section_3d.compute_machine_cutting_plane_section` previously intersected the upper panel skin with `target_xm` but the lower skin with **0.0**. Both now slice at `target_xm`, fixing off-center station geometry.

**Diagnostic additions:** Stage B records `timing_ms` for per-probe placement/section versus tooling/contact, and accepts `deadline_monotonic`. Section-only DFS supplies its overall deadline, enabling Stage B to return `STAGE_B_BUDGET` (unverified), rather than blindly start further samples after the deadline. A single active call still cannot be interrupted, so elapsed time may modestly exceed configured limits. Every attempted planar step is now recorded as `report.evaluated_step_details`, including status, elapsed seconds, section/contact timings, number of sampled phases and a reason. The console `print_planar_summary()` prints each attempted bend **even if zero complete sequences were found**.

**Acceptance remains pending**: No Windows FreeCAD execution or GitHub Actions result was available in the assistant environment. The optimization MUST be compared against the same Z fixture: 2 individual candidate checks should be significantly shorter if polygon broad-phase was the hotspot. If not, the new per-step phase timings will reveal whether geometry placement/setup or tool/contact dominates.

### Single new test run (FreeCAD restarted, branch updated)

```python
from tools.run_planar_sequence_search import run_planar_search, print_planar_summary
result = run_planar_search(
    App.ActiveDocument,
    max_solutions=10,
    max_complete_candidates=1000,
    max_search_time_s=60,
    output_path=str(repo / "reports" / "z_planar_ranked.json"),
)
print_planar_summary(result)
```

For headless tests: `tests.test_contact_oracle_2d`, `tests.test_stage_b_multistation_integration`, `tests.test_sequence_search_2d`. These alone do not establish real-tool geometric equivalence or production safety.


## Nov 9 2026: actual Z planar search JSON acceptance data

User-supplied `z_planar_ranked.json` for `Z-10-20-10-(ok)` reports **56.0408647 seconds**, `evaluated_steps=4`, `visited_nodes=5`, `complete_candidates_examined=2`, `limit_reason=""`, and `status=UNVERIFIED_2D_CANDIDATES`. Both `B01,B02` and `B02,B01` were enumerated with complete heuristic changeover cost **12.0**, one axis group, no group transitions. Neither is physically or even fully planar certified.

Four step timings (seconds):
- B01 initial: `elapsed_s=12.2575`, `pose_and_section=0.1340`, `tooling_and_contact=12.1196`.
- B02 after B01: `elapsed_s=14.0072`, `pose_and_section=0.1291`, `tooling_and_contact=13.8739`.
- B02 initial: `elapsed_s=11.6089`, `pose_and_section=0.1229`, `tooling_and_contact=11.4821`.
- B01 after B02: `elapsed_s=18.1669`, `pose_and_section=0.1263`, `tooling_and_contact=18.0365`.

Thus almost all observed cost is inside tool polygon preparation and/or planar polygon contact evaluation, **not the mathematical FoldState poses**. During BENDING, the sampled 2D statuses include consecutive `UNVERIFIED` results in all four steps; the old JSON does not contain their actual per-tool component/reasons. Backgauge was not provided.

Following this evidence, the 2D contact oracle received a single-pass point-in-polygon winding/boundary check with segment AABB skips and bounds rejection for tool-edge samples; this does not intentionally change contact tolerances. Stage B contact instrumentation now divides `tool_polygon_build` and `polygon_contact` timing and records up to 32 unresolved component findings with phase, tool and reason; the console report displays these for each attempted step. The **new code has not yet been validated or benchmarked in the user's FreeCAD**. Repeat on exactly the same document after branch update and FreeCAD restart, compare the original four step timings and inspect the unknown BENDING contact reason. Never convert UNVERIFIED to CLEAR without evidence.


## Z fixture second benchmark and precise degeneracy diagnosis

The latest user benchmark `z_planar_ranked(1).json` gives **16.1818959 s**, four planar step evaluations, two complete *unverified* sequences, and no search limit. This improves on the previous 56.0409 s by approximately **3.46x**, but is still too slow. Instrumentation proves `tool_polygon_build` totals only about **49.9 ms** across four steps, whereas `polygon_contact` costs **about 15.60 s**; slow geometric polygon checks, not profile construction or FreeCAD FoldState, dominate.

All four bends have six `UNVERIFIED` findings in the `BENDING` phase, identical reason:
`PUNCH / active_bend_zone / Degenerate polygon edge`. Root cause in `contact_oracle_2d._proper_cross`: it considered any segment shorter than **tolerance_mm = 0.02** to be *degenerate*. This incorrectly rejects legitimate short polygon segments arising from curved bend-zone tessellation. The new predicate only rejects **numerically zero-length** edges (`<= 1e-9 mm`), preserving the original `0.02 mm` contact-distance tolerance for crossing/near-contact classification. This does not force any `UNVERIFIED` into `CLEAR`; instead it allows real polygons to be evaluated. Synthetic regression tests check proper interference with a 0.005 mm sheet edge, clear separation, and rejection of genuinely zero-length edge orientation.

Expected next FreeCAD check: compare measured wall time and new finding statuses on same Z part, the same punch/die and 17/9 sample counts. If classification changes to TOUCH or INTERFERENCE, inspect contact geometry; do not assume physically safe. None of these incremental fixes have passed real FreeCAD acceptance or CI in the assistant environment.


## Indexed 2D contact acceleration and TOUCH audit (following Z acceptance)

The `Z-10-20-10-(ok)` third user run reports `17.0378543 s`, `evaluated_steps=4`, two complete `SAMPLED_2D_CLEAR_OR_TOUCH` candidates and zero `UNVERIFIED`. Both sequences (`B01,B02` and `B02,B01`) have heuristic changeover cost 12.0. The precise polygon-contact step dominates wall time (`polygon_contact` is 3694.83, 4142.06, 3421.21, 5200.61 ms for the four bend checks). Tool profile construction takes around 12 ms per bend. Unusually frequent `TOUCH` flags require identifying tool and component; they must **not** be silently interpreted as physical contact without review.

The new `PreparedRing2D` builds an **interval-indexed edge scanline lookup** for each prepared machine-space punch/die outer contour and each hole. Its Y buckets conservatively include every edge whose Y interval intersects the query scanline within the **original** boundary tolerance. The `_point_ring` function now iterates only those candidate edges for boundary detection and winding parity; no geometry is decimated or discretization weakened. Prepared rings are built once per tool per machine-section evaluation and reused across sheet panel queries and polygon-in-tool witness checks. Differential tests compare the indexed point-in-ring result against the unindexed implementation for both contour orientations, hundreds of scanlines and tolerance-edge cases, and compare complete multi-component contact reports for tooling with a hole.

`screen_candidate_stage_b` additionally aggregates `touch_summary`: for each phase it records which tool touched which panel/bend zone, the underlying contact classifier reason, and how many sampled poses produced it. These observations are copied into each standalone 2D DFS step report and printed by `print_planar_summary`. This allows distinguishing legitimate die support contact from spurious punch or other-panel contacts; results remain `SAMPLED_2D` only.

**No claim of measured speedup or validated TOUCH diagnosis yet.** After updating the branch and restarting FreeCAD, rerun the existing full-precision Z test with 17 bending and 9 stroke fractions and compare `polygon_contact` wall times against the old 17.04-second baseline. Review `touch_summary` before treating any plan as physically workable. CI status has not been confirmed.


## Read-only real-tool negative control harness

`tools/run_tooling_negative_controls.py` loads the **same named catalog punch and die** as the experimental Z search (not generic rectangles as tool substitutes). It uses `ToolingSetup2D.punch_polygons_machine` and `die_polygons_machine` and asserts:

1. A translated **copy of each actual tool outline** has `CLEAR` separation beyond the tool's bounding width.
2. Exactly overlapping the **same actual tool outline** returns `INTERFERENCE`, including handling imported tool cavities.
3. A 21-sample horizontal sweep of a synthetic profile-shaped obstruction has both endpoints `CLEAR` but an intermediate `INTERFERENCE`. This demonstrates why endpoint-only path checks are unsound.

These are controlled geometric **negative tests**, not generated modified CAD parts. They do not certify the real `Z-10-20-10-(ok)` extraction path, clamps, backgauge, or collision-free machine movement. The tool profiles are real; synthetic test workpiece shapes are not. The script does not mutate FreeCAD documents and emits JSON. Use this console code after pulling the branch and restarting FreeCAD:

```python
import FreeCAD as App
from pathlib import Path
from tools.run_tooling_negative_controls import (
    run_negative_controls, print_negative_controls
)
output = Path(App.getUserAppDataDir()) / "z_negative_controls.json"
report = run_negative_controls(output_path=str(output))
print_negative_controls(report)
print("REPORT:", output)
```

For lightweight harness tests, execute:

```python
import unittest
unittest.TextTestRunner(verbosity=2).run(
    unittest.defaultTestLoader.loadTestsFromName(
        "tests.test_tooling_negative_controls"
    )
)
```

The real Z-part baseline remains the existing `run_planar_search(App.ActiveDocument)` result. This phase is complete only as a diagnostic harness: a proper physical transfer/withdrawal validator still needs the real FoldState positions at every path sample and the full machine assembly.


## Real FoldState section lateral withdrawal pilot

`tools/run_folded_part_withdrawal.py` uses `build_part_session(doc, [unfold])` and the actual post-bend `FoldState` of the selected bend order. At each completed bend, it calculates `WorkpiecePlacement3D` and calls `compute_machine_cutting_plane_section` (central machine plane, 32 arc segments). The full section includes fixed/moving panels, active bend zone and all intersecting other panels/zones. The real selected SMBS library punch/die are placed at a deliberately parked `20 mm` initial clearance. Each real folded part section is swept by independent +s and -s translations, with 21 discrete samples over 100 mm. The report gives a status for each sampled pose, actual tool/component and specific failure reason. `INTERFERENCE` = sampled central-plane overlap; a `CLEAR`/ `TOUCH` path **must never certify** clearance of a real physical extraction. No FreeCAD document is modified. This deliberately tests an explicit hypothetical lateral withdrawal strategy; it does NOT infer the operator's true removal/reorientation trajectory.

Run in FreeCAD after updating branch and restarting FreeCAD:

```python
import FreeCAD as App
from pathlib import Path
from tools.run_folded_part_withdrawal import run_folded_part_withdrawal, print_folded_part_withdrawal
out = Path(App.getUserAppDataDir()) / "z_folded_withdrawal.json"
probe = run_folded_part_withdrawal(
    App.ActiveDocument,
    sequence=("B01", "B02"),
    distance_mm=100,
    sample_count=21,
    output_path=str(out),
)
print_folded_part_withdrawal(probe)
print("JSON:", out)
```

To compare both sequences, rerun with `sequence=("B02", "B01")`, a different `output_path`, and inspect `steps[].paths.positive_s` and `steps[].paths.negative_s`. Regression tests: `python -m unittest tests.test_folded_part_withdrawal -v` in the configured environment.

**Acceptance is pending**: Real FreeCAD execution and branch CI are not proven here. The pilot may report `UNVERIFIED_2D` if positioning a post-bend section in its own bend's machine placement fails; this must be investigated, never silently treated as clearance. Physical extraction will eventually require complete 3D bodies, actual ram motion, machine obstacles, multiple transfer candidates, rotation and continuous collision sweeps.


## Actual BOTTOM -> RETRACT -> WITHDRAWAL 2D pilot

`tools/run_folded_part_cycle.py` is the next read-only FreeCAD pilot. It builds the actual completed-bend part from `FoldState`, aligns the selected catalog punch and die, and constructs the central machine-space part section at **bottom stroke** using `support_plane_zm = bend_outer_radius - CoupledAirBendingKinematics.stroke_for_angle(target_angle)` (same convention as the Stage B search). The punch moves upward from the model-derived bottom tip height to `contact_z + parked_clearance_mm` over 21 samples. The bent section remains stationary; the die remains fixed. Once the punch is parked, the script tests +/- machine-s lateral withdrawal over 100 mm at 21 samples each. For each step the JSON stores the real section's component identifiers and per-sample collisions, punch `ram_z_mm`, and reasons. Each hypothetical transfer to the next setup is explicitly `UNVERIFIED_2D`: the script does not invent a transfer trajectory, unwind/rotate the workpiece, or certify removal.

**FreeCAD acceptance commands (restart after checkout):**

```python
import FreeCAD as App
import unittest
from pathlib import Path
from tools.run_folded_part_cycle import run_folded_part_cycle, print_folded_part_cycle
suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_folded_part_cycle")
test_result = unittest.TextTestRunner(verbosity=2).run(suite)
print("TEST PASS:", test_result.wasSuccessful())
for sequence, label in ((("B01", "B02"), "forward"), (("B02", "B01"), "reverse")):
    output = Path(App.getUserAppDataDir()) / ("z_cycle_" + label + ".json")
    report = run_folded_part_cycle(
        App.ActiveDocument, sequence=sequence,
        retract_samples=21, withdrawal_samples=21,
        distance_mm=100.0, output_path=str(output),
    )
    print_folded_part_cycle(report)
    print("JSON:", output)
```

This is sampled 2D evidence only. A detected planar `INTERFERENCE` is a useful physical blocker hypothesis requiring verification; `TOUCH` is not automatically a safe contact; `CLEAR` cannot prove a continuous 3D path. No backgauge, frame, clamps, tool-end collision, continuous sweep, springback or true operator transfer is evaluated. The new test harness has **not yet been executed** in the user's FreeCAD installation or confirmed by CI.


## Z real folded-part obstacle reproduced (2026-11-09)

The user supplied `z_cycle_forward.json` and `z_cycle_reverse.json`: across both sequences all four BOTTOM samples are `TOUCH` and all 80 subsequent RETRACT samples are `CLEAR`. Each of eight straight lateral WITHDRAWAL paths collides with the **DIE** at offsets +/-5 and +/-10 mm; offsets +/-15 mm and beyond are sampled `CLEAR`. This *correctly surfaces a critical limitation of the previous 20-mm-parked-only lateral test*: the formed zone remains lowered into the die at the bottom stroke.

`run_folded_part_cycle.py` now additionally tests a hypothetical explicit two-leg strategy: lift the stationary post-bend section by `lift_mm` vertically while the punch is parked, then translate the lifted section +/- machine-s. The two phases `LIFT` and `LATERAL_AFTER_LIFT` each report per-sample status and precise offending tool/component. The unchanged direct lateral-withdrawal result is retained for comparison. This does NOT plan a true handling path or certify continuous or 3D extraction; it does not check tooling end faces, backgauge, clamps, frame, or inter-bend reorientation. Positive/negative paths are assessed independently.

FreeCAD acceptance rerun:

```python
import FreeCAD as App
from pathlib import Path
from tools.run_folded_part_cycle import run_folded_part_cycle, print_folded_part_cycle
for seq,label in ((("B01","B02"),"forward"), (("B02","B01"),"reverse")):
    dest=Path(App.getUserAppDataDir()) / ("z_cycle_lift_"+label+".json")
    r=run_folded_part_cycle(
        App.ActiveDocument, sequence=seq, lift_mm=20.0,
        lift_samples=21, retract_samples=21, withdrawal_samples=21,
        distance_mm=100.0, output_path=str(dest))
    print_folded_part_cycle(r)
    print(dest)
```

Regression additions: `tests.test_folded_part_cycle` covers lift-clearance against a low die obstacle and ceiling interference with a punch. These changes are experimental and not yet verified by user's FreeCAD tests or CI.


## Automatic sampled retract/lift/withdrawal path search

The user-provided Z-10-20-10 reports showed: both bend orders have a clear 20-sample punch RETRACT after BOTTOM TOUCH; direct lateral translations penetrate the DIE near +/-5 and +/-10 mm; adding a fixed 20-mm LIFT removes die contact but at many horizontal offsets penetrates the parked PUNCH. Neither single arbitrary path is adequate as a physical verdict.

`search_sampled_withdrawal_paths()` in `tools/run_folded_part_cycle.py` now **optionally** enumerates a bounded grid of parked punch clearances (default 20, 35, 50 mm), vertical lifts (2, 5, 8, 12, 16, 20 mm), and both +/-s lateral directions for every completed actual FoldState bend. Each punch height is checked via BOTTOM -> RETRACT samples first; candidates with INTERFERENCE, UNVERIFIED or non-bottom TOUCH are disallowed. Each lift and lateral leg uses the actual formed section and full real-profile punch/die polygons. A candidate requires only CLEAR samples along both legs; TOUCH is not silently promoted to CLEAR. Candidates are ranked deterministically by smallest lift, then smallest punch clearance, then direction. All tested candidates retain the first problematic phase, tool and section component in JSON. If no sampled path appears, status is `UNVERIFIED_NO_SAMPLED_PATH`; on per-step time budget exhaustion without candidates it is `UNVERIFIED_SEARCH_LIMIT_REACHED`. No claim of physical impossibility or 3D certification is made.

The new search is **opt-in** with `search_paths=True`; existing baselines are not made more expensive by default. User acceptance script for the two orders (run after updating branch and restarting FreeCAD):

```python
import FreeCAD as App
from pathlib import Path
from tools.run_folded_part_cycle import run_folded_part_cycle, print_folded_part_cycle
for sequence, label in ((("B01", "B02"), "forward"), (("B02", "B01"), "reverse")):
    path = Path(App.getUserAppDataDir()) / ("z_path_search_" + label + ".json")
    report = run_folded_part_cycle(
        App.ActiveDocument, sequence=sequence,
        search_paths=True, path_search_time_s=60.0,
        parked_clearances_mm=(20.0, 35.0, 50.0),
        lift_heights_mm=(2.0, 5.0, 8.0, 12.0, 16.0, 20.0),
        retract_samples=21, lift_samples=21, withdrawal_samples=21,
        distance_mm=100.0, output_path=str(path))
    print_folded_part_cycle(report)
    print(path)
```

Regression tests: `python -m unittest tests.test_folded_part_cycle -v`. The search is deliberately **finite sampled 2D feasibility screening**. It does not search continuous paths, rotate/flip the workpiece between bends, account for supports, end faces, frame, clamps or backgauge, or demonstrate reachability by a real operator.


## 2026-11-09: Real FreeCAD path-search interruption and bounded correction

User ran 9 original regression tests PASS, then attempted automatic path search. Initial call failed because `App.ActiveDocument` was `None` (not a geometry failure). Second call with model open logged a non-fatal warning: flat-section Z span 1.793 mm vs authoritative SheetMetal thickness 1.500 mm; inspect this coordinate/section discrepancy separately. The search was manually interrupted while repeatedly classifying real tool polygons: fixed 3 x 6 x 2 grid at 42 samples per path was too costly, and the previous 60-second time check occurred **only between full paths**.

The experimental search is now bounded *within each sample*, aborts a blocked path on its first INTERFERENCE/UNVERIFIED/non-CLEAR sample, reuses the same LIFT observations for both directions at a given clearance/height, and restores tooling position in `finally`. Only **fully sampled CLEAR** paths become candidates; any partial, ambiguous or deadline-limited evidence is `UNVERIFIED`. The search response includes `evaluated_poses`, `search_limit_reached` and individual first obstruction details.

Use a small smoke test before the full grid, with an explicitly resolved open document:

```python
import FreeCAD as App
from pathlib import Path
from tools.run_folded_part_cycle import run_folded_part_cycle, print_folded_part_cycle
doc = App.getDocument("Z_10_20_10__ok_")
assert doc is not None, "Open Z-10-20-10-(ok) in FreeCAD first"
out = Path(App.getUserAppDataDir()) / "z_path_search_smoke.json"
report = run_folded_part_cycle(
    doc, sequence=("B01", "B02"),
    search_paths=True, path_search_time_s=8.0,
    parked_clearances_mm=(20.0, 35.0),
    lift_heights_mm=(5.0, 12.0),
    retract_samples=9, lift_samples=9, withdrawal_samples=9,
    distance_mm=100.0, output_path=str(out))
print_folded_part_cycle(report)
print(out)
```

This acceptance run remains pending. Re-run unit tests `tests.test_folded_part_cycle` and report failures. Do not treat the sampled search's lack of a candidate as geometric impossibility.


## 2026-11-09 smoke results and regression fixture fix

The user ran `z_path_search_smoke.json` on real FreeCAD Z-10-20-10-(ok): search took 5.1093 seconds total, B01 found 7/8 sampled-clear candidate paths (119 pose evaluations, 1.3863 s), B02 8/8 (126 pose evaluations, 1.3949 s), neither hit the deadline. Both steps' two-axis direct lateral paths still intersect the die. Shortlist candidate: +5 mm vertical lift and +20 mm punch parked clearance, either transverse direction. **These results are coarse sampled evidence only:** 9 lateral samples across 100 mm gives 12.5 mm increments, potentially skipping smaller collision windows. The 9-mm-sampling hypothesis is not the same as a fully physical certified path.

Of 11 regression tests, 10 passed. The failing `test_short_circuit_on_first_lateral_collision` had a punch fixture starting at z=3, exactly touching the vertically raised workpiece at the endpoint of LIFT (z=3). Early stop on TOUCH is intentional. Fixture is corrected to position the punch in x=2.2..4 and z=2.5..6, providing a genuine **area overlap first detected during lateral sweep** rather than premature boundary TOUCH; a second regression explicitly asserts that a boundary-only TOUCH is distinct from interference and stops when instructed. New regressions require rerun in FreeCAD.

For denser acceptance, start with one candidate configuration only and 41 lateral samples (2.5-mm spacing), rather than blindly expanding the entire Cartesian grid:

```python
import FreeCAD as App, unittest
from pathlib import Path
from tools.run_folded_part_cycle import run_folded_part_cycle, print_folded_part_cycle
result=unittest.TextTestRunner(verbosity=2).run(
    unittest.defaultTestLoader.loadTestsFromName("tests.test_folded_part_cycle"))
print("TEST SUCCESS:",result.wasSuccessful())
doc=App.getDocument("Z_10_20_10__ok_")
assert doc is not None
out=Path(App.getUserAppDataDir())/"z_path_search_dense.json"
report=run_folded_part_cycle(
    doc, sequence=("B01","B02"), search_paths=True,
    parked_clearances_mm=(20.0,), lift_heights_mm=(5.0,),
    retract_samples=21, lift_samples=21, withdrawal_samples=41,
    path_search_time_s=15.0, distance_mm=100.0, output_path=str(out))
print_folded_part_cycle(report)
print(out)
```

This remains insufficient to establish continuous collision-free tool movement. A continuous sweep or conservative interval validation, plus 3D geometry and machine assembly, is necessary for a physical certificate.


## Continuous swept-area screening of rigid 2D extraction (experimental)

After user acceptance of the denser sampled search (`tests.test_folded_part_cycle` 12/12 PASS; 5-mm lift with 20-mm punch clearance: 2 sampled-clear candidates per bend at 2.5-mm spacing), the next stage adds `smbs_core/kinematics2d/swept_translation_2d.py`. For each real FoldState section polygon and exact rigid-translation vector, this constructs its **whole swept polygonal region**, not a series of samples: original polygon + terminal translated polygon + quadrilateral strips along every boundary edge. Works on simple concave polygonal components, using existing `check_machine_section` and catalog tool holes; disjoint bounding-box strip prefilters can skip irrelevant patches without approving overlaps. Both legs are screened separately: vertical LIFT, then horizontal +/-s. A reported area overlap is `SWEPT_2D_INTERFERENCE`. Any TOUCH, UNVERIFIED geometry or time limit fails closed and is never reported as swept-clear.

The existing sampled stage is retained; swept screening is **opt-in** via `verify_swept_paths=True`, and only completed sampled-clear candidates are evaluated. Their `candidate["sweep_screening"]` field records continuous-phase result. `path_search["sweep_screened_candidates"]` contains only candidates whose both legs were swept-clear under the current single-plane polygonal model. IMPORTANT: no stage claims continuous 3D clearance, physical tooling validity or inter-bend transfer validity. Tool/part contour tessellation is still an approximation, and press ram retract is still only sampled, not swept.

FreeCAD acceptance script after checking out latest branch and restarting FreeCAD:

```python
import FreeCAD as App, unittest
from pathlib import Path
from tools.run_folded_part_cycle import run_folded_part_cycle, print_folded_part_cycle
suite = unittest.TestSuite((
    unittest.defaultTestLoader.loadTestsFromName("tests.test_folded_part_cycle"),
    unittest.defaultTestLoader.loadTestsFromName("tests.test_swept_translation_2d"),
))
result = unittest.TextTestRunner(verbosity=2).run(suite)
print("TEST SUCCESS:", result.wasSuccessful())
doc = App.getDocument("Z_10_20_10__ok_")
assert doc is not None
output = Path(App.getUserAppDataDir()) / "z_path_swept.json"
report = run_folded_part_cycle(
    doc, sequence=("B01", "B02"),
    search_paths=True, verify_swept_paths=True,
    parked_clearances_mm=(20.0,), lift_heights_mm=(5.0,),
    retract_samples=21, lift_samples=21,
    withdrawal_samples=41, distance_mm=100.0,
    path_search_time_s=30.0, output_path=str(output))
print_folded_part_cycle(report)
print("JSON:", output)
```

Interpretation: Existing sampled-clear paths may be rejected by swept-overlap screening. A surviving swept candidate is a stronger PLANAR straight-translation result, not a certified physical process. The thickness warning `flat_section_z_span=1.793 mm` versus `authoritative_thickness=1.500 mm` remains separately unresolved. Do not promote either order of bending to physically validated until full 3D and transfer checks pass. New code has not yet been executed in actual FreeCAD or GitHub CI.


## 2026-11-09 real swept 2D validation and contact-ranking bug fix

User executed the first swept test in FreeCAD: 18/19 tests passed and real Z-10-20-10-(ok) `z_path_swept.json` recorded 4/4 swept-screened paths (B01/B02, +/-s, 5-mm lift, 20-mm punch clearance), total report time 9.334 s. Each lift+withdraw swept candidate screen evaluated 75 non-disjoint polygon patches. No discovered planar intersection in the catalog polygon approximation. However, the 3D transfer and complete moving-ram swept validation are still absent.

The failing `test_punch_blocks_lift_before_lateral` expected `SWEPT_2D_INTERFERENCE` but received `UNVERIFIED_SWEPT_2D_CONTACT`. Root cause: the sweep is a UNION of section polygons and edge-strips. Some earlier generated patches touch a tool boundary; later patches have genuine positive-area penetration. The screening implementation incorrectly returned at the **first TOUCH** before checking the rest of the sweep. Fix: remember first ambiguous non-CLEAR finding and continue examining the remaining patches. Return immediately on any `INTERFERENCE`, otherwise return the remembered `UNVERIFIED_SWEPT_2D_CONTACT` at the end. Neither TOUCH nor UNVERIFIED can be converted to CLEAR. Added explicit boundary-only touch regression.

After updating the branch and restarting FreeCAD, run:

```python
import unittest
suite=unittest.TestSuite((
    unittest.defaultTestLoader.loadTestsFromName("tests.test_folded_part_cycle"),
    unittest.defaultTestLoader.loadTestsFromName("tests.test_swept_translation_2d"),
))
result=unittest.TextTestRunner(verbosity=2).run(suite)
print("TEST SUCCESS:", result.wasSuccessful())
```

The real model's previously saved JSON is evidence for the pre-fix revision. A repeat swept report on the new code is required to establish that the revised predicate returns the same physical candidates. In particular, no actual 3D press-brake sequence or production feasibility claim is warranted.


## Fixed-CAD benchmark source spans and execution evidence

A review of Antigravity's `tools/benchmark_2d_vs_baseline.py` exposed an unsafe assumption: `length_mm` (falling back to 100mm) was converted into machine spans `(-L/2,+L/2)` and labeled `verified`. This is not a source-CAD measurement.

The benchmark now uses `verified_fixed_tool_spans` to obtain the exact fixed-length selected punch and die FCStd source paths through the same `physical_validation_25d._source_path` contract and machine-placed solid loaders `load_native_punch`/`load_native_die`. It requires `GEOMETRY_VERIFIED` source profiles and uses actual `machine_shape.BoundBox.XMin/XMax`, cross-checked with the native loader's `span_xm`; any mismatch, virtual-length flag, missing source or invalid solid causes an explicit error, never a fabricated span.

**Policy:** this physical benchmark is now strictly `LIBRARY_FIXED` (no `match_active_bend_length` virtual extrusion). Both baseline and 2D modes use this same physical model. Existing independent virtual-length benchmarks are unaffected.

The benchmark identities now include SHA-256 of the model and both selected CAD files, real spans, selected direction, length policy and key tolerances. As before, cold-state results are compared using `compare_measurements`.

The benchmark also runs scoped `count_station_screenings`. This records per-phase multi-station previews, actual evaluated 2D sections, unverified stations and 2D interference reports; mere enabled flags do not count as execution. JSON `screening_evidence.status` is `NO_VERIFIED_2D_STATION_EVALUATION` if no station could be evaluated. Distinct from `Stage B evaluated`, a count of candidate previews.

**Limits:** actual physical results still require a FreeCAD runtime and local execution. The above establishes source spans and observation of sampling, NOT physical certification of the full 3D path or automatic proof of a speedup. New tests are synthetic regression contracts; CI execution is not assumed.


## Two physical 100mm sections for a 200mm Z bend (experimental)

The `Z-(ok).FCStd` physical baseline with AP1017 + AT3015 failed at `INSUFFICIENT_TOOL_COVERAGE`: 200mm bends, one 100mm fixed CAD punch and die. This is a valid guard and must NOT be removed or bypassed with virtual extrusion.

The shared `ToolingSetup2D` now carries optional `punch_section_offsets_xm` and `die_section_offsets_xm`, set via `set_native_sections(...)`. `check_current_25d_pose` and `check_active_25d_bend_trajectory` use the **existing** `load_native_punch` / `load_native_die` `section_offsets_xm` parameter with the same configuration. This also applies to backward search and independent forward replay because those paths use the same physical validator. Step/dead-end cache identities incorporate the section offsets.

The benchmark's `verified_fixed_tool_spans` validates real post-transform CAD `machine_shape.BoundBox`, section count, requested offsets, overlaps and gaps. It does not accept nominal 200mm as evidence. The virtual-bend-length option is prohibited for physical section assemblies. A successful 200mm span establishes geometrical coverage only; it does NOT qualify physical clamping, machine force limits, adapter alignment or continuous trajectory collision freedom.

**macOS command after checking out latest `experiment/2d-search-screening`**:

```bash
cd "$HOME/Library/Application Support/FreeCAD/v1-1/Mod/SMBS_BendSeq_Experiment"
export PYTHONPATH="$PWD:$HOME/Library/Application Support/FreeCAD/v1-1/Mod:/Applications/FreeCAD.app/Contents/Resources/lib"
"/Applications/FreeCAD.app/Contents/Resources/bin/python" tools/benchmark_2d_vs_baseline.py \
  --fixture "Z-(ok).FCStd" --direction FORWARD \
  --sections-xm=-50,50 --arc-segments 32 \
  --phase-time 20 --search-time 40 --max-states 20 --max-checks 50 \
  --output reports/comparison_z_two_sections.json
```

In this example each *real* library tool has source length 100mm and is installed twice at machine-X centers -50mm and +50mm. Both baseline and screened runs use the exact same assembly. Read `kernel_snapshots.*.verified_tool_spans`, `kernel_snapshots.*.step_diagnostics`, `kernel_snapshots.*.search_report`, `screening_evidence` and `savings`. Low search limits are for diagnosis, not for production qualification.

The next required test is a local **native FreeCAD/OCCT run** verifying that the assembled solids load, stay valid, and actually reach collision and five-phase trajectory validation. If the native fuse, geometry, coverage, or full independent replay fails, retain the fail-closed result and diagnose it. No complete sequence or speedup is claimed by these commits. Test both FORWARD and BACKWARD after the first successful diagnosis; then test the eight-bend box model, compare 10 diverse verified sequences if feasible, and extend tooling/clamp/backgauge/frame coverage before any public release.


## Required user deliverable: numbered TechDraw manufacturing sheet

The target addon is **sequence calculation + understandable animation + printable bend instructions**, not industrial finite-element metal-forming simulation. After an accepted bend order, users must be able to create or update a TechDraw page with a flattened view annotated with actual operation numbers, signed bend angles, folded-part view and bend-operation table. The original stable bend IDs are mapped to SheetMetal's bend-line sketches (not lexicographic Bend object order). PDF/print and stale-drawing protection are mandatory. See [TECHDRAW_BEND_SEQUENCE.md](TECHDRAW_BEND_SEQUENCE.md) for exact data, UI, enablement and acceptance contracts. This functionality is **specified but not implemented**; its absence blocks final user-facing release.


## Next core milestone (2026-11-09): 2D candidate orders in FreeCAD GUI

**Scope decision:** work on the sequence solver and usable bend animation now. The TechDraw implementation remains deferred; no work on drawing/PDF in this milestone. The existing 3D collision/contact thresholds and classifications are unchanged.

The FreeCAD PressBrakeSim 2.5D panel now has **`2D варианты`** under the standard Load / Tooling / `Расчёт` row. This runs the existing experimental `search_bend_sequences_2d` from the loaded SheetMetal `LoadedPartSession` and currently selected 2D profiles (10 candidate limit, bounded by 30 seconds, 5000 nodes and 10000 evaluations). Unlike `Расчёт`, it does NOT run native OCCT collision checks. For isolated planar exploration it may use virtual axial tool length; this is explicitly labeled and must never appear as verified physical equipment.

Results open a modal list with ordered bend IDs, planar evidence and operation-cost data. Choosing **`Показать порядок в симуляции`** applies the candidate to the existing `BendSequenceController` so the user can inspect it using the current Play/Pause/Reset controls. The order is explicitly marked **`2D-ПРОСМОТР (НЕ ПРОВЕРЕНО В 3D)`**; choosing it invalidates any previous strict search report. No 2D result is automatically qualified, certified or sent to TechDraw as a manufacturing sequence.

**Acceptance still needed on actual FreeCAD 1.1.x GUI:**
1. Load a model with a genuine SheetMetal Unfold, select punch and die.
2. Click `2D варианты`, inspect candidate list, warnings, status and ordering.
3. Select a candidate and play all bends. Ensure the same IDs and signed angles are used.
4. Check reload, reset, reorder, empty candidate set, stopped playback, no duplicate Qt widgets, and restoration of initial FoldState/tooling after search.
5. Compare actual 2D preview to folded model for L / Z / U / box and investigate panel jumps only if observed.
6. Run the existing `Расчёт` independently for physical qualification; never reclassify an unverified 2D preview as a safe manufacturing instruction.

**Next after GUI acceptance:** use actual verified model runs to improve candidate ranking or choose the correct 2D/3D qualification budget, then test forward and backward replay and the full animation. Continue to protect physical correctness without spending this phase on sub-millimetric punch/contact modeling.


## 8-bend 2D GUI search timeout: first-candidate mode (2026-11-10)

User-confirmed: Z sample loaded, 2D candidate search returned orders, and both bends animated correctly. On an 8-bend part the original GUI candidate search timed out with the 30-second budget. The GUI was requesting up to 100 **complete** candidates before ranking; this can be expensive and unnecessary for a first useful preview.

The button `2D варианты` now requests **one complete 2D candidate** with a strict 30-second budget, rather than 100, and the core DFS now stops immediately when the completed-candidate budget is reached. The standalone `tools/run_planar_sequence_search.py` retains its higher-capacity ranking capability. This is a **latency-oriented experimental change**: the first complete candidate is neither globally minimum-cost nor independently 3D validated.

Acceptance on the same 8-bend model: record elapsed time, `status`, `limit_reason`, `visited_nodes`, `evaluated_steps`, `complete_candidates_examined`, candidate list and playback result. If still timing out before a single complete candidate, investigate Stage B call timing, search ordering, and pruning; do not mask the result by raising the timeout or lowering collision safety thresholds. TechDraw and contact thresholds are untouched.


## 8-bend timeout after first-candidate optimization (2026-11-10)

Observed in FreeCAD GUI: **0 candidates, 118 planar evaluations, MAX_SEARCH_TIME_REACHED**. Reducing `max_complete_candidates` from 100 to 1 was not sufficient. Do not increase timeout as a substitute for diagnosis or claim `NO_FEASIBLE_SEQUENCE`: this is **SEARCH_LIMIT_REACHED** and proves neither feasibility nor impossibility.

Now the existing `PlanarSearchReport` records `max_depth_reached`, `deepest_prefix`, `rejection_counts`, and five `slowest_steps`, and the GUI displays depth/classification and offers **Save diagnostics JSON** in the candidate dialog even with zero candidates. This allows actual 8-bend profiles to distinguish early repeated 2D rejections, slow Stage B evaluation and deep unsuccessful branches. The 3D validator and contact tolerances are unchanged.

**Next gate:** request the real FreeCAD-generated JSON from the eight-bend model. Compare the deepest prefix, per-step reasons, and Stage B timing. Only then choose a deterministic search ordering/pruning/caching change supported by measured evidence. Do not reclassify 2D interference as physically safe to obtain candidates. Qualification and TechDraw are separate milestones.


## 8-bend measured profile and progressive screening (2026-11-10)

Actual FreeCAD JSON `smbs_2d_search_diagnostics.json`: 124 evaluations in 30.007 s, 63 DFS nodes, 0 full candidates, deepest prefix `B01 -> B07 -> B03 -> B06 -> B02 -> B08 -> B04` (7/8), where `B05` was rejected at APPROACH against panel P04. Classification: 74 `2D_INTERFERENCE`, 49 sampled clear/touch, 1 unverified. Aggregate instrumented Stage B `polygon_contact` ~23.52 s of `total_probe` ~29.57 s; `pose_and_section` ~5.8 s. Most common rejection: `APPROACH:P04:proper_material_boundary_crossing` (30 occurrences), then P08 (15). Thus both branch order and exact 2D sample cost matter.

**Change**: GUI `2D варианты` now sets `exploration_fractions=(0,.25,.5,.75,1)` and `exploration_stroke_fractions=(0,.5,1)` instead of the full default 17 bending / 9 stroke points. The existing `search_bend_sequences_2d` accepts these as optional parameters (defaults preserve old behavior). When a full 8-bend path is reached, the solver **replays the entire path** at original 17/9 sampling before presenting any candidate. Any replay interference rejects that path and DFS continues. Coarse and detailed cache entries use separate keys; the original 2D polygon contact predicates and all 3D collision/contact thresholds remain exactly unchanged. `final_replay_attempts` and `final_replay_rejections` are reported for evaluation. This is not a substitute for independent 3D qualification, backgauge or full machine clearance.

The optimization is **coded, not yet FreeCAD-measured**. Run regression `python3 -m unittest tests.test_sequence_search_2d -v` and repeat the same eight-bend GUI search with JSON diagnostics. If still zero completed paths, prioritize machine setup/order constraints and the repeated P04/B05 obstruction; do not silently allow 2D interference or certify sample results.


## New 8-bend real-machine diagnostic and branch ordering experiment (2026-11-10)

Second FreeCAD JSON (progressive sampling) shows 281 checked unique transitions, 494 visited DFS nodes and no complete order in 30.010 s; max depth remains 7/8, with zero final detailed replays. Of 281 steps, 136 are 2D interference and 145 are sampled clear/touch. Most frequent blockers are `APPROACH:P04` (30), `APPROACH:P08` (27) and several other particular machine sections. Importantly, bend B05 passed in only one checked context (after B03); its later placement usually fails at P04. 23.18 s are spent in polygon contact, 5.62 s in placement/section.

The related, separate `experiment/box-last-bend-setups` test tool lists a previously used known box sequence `B08 -> B06 -> B07 -> B05 -> B04 -> B02 -> B03 -> B01` for `box.100out-50in-(ok).FCStd`. This is a **model-specific clue, not proof** that the present user's loaded eight-bend geometry is identical or physically qualified. Before calling any order valid, compare exact source model, tooling, bend identifiers and independent forward replay evidence.

**New experiment:** optional `preferred_bend_order` in `search_bend_sequences_2d` changes DFS branch exploration order only and is validated as a strict permutation of bend IDs. The GUI's `2D варианты` button tries the reverse CAD-order preference for models with >=6 bends (`group_first=False`); smaller models keep the legacy grouping. This aims to visit late construction bends sooner, rather than exhaustively following B01 rooted branches. **No sequence is accepted unless every transition passes the existing Stage A/B, followed by full-density Stage B replay.** Failure/timeouts still report `SEARCH_LIMIT_REACHED`.

This is not a guaranteed fix or universal best heuristic. Next same-fixture run: compare complete candidates, deepest prefix, replay count, step counts and all 2D rejections to the previous JSON. If still blocked, compare with the independently verified backward-search sequence for the exact same fixture and inspect whether fixed section/orientation assumptions reject its known valid steps. Never weaken 3D collision/contact rules to get a result.


## 10-bend timeout investigation and safe 2D DFS scheduling (2026-11-10)

Fixture `smbs_2d_search_diagnostics_test.json` reported 10 bends, 270 unique evaluated steps, 520 DFS node visits, 30.003 s search timeout, no complete candidate and a deepest partial order of eight bends. B09 repeatedly failed (63 recorded rejections) after the initial B10-first branch. This outcome is not an infeasibility proof: none of the other starting bends were fully explored.

In integration branch `integration/gui-2d-search-screening`:
1. The search memoizes **only fully exhausted folded-subset states for which no coarse complete order was reached**. A subset whose coarse full path was rejected during final detailed replay is not memoized as dead (that rejection can be order-dependent). Search cutoffs and resource limits also forbid recording the truncated state as dead. JSON now includes `dead_state_cache_hits` and `dead_states_proven`.
2. Optional `root_probe_nodes` schedules a bounded DFS trial for **each** root bend, then a complete DFS pass if no solution was found. Cutoff states are not incorrectly declared exhausted. JSON now includes `deferred_root_probes`.
3. The GUI enables `root_probe_nodes=65` only at 10+ bends. The GUI's existing eight-bend path (`root_probe_nodes=0`), reverse preference, reduced exploration sampling and strict full-sampling final replay are otherwise unchanged.
4. Regression tests cover late-root discovery, status, state restoration and coarse-to-full replay.

The change is experimental and is **not** proven to solve this real 10-bend model until its FreeCAD JSON confirms a complete full-density replay. Do not bypass collisions, relax tolerance, or confuse a sampled 2D candidate with 3D machine approval. Important: the 10-bend diagnostic has backgauge checking disabled/missing. Run `python3 -m unittest tests.test_sequence_search_2d -v` and the same 10-bend fixture inside FreeCAD. Preserve the eight-bend `B08 -> B07 -> B06 -> B05 -> B04 -> B03 -> B02 -> B01` GUI replay as a separate acceptance test.


## 10-bend live finding: cached states prevented node-based root fairness (2026-11-10)

Actual FreeCAD diagnostics `smbs_2d_search_diagnostics(3).json` after node-budget optimization: 10 bends, 261 unique checked steps, 61 DFS visited nodes, 81 dead-state cache hits, 56 proven dead subsets, 0 deferred root probes, depth 8/10, and `MAX_SEARCH_TIME_REACHED` after 30.0068 s. **Every evaluated step remained under B10**; B09 was rejected 61 times (APPROACH / P02). Although `root_probe_nodes=65`, memo hits kept the visited node count at only 61, so this node threshold did not interrupt B10 even as wall time expired.

Fixed integration search by adding optional `root_probe_seconds` with finite/nonnegative validation and per-root wall-clock deadlines. Local node/time cutoff returns normally, increments `deferred_root_probes`, never marks truncated states as exhausted, and the existing full DFS fallthrough remains. The GUI enables `root_probe_seconds=1.8` for ten or more bends (beside the previous 65-node allowance), **zero for eight bends**, preserving the qualified eight-bend GUI path. Stage B still uses the original global deadline and full-density detailed replay remains mandatory before reporting any candidate.

Regression tests include slow Stage B with node counts below the cutoff to ensure later first-bend roots are examined; invalid time-slice arguments; and prior memo/replay tests. **Requires real FreeCAD re-test**; success is not claimed until JSON shows multiple first-bend roots, full ten-bend sequence, and detailed replay. If still zero candidates, inspect whether B09 has any feasible earliest placement and whether its P02 interference is caused by actual geometry/tooling setup vs preview false positive. No 3D physical approval is implied.


## Two repeat ten-bend runs after wall-time root scheduling

`smbs_2d_search_diagnostics1(1).json` and `smbs_2d_search_diagnostics(4).json` both timed out at 30 s with 0 candidates and depth 8/10. They visited 107 DFS nodes; deferred root cutoffs=57 in both; dead state hits 54 and 53; distinct evaluated steps 299 and 296. Unlike before, the first-level probes cover **all ten starts**. Thus root scheduling is functioning, but it has not established a complete order.

Both reports show B09 was admissible only as the first bend among sampled visited states (B09 checked 75/74 times, with 74/73 interference classifications after a nonempty prefix). In the current pose/tooling, B10 followed by B09 reports `APPROACH:P02:proper_material_boundary_crossing`; B09 followed by B10 reports `APPROACH:P03:proper_material_boundary_crossing`. B09-first branch reached a prefix of length eight but its next B10/B02 candidates collided in those recorded samples. This suggests that machine-space placement and section construction for B09/B10 and P02/P03/P05 should be checked before adding more blind DFS heuristics.

**Diagnostics extension:** each search report now exposes `paired_first_step_obstructions` for every tested pair where both two-bend orders had 2D interference. The GUI prints those advisory pairs and the JSON records each direction's exact reason. These findings are **not logical impossibility certificates** (intervening operations, setups and 3D geometry can differ) and therefore do not prune search branches. A regression test ensures candidates continue to be found when the pair can be performed after an intervening operation.

Next verification in FreeCAD: compare B09->B10 and B10->B09 section geometry and tool positioning, verify FoldState/SheetMetal bend signs, and establish whether the true source model can be bent with the selected tooling/pose. No threshold relaxation or 3D validation bypass. Eight-bend preview settings stay unchanged. Code is committed but not yet exercised in local FreeCAD.
