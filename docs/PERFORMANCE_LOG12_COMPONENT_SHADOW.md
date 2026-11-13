# Native FreeCAD component shadow run 2026-11-13 14:10 + corrective gate

Status: **ONE NATIVE SHADOW RUN VERIFIED / NO PERFORMANCE IMPROVEMENT CLAIMED**.
Original input: user-provided `SMBS_debug_log(20261113-141036).json`; private
1.12 MB local file is not checked in. Metrics below are read from its ten
`PERF_NATIVE_BEND_KERNEL` events and final `MANUAL_NATIVE_CHECK_FINISHED`.

## Installed source and reference

`SMBS_RUNTIME_READY` reports composite installed SHA-256
`a875c255ec2c3da07eea6b05b2b13b6cdc9f6da7342d58cd562b02a37aa8f93d`
and the loaded component module
`e0b7f2798b07c42b7b097bea420a6d3814b9e643870d1801fbad06bb57349870`.
This is a ZIP-installed Mod with branch/commit UNKNOWN; it has the expected
new `component_shadow` event fields. The same recorded 11-panel/10-bend
reference, AP1017 punch, AT3015 V12 die, virtual active bend length,
10-mm Conditional, reversed manual order was used. The actual FCStd/tool
file SHA values are not present; no fully qualified matched benchmark claim.

| Statistic | Observed |
| --- | ---: |
| Manual wall (all ten bends) | **1047.1784 s** |
| Prior native shadow-off 20261113-130304 | **1046.8586 s** |
| Difference | **+0.3198 s** (effectively no improvement) |
| Shadow probes | **20** (two BENDING samples per bend) |
| Component-to-tool screens | **40** |
| Component AABB pair attempts | **840** (40 × 21) |
| AABB-disjoint eliminated pairs | **603** (71.79%) |
| AABB candidate pairs | **237** (28.21%) |
| Additional component exact Common queries | **30** |
| Transformed candidate BReps | **33** |
| Component proven penetrations | **0** |
| Shadow disagreements | **0**, but no proven hits to compare |
| Incomplete shadow tool screens | **29 of 40** |
| Component shadow instrumented time | **0.931675 s** |
| Total native Common calls | **657**, up from 627 without shadow |
| Total native distance calls | **548** (unchanged) |
| Full joined sheets/multiFuse qualifications | **297** (unchanged) |

Exact shadow outcomes: **29** `candidate_budget_fallback`, **10**
`no_individually_proven_component_penetration`, and **1**
`all_aabbs_disjoint_but_topology_unverified`. The screening limit of four
candidates was exceeded too often by real tool BReps (B02 had 7–10 punch
candidates in sampled BENDING). None of these outcomes means CLEAR or proves
physics equal to full OCC.

**Physical statuses unchanged**: B07/B03/B05/B01/B09/B10
`SAMPLED_3D_CLEAR_NOT_CONTINUOUS`; B08/B04/B06
`CONDITIONAL`; **B02** true punch `COLLISION` in
**RETRACT**; same recorded refined bracket `u=[0.140625,0.156250]`,
blocking ram Z `-1.931328` mm, central B09 ~2.752 mm³ component overlap
from independent exact post-collision attribution. The two shadow probes in
this run were at BENDING `u=1.0` and `u=0.5` for every bend; none
sampled B02 RETRACT. Thus zero shadow disagreements cannot establish
component/legacy equivalence.

## Corrective change (headless-tested, not yet verified by FreeCAD)

- Commit `cce4353`: when shadow count is **2** per bend, reserve the
  first probe for a newly built **BENDING** solid, and one further probe for
  the first **non-Conditional PENETRATION** in **RETRACT**, comparing native
  components in the actual machine frame with the full OCC result.
- If that RETRACT pose reused a previously joined full sheet, build native
  panel/zone components again **only in experimental diagnostics** without
  an extra `multiFuse`; the old native verdict remains authoritative.
- Experimental candidate ceiling increased from 4 to 12, still
  `FALLBACK_REQUIRED` on uncertainty. This may add more OCC component
  Commons and *increase* debug-mode time. Default shadow mode remains OFF.
- Added regression tests for a cached-sheet RETRACT blocker and Conditional
  RETRACT not triggering a false real-blocker probe:
  `tests/test_component_shadow_trajectory_integration.py`.
- Commit `0bc4f93`: fixed test fixture so only the actual RETRACT PUNCH
  component has exact positive overlap, and DIE remains nonpenetrating.
  CI https://github.com/Badsmc/SMBS_BendSeq/actions/runs/43441205080
  finished SUCCESS. An intermediate test-assertion failure on
  `fb6c3ea` was corrected; no production OCC regression was reported.

## Next native acceptance

Run the updated branch with `SMBS_COMPONENT_SHADOW_SAMPLES=2` in FreeCAD.
Expected observables: on B02 one `BENDING` probe plus one true
`RETRACT`-blocker probe; `component_shadow.proven_component_hits`
should be nonzero **only if native exact Common actually proves it**.
If still zero, inspect `reason`, component orientation/placement, tool
geometry and overlap rather than assuming no collision. Verify B02's
full OCC collision and all Conditional statuses remain identical.
No-fuse production short-circuit is **NOT ENABLED**.
