# D6 audit: reusable sequence-planning architecture

Baseline `2D@bc2f94c8f5183af30d8d359c311dc20baabd6d91`.
Working branch `d6-physical-sequence-search`.
Reviewed 2026-11-05.

## What already exists, and what it really guarantees

| Existing module | Reuse decision | Caveat |
| --- | --- | --- |
| `smbs_core/Solvers/dfs_backward.py` | Keep as B-Rep backward reference DFS; reuse search concepts and later via explicit state adapter | Requires `Core.FoldState`, `BendGraph`, `PhysicalValidator`, backend shapes; cannot accept 2D FoldState |
| `smbs_core/Solvers/astar_backward.py` | Keep for optional A* once validation is trustworthy | Same Core/B-Rep contract |
| `smbs_core/Solvers/greedy_unfold.py` | Candidate ordering only; not proof | Greedy failure does not mean physically impossible |
| `smbs_core/Core/orchestrator.py` | Existing headless B-Rep orchestrator, eventually integrate via adapter | Imports pythonocc; unsuitable for current pure-Python 2D CI |
| `smbs_core/Physics/validator.py` | 3D oracle contract: approach, self, tool, machine, trajectory, retract | UNKNOWN is rejected under PRODUCTION; some geometric scopes may return UNKNOWN |
| `smbs_core/Physics/collision_detector.py` | Keep exact backend-based 3D collision checks | Planar panel geometry/self collision returns UNKNOWN, not safe |
| `smbs_core/kinematics2d/panel_graph.py` | Authoritative rooted topology and stable bend IDs | Graph traversal order is not manufacturing order |
| `smbs_core/kinematics2d/panel_kinematics.py` | Reuse as authoritative GUI FoldState and transformations | Kinematics alone does not validate machine feasibility |
| `smbs_core/kinematics2d/fold_sequence.py` | Reuse `set_bend_order()` after full verification | Current `Play All` can animate arbitrary/unverified order |
| `smbs_core/kinematics2d/workpiece_placement_3d.py` | Candidate machine-space positioning | Proper isometry does not guarantee die support or backgauge access |
| `smbs_core/kinematics2d/workpiece_transfer_3d.py` | Continuous candidate transfer | Continuous does not imply collision-free |
| `smbs_core/kinematics2d/contact_oracle_2d.py` | Cheap material-overlap prefilter | Cross section alone cannot prove all 3D obstacles clear |
| `smbs_core/kinematics2d/machine_section_3d.py` | Build section from FoldState & machine-space placement | Cross-section fidelity still under D5 review; no full volume proof |
| `smbs_core/kinematics2d/trace_analyzer.py` | Diagnostic evidence and regression | PASS for motion does not certify a feasible sequence |

## Concrete compatibility issue

There are **two different FoldState types**:
1. `smbs_core.Core.fold_state.FoldState`: B-Rep shape, remaining bends, tooling state, geometry-backend transitions, fingerprint.
2. `smbs_core.kinematics2d.panel_kinematics.FoldState`: rooted panel graph, angles, rigid transforms, zones.

Do **not** pass one into the other's solver or clone them into a third authoritative model.
D6 starts with a forward DFS for the 2.5D state, sharing the production requirement for fail-closed physical oracle decisions. Once the physical validator adapter exists, reconcile with existing B-Rep backwards search and compare on identical reference cases.

## Immediate completed work

`smbs_core/kinematics2d/sequence_search.py`:
- Deterministic DFS/backtracking and dead state memoization.
- Frozen sets of completed bend IDs, preserving original FoldState unchanged.
- State key includes previous bend ID to avoid aliasing distinct transfer transitions.
- Explicit resource limits and rejection reasons.
- No dynamic symmetry pruning that could exclude a valid order.
- `StepOracle` dependency with explicit verification scopes.
- No approval on missing evidence.
- `apply_verified_order()` reuses existing BendSequenceController API without starting animation.

## Critical work NOT completed yet

1. Implement a **real** StepOracle that evaluates each candidate installation, available punch/die selection, approach, full signed bend motion, retract, transfer, support, self/tool/machine interference.
2. It must evaluate 3D solid geometry (or label unverified where a 2D projection cannot prove clearance); no assumed tool geometry.
3. Candidate placement orientation and backgauge accessibility must be enumerated, not inferred from bend angle sign alone.
4. Integrate step evidence cache with actual tooling geometry revision, datums, state and previous bend.
5. Add an explicit user-facing `Validate/Find Sequence` action and separate demonstration playback from validated playback.
6. Forward-verify any sequence proposed by legacy backward DFS against the same oracle.
7. Validate on the supplied B01-B08 part, with at least one demonstrably impossible sequence and one demonstrably feasible sequence.

This branch has only the safe search engine and contract tests. No production-ready physically feasible order is asserted.
