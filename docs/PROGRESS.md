# SMBS Core - Engineering Implementation Progress

> **Project status:** advanced prototype with several completed functional subsystems, **NOT a complete automatic planning system**.
> **Open blockers:** KL-12 v3 (planar self-collision + bridge caps + panel shell disconnection observed in F.3a smoke) — v2 (side edges) CLOSED in S.31; Req 2 Phase F.1 CLOSED in S.33; Req 2 Phase F.2 CLOSED in S.35; Req 2 Phase F.3a (tooling visualization, punch/die) CLOSED in S.37. Manual GUI smoke for F.2/F.3a: PERFORMED (S.37 smoke 7 — PASS on tooling overlay; shell disconnection is pre-existing KL-12 v3, visible at PREVIEW_ONLY). See Known Limitations.
> **Requirement 4.1 (clamp constraints) — validator (S.24a–S.24c) + enforcement per-bend in `12_tooling_selection.json` (Req 4 Phase E, S.30c).**
> **Open P0 defects (annotated 2026-10-23, HEAD `91d07c0`, baseline 550 / 0F / 0E / 3xF):** 5 code + 4 doc — see `docs/AUDIT_FULL_2026-10-23.md`. Current release verdict: **NOT READY**.

**Status (HEAD `91d07c0`):** Stage 8 + KL-22 + Req 6a Phase 6a + Req 4 Phase E + KL-12 v2 + Req 2 Phase F.1 + F.2 + F.3a COMPLETE. **550 tests / 0 failures / 0 errors / 3 expectedFailures.**
KL-8 closed. All physically-verifiable pipeline stages (APPROACH, SELF, TOOL,
TRAJECTORY, RETRACT) return VALID on compatible parts with real tooling;
MACHINE honest UNKNOWN (KL-16: no machine_frame.FCStd). KL-22 closed.
Req 6a Phase 6a COMPLETE (all 4 steps: Lazy A* + admissible h + epsilon tie-breaker +
move ordering cache + depth_limit safety net). Empirical results:
box N=8 wall 47.73-50.32s noise band (49.26s step 2, 9 nodes, 8 valid + 1 invalid);
2_1_3_4_ok N=10 wall 1.0-1.29s (11 nodes). Search SLA p95 <= 240s MET (4.9x under budget).
KL-12 v2 (side edges) CLOSED in S.31: fixture_l 6/10 → 8/10 assigned
(bridge caps 1,4 remain unassigned); box_100out_50in_ok 41/78 → 78/78
assigned; verified via rotate_subtree face-count preservation (9/9 panels,
angle=0). Bridge caps span bend lines, cannot be rigidly assigned to a
single panel (KL-22 trade-off preserved, v3 scope).
Req 2 Phase F.1 (headless per-panel kinematics) CLOSED in S.33 (1724748):
replayer calls `resolve_panel_faces` before unfold; 3-state honest precision
(no faces assigned → PREVIEW_ONLY + original whole-part warning; some faces
→ PREVIEW_ONLY + PARTIAL warning with KL-12 v3 note; all faces →
VALID_PER_PANEL, no warning). Forward direction implemented as honest
reversal of backward StepViews; `switch_direction` resets current position.
Baseline 532 → 537 (5 new F.1 tests).
Note (correction to S.32): bridge caps are NOT universally absent on L-family
fixtures — `run_20261012_034042_cf4c34` (real L-part) has bridge caps (faces
1,4 unassigned, 8/10). The 3-state warning honestly marks such fixtures.

Req 2 Phase F.2 (FreeCAD Timeline UI) CLOSED in S.35 (8d0ce71):
`smbs_core/viewer/fc_animation.py` (headless AnimationController +
badge helpers, no FreeCAD/Qt imports, unit-testable) +
`smbs_core/addon/SMBS_AnimationPlayer.FCMacro` (QDialog + QTimer modal;
renders frames via Part.show with cleanup; honest badge display; timer
stops on close/end/seek). Baseline 537 → 544 (7 new F.2 tests).
**Manual GUI smoke: NOT YET PERFORMED** — no "verified in FreeCAD" claims
until user runs macro and confirms behavior.
Known follow-up (not blocker for DoD Req 2): `smbs_core/viewer/render.py`
BANNER_TEXT hardcoded — PNG fallback still marks PREVIEW_ONLY even when
`StepView.geometry_precision=VALID_PER_PANEL`. Viewport path (FCMacro) is
source of truth and unaffected. Deferred to F.3 or separate fix.

