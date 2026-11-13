# Experiment: exact component collision screen (X1 building block)

Branch: `experiment/component-collision-oracle`, forked from
`development/performance-optimization` at `7de4f51d` (2026-11-13).

## Implemented

- `component_collision_oracle.py` implements conservative broadphase for
  **native OCC BRep components already transformed into machine coordinates**.
- Positive exact Common volume strictly greater than the **existing** native
  volume tolerance produces `PROVEN_PENETRATION` with component/tool evidence.
- Every other result (AABB-disjoint, small common, kernel failure, incomplete
  data) is `FALLBACK_REQUIRED`; never `CLEAR`, `CONDITIONAL` or safe.
- Headless mock tests cover AABB, positive overlap, multiple small overlaps,
  invalid tolerance, repeated IDs and OCC exception.

## Not yet implemented

The legacy production full-sheet trajectory remains **unchanged**. Component
screening is **not yet wired into** `create_state_snapshot`; doing so needs
a single authoritative component builder, machine-frame transform and
exact result semantics including conditional punch ends, die, sample provenance,
first blocker and topology validation.

The current module is an **experimental primitive**, not an accelerated
production collision checker. It does not yet reduce 297 multiFuse operations.
No live FreeCAD benchmark, end-to-end speedup or numerical safety qualification
has been performed. Retain existing full-sheet native oracle for all user results.

## Next integration gate

1. Refactor `build_multi_bend_sheet_solid` into authoritative component
   construction and optional full union without changing geometry or cache keys.
2. Validate exact component source/placement coverage (11 panels + 10 zones)
   and independent full-sheet equivalence on reference FCStd and edge cases.
3. Wire fast proof to a *gated* production collision branch only when
   end-zone Conditional policy and complete result semantics are preserved;
   otherwise fall through to existing full solid.
4. Rerun unchanged manual sequence with AP1017/AT3015, 180 degrees,
   Conditional 10 mm; ensure B02 RETRACT remains real COLLISION.
5. Compare exact SHA-confirmed installed FreeCAD runtime, wall time, Common,
   distance, joined-sheet count and peak RAM; no speedup claim before this.
