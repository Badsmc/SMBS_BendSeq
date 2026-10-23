# HISTORICAL — Test Matrix (pre-refactor, 2026-10-11, base commit d63eac2)
#
# Audited body date: 2026-10-11, pre-refactor legacy suite (SMBS baseline 7a0d87b)
# Deprecated at commit: 289daa3 (2026-10-17)
# Does NOT describe current HEAD: 91d07c0 (stage-1-fix-correctness)
# Current test count: 550 (see docs/PROGRESS.md §Test Metrics)
# Current release decision: NOT READY
#   → see docs/AUDIT_FULL_2026-10-23.md §Release Verdict
#
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

---

## Current Status (annotated 2026-10-23)

This file is a HISTORICAL snapshot. The ">70 (74 run typically)" estimate
above refers to the pre-refactor legacy suite inherited from SMBS baseline
commit `7a0d87b`. It does not enumerate current test suites.

It does NOT describe:
- Current HEAD: 91d07c0 (stage-1-fix-correctness), baseline 550 tests.
- Current test suites (test_kl12_v2_side_edges.py, test_req2_f1_per_panel_kinematics.py, etc.).
- The line-41 6-skip/error note was observed in a legacy CLI environment; current FreeCAD-python runs may behave differently.
- Open P0 code defects: P0-CODE-1..5.
- Open P0 documentation defects: P0-DOC-1..4.

**Current release decision: NOT READY.**
Evidence and open blockers: `docs/AUDIT_FULL_2026-10-23.md` §Release Verdict.
