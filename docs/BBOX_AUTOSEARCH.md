# Experimental automatic sequence search

Branch: `experiment/bbox-autosearch`, based on `smbs` at eb5b7b7.

A separate top toolbar **PressBrakeSim — эксперимент → Автопоиск BBox** opens a modal results window. Load the part through the existing simulation, select tooling, stop playback and Reset, then open the search. The manual menus remain unchanged. Search never applies a candidate until **Открыть в симуляции** is pressed. Closing the results window discards its transient results. Search parameters, candidate reports and errors enter the existing debug journal.

The bounded cooperative DFS explores operation order and independent 0/180 loading poses. It keeps each physical bend's partial/finish precedence, retains individual operation IDs and angles, and scores candidates with the existing OperationWeights. It is not a global optimality proof. Stop and time/node/result limits retain already found results. OCC calls cannot be interrupted while the native kernel is executing; the time budget is checked between oracle calls and can overrun by one call.

Ordinary bends use the existing isolated manual native trajectory validator: coarse screening for prefixes and complete candidates; adaptive sampled validation is run only by the separate selected-result verification button. It already uses native AABB clearance pruning before exact OCC overlap/distance evaluation. An AABB intersection is never treated as a proven collision. Explicit native collisions prune a path; unknowns remain visibly unverified. Selected actual punch/die, end-zone policy, virtual length and current real segment assembly are forwarded. For an existing segment assembly the common assembly is recalculated with the existing stock/clamp support planner.

## Current physical scope and remaining work

This branch is an initial integration of the existing collision engine, not the requested complete machine-wide collision oracle. No new per-panel/per-clamp spatial index has been implemented. Backgauge, virtual clamps, frame and transfers retain their existing simulation and settings, but their collisions are **not validated by this search**. Staged bends are enumerated with precedence and independent poses but remain `UNVERIFIED_STAGES`: the current native validator only supports unique single-pass physical bends. Their partial-angle OCC validation must be implemented before they can be physically accepted. A candidate status is therefore not manufacturing approval. Full mesh/component BBox acceleration and intermediate stage collision validation remain necessary follow-up work.

## Manual verification

1. Confirm the extra experimental toolbar is visible and manual tools still work.
2. Search a small known part with actual selected FCStd tools; verify 0/180 results are distinct.
3. Cancel search and verify no order or poses changed in the simulator.
4. Apply a result; compare table numbering, unfold labels, selected tooling and Coin3D motion.
5. For staged parts, verify predecessor constraints and independent flips are retained and status remains unverified.
6. Close/reopen the simulator and save/reopen FCStd using existing persistence.

Automated tests cover backtracking, independent orientations, stage precedence, collision pruning, unknown evidence, cancellation, budgets and final-oracle dispatch. Real FreeCAD Windows validation is still required.

Performance update: lazy DFS descends immediately instead of evaluating every sibling first. Automatic search skips expensive per-collision component attribution; explicit selected-result verification retains it. Previously checked prefixes are still replayed by the legacy validator, so this is not yet a pure in-memory BBox search and native OCC calls can remain slow.
