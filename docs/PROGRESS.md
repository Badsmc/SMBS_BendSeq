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

## Stage 5 — Tooling Library (Phase B) — COMPLETE

**Requirement 3 (roadmap):** users can add their own FCStd tooling to a library, auto-detect role (punch/die/frame/backgauge), and use tools in pipeline via `--punch-id` / `--die-id`.

**Modules (`smbs_core/geometry_backend/io/`):**

| Module | Responsibility |
|---|---|
| `tooling_catalog.py` | Scan `<library>/punch/` + `<library>/die/`, read `.tooling.json` sidecars, build `ToolingCatalog` with `ToolDescriptor` index. |
| `tooling_library.py` | Persistent manager: `add_tool` / `remove_tool` / `list_tools` / `get_tool` / `has_tool`. Sidecar-required (no fabricated datums). Path-traversal-safe. |
| `backends/fcstd_loader.py` (extended) | Q1=D 4-level role resolution: `property` → `name` → `sidecar` → `inferred` (inferred not implemented; honest error instead). Adds `Tooling.smbs_role` + `Tooling.smbs_role_source`. |

**CLI (`smbs_core/cli.py`):** `--punch-id`, `--die-id`, `--library-path`. Mutual exclusion with `--tooling-config`. Default `library_path` = `<repo>/toollibrary/`.

**Coordinator (`smbs_core/coordinator/run_pipeline.py`):** `machine_config={"punch_id", "die_id", "library_path"}` resolves via `ToolingLibrary.get_tool` → rewrites to `punch_path`/`die_path` for existing tooling pipeline. First REAL-tooling runs confirmed (`tooling_status: REAL`, real `model_path` in `tooling_manifest.json`).

**FreeCAD UI (`smbs_core/addon/SMBS_ToolingLibrary.FCMacro`):** dialog with punch/die tabs, add/remove/list/preview, bootstrap. Manual GUI test PASSED on FreeCAD 1.1.3 (add, remove, preview, metadata display).

**Schema:** `smbs_core/contracts/schemas/tooling_manifest.schema.json` extended with 3 optional fields in `payload.punch` and `payload.die`: `target_object_name`, `smbs_role`, `smbs_role_source`. Backward-compatible with all existing manifests.

**Commits:** S.22b–S.22g (see git log for details). Q1=D approved (fallback chain), Q8 approved (2 levels: `<repo>/toollibrary/` + `--library-path`). INFERRED heuristic skipped for MVP (honest error instead).

**Non-goals / deferred:**
- Datum editing UI in macro (sidecar remains manual).
- `~/.smbs/tooling/` user-global storage.
- INFERRED heuristic (height/ratio guessing) — honest error instead.
- Sidecar creation wizard.
- "Use in pipeline" button in macro (requires passing IDs from macro to CLI manually).

## Test Metrics (post Stage 5 Phase B)

- **Discover:** 415 tests / **5 failures** (all KL-8) / **0 errors** / **3 expectedFailures**


## Stage 6 — Requirement 4.1 (Clamp constraints) — COMPLETE

**Requirement 4.1 (roadmap):** пользователь задаёт длину лапок, промежутки, количество. Программа проверяет, что пуансон надёжно удерживается (стыки на лапках, ≥2 лапки под пуансоном, свес ≤30%).

**Что реализовано (S.24a–S.24c):**

| Модуль | Ответственность |
|---|---|
| `smbs_core/contracts/schemas/tooling_library_config.schema.json` | Контракт `<library>/config.json` (clamps + punch_chain + bend_line_tolerance) |
| `smbs_core/geometry_backend/io/clamp_validator.py` | `ClampConfig`, `PunchSegment`, `validate_punch_chain` (5 правил Q2), `load/save_clamp_config`, `validate_config_dict` |
| `ToolingLibrary.get_clamp_config / set_clamp_config / ensure_clamp_config` | Чтение/запись/гарантия `<library>/config.json` |
| `smbs-cli --clamps-config PATH` | CLI валидация JSON через `validate_config_dict` + передача в `Coordinator.start_run(machine_config["clamps_config"])` |

