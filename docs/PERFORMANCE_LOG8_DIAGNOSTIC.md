# Performance evidence: SMBS_debug_log(8).json — 2026-11-13

**Evidence status:** native FreeCAD Windows run observed; exact installed source fingerprint present; input FCStd/tool **file hashes** not captured. **NOT a safety certificate and NOT a fully qualified controlled benchmark.**

The user provided the journal privately. Do not commit the original log or usernames, paths, or 3D CAD contents.

## Verified data in the same journal

Two Manual 180° native D6 checks have identical serialized `MANUAL_NATIVE_CHECK_STARTED` configuration and the same recorded part/tool file paths, bend order and end-zone settings.

| Case | Earlier instrumented run | New optimized runtime |
|---|---:|---:|
| Native 3D manual wall time | 1790.2005 s (29:50.2) | **1512.8474 s (25:12.8)** |
| Wall-time difference | — | **277.3531 s less (15.493% decrease, 1.1833× speed ratio)** |
| Completed bends | 10/10 | 10/10 |
| Full overall status | COLLISION | COLLISION |
| First blocking bend | B02, RETRACT | B02, RETRACT |
| Per-bend statuses | 6 sampled-clear, 3 CONDITIONAL, 1 COLLISION | **identical** |
| Exact Part.common operations | 740 | **623** |
| Exact distToShape operations | 670 | **658** |
| Extra `punch_end_zone_common` | 117 | **0** |
| Instrumented Part.common time | 207.157 s | 151.356 s |
| Instrumented native distance time | 360.972 s | 365.852 s |

The installed optimized runtime logs source SHA-256 composite
`0bc252e3353d03d97aea962f3c9aba67313e069f5c49a9e7b2a514336a6a3b81`
for six core/GUI source files. Its native imported modules came from the
same installed SMBS Mod root. The launcher still reports `git_commit=UNKNOWN`
because the installation is a ZIP without Git metadata.

**Important caveats:** sample coverage changed at APPROACH for B07, B09,
B10 (5 earlier, 3 new); all their recorded phase statuses remain
`SAMPLED_INTERVAL_CONVERGED`. This means detailed sample-count
equivalence is **not** identical, even though physical verdicts agree.
The journal does NOT include part/tool SHA-256, a controlled cold-vs-warm
flag, CPU/RAM load or independent geometric certification. Report the
15.493% as an **observed** difference, not a promised stable speedup or
production qualification.

## New instrumented geometry breakdown (optimized run)

| Metric | Recorded value |
| --- | ---: |
| Full-sheet `multiFuse` calls | 297 |
| Sheet `isValid` / topology / volume qualification | 297 |
| Bend-zone cache lookup/build calls | 2970 |
| Bend-zone actual cache misses | 574 |
| Bend-zone cache hits | 2396 |
| Bend-zone evictions due to 128-entry limit | 446 |
| Panel extrusions | **11 total**; zero after first bend |
| Panel rigid transforms | 3267 |
| Time spent in sheet qualification | 139.553 s |
| Time spent in sheet union | 87.971 s |
| Time spent in bend-zone handling | 82.868 s |
| Time spent in panel transforms | 6.956 s |
| Time spent in native distances | 365.852 s |
| Time spent in native Common | 151.356 s |

These timing categories do NOT exhaust the run wall time. Extra computation
includes tool preparation, 3D model transforms, adaptive sampling,
Conditional classification, Python overhead and post-reject attribution.
Never infer the entire wall-time savings from one timer alone.

**Observed priorities:** exact `distToShape` ~366 s, sheet
qualification ~140 s, `Part.common` ~151 s, sheet union ~88 s,
bend-zone geometry handling ~83 s. Caution: the bend-zone build *call*
counter also includes cache hits, so 2970 calls does NOT mean 2970
freshly extruded solids. Of those, 574 are misses and 2396 hits. Merely
raising the 128-entry limit may increase RAM without reducing misses
from distinct folding poses.

## Native physical result at B02 (same on both runs)

- First sampled native blocker: B02 / RETRACT / PUNCH
- Angle: 90°, ram Z: approximately -0.994513 mm.
- Exact sampled first punch overlap **1360.039981 mm³**.
- Per-component OCC attribution:
  - P09: 518.571837 mm³ (near one punch end).
  - P11: 518.571837 mm³ (near the opposite end).
  - bend_zone:B09: 218.775684 mm³ (crosses center of punch).
  - P03: 95.993798 mm³ (crosses center).
  - P01: 8.126826 mm³ (crosses center).
- Therefore the B02 stop is NOT merely an allowed punch-tip-end zone
  contact and must remain **COLLISION**. Conditional on B08/B04/B06 is
  NOT an OCC CLEAR or evidence of real tool relief.

## Follow-on implementation, not represented in this uploaded log

After inspecting this evidence, a focused next optimization was committed
to `development/performance-optimization`:
- `native_collision_3d.py`: optional exact `prefer_overlap_first`;
  when the previous sampled punch had a confirmed positive-volume OCC
  penetration, run the same exact Common before exact distance. **Only
  if Common is positive** may the redundant distance be omitted; when it
  is zero/below tolerance, use the previous exact distance logic. The
  same Common is reused if a fallback must evaluate contact/enclosure.
- `motion_trajectory_3d.py`: opt in for the next punch pose only after
  a measured prior punch PENETRATION, reset hint after a nonpenetrating
  new sample. Die remains on the original route.
- `tests/test_native_common_first_hint.py`: preserve PENETRATION,
  TOUCHING, NEAR_CONTACT, CLEAR and INVALID_GEOMETRY; do not accept
  unknowns as valid clearance; count exact booleans/distances.
- CI green at `efaa346d`, run
  https://github.com/Badsmc/SMBS_BendSeq/actions/runs/43402767881.

**DO NOT claim numerical speedup for this follow-on commit until the
NEXT real FreeCAD run** confirms installed module SHA and compares
per-bend statuses, sampled phase evidence and timings. If performance
regresses, revert or gate the optional hint; do not relax physical checks.

## Next priority

1. Install the new branch source in the single active FreeCAD Mod directory,
   restart FreeCAD and verify per-file loaded SHA-256 in log.
2. Run the same 10 bends/180°/AP1017+AT3015/Conditional 10mm twice if
   feasible; compare against this observed 1512.8474s run and native
   blocker geometry. Capture part/tool file SHA.
3. If statuses match and measured speed improves, continue to
   geometry-union and sheet-validation optimization. **Do not bypass
   full topology validity** or turn uninspected intervals into CLEAR.
