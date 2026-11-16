# SMBS experiment: staged axis 2D search + 3D preview AABB (2026-11-16)

**Branch:** `experiment/axis-staged-2d-mesh-20261116`  
**Baseline:** `smbs` at `bedd81672d1649a0b1654fc9fb0e317c0539b9c8`  
**Do not merge into `smbs` until FreeCAD acceptance.**

## Goal

Find up to five promising full press-brake bending orders for arbitrary
SheetMetal parts without running expensive native OCC on every DFS branch.

The old `Поиск 2D вариантов`, manual operations, Fast OCC (five angles),
Full D6 and all existing 2.5D/3D views remain unchanged and available.

## Implemented pipeline

1. Extract authoritative bend IDs and flat-axis vectors from `FoldState`,
   group nearly parallel axes with existing `axis_screening`. More than two
   axis groups are supported.
2. Enumerate bounded candidates separately in each axis group, checking
   two workpiece loadings, real selected punch/die 2D profiles, finite bend
   zone, sampled APPROACH/BENDING/BOTTOM/RETRACT and their 2D overlaps.
   One interfering (folded set, active bend, orientation) excludes just
   that *planar hypothesis*, not the bend under all conditions.
3. Cache stage A/B step evidence by completed-bend **set**, active bend
   and loading orientation. The partial folded state is recomputed
   from flat for every transition; operator's original FoldState and
   tooling alignment are restored.
4. Combine the group-local orders with beam-search interleavings.
   The other axis is no longer assumed flat during the full-candidate
   verification: **every combined transition is re-screened in the full
   preceding folded state**. A/B and B/A as well as alternating
   A/B/A/... are considered, subject to the beam and time limits.
5. Apply operation-specific weights
   `C_bend(B_i)` and adjacent transition penalties from the existing
   `operation_cost.transition_penalty(previous_setup, current_setup)`.
   Each interior operation influences both incoming and outgoing
   transition costs. Default per-bend cost uses bend angle and length,
   or the operator can override each bend separately through the
   `Индивидуальные веса гибов…` dialog. Collisions never become legal
   due to a favorable weight.
6. The top 15 complete 2D hypotheses are subjected, if time permits,
   to 3D surface preview AABB broadphase. Real panel top/bottom/edges,
   curved bend-zone strips, and extruded selected punch/die profiles
   are constructed in the same machine reference frame. Three sampled
   bend poses (0%, 50%, 100%) count potentially intersecting face
   boxes. **Overlap is ranking information ONLY**, never proof of
   collision, and absence of overlap is not a continuous clearance
   certificate. If 3D preview cannot be computed, record UNVERIFIED.
7. Return the five best available complete orders, with their per-bend
   loading orientations and explicit 2D/3D preliminary evidence, for
   the existing Manual / Fast OCC / Full D6 process.

## GUI

Open the **Поиск 2D вариантов** task page, after selecting a SheetMetal
unfold and punch/die from the existing library.

- `Эксперимент: оси A/B → 3D BBox → топ-5` runs the isolated mode.
- `Индивидуальные веса гибов…` opens per-bend cost overrides.
- Results appear in the existing complete-sequence selection dialog.
- After selecting a candidate, open **Порядок и проверка** and run
  **Быстрая проверка OCC (5 углов)** followed by **Полная проверка (3D)**,
  including native 0–100% stroke sampling, before any practical use.
- Native 3D is NOT executed automatically for all five candidates yet.
  The manual OCC verifier is still the authoritative next stage.
- Every experiment emits `AXIS_STAGED_EXPERIMENT_COMPLETED` to the
  existing Desktop JSON log, including axis orders, cost-ranked
  finalists, candidate loading orientations and BBox preview risk.

The experiment is synchronous in FreeCAD, capped at **90 seconds soft
budget** in the GUI. A currently running underlying geometric operation
may exceed the deadline. Internal caps are beam width 10, per-axis
candidate limit 5, merged 2D pool 15 and output finalist count 5.
The limits are parameters and not correctness guarantees.

## Critical limitations

- Only one 2D cross section at the active axis is checked with sampled
  punch/die profiles. It can reject an examined *planar setup* but
  cannot prove no other 3D setup works. A potentially usable machine
  loading may be missed because of sampling, beam width or skipped
  interleavings; no solution does **not** mean impossible to bend.
- Stage B uses discrete angle and ram samples, not continuous collision.
- Face AABB is an intentionally conservative preview **broadphase**;
  triangle mesh narrowphase with verified tessellation tolerance is
  not implemented in this first iteration. AABB intersections are not
  used to reject candidates.
- Selected tooling is used for cross-section geometry, but a virtual
  axial tool length, when enabled, is an explicit approximation.
  Clamps, backgauge, frame, tool end relief, punch-die real native
  solids, and inter-operation handling are not fully checked.
- Conditional end zones are operator-given assumptions, not modeled
  physical cutouts or a manufacturing clearance certificate.
- Returned finalists are NOT machine-validated. Native continuous or
  carefully adaptive OCC validation remains outstanding. A five-angle
  test is also not a proof of complete continuous motion.
- Per-bend weight overrides are experimental session settings. They
  appear in the Desktop debug log but are not yet added to the
  persistent FCStd session schema.

## Source and tests

- `smbs_core/kinematics2d/axis_staged_experiment.py`: grouping,
  two-placement 2D transition evaluator, independent beam paths,
  cross-axis interleavings, per-bend and adjacent cost, ranked finalists
- `smbs_core/kinematics2d/axis_staged_broadphase_3d.py`: surface
  AABB risk at sampled machine poses, source tooling preview geometry
- `freecad_smbs/commands/pressbrake_2d.py`: separate experimental
  task page button, individual weights dialog, existing saved orders
- `tests/test_axis_staged_experiment.py`: pure-Python injected
  geometry regressions and BBox classification
- `.github/workflows/axis-staged-experiment-tests.yml`: syntax and
  tests on Python 3.11 for this branch

CI tests are synthetic, not physical FCStd results. The next acceptance
step is to run this branch in FreeCAD 1.1 on the known 10-bend model
and inspect the Desktop debug log for `AXIS_STAGED_EXPERIMENT_COMPLETED`.
Check which axis-local orders survived, how many mixed-axis full
candidates appeared, whether the 3D preview returned real BBox data
rather than UNVERIFIED, and how much time each phase required.

## Next engineering steps (not yet implemented)

1. Real triangle-mesh BVH/narrowphase in machine frame, with
   validated mesh tolerance, proper holes and solid interiors.
2. Automatic bounded queue of up to five finalist native OCC replays,
   with collision-driven backtracking rather than pretending five
   unsuccessful candidates exhaust the search space.
3. Full continuous or adaptive swept-phase checking and independent
   machine/fixture qualification.
4. Matched benchmark vs. the unchanged `smbs` 2D + native workflow.