**Правила валидации (from ROADMAP §11 Q2):**
1. ≥ 2 блока под пуансоном (по количеству)
2. Все стыки пуансонов внутри зон `[center - W/2, center + W/2]` (tolerance 0.1mm)
3. Зазоры между пуансонами запрещены (если `no_gaps_between_punches=True`)
4. Свес крайних пуансонов ≤ 30% от их длины (настраиваемый ratio)
5. Длина пуансона = длина линии гиба − 10..15мм (правило подбора, requirement 4)

**Honest plumbing:** в `run_pipeline.py` `machine_config["clamps_config"]` принимается, но **не используется** в planning. Enforcement (validate_punch_chain при каждом bend, отклонение invalid) — **requirement 4 (Phase E, optimal tooling selection)**. S.24c явно логирует в stderr: `"validated, not enforced — enforcement is requirement 4"`.

**Deferred (backlog):**
- UI-редактор раскладки лапок в FCMacro (JSON-файл достаточно для MVP).
- Auto-repositioning clamps при отсутствии валидной конфигурации (requirement 4).
- Persistence config в артефактах run-директории (когда появится punch chain per bend, requirement 4).

**Test metrics (post Stage 6):**
- **Discover:** 449 tests / 5 failures (KL-8) / 0 errors / 3 expectedFailures
- Stage 6 добавил: +14 тестов (13 clamp_validator + 1 validate_config_dict + 8 tooling_library_clamps + 5 cli_clamps_config = 27... поправлю: 13+1=14, +8=22, +5=27, но baseline было 422 → 435 (S.24a) → 443 (S.24b) → 444 (S.24c-pre) → 449 (S.24c). Итого +27 тестов от 422.)

**Commits:** S.24a-schema, S.24a-code, S.24a-tests, S.24a-docs, S.24b-code, S.24b-tests, S.24c-pre, S.24c-code, S.24c-tests (см. git log).


## Known Limitations

**Template:** each entry uses **Symptom / Cause / Impact / Follow-up**. Priority: P1 (production blocker), P2 (workaround exists), P3 (cosmetic).

### Active

