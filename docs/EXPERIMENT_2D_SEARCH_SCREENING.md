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
