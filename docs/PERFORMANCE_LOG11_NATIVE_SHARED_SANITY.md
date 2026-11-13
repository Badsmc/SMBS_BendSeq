# Native FreeCAD benchmark: shared workpiece sanity, 2026-11-13

Status: **FREECAD_VERIFIED ON ONE REFERENCE RUN / PROVISIONAL SPEEDUP**.
This is a comparison of two user-supplied local developer journals, not a
benchmark executed by the GitHub runner. Raw logs and local file paths are
not committed.

## Comparison

Reference FCStd: 10 bends / 11 panels, same recorded Manual order
`B07 B03 B05 B01 B08 B04 B09 B10 B06 B02`, 180-degree operator orientation,
AP1017 punch, AT3015 V12 die, virtual active bend length, Conditional end zones
10 mm, identical recorded `AdaptiveSamplingConfig` and source document path.
No independently confirmed part/tool FCStd file SHA-256 or repeat cold/warm
hardware trials.

| Metric | Previous log10, last run | New 20261113-130304 | Change |
| --- | ---: | ---: | ---: |
| Manual wall, seconds | 1180.5304 | **1046.8586** | **-133.6718 s (-11.323%)** |
| Repeated `pair_geometry_sanity` calls | 1322 | **1024** | -298 calls (-22.54%) |
| Sanity region wall, seconds | 347.9817 | **197.4414** | **-150.5403 s (-43.26%)** |
| `Part.common` calls | 627 | 627 | 0 |
| `distToShape` calls | 548 | 548 | 0 |
| Full-sheet unions and qualifications | 297 | 297 | 0 |
| `Part.common` measured seconds | 153.4613 | 156.9805 | +3.5192 |
| `distToShape` measured seconds | 180.4669 | 182.9966 | +2.5297 |
| Sheet union measured seconds | 87.6076 | 90.0206 | +2.4130 |
| Sheet qualification measured seconds | 116.7888 | 119.3580 | +2.5692 |
| Bend-zone creation measured seconds | 82.6134 | 84.3823 | +1.7689 |

The code change is a per-pose reuse of **one exact workpiece validity check**
when evaluating the same unmutated machine-frame shape against punch and die.
It does NOT suppress workpiece validity for the pose, independently required
tool-shape validity, or any collision predicate. The 298 removed duplicate
checks and reduction in sanity wall agree with the intended mechanism.
The difference of saved sanity time vs whole-run time reflects variations
elsewhere in the real OCC workload; no additive speedup guarantee is implied.

## Loaded source identity

The installed ZIP reports `git_branch=UNKNOWN`; it is not a Git checkout.
However, direct SHA-256 of exact files fetched from
`experiment/component-collision-oracle` matches the user journal:

- `native_collision_3d.py`: `5e50e03034ccdf6f259ba449946655edb33bf3d5b1524467320f80a868a033e4`
- `native_bend_solid_3d.py`: `6ffdce7e13b289e38c949170c6c4eab4c796a269ea94dc838d8b43a635ad2ae6`
- `motion_trajectory_3d.py`: `b8265716f93c54d3903fa70822e1675e6e86750606c35564204cccf51f6ad7c5`

The runtime composite source fingerprint was
`1be2bbb8551100408e70fe3dbe531ca3e48bdff67367cf04a492f132b57f625f`.
A composite change from the previous run is expected from the one-file
optimization; no claim is made about byte-identical FCStd/tool binaries.

## Physical correctness and coverage

- All ten requested bends completed and the per-bend **status** and
  `native_status` fields exactly match the preceding last log10 run.
- B08, B04, B06 remain `CONDITIONAL`, a virtual punch end-zone allowance.
- B02 remains **PUNCH COLLISION during RETRACT**, not allowed Conditional.
  Native component forensics attribute ~500.926 mm³ overlap each to P09
  and P11 and ~2.75176 mm³ to central bend zone B09 at the refined sampled
  blocking pose. It is not a numerical tolerance-only false hit.
- Six other bends are `SAMPLED_3D_CLEAR_NOT_CONTINUOUS`, not proven
  safe over all continuous motions. Transfer/gauge/clamps/frame are not
  certified. One 2D/native disagreement remains for B03, not newly created.
- The **same exact 627 Common and 548 distance queries** and the same
  297 whole-sheet reconstructions show the component-only oracle is still
  **not integrated**. This is a per-pose validity improvement, not the
  expected component-first structural acceleration.

## Decision and next gate

**Accept** the scoped shared workpiece-sanity optimization as
`FREECAD_VERIFIED` for this one reference case, but speedup is PROVISIONAL
until additional matched trials and source tool/part hashes are collected.
Keep the strict old full-sheet oracle by default.

Next work: integrate the authoritative sheet components into a shadow
component-to-tool comparison with exact machine-frame transforms and
fallback logic; compare per-sample component candidate counts, intersections,
Conditional classification and B02. No `CLEAR` or production rejection
without exact end-zone/topology evidence. Only then progressively skip
297 full unions where strict equivalence is independently established.
