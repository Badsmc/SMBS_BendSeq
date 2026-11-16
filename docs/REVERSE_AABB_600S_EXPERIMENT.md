# Experiment: Reverse AABB unbending search (600 s)

This branch is isolated from `main`, `smbs`, and the earlier fast-feasible
search. Do not merge until the FreeCAD regression fixture has been tested.

## Current implementation

1. Load SheetMetal-derived panel graph and original FreeCAD panel-face outlines.
2. Use the target signed bend angles as the fully formed starting state.
3. DFS over inverse bend-removal moves (reverse unbending).
4. Build 3D AABBs for each rigid panel with the existing FoldState transformations
   at intermediate unbend fractions (0.75, 0.50, 0.25, 0).
5. Rank moves by nonadjacent panel AABB overlap; cache states and transitions.
6. Return candidate *forward* orders by reversing the unbend path.
7. Verify candidates independently using the existing native D6 forward replay
   against the selected punch/die. Virtual-length mode remains explicitly
   non-physical; the forward replay does not certify loading, backgauges,
   clamps, frame, transfers or continuous trajectories.
8. If reverse geometry is unavailable or no full candidate is found, fall back
   to the existing 2D search.

## Important limits

**The reverse search currently uses panel-panel AABBs as a ranking heuristic.**
It does **not** yet implement swept machine-space BBox collision against
punch/die, machine frame, or backgauge. AABB *overlap* is never treated as
proof of collision; AABB *separation* is meaningful only for the specific
sampled pair and instant, not as continuous clearance.

Every reverse order has evidence
`REVERSE_AABB_RANKED_UNVERIFIED` until the independent native D6
replay succeeds. Its default orientation is a candidate, not a validated
mounting direction. Search does not alter the operator's FoldState.

## Budget policy

Experiment GUI: 600 seconds, best effort (a running OCC operation may exceed
the wall clock deadline). Reverse candidate generation: at most 90 seconds.
When reverse candidates exist, skip the costly planar DFS and spend remaining
time on the forward D6 validator. Otherwise use up to 150 seconds for existing
planar DFS and reserve remaining time for 3D validation.

## Check before merging

- Run `python -m unittest tests.test_reverse_aabb_search` outside FreeCAD.
- In FreeCAD 1.1, test the 10-bend fixture with selected FCStd tooling and
  virtual tool length enabled; collect the full SMBS debug JSON.
- Confirm that `reverse_search.geometry_available` is true and
  `reverse_search.candidates` is nonempty in the finish event.
- Confirm *full* forward physical replay, orientation, and geometry
  before treating any candidate as sampled collision-clear.
- Compare elapsed and exact outcome against the prior 60-second 2D approach.

## Next development milestone

Add actual moving punch/die tool AABB and OBB broadphase in machine coordinates,
followed by swept-path conservative screening, pose/turn alternatives, and
selective exact OCC narrowphase. Only safe-proven exclusions may prune;
ambiguous contacts must remain UNKNOWN or defer to D6.

## Results from 2026-11-16 Windows debug log

Two 600-second test invocations took 104.21 and 103.35 seconds. AABB DFS
explored 1024 states / 14800 transitions in about 14 seconds and produced
three full permutations with overlap scores of zero. Each 3D candidate
stopped at its first bend (B09, B10, or B01): BENDING intervals exceeded
the old 6-second phase limit; one bounded retry with a ~9-second cap
also exhausted. Recorded sampled punch overlaps were <= 0.000098 mm³;
these are **not proof of a blocker**. No sequence was physically validated.

Follow-up changes on this experimental branch:
- Raise D6 per-phase allowance to 24 seconds and timeout retry up to 40s
  while respecting the overall deadline and preserving UNVERIFIED.
- Diversify reverse search by first forward bend and root-unbend path,
  rather than testing multiple almost identical first operations.
- Exclude unnecessary fallback operator seed when reverse candidates exist.
- Label simulator preview as AABB/DFS UNVERIFIED (never 2D-cleared).
- Continue to treat zero AABB-overlap score as a weak ranking result, not
  evidence of actual 3D machine clearance.

The new tuning requires physical FreeCAD tests; elapsed performance is
not established by static code changes.
