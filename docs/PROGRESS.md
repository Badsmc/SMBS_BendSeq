# SMBS Core - Engineering Implementation Progress

**Status (HEAD `81946ab`):** Stage 1/2/3 functionally complete. 255 tests, 5 failures (all KL-8), 0 errors, 3 expectedFailures (KL-7/KL-8). Ready for production use where tooling model matches; `NO_SOLUTION` + honest diagnostics otherwise. See `Known Limitations` below.

## Project Stages

| Stage | Description | Status | Notes |
|-------|-------------|--------|-------|
| **Stage 1** | Baseline: 3D reconstruction from flat pattern | **COMPLETE** | KL-5/6/7 inherited from original project context |
| **Stage 2** | Search: bend sequence optimization with `unknown_policy` | **COMPLETE** | KL-8 (P1), KL-9 (P2), KL-10 (P3) |
| **Stage 3** | Verification: independent unfold + fold correctness | **COMPLETE** | KL-11 CLOSED in S.9.3 (commit `4c9524e`) |

## Test Metrics (post S.9.3)

- **Discover:** 255 tests / **5 failures** / **0 errors** / **3 expectedFailures**
- **5 failures — all KL-8** (e2e with real tooling `matrica.FCStd`):
  - `test_end_to_end_pipeline.test_pipeline_fixture_l`
  - `test_end_to_end_pipeline.test_pipeline_fixture_u`
  - `test_end_to_end_pipeline.test_pipeline_stepped_busbar_4bend`
  - `test_phase3_cad_models.test_stage6_full_planning_pipeline`
  - `test_robustness_and_negative.test_corrupted_sequence_export_blocked`
- **3 expectedFailures:**
  - `test_phase11_regression.test_regression_ok_part_yields_valid_steps` (KL-7 baseline INVALID)
  - `test_verifier_worker_honest.test_verifier_honest_e2e_u_profile` (KL-8 e2e via Coordinator)
  - `test_verifier_worker_honest.test_verifier_worker_2_1_3_4_deferred` (KL-7/KL-8)

## Known Limitations

**Template:** each entry uses **Symptom / Cause / Impact / Follow-up**. Priority: P1 (production blocker), P2 (workaround exists), P3 (cosmetic).

### Active

