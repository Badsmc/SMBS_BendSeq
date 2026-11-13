# SMBS debug log (10): repeated native performance evidence

Date: 2026-11-13. Input: user-supplied **Desktop developer journal**, NOT
committed to the repository. Numbers below are extracted from the journal;
not an independent FreeCAD execution on this GitHub branch.

## Four complete 10-bend manual native runs

Same recorded manual order B07 B03 B05 B01 B08 B04 B09 B10 B06 B02,
180-degree operator setup, AP1017 punch / AT3015 V12 die, virtual tool length
and 10mm Conditional option. All four checked all ten bends. This document
does not prove identical FCStd/tool binary SHA-256 between sessions.

| Run | Runtime source fingerprint (prefix) | Wall seconds | Full-sheet unions | Native Part.common | distToShape | Result |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| 1 | `e1257326` | 1321.3356 | 297 | 627 | 548 | B02 RETRACT PUNCH COLLISION |
| 2 | `d8ba894b` | 1185.8569 | 297 | 627 | 548 | same statuses |
| 3 | `d8ba894b` | 1182.6764 | 297 | 627 | 548 | same statuses |
| 4 | `079d4eb3` | 1180.5304 | 297 | 627 | 548 | same statuses |

All four reported B08/B04/B06 CONDITIONAL and B02 COLLISION, while
B07/B03/B05/B01/B09/B10 were sampled-clear (NOT continuously certified).
B02's significant central overlap must NOT be waived by Conditional.

Measured difference run 1 -> run 4: **140.8052 s / 10.66%** wall reduction.
The differing installed-source fingerprints and unavailable source model/tool
file hashes mean this is an observed result, NOT a matched performance claim
for the `experiment/component-collision-oracle` branch.

## Last run: measured categories, seconds

- **347.9817s** `pair_geometry_sanity`, **1322** validation calls.
- **180.4669s** exact `distToShape`, **548** queries.
- **153.4613s** `Part.common`, **627** queries.
- **116.7888s** `sheet_qualification`, **297** runs.
- **87.6076s** `sheet_union`, **297** runs.
- **82.6134s** `bend_zone_build`, **2970** requests.
- **17.1351s** `machine_workpiece_copy`, **363** calls.
- **6.9711s** `panel_transform`, **3267** calls.
- Native punch pose / workpiece transform / panel extrude: under 1s total.
- **186.9s** not covered by these nonoverlapping named timers.

The largest identified cost is repeated full-shape validity/closure checking
in `check_pair_clearance`; the same machine-frame workpiece is checked again
for punch and die when both queries are uncached. A carefully scoped shared
validity result for the identical object is now prototyped on this child
branch, commit `5d65c1a`. It continues performing strict per-pose
workpiece validation and independently validates each tool shape; it does
not cache across poses or retain a verdict after shape mutation. It is
HEADLESS_TESTED only; FreeCAD safety and speed effects remain **NOT MEASURED**.

## Next acceptance

1. Install current experiment source in FreeCAD with exactly one active Mod
   root, restart application and record `SMBS_RUNTIME_READY` composite SHA.
2. With the same FCStd and tool files/settings, run the manual native D6
   comparison. Require equal B02 RETRACT and unchanged first evidence,
   B08/B04/B06 Conditional; no false CLEAR.
3. Report `pair_geometry_sanity` calls and wall time,
   `sheet_union`/qualification count, exact calls, total wall, peak RAM.
4. If per-pose shared validation does not maintain original results in real
   FreeCAD, revert that change and document failure before any production use.
5. Only after native acceptance, wire the component screen to the authoritatively
   transformed machine-frame solids, with fallback for uncertain or
   Conditional collisions. Current branch still performs 297 full unions.
