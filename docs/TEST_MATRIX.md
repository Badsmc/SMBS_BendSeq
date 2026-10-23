> **DEPRECATED (2026-10-17).** This file predates the Stage 1/2/3 refactor.
> Canonical progress and test metrics: `docs/PROGRESS.md` §Test Metrics.
> Kept for historical reference only. Do not update.

---

# SMBS / BendSeq - Test Matrix

The test suite validates components using isolated mathematical checks and mocked FreeCAD objects, allowing fast CLI execution (`run_tests.py`).

Total tests: >70 (74 run typically).

## 1. Geometry & Transformation
- `test_stage2_rotate_subtree.py`: Validates Rodrigues' rotation of subsets.
- `test_phase1_geometry.py`: Validates coordinate calculations and topological graph construction.

## 2. Machine & Tooling Physics
- `test_phase2_kinematics.py`: Validates tool profile construction (Die/Punch V-Widths, tonnage).
- `test_stage7_kinematics.py`: Validates proper tool bounding box overlaps.
- `test_stage14_backgauge.py`: Validates backgauge contact point transformations.

## 3. Collision Detection
- `test_stage6_self_collision.py`: Validates self-intersections of moving panels.
- `test_stage8_tool_collision.py`: Validates punch/die intersections.
- `test_stage9_machine_collision.py`: Validates machine bed overlaps.

## 4. State & Caching
- `test_phase3_fold_state.py`: Validates caching hashes and dynamic axis tracking (directional updates).
- `test_stage3_foldstate.py`: Regression tests for state-space explosion prevention.

## 5. Solvers (DFS, A*)
- `test_phase6_dfs.py`: Complete traversal check using Depth-First.
- `test_stage18_astar.py`: A* heuristic accuracy.
- `test_stage19_astar_optimized.py`: Ensures A* naturally prefers "Center-First" sequences.

## 6. Output & Integration
- `test_stage3_bend_matcher.py`: Validates `AMBIGUOUS` flat-pattern matching logic.
- `test_stage15_tool_optimizer.py`: Validates tool station assignments and flips.
- `test_stage16_simulation.py`: Validates kinematic playback.

*Note: 6 tests natively attempt to import FreeCAD components intended for GUI execution, resulting in expected skips/errors during automated CLI execution.*