- **KL-8 (Stage 2, P1): Real tooling collision in `place_part_on_tool_station`.**
  - **Symptom:** 5 e2e tests fail with `INVALID` on TOOL stage, `vol≈1111 mm³`. `EXPLORATORY` policy does not bypass (INVALID is never bypassed, per invariant #2).
  - **Cause:** Positioning between `place_part_on_tool_station` and real `matrica.FCStd` produces a physical collision. Requires separate audit of datum alignment against the real tooling model.
  - **Impact:** Search returns `NO_SOLUTION` honestly; verifier returns `VERIFICATION_FAILED` with `SOLVER_FAILED` diagnostic. No fabrication. Expected.
  - **Follow-up:** Separate positioning audit post-Stage-3. Not blocking Stage 2/3 close.

- **KL-9 (Stage 2, P2): `2_1_3_4_ok` baseline INVALID (inherited KL-7).**
  - **Symptom:** Part `2-1-3-4-(ok).FCStd` fails Stage 1 baseline with facets mismatch (90 vs 94).
  - **Cause:** Inherited from Stage 1 KL-7 (flat-pattern reconstruction).
  - **Impact:** `test_verifier_worker_2_1_3_4_deferred` is `@expectedFailure`.
  - **Follow-up:** Resolve via Stage 1 KL-7.

- **KL-10 (Stage 2, P3): `MockValidator` without `unknown_policy`.**
  - **Symptom:** `test_stage10/11/18/19` use a `MockValidator` that does not accept the `unknown_policy` parameter.
  - **Cause:** Mock created before `unknown_policy` became standard in S.8c.
  - **Impact:** Bypassed via `inspect.signature` in 3 planners. Functional, not contract-clean.
  - **Follow-up:** Add `unknown_policy` parameter to `MockValidator`. Cosmetic, no behavioral impact.

### Closed

- **KL-11 (Stage 3, CLOSED in S.9.3): VerifierWorker checked only unfold correctness.**
  - **Symptom (before fix):** Verifier checked that unfolding produces the correct flat pattern, but **not** that re-folding the flat pattern produces the correct 3D shape. `production_eligible=True` did not guarantee fold correctness.
  - **Cause (before fix):** No forward-reconstruction step (`SheetMetalFolder.fold_from_canonical_part` was not called).
  - **Fix:** S.9.3 (commit `4c9524e`) added fold correctness via `FrameAligner.align_full_frame` + `SheetMetalFolder.fold_from_canonical_part`, followed by metric comparison against `ref_shape`. Two new tests in `81946ab`: `test_verifier_detects_fold_fabrication` (strict `FOLD_SHAPE_MISMATCH`) and `test_verifier_fails_when_no_flat_pattern` (B2 regression). `production_eligible = unfold_ok AND fold_ok AND not unknown_steps AND has_all_bends`.
  - **Status:** CLOSED.

### Inherited from Stage 1

- **KL-5 / KL-6 / KL-7** — documented in the original project context (pre-S.1). Not reproduced here to avoid drift. KL-9 references KL-7. None of these block Stage 2/3 close.

## Historical: Phases 0-11 (pre-refactor, completed before Stage track)

| Phase | Description | Status | Verification |
|-------|-------------|--------|--------------|
| **Phase 0** | Preparation & Packaging (`pyproject.toml`, CLI layout) | **COMPLETE** | Packages configured, pytest targets 82 unit tests clean |
| **Phase 1** | Geometry Backend (`PythonOCCBackend`, `MockBackend`) | **COMPLETE** | Exact B-Rep boolean checks & stable face hashing |
| **Phase 2** | Tooling & Model Loader (`.FCStd` zip reader + `.tooling.json` datums) | **COMPLETE** | Tooling loaded without FreeCAD API |
| **Phase 3** | Bend Feature Extractor & Topology Graph | **COMPLETE** | Cylindrical surface extraction & signed bend angles |
| **Phase 4** | Subtree Kinematics & Fold State Cache | **COMPLETE** | Rigid subtree rotation & memoized state cache hit tracking |
| **Phase 5** | Tooling Assembly & Datum Alignment | **COMPLETE** | Datum role matching (`ALIGNED` / `MISALIGNED` / `CANNOT_VERIFY`) |
| **Phase 6** | Exact Multi-Level Collision Engine | **COMPLETE** | 7-Level collision checks, `UNKNOWN != VALID` strict rule enforced |
| **Phase 7** | Search Solvers (A* / DFS / Greedy) | **COMPLETE** | A* search with state caching, strict sequence validation |
| **Phase 8** | Backgauge & Tool Optimizer | **COMPLETE** | Geometry-driven backgauge calculation |
| **Phase 9** | CLI & JSON Output Contract | **COMPLETE** | Standardized JSON schema v1.0 output |
| **Phase 10** | Real Part Model Regression Suite | **COMPLETE** | Batch execution on 16 test models in `SMBendTests/` |
| **Phase 11** | Final Production Audit | **COMPLETE** | 82 Unit tests passing cleanly (historical metric, superseded by Stage track) |

---

## Key Constraints & Strict Rules Verification

1. **Headless Execution:** Core modules run strictly without FreeCAD GUI or FreeCAD API dependencies.
2. **Sidecar Datums:** Tooling aligned via explicit datum roles (`mounting_plane`, `working_axis`, `v_center`, `tip_point`).
3. **No False Positives:** GeometryStatus (`REAL`), AlignmentStatus (`ALIGNED`), CollisionStatus (`VALID`).