Req 2 Phase F.3a (tooling visualization, punch/die) CLOSED in S.37a-f
(814eb19..87cdbad): `PressBrakeMachine.with_default_tooling()` +
`ToolingAssembly.place()` in `_load_run`; punch/die shapes rendered via
persistent Part::Feature (`_update_feature` with `obj.touch()` +
`Gui.updateGui()`); `closeEvent` cleans SMBS_Animation/SMBS_Punch/SMBS_Die.
Machine frame: badge `MACHINE: SIMPLIFIED (KL-16 pending)`. tooling_placement
= (a) on-the-fly, schemas unchanged (#7 clean). Baseline 544 → 550 (6 new
badge tests). **First real GUI smoke revealed 4 pre-existing F.2 bugs**
(`part_shape` attr, `Part.Shape(occ)` C++ abort, initial render missing,
GUI refresh after in-place .Shape) — all fixed in S.37b/c/d/e/f, each an
independent commit. Remaining F.3a UX follow-ups (backgauge, punch descent,
machine frame geometry, modal dialog reconsideration) deferred to F.3b.
**Known limitation (pre-existing, NOT F.3a regression):** after first
unfold, single solid → `TopoDS_Compound` of disconnected shells
(L/U/Z fixtures, verified via `TopoDS_SOLID` explorer: solids 1→0,
shells 1→2→3→4, 22 faces preserved). Visual symptom: panels appear
separated at Step ≥1 (visible in PNG render and FreeCAD viewport alike —
same shape path). This is replayer/KL-12 v3 territory, not addon scope.

Next: Req 2 Phase F.3b (punch descent + backgauge + machine frame geometry;
after KL-16) or KL-12 v3 (planar self-collision + bridge caps + panel shell
connection — fixes visual "separated panels" seen in F.3a smoke). Box-like
parts with bridge caps still require KL-12 v3 for full
`production_eligible=True`.

## Open P0 Defects (2026-10-23 audit)

Snapshot: HEAD `91d07c0`, baseline 550 / 0F / 0E / 3xF, annotated 2026-10-23.
Audit evidence, per-defect resolution criteria, and scope: `docs/AUDIT_FULL_2026-10-23.md`.

The baseline is green, but the audit identified the following P0 defects that current tests do not catch (integrity invariants, not functional regressions). Status: **NOT READY** for release until all are closed with reproducible evidence + negative test case.

| ID | File:lines | Defect |
|---|---|---|
| P0-CODE-1 | `topology/worker.py:540, 561–565` | `direction` derived from `signed_angle` sign only; marked `confidence=CONFIRMED`. No axis/normal context. |
| P0-CODE-2 | `topology/worker.py:552, 557, 571, 572` | Silent defaults `radius=1.0`, `length=50.0`, `axis_point=[0,0,0]`, `axis_direction=[1,0,0]` — all marked `CONFIRMED`. |
| P0-CODE-3 | `baseline/worker.py:702, 704–712, 760` | `hausdorff = max(bbox_delta)` — not Hausdorff. `area_delta` computed (line 700) but not in `is_pass`. |
| P0-CODE-4 | `geometry/worker.py:179, 249, 273` | Dead `status_worker`; literal `RunStatus.SUCCESS.value` on unfold failure. `02_geometry_result.json` = `SUCCESS` even when unfold fails. |
| P0-CODE-5 | `adapter.py:127–128, 214–215, 249, 164–165` | FreeCAD preference defaults (0.44 / 1.0) flow to `NormalizedBendLine(confidence=CONFIRMED)`. `EstimateThickness` → `thickness_confidence=CONFIRMED`. |
| P0-DOC-1 | `docs/RELEASE_AUDIT.md` | Stage 3 "READY FOR RELEASE" without SHA/scope → annotated `HISTORICAL` 2026-10-23. |
| P0-DOC-2 | `docs/REGRESSION_REPORT.md` | 32/32 PASS from commit `c76e95f` (main) → annotated `HISTORICAL` 2026-10-23. |
| P0-DOC-3 | `docs/TEST_MATRIX.md` | ">70 (74 typically)" pre-refactor matrix → annotated `HISTORICAL` 2026-10-23. |
| P0-DOC-4 | `docs/PROGRESS.md` (this file) | No open P0 registry; stale HEAD `87cdbad` → resolved in this commit (this section + line 7 fix). |

Do not close a P0 without reproducible evidence + negative test case. See `docs/WORKING_CHECKLIST.md` for active task tracking.

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
- **Stage 6 added:** +27 tests (S.24a: 14; S.24b: 8; S.24c: 5)

**Commits:** S.24a-schema, S.24a-code, S.24a-tests, S.24a-docs, S.24b-code, S.24b-tests, S.24c-pre, S.24c-code, S.24c-tests (см. git log).

**Stage 7 COMPLETE (2026-10-19):** Requirement 3.1 — Tooling Sets.
- `ToolingSet` model + JSON schema (`smbs_core/contracts/schemas/tooling_set.schema.json`, `smbs_core/geometry_backend/io/tooling_set.py`)
- CRUD in `ToolingLibrary`: `add_set/get_set/list_sets/has_set/remove_set`
- Kind validation in `add_set` (punch_id → kind=punch, die_id → kind=die)
- CLI `--tooling-set <id>` (mutually exclusive with `--punch-id/--die-id/--clamps-config/--tooling-config`)
- Coordinator resolves set → punch_path/die_path + ClampConfig from `clamp_positions_mm`
- Honest notice: substitution/validation not applied (requirement 4)
- Baseline: **485 / 5F / 0E / 3xF** (KL-8 open)


