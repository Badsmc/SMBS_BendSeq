# Axis-staged 2D sequence search — experimental implementation

Repository branch: experiment/axis-staged-2d-search. This branch was created from smbs. The original installable branch and archived fast-search experiments are unchanged.

## Algorithm

1. SheetMetal FoldState supplies bend IDs, signed target angles, panel geometry and bend axes. Existing axis_screening groups parallel bends within one degree. The number of groups is unrestricted.
2. Within each axis group, bounded beam search explores bend order and both machine loading orientations (normal and 180-degree reverse). Each transition uses the existing Stage A / Stage B contact pipeline and real selected 2D punch/die cross sections. A verified planar overlap rejects only that particular state, bend and orientation. UNVERIFIED results never count as clearance.
3. The independent local results are then combined by preserving each selected local order while exploring cross-axis interleavings. A1-B1-A2-B2 can be tried, not just A1-A2-B1-B2. **Every merged transition is screened again against the entire FoldState with all previous bends from both axes.** Complete results also receive a full-density 17-angle and 9-stroke-fraction sampled 2D replay.
4. Each bend has an optional intrinsic cost AND an individual multiplier on the actual transition cost into that bend (default 1.0). A fixed intrinsic cost alone is constant across complete permutations; the multiplier lets a particular bend's handling difficulty change the ranking. Each directed pair (previous, next) has an independent extra cost. The existing OperationWeights model adds machine rotation, flip, shifts, tool length and other changeovers; axis switching can have a further user-defined cost. When a new bend is appended, its transition cost includes the previous setup. The eventual next bend adds its own transition; no look-ahead is mistaken for measured clearance. The JSON includes the full cost configuration and a per-operation breakdown.
5. A conservative machine-space 3D BBox proxy transforms panel vertices and extruded selected punch/die section bounds at 0%, 50% and 100% states. It reports overlapping AABB pairs only as a soft ranking metric. This proxy omits curved bend zones and the exact press path and therefore can never prove a collision OR certify clearance. It does not reject candidate orders. A mesh/BVH narrowphase using authoritative 3D solids is explicitly **not yet implemented**.
6. Up to five ranked complete candidates can enter existing native adaptive D6 OCC verification when the user enables the slow option in the GUI. The sequence and exact orientation of each bend are preserved. A native proven COLLISION removes only that ordered-and-oriented candidate from the selectable list. Unknown or partially sampled results are never promoted to PASS.

## GUI / data persistence

In FreeCAD, open the Search Sequence Tasks section. The original toolbar 2D search is still available. New experimental actions:

- Experimental: axis-staged search (2D to 3D).
- Individual bend and transition weights: a per-bend table plus directed predecessor-to-successor cost matrix.
- Check up to five finalists with full adaptive 3D OCC (slow): unchecked by default to avoid unexpectedly expensive computation.

Each run writes Desktop SMBS_axis_staged_search_YYYYMMDD-HHMMSS.json with separate axis plans, global merged candidates, BBox results, OCC verdicts, limits and warnings, and emits the same result to the SMBS developer log.

Default search limits: 180 seconds for the 2D portion (soft), 800 unique section transitions, beam widths 8 (axis) and 24 (merge), 16 axis-plan combinations, up to 24 complete 2D results and five finalists. The best result is the best among **evaluated** candidates; it is not proven globally optimal.

## Correctness contract

- The existing SheetMetal FoldState and profile geometry remain the source of truth; tools are not silently shortened. A virtual matching length is only a modeling assumption.
- FoldState angles and panel transforms are restored after each attempt and after the search.
- 2D sections are sampled and cannot prove full 3D clearance. A 2D punch-only overlap with explicitly enabled CONDITIONAL is retained as UNVERIFIED for eventual 3D classification, never automatically accepted. Die/mixed collisions remain planar-model blockers.
- The present BBox preview is a coarse proxy, not full-shape 3D penetration or mesh testing. Do not treat its nonoverlap as proof.
- Final native D6 is adaptive but not a mathematical guarantee of continuous collision-free tool/workpiece motion. Frame, clamps, real backgauge transfer, machine configuration and operator support are not fully certified.
- Existing smbs branch commands are unaffected.

## Tests

The branch has a dedicated GitHub Actions workflow for Python syntax checks and pure-Python regressions. It tests individual/pair costs, cross-axis global rescreening, reverse-orientation alternatives, BBox classification, and that native collision never becomes a selectable approved order.

Live FreeCAD/OCCT testing remains necessary on the ten-bend reference part. Acceptance should check the complete Desktop JSON, actual pose consistency, collision blocker IDs, 2D and native durations, and whether five finalists are physically promising before considering any integration into main or smbs.

Next development task, if benchmarks warrant it: add a genuine collision-capable 3D narrowphase over native panel/bend-zone/tool solids (BVH mesh or conservative component-level OCC), not a BBox-overlap rejector.
