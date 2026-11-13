# SMBS native performance: uploaded SMBS_debug_log(9).json

Date: 2026-11-13. This document contains **summarized results**, not the user's raw Desktop developer journal. Original journal must not be committed. Source SHA-256 of the supplied log:
`91a99b8ac3c8cf023f7d9387690e698c882ff405b96ba497cd9dc85edba85734`.

## Baseline comparison (latest completed runs from uploaded logs 8 and 9)

Same recorded Manual order, selected tooling, 180° reverse-axis selection,
virtual tool lengths, Conditional punch-end allowance of 10mm, and
`AdaptiveSamplingConfig`. Both are real Windows FreeCAD native D6
runs against the recorded `2-1-3-4-(ok) (1).FCStd`.

| Measurement | Log8 previous optimized | Log9 Common-first enabled | Difference |
| --- | ---: | ---: | ---: |
| Full Manual wall | 1512.8474 s (25:12.85) | **1321.3356 s (22:01.34)** | **191.5118 s saved; 12.659% reduction, 1.145× ratio** |
| Part.common count | 623 | 627 | +4 |
| Exact distToShape count | 658 | **548** | **−110** |
| Instrumented Part.common time | 151.356 s | 153.878 s | +2.522 s |
| Instrumented distance time | 365.852 s | **179.738 s** | **186.114 s saved** |
| Full-sheet multiFuse | 297 | 297 | unchanged |
| Full-sheet qualification | 297 | 297 | unchanged |
| Bend-zone cache hits | 2396 | 2396 | unchanged |
| Bend-zone cache misses | 574 | 574 | unchanged |
| Bend-zone cache evictions | 446 | 446 | unchanged |

The identical geometry-building and sample counts across all bends support
attributing the reduced expensive distance work to Common-first. This
is an **observed single-run improvement**, not a statistically qualified
speedup; controlled repeated runs, model/tool file SHA-256 and machine CPU/RAM
metadata were not recorded.

The new run logged runtime fingerprint
`e1257326e357081169bc1a8491fcb0925b13d7e2475cf7286e145434dfd26e57`.
This is the actual installed source bytes, not a Git commit SHA, because
the Mod installation is a ZIP without `.git`. The source fingerprint
differs from the preceding log8 fingerprint; native source paths and hashes
were captured in the journal.

## Physical result, unchanged blocking verdict

All **10/10** bends were checked in the same order. Per-bend result:

- B07, B03, B05, B01, B09, B10:
  `SAMPLED_3D_CLEAR_NOT_CONTINUOUS` (not continuous safety certification).
- B08, B04, B06: `CONDITIONAL` punch end-zone allowance (not cut-out tooling clearance).
- B02: `COLLISION` with real punch in `RETRACT`. Not conditional safe.

Log9's longer, faster onset refinement sampled more positions in the RETRACT
phase (eight versus four in log8). It sharpened the first blocking
sample bracket:

- Log8: nonblocking `u=0.0`, blocking `u=0.25`, sampled blocking ram
  `Z≈−0.994513 mm`, overlap `1360.039981 mm³`.
- Log9: nonblocking `u=0.140625`, blocking `u=0.15625`, sampled blocking
  ram `Z≈−1.931328 mm`, overlap `1004.604607 mm³`.

**This is improved sampled onset localization, not a change in physical
clearance.** The inspected exact Common scene was opened, and the first
blocking overlap was attributed to panels P09 (~500.926423 mm³) and
P11 (~500.926423 mm³) at the punch ends, **plus central bend zone B09
(~2.751760 mm³)**. That central overlap is far above `0.0001 mm³`
tolerance. The default 10mm Conditional allowance must NOT declare
B02 safe. Continuous motion, gauge, clamps, frame and transfer still
lack independent certification.

## Remaining actual measured bottlenecks

The native geometry builder still executes 297 joined-sheet operations
per complete check:

- sheet qualification: **139.231 s**;
- sheet union: **88.146 s**;
- bend-zone handling: **82.841 s**;
- panel rigid transforms: **6.958 s**;
- panel source extrusions: 11 for the first bend only.

The geometry uses 2970 bend-zone requests (2396 cached, 574 built),
with 446 128-entry LRU evictions. Increasing this limit blindly
may increase RAM and is not an evidence-based fix yet.

## Follow-on optimization after THIS uploaded run

The branch now makes precisely **one** OCC topological `joined.isValid()`
and `joined.isClosed()` check per full-sheet `multiFuse`; previously
the successful code performed them once in strict qualification and
again to populate result metadata. The last successful validation result
and already read volume are reused in the report; **no validity
check, volume/topology acceptance predicate, geometric boolean, or
physical collision test was removed**.

- Source `smbs_core/kinematics2d/native_bend_solid_3d.py`:
  `_validated_joined_sheet_volume` helper and unchanged strict volume
  consistency check. Commit `b87d95d`.
- Tests `tests/test_native_sheet_single_qualification.py`, commit
  `1669d73`, cover valid, null, invalid, open, multi-solid,
  nonpositive/nonfinite volumes and fail-closed OCC errors.
- CI registration `14749ed`:
  https://github.com/Badsmc/SMBS_BendSeq/actions/runs/43407959920
  — success for Python compile/unit checks.
- **Real native FreeCAD verification of this new follow-on change: NOT YET RUN.**
  Do not claim an additional measured speedup or production safety.

## Next evidence gate

1. Install this latest development branch (one active FreeCAD Mod), restart
   and log actual loaded module SHA-256.
2. Repeat the same ten-bend 180°/AP1017/AT3015/Conditional 10mm sequence.
   Confirm B02 remains a correctly attributed block on RETRACT and all other
   per-bend statuses / phase evidence hold.
3. Compare the new full-sheet qualification time, number of OCC validity
   calls if instrumented, total wall, CPU/RAM against **log9's
   1321.3356s** as the new one-run comparison baseline.
4. Do not begin parallelizing mutable FreeCAD Part.Shape in threads.
   Use separate worker processes only after exact CAD equivalence is
   established and memory resource limits are measured.