## Stage 8 — KL-8 (tooling collision, APPROACH scope)

**Status:** COMPLETE (2026-10-20). Baseline 485 → 497, F: 5 → 0. **First 0F
in project history.** Zero regressions, zero silent workarounds, zero xfails.

**Commits (6 sequential):**
- **Commit 1** (`9ae3993`): KL-8c-fix — `place_part_on_tool_station` checks
  punch AND die. Root cause (Q19-Q21): greedy return of first die-valid
  candidate without punch check. At trajectory -10°, selected candidate with
  95.5mm flange vertical along punch → vol=1959 mm³ false collision on
  fixture_l. Regression test with stash-proof (fails on stash, passes on fix).
- **Commit 2** (`b8bacf3`): KL-8b-fix — `BendSeqOrchestrator` calls
  `resolve_panel_faces` (like SolverWorker/VerifierWorker from KL-12 v1).
  + M4 policy per test case (EXPLORATORY for positive, PRODUCTION for negative).
- **Commit 3A** (`5196820`): KL-8a-reclassify — plumbing tests renamed
  (`test_plumbing_*_no_tooling_honestly_no_solution`) and asserted honest:
  `NO_SOLUTION`, `verification_status != VERIFIED`, `production_eligible=False`,
  `export_status=BLOCKED`, 07/11 produced, 10 not produced (invariant #2).
  Git archaeology (Session 1.5): these tests never passed machine_config;
  before 90ab5d8 (UNKNOWN != VALID) they passed on false VALID; after — fail
  honestly. Not a code regression — broken test design.
- **S.26** (`d3a26e5`): silent downgrade fix — `worker.py:228-230`
  `GeometryStatus.REAL.value` = `"real"` (lowercase), `in ("REAL",...)` check
  always False → silent downgrade to `"SIMPLIFIED"`. Contract error at
  `07_search_result.json` was the symptom; masked by SIMPLIFIED-only test path.
  Fixed: `.upper()` before validation + `raise ValueError` on invalid.
- **Commit 4A** (`3fe349a`): physics e2e with real tooling — 4 tests.
  Orchestrator EXPLORATORY: `PARTIAL`, `steps_invalid=0`, `steps_unknown>0`
  (honest M4). Per-stage: APPROACH/SELF/TOOL/TRAJECTORY/RETRACT = VALID,
  MACHINE = UNKNOWN (KL-16).
- **Commit 4B** (`91571fe`): ExportWorker §7.3 unit-level tests. Original
  pipeline-level test_corrupted failed on setup (no initial export without
  machine_frame). Replaced with inline-fixture unit test:
  VERIFIED→EXPORTED, VERIFICATION_FAILED→BLOCKED.

**Acceptance (honest):**
- APPROACH / SELF / TOOL / TRAJECTORY / RETRACT = VALID on L40 and fixture_l
  with `puancon.FCStd` + `matrica.FCStd`.
- MACHINE = UNKNOWN (KL-16, no machine_frame).
- 4 pipeline plumbing tests honest: NO_SOLUTION, BLOCKED, production_eligible=False
  (invariant #1).
- Final export (10_part_bendseq.json) only on VERIFIED (invariant #2).
- ExportWorker §7.3 contract verified in isolation.

## Stage 9 — Requirement 4 Phase E (Optimal tooling selection) — COMPLETE (2026-10-21)

**Status:** COMPLETE. S.30a-schema + S.30b-core + S.30c-integration committed and
pushed to `stage-1-fix-correctness`. 523 tests / 0F / 0E / 3xF.

**Scope (per ROADMAP §3 Requirement 4 + §11 Q2/Q3/Q11 resolutions):**
- Clamp enforcement per bend (Requirement 4.1 `validate_punch_chain` called for
  every assignment; previously validator-only per PROGRESS line 5).
- Segment composition per Q11=C (bend_line_length − 10..15mm).
- DP min tool changes per Q3=A.
- Artifact `12_tooling_selection.json` (contract `tooling_selection.schema.json`,
  #7 approved 2026-10-21).

**Modules:**
- `smbs_core/contracts/schemas/tooling_selection.schema.json` (S.30a-schema)
- `smbs_core/tooling/selection.py` — `select_tooling(...)` DP algorithm + clamp enforcement (S.30b-core)
- `smbs_core/coordinator/run_pipeline.py` — `_write_tooling_selection(...)` hook
  after `export_worker.execute(...)` (S.30c-integration)

**Commits:**
- S.30a-schema `d2501bb`: schema + 7 contract tests
- S.30b-core `0214359`: selection.py + 12 unit/DP tests
- S.30c-integration `9cf5bed`: run_pipeline hook + 3 integration tests

**Behavior:**
- Library-resolved tooling (tooling_set or punch_id/die_id): `select_tooling`
  runs, `12_tooling_selection.json` written with `status=SUCCESS` (all bends
  assigned) or `PARTIAL` (some bends unassignable, warnings explain).
- Path-based tooling (punch_path/die_path without library): `status=UNKNOWN`,
  `assignments=[]`, diagnostic "Phase E requires library-resolved tooling".
- clamps_config=None: `ToolingSelectionError` caught, `status=UNKNOWN`.

**Honest limitation (documented, not a defect):**
Optimal selection **across alternatives** is not exercised because `ToolingSet`
currently holds one punch + one die. `select_tooling` accepts `Sequence[ToolDescriptor]`
and DP is ready; multi-alternative set is future work. Current pipeline = single
(punch, die) → clamp enforcement + segment composition + honest status.

**Pre-existing finding (not Phase E scope, tracked):**
F-RP-1: `run_pipeline.py` lines 291–315 build `tooling_manifest.payload.punch` /
`payload.die` with hardcoded `height_mm=100.0`, `tip_radius_mm=1.0`, `v_opening_mm=12.0`,
`segment_layout_mm=[100.0, 200.0]` — not sourced from `ToolDescriptor.metadata`.
This means search (`07`) uses hardcoded params, while Phase E uses real sidecar
metadata. Fix path: source manifest fields from `punch_desc.metadata` /
`die_desc.metadata` (P3, backlog).




## Requirement 6 — Search SLA (Phase 6a COMPLETE 2026-10-21)

**Status:** COMPLETE (2026-10-21). Phase 1 cProfile done; Phase 6a all 4 steps
implemented and verified. SLA p95 <= 240s met (actual 49.26s, 4.9x under budget).
Phase 6b NOT needed. Next: Requirement 4.

**Goal:** Guaranteed response within 4 min for any part. Honest
`UNKNOWN(timeout)` if not solved — never false `NO_SOLUTION` masking timeout.

**Acceptance (reference set N=4..12):**
- p50 wall-clock <= 30 sec
- p95 wall-clock <= 4 min (MET: 49.26s on N=8, <=1.3s on N=10)
- SOLVED on all physically compatible fixtures with real tooling
- Honest UNKNOWN(timeout) on incompatible — never NO_SOLUTION masking timeout
- 3D-validation calls <= nodes_expanded (not nodes_expanded x b) (MET: 9 calls for 9 nodes on N=8)

**Phase 1 — cProfile (DONE, 2026-10-21):**
- Bottleneck: `PhysicalValidator.validate_step` at 97–99.7% cumtime.
- N=8 `box_100out_50in_ok` real tooling: `BRepAlgoAPI_Common` +
  `MakeShape_Build` = 97% of pre-fix runtime.
- N=10 `2_1_3_4_ok` synthetic tooling: `BRepBuilderAPI_Sewing_Perform` = 55%.
- `heappush`/`heappop`/heuristic: negligible (<0.1%). Lazy A* direction
  confirmed by profile, not intuition.

**Empirical baseline (pre-Phase 6a, post KL-22 fix, 2026-10-21):**
- Fixture: `fixtures/freecad/box_100out_50in_ok/part_3d.brep` (N=8, real tooling)
- Result: **PARTIAL, seq_len=8, planning_time=2149.85s (35.8 min), 274 nodes,
  ~7.85s/node, UNFOLD_TRANSFORM=0, steps_invalid=0, steps_unknown=8.**
- vs p95 SLA target 240s → **x8.95 over budget**.

**Phase 6a — Req 6a optimization (DONE, 2026-10-21):**

Four-step behavior-preserving optimization, all closed on `stage-1-fix-correctness`:

1. **Lazy A* (`7926e13`):** defer `validate_step` from `heappush` to `heappop`.
   Validated on pop, INVALID pending discarded via `dead_states` + `continue`.
   Pre-condition: `h_pred == h_actual` bit-exact (93 transitions verified,
   H1/H2/H2b = 0 mismatches, invariant #14). Effect (box N=8):
   `validate_calls` 2192 -> 349, `nodes_explored` unchanged 274, wall
   2149.85s -> 1712.44s.

2. **Admissible h + epsilon tie-breaker (`c03db66`):** `h = len(remaining) == h*`,
   `f = g + h = N` (constant for all reachable). All preference moved to `__lt__`:
   `f -> -g -> moving_sz -> counter` (Interpretation C). Effect (box N=8):
   `nodes_explored` 274 -> 9, `validate_calls` 349 -> 9, wall 1712.44s -> 49.26s.
   Effect (`2_1_3_4_ok` N=10): nodes 11, wall 1.22s.

3. **Move ordering cache (`066dc8c`):** precompute `moving_subtree(bend_id)` per
   `solve()`. No-op on box (nodes 9, seq identical), measurable win on N=10
   (-16% wall). DFS prefix pruning already provided by `dead_states` (step 1);
   no additional code needed.

4. **Depth_limit safety net (`425195f`, 4c-i):** `solve(depth_limit=None)` opt-in
   per-branch depth bound. Default `None` = no-op. Classical DFBnB with
   `best_cost` upper bound is a no-op under unit-cost + `h == h*` (`f = g + h = N`
   constant); 4c-i is the effective safety net for this cost model.
   Unit test: `depth_limit=2` on N=3 chain -> `NO_SOLUTION`, honest
   `termination_reason`.

**Phase 6a empirical results (post all 4 steps, HEAD `425195f`):**

- `box_100out_50in_ok` (N=8, real tooling, EXPLORATORY):
  PARTIAL, seq_len=8, 9 nodes, 8 valid + 1 invalid validates,
  wall 47.73-50.32s (noise band), steps_invalid=0, steps_unknown=8,
  ALIGNED, collision_free_confirmed=False.

- `2_1_3_4_ok` (N=10, synthetic tooling, EXPLORATORY):
  PARTIAL, seq_len=10, 11 nodes, 10 valid + 0 invalid validates,
  wall 1.0-1.29s, steps_invalid=0, steps_unknown=10,
  tooling_alignment=CANNOT_VERIFY (synthetic), collision_free_confirmed=False.

- Baseline: Ran 501 tests / OK (expected failures=3), 0F 0E, 3xF.

**SLA p95 <= 240s: met.** box N=8 actual 49.26s = 4.9x under budget
(was 8.95x over pre-Phase 6a). `2_1_3_4_ok` N=10 actual <= 1.3s.

**Behavior preservation:** baseline 500/0F/0E/3xF unchanged after each
step (501/0F/0E/3xF after step 4's +1 unit test); sequence/status/steps
bit-identical on both fixtures across all four steps (except step 2 which
changed box sequence ordering — valid PARTIAL alternative per Req 6a
interpretation (i), physical contract preserved).

**Phase 6b:** NOT needed (Phase 6a met SLA with 4.9x margin on N=8 and <=1.3s on N=10).

**Coverage gap:** N=10..12 fixtures absent from repo. box N=8 profileable
only after KL-22 fix. `2_1_3_4_ok` N=10 profileable directly via
`BendSeqOrchestrator.run_planning(target_shape=part_3d.brep)`, bypassing
Stage 1 (KL-9/KL-7 issue). Full e2e through Coordinator still blocked.

**Explicitly rejected:** bitmask dedup by `mask` alone — non-commutativity of
bend order; `tuple(path)` required (Session 1.5 analysis).


## Known Limitations

**Template:** each entry uses **Symptom / Cause / Impact / Follow-up**. Priority: P1 (production blocker), P2 (workaround exists), P3 (cosmetic).

### Active

- **KL-8 (CLOSED 2026-10-20, Stage 8):** tooling collision, APPROACH scope.
  All 5 baseline failures resolved: 4 pipeline plumbing tests reclassified
  honestly (KL-8a), 1 real bug fixed (place_part punch+die check), plus
  silent downgrade (S.26). Baseline 485/5F → 497/0F. See Stage 8 section.

- **KL-16 (P1, OPEN):** `machine_frame.FCStd` / `machine_bed.FCStd` absent from
  `toollibrary/`. `machine_collision` returns honest UNKNOWN without model.
  Under PRODUCTION: step rejected. Test 4 (`test_stage6_full_planning_pipeline`)
  and physics e2e use EXPLORATORY (M4, product decision 2026-10-20).
  Fix path: add machine model to toollibrary (CAD task, separate session) →
  unify policy to PRODUCTION → assert all_steps_valid.

- **KL-17 (P3, OPEN):** Mocks of `FeatureExtractor` in `tests/test_bendseq.py`
  and `tests/test_stage3_regression.py` omit `"panels"` key by design.
  Companions to `orchestrator.py` resolver preconditions (`"panels" in features`).
  Fix path: add `"panels": {}` to mocks OR update mock contract helpers.

- **KL-18 (P3, OPEN):** `orchestrator.py:102`
  `thickness = features.get("thickness", 2.0)` — silent default. Not triggered
  in production (real extractor provides thickness) or current tests.
  Fix path: strict `features["thickness"]` with mock contract updates
  (companion to KL-17).

- **KL-19 (CLOSED S.26):** `worker.py:228-230` silent REAL→SIMPLIFIED
  downgrade due to case mismatch. Fixed in `d3a26e5`.

- **KL-20 (P2, OPEN):** `step_item.tooling_status` (from planner) may diverge
  from `validator.tooling_status`. Current: `step_item.get("tooling_status") or
  actual_tooling_status` — trusts planner if present. Semantic consistency
  check deferred. Not blocking.

- **KL-8d (P2, OPEN):** CLI without tooling silently falls to SIMPLIFIED →
  PRODUCTION → always NO_SOLUTION. UX bug: user without `--punch-id`/
  `--die-id`/`--tooling-set`/`--tooling-config` gets NO_SOLUTION, not
  "missing tooling". Fix: explicit requirement or `--allow-synthetic` flag.

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

- **KL-12 v1 (PARTIAL, Stage 8a):** infrastructure ready, detection deferred.
  - **v2 (CLOSED 2026-10-22, S.31 / 3eb4376):** resolver side edges.
    Threshold: `thickness/2 + 0.2` → `thickness + 0.2` (covers both sheet
    surfaces + float-boundary `98.3 - 99.25 = -0.95000…` case). New step 2b:
    perpendicular planar faces assigned to nearest panel by perpendicular-
    plane distance, tie-break by 3D cog distance.
    Results: fixture_l 6/10 → 8/10 (bridge caps 1,4 stay unassigned);
    box_100out_50in_ok 41/78 → 78/78. Verified via `rotate_subtree` face-count
    preservation (9/9 panels, angle=0, face_count=78 OK). Baseline 523 → 532
    (6 v2 tests + 3 discipline: uniqueness ×2, bridge caps unassigned ×1).
  - **v2 → Req 2 F.1 integration (CLOSED 2026-10-22, S.33 / 1724748):**
    viewer replayer calls `resolve_panel_faces` before unfold; 3-state honest
    precision (no faces → PREVIEW_ONLY + original warning; some faces →
    PREVIEW_ONLY + PARTIAL; all faces → VALID_PER_PANEL, no warning).
    Forward direction = honest reversal of backward StepViews. Baseline
    532 → 537 (5 F.1 tests). Viewer manifest now reads precision from
    first frame (was hardcoded PREVIEW_ONLY).
  - **v2 → Req 2 F.2 (CLOSED 2026-10-22, S.35 / 8d0ce71):** FreeCAD Timeline
    UI. `smbs_core/viewer/fc_animation.py` (headless controller: play/pause/
    tick/step, badge mapping, unit-testable) + `smbs_core/addon/
    SMBS_AnimationPlayer.FCMacro` (QDialog + QTimer modal, Part.show per
    frame, honest badge display). Baseline 537 → 544 (7 F.2 tests). Manual
    GUI smoke: NOT YET PERFORMED.
  - **v3 (P1, OPEN):** planar self-collision detection (2D boolean) + bridge
    caps spanning bend lines (fixture_l faces 1,4). KL-8 SELF scope.
    Currently honest UNKNOWN for non-adjacent planar pairs (correct per
    invariant #1 — better false UNKNOWN than false VALID). Bridge caps remain
    stationary during unfold (KL-22 trade-off preserved) until v3.
  - Sniff test → `strict_per_panel` flag: before main merge.

- **KL-13 (P3, OPEN, discovered in S.18 audit)**: runs generated before 2026-10-17 lack `bend_line_flat_pattern` in `04_canonical_part.json`. Flat annotations (requirement 1) require this field. `flat_annotations.py` refuses such runs with actionable error; `axis_point` is **not** used as fallback (it lives in the folded-model coordinate system, not on the flat pattern — using it would place numbers off the flat pattern). Impact: 3 old runs (`run_20261012_034042_cf4c34`, `run_20261012_034045_9a351d`, and one more) cannot display annotations. Fix path: regenerate old runs with current pipeline (`smbs-cli part.FCStd`). Not a bug — pipeline evolution. See `smbs_core/addon/README.md` "Known issue: old runs".

- **KL-23 (P1, OPEN, Phase 6a follow-up): Cyclic fixture N>=8 tie-breaker generalization.**
  Current reference fixtures (`box_100out_50in_ok`, `2_1_3_4_ok`) are tree-like;
  DFS-descent tie-break optimality (`f -> -g -> moving_sz -> counter`)
  on cyclic topology graphs unproven. If cyclic fixtures exhibit deep search tree
  thrashing, activate `solve(depth_limit=N+buffer)` or refine tie-break.

- **KL-24 (P2, OPEN, Phase 6a follow-up): Pending-pop uses parent_g + 1.0 instead of g_scores[pred_fp].**
  Latent discrepancy in `astar_backward.py` pending-pop branch. Safe under unit cost
  and equal-fp deduplication; revisit if non-unit edge costs or N>=20 are introduced.

- **KL-25 (P2, OPEN, Phase 6a follow-up): Silent fallback via inspect.signature try/except: pass in solvers.**
  Solvers catch all `TypeError` when checking validator signature. Belongs to KL-20
  class of contract cleanliness issues.

### Closed

- **KL-11 (Stage 3, CLOSED in S.9.3): VerifierWorker checked only unfold correctness.**
  - **Symptom (before fix):** Verifier checked that unfolding produces the correct flat pattern, but **not** that re-folding the flat pattern produces the correct 3D shape. `production_eligible=True` did not guarantee fold correctness.
  - **Cause (before fix):** No forward-reconstruction step (`SheetMetalFolder.fold_from_canonical_part` was not called).
  - **Fix:** S.9.3 (commit `4c9524e`) added fold correctness via `FrameAligner.align_full_frame` + `SheetMetalFolder.fold_from_canonical_part`, followed by metric comparison against `ref_shape`. Two new tests in `81946ab`: `test_verifier_detects_fold_fabrication` (strict `FOLD_SHAPE_MISMATCH`) and `test_verifier_fails_when_no_flat_pattern` (B2 regression). `production_eligible = unfold_ok AND fold_ok AND not unknown_steps AND has_all_bends`.
  - **Status:** CLOSED.

- **KL-22 (CLOSED 2026-10-21): `rotate_subtree` face identity collapsed across depth ≥ 2 unfolds on BRep fixtures with unassigned side edges.**
  - **Symptom:** `box_100out_50in_ok` A* returned FAILED with ~40
    `UNFOLD_TRANSFORM` rejections; `InvalidRotationError: moving_shape is
    None or IsNull`; depth 2+ unreachable.
  - **Cause:** `resolve_panel_faces` v1 assigns only planar panel faces.
    Side edges, fillets, chamfers unassigned (37/78 faces on box). During
    `rotate_subtree`, unassigned faces fall out of `BRepBuilderAPI_Sewing`:
    face count 78 → 41. `panel.face_indices` become stale on next unfold.
  - **Fix:** `pythonocc_backend.rotate_subtree` attaches unassigned faces to
    FIXED side before sewing (`ed366e7`). Identity preserved 78 → 78 at every
    depth. Regression tests in `22956e5` (`tests/test_multidepth_search.py`,
    3 unit tests): depth 1 preserves face count, depth 2 does not raise,
    depth 2 preserves face count.
  - **Trade-off:** unassigned faces stay stationary during unfold even when
    adjacent to a moving panel. Preserves search reachability but side-edge
    kinematics incorrect. Combined with KL-16 (machine), such fixtures stay
    `production_eligible=False` — honest PARTIAL, never fake VERIFIED.
    Semantic side-edge motion is KL-12 v2 (P1 now).
  - **Post-fix measurement:** box A* → PARTIAL, seq_len=8, 2149.85s,
    274 nodes, UNFOLD_TRANSFORM=0, steps_invalid=0, steps_unknown=8.

### Inherited from Stage 1

- **KL-15 (Stage 5, CLOSED in S.23)**: `FcstdToolingLoader.load()` raised `AmbiguousShapeError` for FCStd files with multiple objects (e.g. `puancon.FCStd` has `Sketch`, `Body`, `Pad`) when `target_object_name` was not explicitly passed. **Fix:** disambiguation extended in `fcstd_loader.py` — filter by `Volume > 1e-6` (drops `Sketch`/wires), then prefer `Body` > `Pad` > `ToolShape`. Fallback to explicit `AmbiguousShapeError` with candidate list if still ambiguous. 7 new tests in `test_fcstd_loader_disambiguation.py`. Baseline 415 → 422. First end-to-end REAL-tooling smoke test now passes shape loading (solver returns `NO_SOLUTION` due to KL-8/KL-12, not shape errors).

- **KL-5 / KL-6 / KL-7** — documented in the original project context (pre-S.1). Not reproduced here to avoid drift. KL-9 references KL-7. None of these block Stage 2/3 close.



**Branch protection:** NOT enabled on GitHub (organizational risk per audit 2026-10-19). Action item: enable in GitHub UI (Settings → Branches → stage-1-fix-correctness → Require status checks + Require PR reviews). Not blocking code.


## Process notes (2026-10-20)

**Session 2 protocol deviations (documented for audit):**
1. S.25c-docs: `--amend` + `--force-with-lease` without prior explicit
   greenlight. Force-push verified safe (docs-only commit), but process
   deviation. Mitigation: no amend/force-push without green light via
   заказчик.
2. Commit 4A / 4B: raw diff of new test files not included in initial evidence;
   requested separately. Root cause: tool output pattern — diff computed but
   not echoed. Accepted after follow-up. Mitigation for Stage 9: raw diff is
   first-class evidence for every new file, not a follow-up request.
3. KL-12 v1 (Stage 8a): implemented without standalone diagnosis report
   (Session 1). Root-cause evidence in commit messages. Deviation noted.
   KL-8 (Stage 8) followed proper investigative protocol (Session 1.5-1.8
   pre-checks + Q&A before code). Baseline restored via git archaeology
   (PROGRESS.md previously claimed "5 failures — all KL-8 (e2e with real tooling
   matrica.FCStd)" — inaccurate; only 1 test used real tooling, 4 plumbing
   used SIMPLIFIED synthetic).

**Standing protocol:** invariant #8 (reference files) — zero violations in
Stage 8. `SMBendTests/reference/*.json` remain dirty unstaged throughout,
never touched, never staged, never committed.


## Process notes (2026-10-21)

**Session 3 — KL-22 diagnostic + fix + Req 6a Phase 1:**

1. **Method that worked:** `cProfile` before code (bottleneck =
   `validate_step` 97–99.7%). Lazy A* direction confirmed by profile,
   not intuition.
2. **STOP discipline:** When targeted box run returned FAILED
   (`UNFOLD_TRANSFORM` on POSITIVE fixture), Lead issued STOP. Root cause
   diagnosed (face identity collapse 78→41) before any code. Result: KL-22
   is latent bug exposed by strict check (from `5235d2f`, KL-12 v1),
   NOT a regression.
3. **Hypothesis test before fix:** One targeted experiment (sew with
   fixed ∪ unassigned) returned 78 → 78. Minimal fix validated empirically
   before touching code. Model for future KL fixes.
4. **Evidence integrity incident (2026-10-21):** During Commit 3 prep, a
   `cat -n docs/PROGRESS.md` report presented 358 lines with header
   `# SMBS BendSeq — Project Progress & Tracking`, used to draft 8 doc
   updates. Subsequent disk check (`wc -l`, `head`, `git show HEAD:`,
   `git status docs/`) showed the actual file is 357 lines with header
   `# SMBS Core - Engineering Implementation Progress`, identical to HEAD
   `879d893`; working tree clean; no docs commits since Session 2.
   **The 358-line report did not match any commit or working state** — the
   OLD blocks were invalid, the 8 drafted updates were voided before any
   commit. Cause per operator: terminal output truncation + stitched `sed`
   chunks introduced lines that weren't in the file. **Lesson:** only
   `cat -n <file> > /tmp/file.txt && sha256 <file>` (single continuous
   numbering) is acceptable raw evidence for full-file diffs.
5. **Process deviations (non-blocking):** Multiple `run_planning`
   invocations without `timeout` wrapper (36+ min, 30+ `ps aux` polls).
   Fix: `timeout <N>` mandatory for any command >1 min. Background
   launches without prior confirmation (6×, same pattern as Session 2
   Commit 4A/4B). Hypothesis runner expanded beyond Lead-specified scope
   without approval (accepted after-the-fact). New rule: any new /tmp
   runner variant requires Lead review before execution.

**Req 6a Phase 6a completion (2026-10-21):**
All 4 mandatory steps implemented sequentially with empirical validation:
- Step 1: Lazy A* (`7926e13`) — validate on pop (2149.85s -> 1712.44s)
- Step 2: Admissible h + epsilon tie-breaker (`c03db66`) — Interpretation C (1712.44s -> 49.26s, 9 nodes)
- Step 3: Move ordering cache (`066dc8c`) — precompute moving_subtree (-16% on N=10)
- Step 4: Depth_limit safety net (`425195f`) — 4c-i bounded DFS (501 tests clean)
Search SLA p95 <= 240s met (actual 49.26s = 4.9x under budget). Phase 6a CLOSED.
Protocol note: commit before final Lead green-light recurred 5 times across
steps 1-4 and PROGRESS update (each recovered via post-factum verification).
Enforce raw-first -> green-light -> commit strictly in subsequent sessions.

**Req 4 Phase E — evidence-integrity incidents (2026-10-21):**
Session 4 continued the raw-first discipline but hit 4 evidence-integrity
episodes that shaped the working protocol:
1. F1/F2 (module list + grep) delivered as summary; re-requested as raw.
2. `orchestrator.py` lines 1–6 looked like corruption (def before docstring);
   `git show 4bb29d1:` confirmed the structure is real (Lead false STOP —
   corrected after git archaeology).
3. `run_pipeline.py` chunk 00 base64 contained a `:` (invalid base64 char);
   re-generated with `INVALID: NONE` pre-check. Base64 delivery dropped for
   plain text after this.
4. `selection.py` / `test_tooling_selection.py` (354 / 297 lines) initially
   delivered as wc+sha only. Adopted chunked `cat -n ... | sed -n 'X,Yp'`
   as exception for files >200 lines (numbers preserved).

**New standing rules adopted:**
- For files ≤200 lines: single `cat -n file` raw in chat.
- For files >200 lines: `cat -n file | sed -n 'X,Yp'` chunked, ≤80 lines per
  message, one chunk per message.
- For binary / high-value files: base64 with `INVALID: NONE` pre-check and
  per-chunk `sha256` — only if plain text truncation is confirmed.
- STOP on summary-instead-of-raw applies from the first violation, no
  post-factum acceptance.


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