- **KL-8 (Stage 2, P1, blocked by KL-12): Real tooling collision in `place_part_on_tool_station`.**
  - **Symptom:** 5 e2e tests fail with `INVALID` on TOOL stage, `vol≈1111 mm³`. `EXPLORATORY` policy does not bypass (INVALID is never bypassed, per invariant #2).
  - **Cause:** Positioning between `place_part_on_tool_station` and real `matrica.FCStd` produces a physical collision. Requires separate audit of datum alignment against the real tooling model.
  - **Impact:** Search returns `NO_SOLUTION` honestly; verifier returns `VERIFICATION_FAILED` with `SOLVER_FAILED` diagnostic. No fabrication. Expected.
  - **Follow-up:** Separate positioning audit post-Stage-3. Not blocking Stage 2/3 close.

  - **UPDATE 2026-10-18 (S.19a/S.19a2 audit)**: `place_part_on_tool_station` now uses die V-groove axis + mounting_plane normal (not hardcoded `(1,0,0)`/`(0,0,1)`) and tries both axis signs (`±axis_dir`). APPROACH-stage die collisions eliminated. HOWEVER, KL-8 is now **blocked by KL-12**: after APPROACH passes, the next stage `SELF` returns `UNKNOWN` (`No extracted panel geometry available for self_collision`) because `PanelGraph.panels[*].face_indices` is empty. Under `PRODUCTION` policy, `UNKNOWN != VALID` (invariant #1) → step rejected → `NO_SOLUTION` → e2e tests still fail with `production_eligible=false`. **Fix path: KL-12 (per-panel kinematics) must be closed before KL-8 can be verified. Priority: KL-12 → KL-8.**

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
  - **UPDATE 2026-10-18 (S.20/S.21a-pre/S.21a-pre2 audit)**: root cause fully mapped. `PanelTopologyBuilder.build_panel_graph` uses `PANEL_{f_hash[:8]}` (single-face hash), while `FeatureExtractor` uses `PANEL_{stable_hash(centroid, normal)}` (cluster hash) — IDs never match, so `face_indices` is always empty. 90/90 existing runs have no `face_indices`. **H2 (runtime face_indices enrichment) tested and rejected**: `rotate_subtree` on multi-step unfold loses 16/22 faces (cylinders dropped), invalidates `face_indices` after step 1 (indices remap on topology change), and `intersect_volume` on planar compound pairs always returns 0 (mathematically correct for 2D surfaces). **Fix paths identified:** (A) bbox approximation for `self_collision` (~1 session, honest `SELF_BBOX_APPROXIMATION` marking, baseline 5F→4F); (B) solid-preserving split+fuse in `rotate_subtree` (~2–3 sessions, closes KL-12 fully, unblocks viewer `PER_PANEL_KINEMATIC`). **DECISION 2026-10-18: KL-12 deferred to backlog.** Rationale: KL-12 does not block roadmap requirements 3–5 (tooling library, tooling sets, optimal selection), only requirement 2 (animation). Roadmap §8 order honored: requirements 3 → 4.1 → 3.1 → 4 → 2 → 5. KL-12 resumed after requirement 4 or as a dedicated stage.

- **KL-13 (P3, OPEN, discovered in S.18 audit)**: runs generated before 2026-10-17 lack `bend_line_flat_pattern` in `04_canonical_part.json`. Flat annotations (requirement 1) require this field. `flat_annotations.py` refuses such runs with actionable error; `axis_point` is **not** used as fallback (it lives in the folded-model coordinate system, not on the flat pattern — using it would place numbers off the flat pattern). Impact: 3 old runs (`run_20261012_034042_cf4c34`, `run_20261012_034045_9a351d`, and one more) cannot display annotations. Fix path: regenerate old runs with current pipeline (`smbs-cli part.FCStd`). Not a bug — pipeline evolution. See `smbs_core/addon/README.md` "Known issue: old runs".

### Closed

- **KL-11 (Stage 3, CLOSED in S.9.3): VerifierWorker checked only unfold correctness.**
  - **Symptom (before fix):** Verifier checked that unfolding produces the correct flat pattern, but **not** that re-folding the flat pattern produces the correct 3D shape. `production_eligible=True` did not guarantee fold correctness.
  - **Cause (before fix):** No forward-reconstruction step (`SheetMetalFolder.fold_from_canonical_part` was not called).
  - **Fix:** S.9.3 (commit `4c9524e`) added fold correctness via `FrameAligner.align_full_frame` + `SheetMetalFolder.fold_from_canonical_part`, followed by metric comparison against `ref_shape`. Two new tests in `81946ab`: `test_verifier_detects_fold_fabrication` (strict `FOLD_SHAPE_MISMATCH`) and `test_verifier_fails_when_no_flat_pattern` (B2 regression). `production_eligible = unfold_ok AND fold_ok AND not unknown_steps AND has_all_bends`.
  - **Status:** CLOSED.

### Inherited from Stage 1

- **KL-15 (Stage 5, CLOSED in S.23)**: `FcstdToolingLoader.load()` raised `AmbiguousShapeError` for FCStd files with multiple objects (e.g. `puancon.FCStd` has `Sketch`, `Body`, `Pad`) when `target_object_name` was not explicitly passed. **Fix:** disambiguation extended in `fcstd_loader.py` — filter by `Volume > 1e-6` (drops `Sketch`/wires), then prefer `Body` > `Pad` > `ToolShape`. Fallback to explicit `AmbiguousShapeError` with candidate list if still ambiguous. 7 new tests in `test_fcstd_loader_disambiguation.py`. Baseline 415 → 422. First end-to-end REAL-tooling smoke test now passes shape loading (solver returns `NO_SOLUTION` due to KL-8/KL-12, not shape errors).

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
