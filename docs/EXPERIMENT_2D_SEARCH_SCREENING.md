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
