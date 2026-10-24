# TEST MATRIX — HEAD 917eb71 (2026-10-24, ACTIVE)

> **Status:** ACTIVE — reflects 118 test files in `tests/test_*.py`
> **Baseline:** 578 tests / 0 failures / 0 errors / 3 expected failures (143.8s)
> **Critical Path Coverage:** Complete (0 `NOT RUN` in critical paths)
> **Scope:** stage-1-fix-correctness pipeline. See §Historical Archive below for 2026-10-11 pre-refactor matrix.

## Active Test Matrix by Subsystem (118 files, 578 tests)

| Subsystem | Files | Critical Path Coverage | Status |
| :--- | :--- | :--- | :--- |
| **Geometry & Unfold** | 18 | `smbs_core/workers/geometry/` (unfold exceptions, fold primitives, face resolver, mesh generation) | **PASS** (100%) |
| **Topology & Feature Extraction** | 8 | `smbs_core/workers/topology/` (origin/axis guards, bend direction, graph extraction, profile validation) | **PASS** (100%) |
| **Baseline & Reconstruction** | 4 | `smbs_core/workers/baseline/` (strict Hausdorff $h=0.0000$ mm, 10 canonical fixtures, vertex alignment) | **PASS** (100%) |
| **Solvers & Search** | 9 | `smbs_core/workers/search/` (DFS depth limit, A* heuristics, collision caching, budget exhaustion) | **PASS** (100%) |
| **Verifier & Honest Validation** | 7 | `smbs_core/workers/verifier/` (`valid=False` on `UNKNOWN`, simulation, multi-stage collision) | **PASS** (100%, 2xF) |
| **Tooling, Clamping & Kinematics** | 19 | `smbs_core/physics/`, `smbs_core/workers/tooling/` (datum transforms, clearance, backgauge, machine models) | **PASS** (100%) |
| **Coordinator & Contracts** | 6 | `smbs_core/coordinator/`, `smbs_core/contracts/` (schema validation, pipeline dispatch, error payloads) | **PASS** (100%) |
| **Viewer & CLI** | 9 | `smbs_core/viewer/`, `smbs_core/cli/` (export, visualization, CLI exit codes) | **PASS** (100%) |
| **Pipeline & Historical Regression** | 38 | End-to-end integration, phase progression, edge cases, regression pins | **PASS** (100%, 1xF) |
| **Total** | **118** | **578 active tests (587 methods discovered, 9 non-discovered test methods)** | **PASS (3xF)** |

---

# Historical Archive — Test Matrix (pre-refactor, 2026-10-11, base commit d63eac2)

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
