## Update: time-budget fallback and toolbar navigation (2026-11-17)

Branch: `experiment/axis-budget-fallback-menu-20261117`.

- Axis-local search budget: first **30%** of the 2D search time.
  Cross-axis merge: up to **80%** total. Final **20%** reserved for
  sampled AABB preview ranking (all are soft budgets).
- The **Автопоиск A/B + OCC** UI now allocates up to **90 seconds**
  per staged candidate generation instead of 45. Five native OCC
  attempts and the 600-second soft overall limit remain.
- If bounded interleaving cannot produce a complete candidate, the
  algorithm constructs a limited number of full **provisional** orders
  from the original order, axis-major orders and A/B interleavings.
  They are labeled **UNVERIFIED_2D** and go ONLY to independent native
  OCC. No unsampled transition is reported as collision-free.
  Known exact native blockers and cached proven 2D obstacles are
  respected; the fallback is not proof that a collision-free order exists.
- Top FreeCAD toolbar commands **Поиск 2D вариантов** and
  **Автопоиск последовательности** open the search task page WITHOUT
  executing a search. Explicit buttons inside the page start
  the legacy 2D search, the original full D6 search, staged A/B preview,
  and staged A/B+OCC independently.
- Added synthetic regression tests for provisional candidates, exclusion
  of exact blockers, and toolbar command side effects.

**Acceptance in FreeCAD is still pending**: confirm first native OCC
attempt is reached with the actual ten-bend FCStd sample, that a native
collision queues a 180° opposite loading, and that no unresolved
or conditional result is marked physically approved.

# Axis-staged search: native collision feedback (experimental)

Branch: `experiment/axis-2d-collision-feedback-20261116`.
Base: `experiment/axis-staged-2d-mesh-20261116`.
Do not merge into `smbs` until real FreeCAD acceptance.

## Update: 180-degree loading is checked before any new order (2026-11-17)

This experimental branch is `experiment/native-flip-first-20261117`,
based on `experiment/axis-2d-collision-feedback-20261116`.

When native OCC confirms a collision at a bend (for example B01 against
P11), the FreeCAD **Автопоиск A/B + OCC** action now:

1. Records the exact native rejection with completed-bend set, bend ID
   and machine loading.
2. Queues the **same ten-bend order**, with only the blocked bend's
   `reverse_axis` switched to its opposite (180-degree machine loading).
   All earlier and later bend loading choices are left unchanged.
3. On the *next native attempt*, runs `check_manual_3d_sequence` directly
   with this orientation. An `UNVERIFIED_2D` warning cannot deprioritize
   this OCC retry; no 2D approval is assumed.
4. If the opposite loading also collides, records that exact blocked
   orientation and resumes normal staged 2D candidate generation.
5. Stops with an explicit UNVERIFIED outcome on unresolved native
   evidence; a sampled OCC clear remains
   `SAMPLED_3D_CLEAR_NOT_CONTINUOUS`, never continuous certification.

New Desktop events:
`AXIS_NATIVE_FEEDBACK_FLIP_QUEUED` and
`AXIS_NATIVE_FEEDBACK_FLIP_TRIAL_STARTED`, plus
`trial_kind=OPPOSITE_LOADING_AFTER_NATIVE_COLLISION` in
`AXIS_NATIVE_FEEDBACK_OCC_ATTEMPT`.

The native feedback run respects the selected virtual-tool-length
setting for both 2D planning and OCC. It allows at most five native
attempts and a 600-second **soft** budget; a blocking OCC operation
may exceed the deadline. Existing manual buttons and stable `smbs`
branch remain unchanged.

**Unverified until real FreeCAD acceptance:** 3D result of the flipped
B01 pose, real transfer/backgauge/clamp/frame feasibility, and
continuous motion clearance. Synthetic unit tests alone cannot prove a
physical bending sequence.

## Implemented

- `axis_staged_experiment.search_axis_staged_experiment(..., blocked_transitions=...)`
  accepts a dictionary keyed by `(frozenset(completed_bend_ids), bend_id, reverse_axis_bool)`.
- Every exact blocked transition is rejected as `CAD_3D_INTERFERENCE`
  **before** running the sampled 2D transition screen.
- `native_collision_feedback.NativeCollisionFeedback` validates the
  full candidate order, failure index, per-bend orientation and evidence,
  and derives the exact key. `add_native_blocker` accumulates blockers
  without mutating the previous map.
- `tests/test_axis_staged_experiment.py` tests targeted blocking,
  alternatives and rejection of uncertain AABB evidence.

## Example integration with the actual OCC checker

After native replay confirms a collision on a selected candidate,
construct `NativeCollisionFeedback(sequence=tuple(sequence),
failing_index=the_zero_based_step_index,
orientation_by_bend=orientations, evidence="CAD_3D_INTERFERENCE",
reason=diagnostic_reason)`. Pass
`add_native_blocker(previous_blockers, feedback)` to the next
`search_axis_staged_experiment(..., blocked_transitions=...)` call.
Preserve the active punch/die and tool settings. **Discard blockers if
the part, tooling, tolerances, virtual lengths or collision policy changes.**

## NOT implemented / acceptance blockers

- `search_with_native_feedback(planner, native_verifier, max_attempts=5)` now provides a bounded retry loop with exact-blocker feedback, duplicate-candidate prevention and explicit UNVERIFIED status. The caller must supply a genuine native checker; this helper makes no geometric safety claims on its own.
- The GUI now provides a separate **«Автопоиск A/B + OCC (до 5 попыток)»**
  action under **«Поиск 2D вариантов»**. It runs bounded staged search
  and the existing independent native `check_manual_3d_sequence`, extracts
  the exact collision step and replans with `blocked_transitions`. It writes
  `AXIS_NATIVE_FEEDBACK_SEARCH_ROUND`, `AXIS_NATIVE_FEEDBACK_OCC_ATTEMPT`,
  `AXIS_NATIVE_FEEDBACK_BLOCKER_ADDED`, and
  `AXIS_NATIVE_FEEDBACK_COMPLETED` to the Desktop JSON log.
- The action is synchronous and has a **600-second soft deadline**.
  A single native OCC call may exceed this deadline; it does not provide
  a hard wall-clock cap. Native phase caps (45 seconds / 32 states)
  may cause UNVERIFIED rather than a false PASS.
- A reported `SAMPLED_3D_CLEAR_NOT_CONTINUOUS` is a sampled punch/die
  clearance only; it is **not** continuous-motion or whole-machine approval.
- Native end-zone conditional contacts, 3D setup alternatives beyond the
  two existing 2D loadings, triangle-mesh narrowphase and continuous
  swept collision are not certified or completed here.
- Native OCC feedback must originate from a confirmed collision, not
  just a 2D sample, BBox risk, timeout or unresolved state.
- CI synthetic regressions do not replace FreeCAD 1.1 physical testing.

## Required next steps

1. Validate the new FreeCAD button against the real ten-bend FCStd,
   including loading flips, and inspect all returned JSON events.
2. Add durable geometry/tool/config fingerprints if blockers are ever
   persisted outside one invocation. Current blockers are invocation-local.
3. Validate on the known 10-bend FCStd example, including both 180-degree
   orientations of B10 and actual contact of punch against panel P05.
4. Profile mesh BVH narrowphase against OCC; never use AABB intersections
   alone to reject a candidate.
