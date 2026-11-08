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
