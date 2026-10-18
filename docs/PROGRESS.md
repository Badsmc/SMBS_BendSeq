# SMBS Core - Engineering Implementation Progress

**Status (HEAD `6a507f6`):** Stage 1/2/3/4 functionally complete. 350 tests, 5 failures (all KL-8), 0 errors, 3 expectedFailures (KL-7/KL-8). Stage 4 adds headless viewer + offscreen PNG renderer (Layer 5); geometry is `PREVIEW_ONLY` pending KL-12 fix (S.13b). Ready for production use where tooling model matches; `NO_SOLUTION` + honest diagnostics otherwise. See `Known Limitations` below.

## Project Stages

| Stage | Description | Status | Notes |
|-------|-------------|--------|-------|
| **Stage 1** | Baseline: 3D reconstruction from flat pattern | **COMPLETE** | KL-5/6/7 inherited from original project context |
| **Stage 2** | Search: bend sequence optimization with `unknown_policy` | **COMPLETE** | KL-8 (P1), KL-9 (P2), KL-10 (P3) |
| **Stage 3** | Verification: independent unfold + fold correctness | **COMPLETE** | KL-11 CLOSED in S.9.3 (commit `4c9524e`) |
| **Stage 4** | Viewer: headless CLI + offscreen PNG renderer (Layer 5) | **COMPLETE** | KL-12 (P1) drives `PREVIEW_ONLY`; forward deferred to S.13b |

## Stage 4 (Viewer / Layer 5) — COMPLETE

**Purpose:** headless visualization of a pipeline run's `candidate_sequence` — step-by-step replay, metadata timeline, and offscreen PNG frames. Honest marking (C2): `production_eligible` is read from `09_verification_result.json` only; never computed by the viewer. Missing `09` yields `verification_status="UNVERIFIED"` (invariant #1: `UNKNOWN != VALID`). All geometry is `PREVIEW_ONLY` pending KL-12 fix (S.13b); forward mode is deferred.

**Modules (`smbs_core/viewer/`):**

| Module | Responsibility |
|---|---|
| `run_loader.py` | Load `04/07/09` from run dir; resolve `geometry.brep`; `09` optional → `None` (UNVERIFIED). |
| `graph_builder.py` | Build `BendGraph` + `PanelGraph` from `04_canonical_part.json` (strict; no silent defaults). |
| `replayer.py` | Step-by-step backward unfold: `backend.rotate_subtree` + `FoldState.apply_unfold`. Yields `StepView` with `geometry_precision="PREVIEW_ONLY"`. |
| `player.py` | Timeline state machine (`reset/next/prev/current`). Reads `production_eligible` and `diagnostics` from `09` only. |
| `render.py` | Offscreen HLR + PIL → PNG. Honest edge-skip handling: visible edge loss → `RenderError`; hidden edge loss → subtitle banner. |
| `cli.py` / `__main__.py` | `python -m smbs_core.viewer view <run_dir>` — table / `--json` / `--dump-frames` / `--render-png`. |

**CLI:** `smbs-cli` entry remains pipeline-only. Viewer runs from the module entry point: `python -m smbs_core.viewer view <run_dir> [--direction backward] [--dump-frames DIR] [--render-png DIR] [--render-stride N] [--json] [--allow-mock-backend]`.

**Commits:** S.11 `5a973d0`/`2d5bf26` (run_loader), S.12 `4a83ac6`/`dffeef5` (graph_builder), S.13a `f1d4f19`/`41ae4c0` (replayer backward + PREVIEW_ONLY), S.14 `99fd8b7`/`c474306` (player + cli), S.15a `4549f85`/`6a507f6` (HLR+PIL renderer + CLI render flags). KL-12 documented in `afb0626`.

**Manual GUI test status (S.18b, PASSED on FreeCAD 1.1.3 / PySide6 / Qt 6.8.3):**
- Synthetic VERIFIED fixture → green label '1' above flat pattern, no anchor dot.
- Old run without `bend_line_flat_pattern` → red actionable error dialog (KL-13), no annotations.
- Synthetic VERIFICATION_FAILED fixture → orange label '1', honest marking confirms FAILED.
- Real production runs (non-empty `candidate_sequence` + `bend_line_flat_pattern`) blocked until KL-8 and/or KL-13 fixed.

**Non-goals / deferred:**
- Forward fold mode — blocked on KL-12 (S.13b). `Player.switch_direction("forward")` raises `PlayerError`.
- FreeCAD addon GUI — not started; better after per-panel kinematics (KL-12 close).
- Animated GIF/MP4, interactive playback in a 3D viewport — out of scope for headless viewer.

## Test Metrics (post Stage 4)

- **Discover:** 350 tests / **5 failures** / **0 errors** / **3 expectedFailures**
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

## Future Stages

See `docs/ROADMAP.md` for the full integration roadmap (FreeCAD UI, tooling library, optimal tooling selection). Requirements 1–5 with dependencies, estimates, and open questions.

Summary: MVP (requirement 1 — bend sequence numbers on flat pattern) can start immediately without contract changes. Requirements 2–5 require schema agreements (#7) and/or KL-12 fix.

**User-confirmed decisions (from roadmap review):**
- **Q4** → 3D annotations on the unfolded part (not TechDraw).
- **Q7** → tooling moves during animation (punch descends, backgauge repositions).

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

- **KL-12 (P1, OPEN, discovered in Stage 4 audit)**: `PanelGraph` has no `face_indices` populated from `04_canonical_part.json`. `PythonOCCBackend.rotate_subtree` therefore falls back to whole-part rotation (`pythonocc_backend.py:294` — `extract_faces` returns `None` for empty `face_ids`). Effect: solver and verifier re-simulation rotate the entire part instead of the per-panel subtree; `PhysicalValidator.validate_step` (validator.py:174) passes because whole-part rotation still changes the shape; `collision_detector.py:71` skips self-collision when `face_indices` are empty. `PanelTopologyBuilder.build_panel_graph` is the only code path that fills `face_indices`, but its `PANEL_{f_hash[:8]}` IDs never match the topology-extractor IDs in `04_canonical_part.json` (never exercised on real shapes — only with `shape=None` in `test_phase4_extraction`). Fix path: cluster faces by plane + map to canonical panel IDs by centroid+normal (S.13b backlog). Viewer (Stage 4) is `PREVIEW_ONLY` until then; honest marking via `StepView.geometry_precision`.

- **KL-13 (P3, OPEN, discovered in S.18 audit)**: runs generated before 2026-10-17 lack `bend_line_flat_pattern` in `04_canonical_part.json`. Flat annotations (requirement 1) require this field. `flat_annotations.py` refuses such runs with actionable error; `axis_point` is **not** used as fallback (it lives in the folded-model coordinate system, not on the flat pattern — using it would place numbers off the flat pattern). Impact: 3 old runs (`run_20261012_034042_cf4c34`, `run_20261012_034045_9a351d`, and one more) cannot display annotations. Fix path: regenerate old runs with current pipeline (`smbs-cli part.FCStd`). Not a bug — pipeline evolution. See `smbs_core/addon/README.md` "Known issue: old runs".

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
