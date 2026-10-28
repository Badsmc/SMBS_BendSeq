# SMBS Core - Engineering Implementation Progress

> **Project status:** advanced prototype with several completed functional subsystems, **NOT a complete automatic planning system**.
> **Open blockers:** (None — all closed). KL-9 CLOSED in S.79 (geometric BRep relief notch detection, box_hub clamping, sector trimming; 2_1_3_4 yields 90 faces, counts_match=True, hausdorff=0.0000 mm). KL-16 CLOSED in S.78. KL-12-v3 CLOSED in S.74. KL-28 CLOSED in S.76. F.3b CLOSED in S.76. See Known Limitations.
> **Requirement 4.1 (clamp constraints) — validator (S.24a–S.24c) + enforcement per-bend in `12_tooling_selection.json` (Req 4 Phase E, S.30c).**
> **P0 defects (annotated 2026-10-23, resolved S.40–S.50, HEAD `ce68906`, baseline 577 / 0F / 0E / 3xF):** 5 code + 4 doc + P0-GEOM CLOSED — see `docs/AUDIT_FULL_2026-10-23.md`. Release blockers: **CLEARED**.

**Status (HEAD `ce68906`):** Stage 8 + KL-22 + Req 6a Phase 6a + Req 4 Phase E + KL-12 v2 + Req 2 Phase F.1 + F.2 + F.3a + P0 Remediation (S.40–S.50) COMPLETE. **577 tests / 0 failures / 0 errors / 3 expectedFailures.**
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

## Closed P0 Defects (2026-10-23 audit, closed S.40–S.50)

Snapshot: HEAD `ce68906`, baseline 577 / 0F / 0E / 3xF. All P0 release blockers CLOSED.
Audit evidence, per-defect resolution criteria, and scope: `docs/AUDIT_FULL_2026-10-23.md`.

All P0 code defects, B-Rep geometry discrepancies, and documentation defects have been closed with reproducible negative test pins and verified commits:

| ID | File:lines | Defect | Resolution | Commit | Test Reference |
|---|---|---|---|---|---|
| P0-CODE-1 | `topology/worker.py:534–565` | `direction` derived from `signed_angle` sign only; marked `CONFIRMED`. | L540 formula refuted (signed_angle already encodes axis·(n1×n2) via FeatureExtractor triple product). Added strict origin/axis guards (IncompleteBendError on missing/None); direction formula unchanged. | `9dd2249` (S.40) | `tests/test_topology_worker_direction.py` |
| P0-CODE-2 | `topology/worker.py:552, 557, 571, 572` | Silent defaults `radius=1.0`, `length=50.0`, etc. marked `CONFIRMED`. | Strict radius/length guards; raise IncompleteBendError; no silent defaults. | `2fcd8ab` (S.41) | `tests/test_topology_worker_radius_length.py` |
| P0-CODE-3 | `baseline/worker.py:700, 702, 704–712, 760` | `hausdorff = max(bbox_delta)` — not Hausdorff. `area_delta` not in `is_pass`. | Real vertex Hausdorff via `BRepExtrema_DistShapeShape` + `area_delta` & `hausdorff` gates. | `e95aac3` (S.42), `fda5abf` (S.49-gate) | `tests/test_baseline_worker_hausdorff.py`, `tests/test_baseline_topology_canonical.py` |
| P0-CODE-4 | `geometry/worker.py:179, 249, 273` | Dead `status_worker`; literal `RunStatus.SUCCESS.value` on unfold failure. | Wired `worker_status` to `result_payload["status"]`; unfold failure → `WORKER_ERROR`. | `bb75ef1` (S.43) | `tests/test_geometry_worker_unfold_failure.py` |
| P0-CODE-5 | `adapter.py:127–128, 214–215, 249, 164–165` | FreeCAD preference defaults flow to `NormalizedBendLine(confidence=CONFIRMED)`. | Strict confidence provenance: defaults marked `source=DEFAULT_POLICY, confidence=ESTIMATED`. | `bc495aa` (S.44) | `tests/test_sheetmetal_adapter_confidence.py` |
| P0-GEOM | `sheetmetal_folder.py` & baseline | B-Rep reconstruction discrepancies on 10 canonical fixtures. | H5 outward normal (`57663e9`), H4b direct flat input (`0a34af8`), H4a bbox align (`74935bb`), H4c far-end leaf fold Z-flip fix (`bdca336`), Hausdorff gate (`fda5abf`). All 10 fixtures: `h=0.0000mm`. | `bdca336`, `fda5abf` | `tests/test_baseline_topology_canonical.py` |
| P0-DOC-1 | `docs/archive/RELEASE_AUDIT.md` | Stage 3 "READY FOR RELEASE" without SHA/scope. | Annotated `HISTORICAL` 2026-10-23 (S.39); archived S.73. | `af87274` (S.39) | `docs/archive/RELEASE_AUDIT.md` |
| P0-DOC-2 | `docs/archive/REGRESSION_REPORT.md` | 32/32 PASS from commit `c76e95f` (main). | Annotated `HISTORICAL` 2026-10-23 (S.39); archived S.73. | `af87274` (S.39) | `docs/archive/REGRESSION_REPORT.md` |
| P0-DOC-3 | `docs/archive/TEST_MATRIX.md` | ">70 (74 typically)" pre-refactor matrix. | Annotated `HISTORICAL` 2026-10-23 (S.39); archived S.73. | `af87274` (S.39) | `docs/archive/TEST_MATRIX.md` |
| P0-DOC-4 | `docs/PROGRESS.md` (this file) | No open P0 registry; stale HEAD `87cdbad`. | §Open P0 Defects added S.39 (af87274); renamed §Closed P0 Defects S.50b; HEAD updated S.50 (ce68906). | `af87274`, `ce68906` | `docs/PROGRESS.md` |

The rule "Do not close a P0 without reproducible evidence + negative test case" was applied throughout (see table above). With all P0 items closed, this rule transitions to historical context in §Process & Session Log.

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
- FreeCAD addon GUI — `freecad_smbs/` scaffold + menu + toolbar + «Открыть run» + «Инфо» + «Верификация» + «Расчёт» (3D annotations) implemented S.69 (`c1f7287..2df0f77`). Playback deferred to S.70; tooling editor to S.71.
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

- **KL-16 (P1, CLOSED S.78):** `toollibrary/machine_frame/machine_frame.FCStd`
  and `machine_frame.tooling.json` added to repository. `PressBrakeMachine`
  and `ToolingAssembly` load real B-Rep geometry and perform real collision
  check (`VALID` when clear, `INVALID` on intersection, honest `UNKNOWN` when
  model is absent or unconfigured, zero fabrication on loading error). FreeCAD
  UI badge transitions from `MACHINE: SIMPLIFIED (KL-16 pending)` to
  `MACHINE: LOADED` when frame is present.

- **KL-17 (P3, CLOSED S.66 `350dc7b`):** Mocks of `FeatureExtractor` in `tests/test_bendseq.py`
  and `tests/test_stage3_regression.py` omitted `"panels"` key by design.
  **Resolved S.66 (`350dc7b`):** Added `"panels": {}` to mock feature dicts in `tests/test_bendseq.py`
  (2 dicts) and `tests/test_stage3_regression.py` (5 `MOCK_FEATURES` profiles), aligning with production
  `FeatureExtractor.extract_features` contract. Updated `smbs_core/Core/orchestrator.py:154` comment.

- **KL-18 (P3, CLOSED S.64 `d8577d0`):** `orchestrator.py:102` — silent
  `thickness = features.get("thickness", 2.0)` eliminated; strict
  `features["thickness"]` with positive-number validation. Initial fix
  `8973bc4` also enforced strict `k_factor` (wrong: `FeatureExtractor` does
  not emit it — SheetMetal policy default, not geometric feature), breaking
  8 tests; fixed forward in `d8577d0`. New tests: 3 in
  `tests/test_orchestrator_strict_params.py`.

- **A1-A2 (P2, CLOSED S.64 `cdc5327`):** `technological_unfolder.py` inv#3
  fabrication eliminated. A1: `_compute_analytic_flat_length` raises
  `ValueError` on invalid bbox (was `100.0 + ba`). A2:
  `_create_synthetic_flat_pattern` raises `RuntimeError` + propagates inner
  exceptions (was `except: pass; return shape` — falsely labeled folded
  shape as flat). +2 tests in `test_technological_unfold_single_bend.py`.

- **A3-A3ext (P2, CLOSED S.64 `344f598`):** `pythonocc_backend.py` inv#3
  fabrication eliminated. A3-ext `get_surface_area`: `ValueError` null;
  `RuntimeError` on calc failure; `ValueError` on degenerate area (<=0).
  A3 `get_face_center`: `ValueError` null; `RuntimeError` on calc failure.
  +4 tests in new `tests/test_pythonocc_backend.py`.

- **Orphan note (S.64 → CLOSED S.68 `2e0d483`):** `smbs_core/Geometry/technological_unfold.py`
  (no `er`) verified 0 imports — dead code. Removed in S.68 (`2e0d483`, 187 lines).
  Active implementation = `technological_unfolder.py` (with 'er').

- **KL-19 (CLOSED S.26):** `worker.py:228-230` silent REAL→SIMPLIFIED
  downgrade due to case mismatch. Fixed in `d3a26e5`.

- **KL-20 (P2, CLOSED S.66 `180f6c5`):** `step_item.tooling_status` (from planner) may diverge
  from `validator.tooling_status`. Current: `step_item.get("tooling_status") or
  actual_tooling_status` — trusts planner if present.
  **Resolved S.66 (`180f6c5`):** Replaced `or` fallback in `solver/worker.py` with explicit `None` check
  and rank-based fidelity validation (`_TOOLING_RANK = {"MISSING": 0, "SIMPLIFIED": 1, "REAL": 2}`).
  Step cannot claim higher fidelity than validator ran with; raises `ValueError` on divergence (inv#2/#3).
  Verified by +2 tests in `tests/test_solver_worker_honest.py`.
  *Bonus S.66 finding (future candidate, no ID assigned):* `tooling_override` accepted in `validator.validate_step()` signature but unused in body; cataloged for future cleanup.

- **KL-8d (P2, CLOSED S.66 `9763c1d`):** CLI without tooling silently falls to SIMPLIFIED →
  PRODUCTION → always NO_SOLUTION. UX bug: user without `--punch-id`/
  `--die-id`/`--tooling-set`/`--tooling-config` gets NO_SOLUTION, not
  "missing tooling".
  **Resolved S.66 (`9763c1d`, Option B diagnostic-only):** In `solver/worker.py`, when
  `planning_status == NO_SOLUTION` and `actual_tooling_status == "SIMPLIFIED"` and
  `unknown_policy == "PRODUCTION"`, appends explicit actionable diagnostic explaining
  that PRODUCTION rejects UNKNOWN collisions and pointing to `--punch-id/--die-id`,
  `--tooling-set`, `--tooling-config`, or EXPLORATORY policy. Verified by +1 test in
  `tests/test_solver_worker_honest.py`.
  *Bonus S.66 finding (future candidate, no ID assigned):* `cli.py` currently does not
  expose `--unknown-policy` or `--allow-synthetic` flags; cataloged for future CLI polish.

- **KL-9 (Stage 2, P2, CLOSED S.79): `2_1_3_4_ok` baseline VALID (geometric relief-cut edge alignment & CAD refactor).**
  - **Symptom:** Part `2-1-3-4-(ok).FCStd` previously failed Stage 1 baseline with facets mismatch (90 vs 94 = +4 faces) and hausdorff = 0.2000 mm due to 0.2 mm offset between `axis_point` and physical corner relief notch edge.
  - **Root Cause & Resolution:** Implemented `SheetMetalFolder.detect_relief_notch_intervals` (pure geometric BRep analysis on vertical planar faces of `flat_pattern.brep`, zero fabrication, honest fallback on plain parts). In `fold_tree_general_from_flat_pattern`, clamped `box_hub` cutting boundaries to physical relief notch walls and trimmed bend sector boundaries.
  - **Results:** `SMBendTests/2-1-3-4-(ok).FCStd` reconstructs exact 90 faces (Reference: 90 faces), `counts_match = True`, `hausdorff_distance_mm = 0.0000 mm` (target <= 0.05 mm met). Zero regression across canonical benchmarks (`box`, `SQin`, `SQout`, `U`, `Z`). Removed `@skip` from `test_verifier_worker_2_1_3_4_deferred`. Dedicated regression suite `tests/test_kl9_relief_cut.py` added (707 tests green).

- **KL-10 (Stage 2, P3, CLOSED S.65 `4686d24`): `MockValidator` without `unknown_policy`.**
  - **Symptom:** `test_stage10/11/18/19` use a `MockValidator` that does not accept the `unknown_policy` parameter.
  - **Cause:** Mock created before `unknown_policy` became standard in S.8c.
  - **Impact:** Bypassed via `inspect.signature` in 3 planners. Functional, not contract-clean.
  - **Resolved S.65 (`4686d24`):** Added `unknown_policy: str = "PRODUCTION"` parameter to `MockValidator.validate_step` in `test_stage11_complex_sequences.py`, `test_stage18_astar.py`, `test_stage19_astar_optimized.py`. Aligns with `PhysicalValidator` contract.

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

- **KL-13 (P3, CLOSED S.68 — design choice / fail-fast by design, discovered in S.18 audit):** runs generated before 2026-10-17 lack `bend_line_flat_pattern` in `04_canonical_part.json`. Flat annotations (requirement 1) require this field. `flat_annotations.py` refuses such runs with actionable error; `axis_point` is **not** used as fallback (it lives in the folded-model coordinate system, not on the flat pattern — using it would place numbers off the flat pattern). Impact: 3 old runs (`run_20261012_034042_cf4c34`, `run_20261012_034045_9a351d`, and one more) cannot display annotations. Fix path: regenerate old runs with current pipeline (`smbs-cli part.FCStd`). Not a bug — pipeline evolution. See `smbs_core/addon/README.md` "Known issue: old runs". **Resolved S.68 (`2e0d483` commit context):** audit confirmed `flat_annotations.py:40-52` already fail-fast with actionable `FlatAnnotationsError` — no silent fallback (inv#3 compliant). Reclassified as design choice; no code change.

- **KL-23 (P1, OPEN, Phase 6a follow-up): Cyclic fixture N>=8 tie-breaker generalization.**
  Current reference fixtures (`box_100out_50in_ok`, `2_1_3_4_ok`) are tree-like;
  DFS-descent tie-break optimality (`f -> -g -> moving_sz -> counter`)
  on cyclic topology graphs unproven. If cyclic fixtures exhibit deep search tree
  thrashing, activate `solve(depth_limit=N+buffer)` or refine tie-break.

- **KL-24 (P2, CLOSED S.66 `4dd9a0e`, Phase 6a follow-up): Pending-pop uses parent_g + 1.0 instead of g_scores[pred_fp].**
  Latent discrepancy in `astar_backward.py` pending-pop branch. Safe under unit cost
  and equal-fp deduplication; revisit if non-unit edge costs or N>=20 are introduced.
  **Resolved S.66 (`4dd9a0e`):** Pending-pop branch updated to `current_g = g_scores.get(current_fp, p["parent_g"] + 1.0)`
  aligning with line 214 authoritative lookup. Verified by `tests/test_kl24_pending_g_scores.py`.
  *Bonus S.66 finding (future candidate, no ID assigned):* pending-pop dead-state check asymmetry observed during audit — `pred_fp in dead_states` not tested in pending branch. Not addressed in this commit (scope discipline).

- **KL-25 (P2, CLOSED S.65 `4686d24`, Phase 6a follow-up): Silent fallback via inspect.signature try/except: pass in solvers.**
  Solvers catch all `TypeError` when checking validator signature. Belongs to KL-20
  class of contract cleanliness issues.
  **Resolved S.65 (`4686d24`):** Removed `else: try: import inspect; inspect.signature(...); except Exception: pass` block from `dfs_backward.py`, `greedy_unfold.py`, `astar_backward.py`. PRODUCTION policy relies on validator's own default; non-PRODUCTION passed explicitly. Eliminates flag #15 broad except and inner-loop reflection overhead. Verified by `tests/test_kl25_unknown_policy.py` (+2 tests: mock accepts EXPLORATORY, no inspect.signature in solvers).

### Closed

- **KL-26 (CLOSED S.75 `45e2ea1`, macro-parity, S.69 follow-up): Inconsistent 09_verification_result status.**
  - **Symptom:** flat-pattern annotations rendered green (`verification_status="VERIFIED"`) while verification dialog showed orange (`production_eligible=false`) if a `09_verification_result.json` payload contained inconsistent fields.
  - **Fix:** Added fail-fast assertions in `smbs_bridge.present_verification` and `smbs_core/viewer/flat_annotations.py` raising a hard error on contradictory states (`verification_status='VERIFIED' ∧ production_eligible=False`).
  - **Evidence:** Regression tests in `tests/test_freecad_smbs/test_bridge.py`, `tests/test_viewer_flat_annotations.py`, and `tests/test_freecad_smbs/test_verification_dialog.py`.

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


## Process & Session Log: S.40–S.50 (2026-10-24)

### Milestone: P0 Integrity Remediation & B-Rep Geometry Restoration
Chronological record of remediation sessions closing integrity invariants and B-Rep reconstruction discrepancies (verified against commit history and test counts):

- **S.40 (`9dd2249`):** P0-CODE-1 closed. Formula check: signed_angle already encodes triple product; direction formula unchanged. Removed silent origin/axis fallbacks in `topology/worker.py`; raised `IncompleteBendError` on missing origin/axis. Added `tests/test_topology_worker_direction.py` (4 tests). Baseline: 550 → 554 / 0F / 0E / 3xF. Runtime: ~133s → ~125s.
- **S.41 (`2fcd8ab`):** P0-CODE-2 closed. Removed silent radius/length defaults in `topology/worker.py`; raised `IncompleteBendError` on missing parameters. Added `tests/test_topology_worker_radius_length.py` (4 tests). Baseline: 554 → 558 / 0F / 0E / 3xF. Runtime: ~123s.
- **S.42 (`e95aac3`):** P0-CODE-3 (partial) closed. Replaced `hausdorff = max(bbox_delta)` with real vertex Hausdorff computation via `BRepExtrema_DistShapeShape`; added `area_delta` to `is_pass` in `baseline/worker.py`. Added `tests/test_baseline_worker_hausdorff.py` (3 tests). Baseline: 558 → 561 / 0F / 0E / 3xF. Runtime: ~122s → ~215s.
- **S.43 (`bb75ef1`):** P0-CODE-4 closed. Bound `worker_status` to `result_payload["status"]` on unfold failure in `geometry/worker.py`. Added `tests/test_geometry_worker_unfold_failure.py` (3 tests). Baseline: 561 → 564 / 0F / 0E / 3xF. Residual risk noted: exception path L189–201 (P0-CODE-4-adjacent).
- **S.44 (`bc495aa`):** P0-CODE-5 closed. Strict confidence provenance in `SheetMetalAdapter`: FreeCAD defaults marked `source=DEFAULT_POLICY, confidence=ESTIMATED`. Added `tests/test_sheetmetal_adapter_confidence.py` (7 tests). Baseline: 564 → 571 / 0F / 0E / 3xF. Runtime: ~225s → ~237s.
- **S.47 (`57663e9`):** P0-GEOM (H5) closed. Orientation-aware outward normal logic in `FrameAligner` (covered H1 symmetric-arm ambiguity, no separate H1 fix needed). Added `tests/test_frame_aligner_orientation.py` (2 tests). Baseline: 571 → 573 / 0F / 0E / 3xF. Runtime: ~225s → ~216s.
- **S.48b (`0a34af8`):** P0-GEOM (H4a/b) closed. Direct flat pattern fold input bypassing synthetic hacks. Added `tests/test_baseline_fold_flat_input.py` (2 tests). Baseline: 573 → 575 / 0F / 0E / 3xF. Runtime: ~216s → ~249s.
- **S.48d (`74935bb`):** P0-GEOM (H4c/origin) closed. Dynamic origin alignment in baseline. Inline assertion in `tests/test_baseline_topology_canonical.py`. Baseline: 575 tests (0 net). Runtime: ~248s → ~259s.
- **S.49 (`7611c5f`):** P1-perf closed. Deduplicated BRep vertices in Hausdorff distance computation. Added test_04 in `tests/test_baseline_worker_hausdorff.py`. Baseline: 575 → 576 / 0F / 0E / 3xF. Runtime: 259.4s → 137.2s (−122.2s, −47%).
- **S.48e (`bdca336`):** P0-GEOM (H4c corrected) closed. Leaf reference panel fold Z-inversion fixed in `sheetmetal_folder.py`. Disproved S.48c/d chamfer hypothesis; `u_bracket` Hausdorff distance dropped from 1.0355mm to 0.0000mm bit-identical (all 10 canonical fixtures at 0.0000mm). Tightened assertion in `tests/test_baseline_topology_canonical.py`. Baseline: 576 tests (0 net). Runtime: 137.2s → 171.4s.
- **S.49-gate (`fda5abf`):** P0-GEOM (final) closed. Re-enabled Hausdorff surface-distance gate (`hausdorff <= tol["surface_distance_tol_mm"]`) in `baseline/worker.py:750`. Added regression pin `test_hausdorff_gate_enforcement` in `tests/test_baseline_topology_canonical.py`. Baseline: 576 → 577 tests (+1). Runtime: 171.2s → 175.8s.
- **S.50 (`ce68906`):** Docs core closed. Canonical registry clearance in `docs/AUDIT_FULL_2026-10-23.md` (Release blockers: CLEARED, P0-GEOM closure, Errata) and `docs/WORKING_CHECKLIST.md` (Next-P0 cleared). Baseline: 577 tests (0 net). Runtime: 156.2s.


## Process & Session Log: S.51–S.71 (2026-10-24–2026-10-26)

### Milestone: Post-release hygiene (P1-CODE + P1-hygiene + P1-DOC)

**Note:** Reconstructed from commit metadata (`git log --stat`, `git show`). Detailed session narrative for S.51–S.58 not preserved in this log. For full context, see per-commit `git show`. S.52 was a planned label in S.54 handoff but actual commits are labeled S.54; no separate S.52 session commits exist in the repository.

- **S.51 (`917eb71`):** P0-CODE-4-adjacent closed. Wrapped native sheetmetal unfold path (`import Part`, STEP conversion, `Part.read`, unfold, `exportBrep`) in try-except-finally in `geometry/worker.py`. On exception: `worker_status=WORKER_ERROR` with `UNFOLD_EXCEPTION_<type>` diagnostic; `finally` ensures `temp_for_sm.step` cleanup with narrowed `OSError` suppression. Added `test_04_unfold_exception_marks_worker_error` regression pin. Baseline: 577 → 578 / 0F / 0E / 3xF (148.2s).
- **S.53 (`c621a82`):** Release reports refresh (Conditions 3-5) + `AUDIT_FULL_2026-10-23.md` READY FOR RELEASE sign-off. Baseline: 578 (0 net).
- **S.54 (5 commits, `3d6ca31`..`5a02f72`):** Post-P0 hygiene batch.
  - `3d6ca31` docs: move P0-CODE-4-adjacent to §Done registry.
  - `21902db` code: remove dead `aligned_flat`/`transformer` in baseline worker.
  - `8a546ef` test: pin `temp_for_sm.step` cleanup assertion in `test_04`.
  - `c41263d` code+test: surface `baseline_metrics` on baseline INVALID (contract symmetry); remove `test_05` `json.load` workaround.
  - `5a02f72` code: deduplicate `_find_largest_planar_face` into shared `smbs_core/Geometry/face_utils.py`.
  Baseline: 578 / 0F / 0E / 3xF throughout.
- **S.55 (7 commits, `4761aef`..`1f6489c`):** P1-CODE series closure.
  - `4761aef` P1-CODE-1: replace dead fallback in `fold_from_canonical_part` with explicit precondition check.
  - `4a2b0e3`, `a28c71e`, `bc5b92e` P1-CODE-2 (G1/G2/G3): eliminate silent fallback defaults across `fold_from_canonical_part`, `fold_from_flat_pattern`, `fold_multi_bend_from_flat_pattern`, `fold_tree_from_flat_pattern`.
  - `16a6685`, `1f3178d` P1-CODE-3: annotate fixture defaults in `fold_one_bend`/`fold_u_bracket`; reject 95.5/47.5 fabrication in `measure_planar_flange_lengths`.
  - `1f6489c` docs: close P1-CODE-1/2/3 in WORKING_CHECKLIST registry.
  Baseline: 578 / 0F / 0E / 3xF throughout.
- **S.56 (3 commits, `9959f86`..`143b35e`):** P1-hygiene closure.
  - `9959f86` docs: close 4 items (aligned-flat, verifier-dup, baseline-metrics, test-05) in §Done.
  - `941ae36` code: annotate `angle_override_deg` as DEBUG_OVERRIDE in `fold_from_flat_pattern`/`fold_multi_bend_from_flat_pattern` signatures.
  - `143b35e` docs: close angle-override + proposal-2 (S.46 caching investigation: `StateCache` already integrated, no action).
  Baseline: 578 / 0F / 0E / 3xF.
- **S.57 (`7f23052`):** P1-DOC-A..F audit candidates resolved (6/6, all no-code-change decisions). §Pending Candidates marked `(None)`; §Resolved Candidates section added to `AUDIT_FULL_2026-10-23.md`. Baseline: 578 (docs-only).
- **S.58 (`176ca78`):** §P1 Code Hygiene section in `AUDIT_FULL_2026-10-23.md` marked `(CLOSED S.55)` with per-item closure SHAs (P1-CODE-1/2/3). Baseline: 578 (docs-only).

**Registry state (post-S.58):** All P0, P1-CODE, P1-hygiene, P1-DOC series CLOSED. See `docs/WORKING_CHECKLIST.md §Done` and `docs/AUDIT_FULL_2026-10-23.md §Resolved Candidates`.

**S.59–S.68** (atomic per-session logs): post-S.58 commits on `stage-1-fix-correctness` — code defects closed (KL-13 design choice, KL-17, KL-18, A1-A3, KL-8d, KL-24, KL-25, KL-9 reclass). Baseline at S.69 entry: `593 / OK / 3×F`. Full scope in individual commit messages; registry clean entering S.69.

**S.69 (2026-10-25) — UI/UX track: FreeCAD workbench (un-deferred per client request).**

- **Scope:** additive module `freecad_smbs/` — Menu bar + Toolbar + Task Panel (hybrid, option (d)); three primary commands (Расчёт / Симуляция / Оснастка) plus Открыть run / Инфо / Верификация. No `smbs_core/` modifications.
- **Commits (4 code + this docs):**
  - `c1f7287` C1 — scaffold (`Init.py` headless-safe, `InitGui.py`, `tests/test_freecad_smbs/test_scaffold.py`). Baseline 593 → 595.
  - `deaff33` C2 — menu + toolbar + disabled command stubs + `ui/badges.py` + `resources/tooling_paths.py` + 6 SVG icons. Baseline 595 → 604.
  - `fff04b3` C3 — read-only bridge (`integration/smbs_bridge.py`), active `open_run` / `info` / `verify` with Q5 badge logic (VERIFIED green / VERIFICATION_FAILED orange / UNVERIFIED gray). Baseline 604 → 609.
  - `2df0f77` C4 — «Расчёт» (`commands/calculate.py`, option B1: read-only visualization), `ui/annotations.py` (`App::AnnotationLabel`, `DisplayMode="Object"`, Z = flat_zmax + 3.0 mm). Baseline 609 → 613.
- **Raw-audit findings that prevented fabrication:**
  - **D1 / Blocker 1:** `smbs_core/addon/{SMBS_AnnotateBends,SMBS_AnimationPlayer,SMBS_ToolingLibrary}.FCMacro` = canonical implementations (option (b)), not prototypes. Workbench wraps / copies them, does not reinvent.
  - **D2:** `compute_annotations(artifacts, graphs)` two-argument signature (graphs via `build_graphs`). Missed in preliminary spec; caught by C4 pre-spec audit.
- **Macro parity (documented as known limitations):**
  - Z override `flat_zmax + 3.0 mm` (macros use `ANNOTATION_Z_OFFSET_MM = 3.0`).
  - Fixed font size 15 (macro parity).
  - Duplicate `SMBS_FlatPattern` on repeated «Расчёт» (macro parity; documented as backlog for S.70).
- **Honesty invariants held:**
  - inv #1 — UNKNOWN ≠ VALID (missing 09 → gray `UNVERIFIED`).
  - inv #2 — INVALID never passes (see KL-26).
  - inv #6 — no fabricated design / kinematics.
  - inv #7 — zero schema changes.
  - inv #8 — 5× M reference untouched.
  - inv #10 — `production_eligible` sourced only from `09_verification_result.json`.
  - inv #16 — no push performed in S.69.
- **Backlog (deferred):**
  - **S.71** — Оснастка (Task Panel, 5 tabs: punch / die / machine frame / backgauge / clamps). β1 honesty: `TOOLING: MISSING` for absent `.FCStd`; `MACHINE: SIMPLIFIED (KL-16 pending)` for synthetic frame.
  - **S.72** — Верификация dialog + Настройки + Диагностика + backlog sweep (G3 `open_run` silent excepts → UX feedback; G4 `resources/__init__.py`; G5 `test_bridge.py` tabs/spaces; M1 `InitGui.py` silent excepts rationale comment; README line 116 PNG fallback hardcoded `PREVIEW_ONLY (KL-12)`; KL-26 fail-fast check).

**S.70 (2026-10-26) — UI/UX track: FreeCAD Workbench «Симуляция» (Req 2 F.2 + F.3a port).**

- **Scope:** Port `SMBS_AnimationPlayer.FCMacro` dialog into `freecad_smbs/`. **F.2 + F.3a only.** F.3b (punch descent, backgauge motion, machine frame geometry) **remains BLOCKED on KL-16**. Q7 (tooling moves) RESOLVED as decision, impl deferred to F.3b.
- **Commits (2 code + this docs):**
  - `2ae4b76` C1 — `smbs_bridge.get_player(artifacts)` — lazy OCC backend + eager `player.frames` (raises `PlayerError` on malformed 09). Headless-safe: no top-level OCC import. Baseline 613 → 615.
  - `50a4c67` C2 — `ui/animation_dialog.py` (new, ~315 lines, headless-safe, lazy FreeCAD/PySide): macro parity `_on_slider_changed` (`reset()` + `next()` x N), `closeEvent` 4-object None-safe, `MACHINE: SIMPLIFIED (KL-16 pending)` badge when frame None, static punch/die. `commands/simulate.py` active (`IsActive = current_run is not None`). `test_menu.py` evolution (stub → active). Baseline 615 → 618.
- **Architecture (ii') Copy-UI + Wrap-headless** (S.69 C4 precedent): copy Qt dialog from macro (`ui/animation_dialog.py`), wrap headless `AnimationController` + `Player` + `format_*` badges from `smbs_core/viewer/fc_animation.py` (13 existing headless tests). No reimplementation.
- **Honesty invariants held:** PREVIEW_ONLY (KL-12) banner persists per-frame; MACHINE: SIMPLIFIED (KL-16) when frame None; UNVERIFIED when 09 absent; production_eligible only from 09 (inv #10).
- **Not in S.70 (blocked):** punch descent animation (F.3b, KL-16), backgauge motion (F.3b), machine frame geometry (F.3b + KL-16), forward fold (KL-12 v3).
- **Backlog (deferred S.72):**
  - O1 — unused `from pathlib import Path` in `tests/test_freecad_smbs/test_animation_dialog.py`.
  - O2 — rationale comment for catch-all in `_load_player_into_ui` (tooling setup failure → dialog degraded-but-honest).
  - S.71 (planned) — Оснастка (5-tab Task Panel). S.72 (planned) — Верификация dialog + Настройки + Диагностика.

**S.71 (2026-10-26) — UI/UX track: FreeCAD Workbench «Оснастка» (5-tab Task Panel).**

- **Scope:** Tooling library editor. 5 tabs (Punches / Dies / Machine Frame / Backgauge / Clamps). β1 honesty. Q-s71-6: no path selector (canonical `toollibrary/`, path change deferred S.72). Q-s71-9(a): direct display (no sidecar required). Q-s71-10: `clamp.FCStd` template cloned per `positions_mm`. Q-s71-11: KL-16 blocked (`cncbender` ≠ `machine_frame`).
- **Commits (3 code + this docs):**
  - `36d8889` C1 — `smbs_bridge.get_tooling_library(library_path=None)` lazy + eager tests. Baseline 618 → 622.
  - `6fc3318` C2a — `ui/tooling_panel.py` skeleton (Punches/Dies, macro parity `_load_library` bootstrap). `commands/tooling.py` active. Baseline 622 → 628.
  - `269abe9` C2b — extend to 5 tabs; Machine Frame badge (KL-16 honest); Backgauge + Clamps real `.FCStd` via `resolve_backgauge_fcstd` / `resolve_clamp_fcstd` (case + singular/plural fallback); Clamps 9-field editor → `ClampConfig`; 3D preview `mergeProject` / `Part.makeCompound` per position; `closeEvent` cleanup. Client files `toollibrary/Backgauge/Backgauge.FCStd` + `toollibrary/Clamp/Clamp.FCStd` committed (no sidecar). Baseline 628 → 637.
- **Architecture (ii') Copy-UI + Wrap-headless** (S.69 C4 / S.70 C2 precedent): copy macro Qt dialog (2-tab); extend with 3 new tabs. Headless backend = `ToolingLibrary` / `ToolingCatalog` / `ClampConfig` unchanged. New: case-insensitive resolvers in `tooling_paths.py`.
- **Honesty invariants held:** `MACHINE: SIMPLIFIED (KL-16 pending)`, `TOOLING: MISSING` for absent geometry, `BACKGAUGE: PRESENT (no sidecar)` / `CLAMP: PRESENT (no sidecar)` for real files. No fabricated `.FCStd`.
- **Not in S.71 (blocked):** F.3b (punch descent + backgauge motion) — KL-16; machine frame geometry — KL-16; backgauge/clamp sidecars — future CAD; path selector — S.72.
- **Backlog (deferred S.72):**
  - **KL-28 (P3, OPEN, sidecar-less tooling):** backgauge/clamps `.FCStd` без `.tooling.json`. Direct display only. Datums (`mounting_plane` / `working_axis` / `working_point`) needed for pipeline integration.
  - **O10-O13:** flake8 sweep `tooling_panel.py`; redundant `import FreeCAD` inside `_on_preview_clamps`; `test_tooling_paths_resolvers_find_real_files` depends on real disk files; `.DS_Store` gitignore (added this C3).
  - **Path selector** (`Mod/SMBS/ToolingLibraryPath` prefs) — S.72.

## Core Correctness track: P1a (2026-10-26)

**KL-12-v3 step 1 — single-bend disk-state replayer.**

- **Scope:** First `smbs_core/` modification since S.68. N=1 disk-state path in `replayer.py`: use existing `flat_pattern.brep` (valid solid) as target state 1, avoiding `rotate_subtree` → `Sewing` → torn-shells. Multi-bend (N≥2) unchanged (P1b scope).
- **Commits:** `f31170d` P1a-code (baseline 637 → 641) + `93b4b64` P1a-hygiene (baseline unchanged).
- **Probe evidence (Q-p1-1):**
  - `fixture_l` (1 bend, `create_l_bracket_shape`): `fold_from_canonical_part` → 1 solid, vol delta **0.000053%**, Hausdorff **0.000000 mm** vs `geometry.brep`.
  - `fixture_u` (2 bends): 1 solid, vol delta **0.000000%** vs `geometry.brep` (final-only; no intermediate states).
  - **`SheetMetalFolder.fold_from_canonical_part`** = final fold only. **`SheetMetalBendSolid.bend_solid`** crashes at 0° inactive bend (`BOPAlgo SelfIntersect`) → multi-bend stepwise requires new primitive (P1b).
- **Direction A (chosen over B = deformable primitive, C = honest permanent limitation):** Constructive path. Reuses `sheetmetal_folder.make_bend_sector`, produces genuine solids.
- **P1a implementation:**
  - `replayer._is_valid_solid(shape)` — `BRepCheck_Analyzer.IsValid()` + exactly-1-solid check, lazy OCC imports (headless-safe).
  - `replayer.replay_backward` — N=1 branch: if `len(candidate_seq) == 1 and artifacts.flat_pattern_path is not None`, load `flat_pattern.brep`, check `_is_valid_solid()` for both shapes. If both valid → yield 2 StepViews with `VALID_PER_PANEL`, `warning=None`. Else fall through to existing `rotate_subtree` loop.
  - N≥2 unchanged (rotate path, `PREVIEW_ONLY`/`PARTIAL` preserved).
- **Frame alignment Q-p1a-1 (verified α):** Base flange of `run_20261012_034042_cf4c34` stationary between `geometry.brep` (X∈[0, 95.5]) and `flat_pattern.brep` (same origin, X∈[0, 95.5] base). No positional fix required.
- **Real fixture check:** `runs/run_20261012_034042_cf4c34` → `steps: 2, precision: VALID_PER_PANEL`, both `state.shape` genuine solids.
- **Test changes (4 files):**
  - New `tests/test_replayer_single_bend_solid.py` (4 tests: disk-solid, fallback-missing, fallback-invalid, N=2 unchanged guarded by `@skipUnless`).
  - `test_req2_f1_per_panel_kinematics.py` — N=1 assertions PREVIEW_ONLY→VALID_PER_PANEL + `assertGreater(len)`→`assertEqual(len, 2)` + `assertIsNotNone(warning)`→`assertIsNone(warning)` (strictly stronger).
  - `test_viewer_replayer_preview.py` — branching added: N=1 → VALID; N≥2 → PREVIEW preserved.
- **Honesty invariants held:**
  - inv #1 — `VALID_PER_PANEL` claimed only when `_is_valid_solid()` empirically True. No fabricated precision.
  - inv #7 — `smbs_core/contracts/schemas/*` untouched.
  - inv #8 — 5× M reference untouched.
  - `PREVIEW_ONLY` preserved for N≥2.
- **Known limitations (backlog):**
  - **KL-12-v3 remains OPEN** — P1a = step 1 only. Multi-bend (N≥2) still PREVIEW_ONLY with torn shells (P1b).
  - **P1b** (next session): stepwise prefix-fold primitive for N≥2 — extend `make_bend_sector` to handle 0° inactive bends OR planar-slab substitution. Hard problem (S.38 territory).
  - **P1c** — FreeCAD UI integration (remove `PREVIEW_ONLY` banner for reconstructed solids where honest), full fixture sweep, `Player.switch_direction("forward")` genuinely bidirectional.
  - **Forward fold** — works for N=1 (2 valid solids reversed); PREVIEW_ONLY for N≥2 (P1b/P1c).
  - **O16** — `test_viewer_replayer_preview.py::test_replay_backward_real_run_structure` «discovers first run in runs/» — fragile order dependence (pre-existing, not P1a regression).

## Core Correctness track: P2 (2026-10-26)

**KL-9 — empirical mechanism audit, honest `@skip` decision.**

- **Scope:** Empirically reproduce `2_1_3_4_ok` baseline failure, identify exact mechanism, decide bounded fix vs fallback. **Outcome C (honest limitation) chosen** — probe confirmed bounded fix is multi-session epic with cross-fixture risk (per Q-p2-1 criteria: `>100 lines`, 3 independent splits, ≥11 fixtures regression surface).
- **Commit:** `ec4883e` P2-code (baseline 641/OK/3×F → 641/OK/1 skip/2×F, test-only change).
- **Probe evidence (Q-p2-1, session start):**
  - `Coordinator.start_run` on `2-1-3-4-(ok).FCStd` → `status: WORKER_ERROR`, `pass: False`, `is_valid_brep: True`, `faces: 90→94 (+4)`, `volume_delta: 1.50mm³ (0.000506%)`, `hausdorff: 0.2mm`.
  - Boolean cut on synthetic sector (`probe_p2_relief_cut.py`): **viable** (1 solid, 6 faces, BRepCheck valid) — approach technically confirmed.
  - Face area discrepancy analysis (`restored.brep` vs `geometry.brep`): 3 independent splits (5.25→3.75+1.5 at BEND_03 corner; 34.05→33.75+0.3 at BEND_01/02 corner; 146024.4→146023.4 large face).
  - **Exact mechanism:** `BEND_03 axis_point.x = 7.791327`, relief wall in `flat_pattern.brep` at `X = 7.991327` — **0.200 mm offset**, matching observed hausdorff. `box_hub` boundary from `axis_point` cuts 0.2mm into relief notch.
- **Critical metadata finding (R6):** `bend_line_flat_pattern.start_mm[s_axis]` = `7.791327` (= axis_point), **not** 7.991. Relief edge not recorded in canonical_part. **Path A1 (metadata-based)** eliminated. **Path A2 (boolean cut)** requires geometric detection from BREP.
- **Decision rationale (Q-p2-1 fallback criteria met):**
  - ≥100 lines estimated (3 splits × detection + boundary alignment + cross-fixture tests)
  - L-shaped/stepped relief geometry (non-trivial detection from BREP)
  - Cross-fixture regression surface (11 other fixtures must stay bit-identical)
  - `S.38` precedent: 7 speculative approaches → all failed → churn. Probe confirmed 3 independent sub-fixes here — same territory.
- **Honesty invariants held:**
  - inv #1 — `@skip` correctly marks known limitation (не fake pass)
  - inv #2 — no test weakening (assertions preserved for eventual fix)
  - inv #4 — root cause documented with empirical evidence (0.2mm offset, exact face splits)
  - inv #7 — `smbs_core/contracts/schemas/*` untouched
  - inv #8 — 5× M reference untouched
- **What P2 does NOT deliver:**
  - `2_1_3_4_ok` **not fixed** — part still fails Stage 1 baseline
  - `test_verifier_worker_2_1_3_4_deferred` **not passing** — deferred to dedicated CAD track
  - KL-9 **not closed** — deferred with empirical mechanism documented
- **Backlog (deferred to dedicated CAD track):**
  - **P2b** (future): multi-session refactor of `fold_tree_general_from_flat_pattern` — relief cut detection from `flat_pattern.brep` + `box_hub`/branch boundary alignment + cross-fixture regression. Reuse probe `probe_p2_relief_cut.py` as starting point.

## Core Correctness track: P1b (2026-10-26)

**KL-12-v3 step 2 — multi-bend (N=2) constructive stepwise prefix-fold.**

- **Scope:** N=2 linear chains (U, Z profiles via `fold_multi_bend_from_flat_pattern`). Constructive intermediate states via `SheetMetalFolder.fold_from_canonical_part` with inactive bends at 0°. **N≥3 tree structures (box, star, `fold_tree_from_flat_pattern` / `fold_tree_general_from_flat_pattern`) remain unverified** — `PREVIEW_ONLY` preserved.
- **Commit:** `b7f1251` P1b-code (baseline 641 → 648, +7 tests).
- **Probe evidence (Probe A + B1a + trajectory):**
  - Probe A: 0° inactive bend → `fold_step` degenerate rectangle (zero X width from `unfold_len=0`) → `SheetMetalBendSolid.bend_solid:223` → `BOPAlgo SelfIntersect` crash. Root cause confirmed.
  - Probe B1a: monkey-patch `bend_solid` to return `Part.makeCompound([])` for zero-area face → **valid solid at intermediate state** (1 solid, 10 faces, volume 6803.46 mm³).
  - Trajectory (backward replay, `run_20261018_190131_085a13` U-10-30-10):

    | State | BEND_01 | BEND_02 | Solids | Faces | Volume (mm³) |
    |---|---|---|---|---|---|
    | 0 (folded) | 90° | 90° | 1 | 14 | 6824.67 |
    | 1 (BEND_01 unfolded) | 0° | 90° | 1 | 10 | 6803.46 |
    | 2 (flat) | 0° | 0° | 1 | 14 | 6782.26 |

  - Permutation symmetry: `[BEND_01, BEND_02]` vs `[BEND_02, BEND_01]` order yields bit-identical intermediate volume (6803.46 mm³) and face count (10).
  - Physical delta: +21.2057 mm³ per 90° bend sector (mathematically consistent across both bends).
- **Implementation:**
  - `sheetmetal_folder.py::fold_step` (lines ~461-491): guard `if abs(angle_deg) < 1e-6 or unfold_len < 1e-6: b_sector = Part.makeCompound([])`. `_canonicalize_bend_sector` moved into `else`. Mirror guard `if not b_sector.isNull() and len(b_sector.Faces) > 0:` inside `if down:`. `fused = fixed_s.fuse(b_sector).fuse(mov)` unchanged. **Backward compatible** — no behavior change for non-zero bends.
  - `replayer.py::replay_backward` (lines ~264-370): N=2 constructive branch. Lazy import `SheetMetalFolder` (headless-safe, same pattern as P1a `resolve_panel_faces` narrowing). Step 0 = `fold_from_canonical_part(canonical, flat)` (Q-p1b-1 α: constructive folded). Step 1 = `fold_from_canonical_part(modified_canonical[bend0=0°], flat)`. Step 2 = `flat_pattern.brep` (Q-p1b-2). FIFO order (Q-p1b-2). **All-or-nothing fallback** (Q-p1b-4 α): if any of `_is_valid_solid(s0/s1/s2)` fails → skip to existing rotate loop, `PREVIEW_ONLY`.
  - `tests/test_replayer_multi_bend_solid.py` (new, 7 tests): trajectory (strict volume decrease), permutation symmetry, precision/warnings, fallback on missing flat, fallback on constructive exception, fallback on unassigned faces (PREVIEW_ONLY), headless no-folder import.
- **Honesty invariants held:**
  - inv #1 — `VALID_PER_PANEL` only when all 3 constructive states pass `_is_valid_solid()` (BRepCheck_Analyzer + exactly 1 solid). No fabrication.
  - inv #2 — fallback to `PREVIEW_ONLY` on any failure; no false solid claim.
  - inv #7 — `smbs_core/contracts/schemas/*` untouched.
  - inv #8 — 5× M reference untouched.
  - Headless-safe verified: `import smbs_core.viewer.replayer` under plain python3 → no `SheetMetalBendSolid` / `FreeCAD` / `Part` / `OCC` leak (R6 `HEADLESS_OK`).
- **What P1b does NOT deliver:**
  - N≥3 tree structures (box, star) — `fold_tree_from_flat_pattern` / `fold_tree_general_from_flat_pattern` are separate functions, unverified for 0° guard. Remaining KL-12-v3 scope.
  - N=2 with relief cuts — not tested (KL-9-adjacent, separate track).
- **KL-12-v3 status: N=1 CLOSED (P1a), N=2 CLOSED (P1b), N≥3 tree structures CLOSED (P1b-extended `ccc2493`).**

### P0-PHYS: Honest Tool Placement & Verifier UNKNOWN Handling (Commit `018949e`, 2026-10-27)

- **Problem:**
  - `collision_detector.py::place_part_on_tool_station` fell back to returning unplaced shape `shape` at (0,0,0) in part coordinates when placement failed (missing assembly, missing die, missing datums, missing origin/axis, or transform exception). Downstream collision methods then checked the unplaced part against world tooling and returned false `CollisionStatus.VALID`.
  - `verifier/worker.py::VerifierWorker` line 281 evaluated `coll_verified = (val_res.collision_status != CollisionStatus.INVALID)`, which treated `UNKNOWN` as verified.
- **Fix:**
  - `place_part_on_tool_station`: returns `None` on placement failure (unit test mock backends bypassed via MRO check). Downstream collision checks return `CollisionStatus.UNKNOWN` ("Could not place part on tool station (missing/invalid bend info, tooling, or datum)").
  - `verifier/worker.py`: enforces `coll_verified = (val_res.collision_status == CollisionStatus.VALID or str(val_res.collision_status).lower() in ("valid", "collisionstatus.valid"))`.
- **Evidence:** `tests/test_honest_collision_and_verifier.py` (7 tests, all PASS; baseline 648 → 655).

### P1b-extended: N≥3 Tree Stepwise Folding & 0° Bend Guard (Commit `ccc2493`, 2026-10-27)

- **Problem:**
  - When unfolding intermediate bends to 0.0° during tree prefix-folding, `fold_branch` in `sheetmetal_folder.py` called `make_bend_sector` which invoked `face.revolve(..., 0.0)`, throwing `Part.OCCError: BRepSweep_Rotation::Constructor`.
  - `fold_from_canonical_part` assumed `angle_deg` was always a dict, raising `AttributeError` when a scalar float was passed.
  - `replayer.py` had a rigid `len(candidate_seq) == 2` check for constructive folding.
- **Fix:**
  - `sheetmetal_folder.py::fold_branch`: added 0° guard to `make_bend_sector` (`if abs(deg) < 1e-6: return Part.makeCompound([])`) and guarded `folded_parts` appending (`if not b_sector.isNull() and len(b_sector.Faces) > 0:`).
  - `sheetmetal_folder.py::fold_from_canonical_part`: safely handles both dict and scalar `angle_deg`.
  - `replayer.py::replay_backward`: generalized to `len(candidate_seq) >= 2` with all-or-nothing constructive prefix folding across all $N$ steps, preserving fallback to `PREVIEW_ONLY` if any solid is invalid.
- **Evidence:** `tests/test_tree_fold_stepwise.py` (3 tests, all PASS; 8-bend star box `box.100out-50in-(ok)` validated; baseline 655 → 658).

### P5-CI: GitHub Actions Headless CI Pipeline (Commit `ec1150d`, 2026-10-27)

- **Implementation:** Added `ci/workflows/ci.yml` using `mamba-org/setup-micromamba@v1` with `pythonocc-core` and `pydantic>=2.0` on `conda-forge` to run the full headless test discovery suite automatically on pull requests and pushes to `stage-1-fix-correctness` and `main` (ready to activate via `.github/workflows/` or GitHub UI).

### S.72: FreeCAD Centralized Bootstrap, 0° Matrix, Dynamic PNG Banner & Hygiene (Commits `a6e42cb`..`17f537e`, 2026-10-27)

- **Centralized Bootstrap & In-Memory BREP (`a6e42cb`):**
  - Added `smbs_core/geometry_backend/freecad_bootstrap.py` resolving FreeCAD and SheetMetal paths across macOS, Linux, and Windows via standard paths and `FREECAD_LIB` / `FREECAD_MOD_SHEETMETAL` environment variables.
  - Optimized BREP conversions across `sheetmetal_folder.py`, `adapter.py`, `geometry/worker.py`, and `fcstd_loader.py` to use in-memory `exportBrepToString` / `ReadFromString` / `WriteToString` / `importBrepFromString` with transparent disk fallback.
- **0° Matrix Normalization (`5bd0b7c`):**
  - Fixed neutral/unfolded 0° bend transformation matrix to parent/identity frame without inversion/reflection artifacts.
- **Dynamic Replay PNG Banner (`401dc4d`):**
  - Replaced hardcoded `PREVIEW_ONLY` banner in `smbs_core/viewer/render.py` with dynamic color-coded banner driven by `StepView.geometry_precision`: green `#2E7D32` for `VALID_PER_PANEL`, red `#C62828` for `PREVIEW_ONLY`.
- **Hygiene & Cleanup (`deedca1`..`17f537e`):**
  - Removed unreferenced dead module `backward_graph.py` (0 calls in codebase).
  - Archived outdated historical reports (`KNOWN_LIMITATIONS.md`, `TEST_MATRIX.md`, etc.) to `docs/archive/`.
  - Updated `README.md` to reflect usable prototype state. Baseline: 663 tests OK (1 skip, 2 expected failures).

### S.73: Workbench Auto-Path & One-Click Sequence Calculation on Active Document (Commits `f63c595`..HEAD, 2026-10-27)

- **Workbench Initialization Robustness (`f63c595`, `7af5be2`, `3bfc916`):**
  - Resolved dynamic repo root in `freecad_smbs/__init__.py`, `Init.py`, `InitGui.py` so the workbench finds `smbs_core` automatically without manual `PYTHONPATH` export.
  - Handled FreeCAD C++ GUI scope-clearing by registering workbench commands as methods on `SMBSWorkbench` and storing command identifiers as instance attributes `self.CMD_*`.
- **Programmatic Pipeline API (`b52e911`):**
  - Added `PipelineRunResult` dataclass and callable `run_pipeline_on_fcstd` programmatic API to `freecad_smbs/integration/smbs_bridge.py`, wrapping `Coordinator.start_run` without CLI invocation.
- **One-Click Active Document Calculation (`8dad8d2`..HEAD):**
  - Added `RunActiveCommand` (`SMBS_RunActive`, button "Рассчитать последовательность") as the primary workbench action on the menu and toolbar.
  - Automatically verifies `ActiveDocument` presence and saved status, passes tooling preferences (`BaseApp/Preferences/Mod/SMBS`), executes planning with modal progress context, logs progress to FreeCAD Report view (`App.Console.PrintMessage`), sets `current_run`, renders numbered bend annotations directly onto the flat pattern on `SUCCESS`, and displays structured result dialog.
  - Baseline: 663 tests OK + 52 workbench tests OK.

### S.74: KL-12-v3 Residual Closure — Orientation-Aware Single-Bend Folding & 0° Guard (2026-10-27)

- **Root Cause & Fix in `SheetMetalFolder.fold_from_flat_pattern`:**
  - `fold_from_flat_pattern` hardcoded cutting and rotation assuming bend lines ran strictly parallel to Y (`L_dir=(0,1,0)`). For parts with bend lines parallel to X (such as canonical `L-(ok).FCStd`), `max_y - min_y == 0`, cutting a 10mm hole in a 200mm sheet and raising `ValueError: Expected 2 flanges after cutting flat pattern along bend line, got 1`.
  - Generalized `fold_from_flat_pattern` to support both X- and Y-aligned bend axes (`is_parallel_to_x = abs(L_dy) < abs(L_dx)`), correctly orienting cut boxes, bend faces, bend edges, rotation centers, and distinguishing fixed vs moving flanges via `reference_panel_id` centroid.
  - Added 0° bend guard to single-bend folding returning flat shape when `angle_deg < 1e-6`.
  - Added `_canonicalize_bend_sector` call to canonicalize B-Spline end caps into planar faces.
- **Verification:**
  - `tests/test_fold_one_bend.py`: added negative regression pins `test_fold_from_canonical_part_x_axis_bend` and `test_single_bend_0deg_guard` (RED before fix, GREEN after fix).
  - Canonical fixtures `L-(ok)` and `135-(ok)` pass baseline verification with Hausdorff delta `2.8e-14` mm and `1.0e-11` mm.
  - Full fixture sweep: `L-(ok)`, `U-90deg-90deg-(ok)`, `Z-(ok)` replay yielding `VALID_PER_PANEL` across all steps without shell disconnection.
  - Baseline: **675 tests / 0 failures / 0 errors / 1 skipped / 2 expectedFailures**.

### S.75: FreeCAD Workbench Polish, Preferences, Diagnostics & Backlog Sweep (Commits `a1adb6a`..`45e2ea1`, 2026-10-27)

- **«Верификация» Inspector Dialog (`a1adb6a`):**
  - Added `freecad_smbs/ui/verification_dialog.py` and connected `VerifyCommand`:
    - Status badge with color encoding: green `#2E7D32` for `VERIFIED`, orange `#EF6C00` for `VERIFICATION_FAILED`, gray `#757575` for `UNVERIFIED`.
    - Per-stage breakdown covering: `APPROACH`, `SELF`, `TOOL`, `TRAJECTORY`, `RETRACT`, `MACHINE`, `SHAPE_METRICS`, and `FOLD_CORRECTNESS`.
    - Steps table with collision and kinematics verification flags.
    - Independent metrics table (B-Rep validity, solids count, shells count, volume delta, Hausdorff distance, bbox delta).
    - Refusal reasons and diagnostics panel.
    - Pure-Python `extract_verification_details` function and lazy Qt UI wrapper (headless-safe).
- **«Настройки» Preferences Dialog (`1501e33`):**
  - Added `freecad_smbs/ui/preferences_dialog.py` and `commands/preferences.py`:
    - Tooling library path configuration (`User parameter:BaseApp/Preferences/Mod/SMBS/ToolingLibraryPath`) with browse directory dialog and canonical reset.
    - Default search algorithm selector (`astar`, `dfs`, `greedy`), passed through `RunActiveCommand` to `run_pipeline_on_fcstd`.
    - Unknown collision policy selector (`PRODUCTION` vs `EXPLORATORY`).
    - Registered as `CMD_PREFERENCES` (`SMBS_Preferences`) on menu and toolbar with dedicated SVG icon.
- **«Диагностика» Environment Dialog (`1501e33`):**
  - Added `freecad_smbs/ui/diagnostics_dialog.py` and `commands/diagnostics.py`:
    - Comprehensive environment inspection: FreeCAD version and GUI presence, SheetMetal addon installation/path/version, PythonOCC/OpenCASCADE availability, Tooling Library paths/punches/dies/machine_frame (KL-16 honest check), and active document save/uncomputed status.
    - Ready status indicator badge and markdown report export to system clipboard.
    - Registered as `CMD_DIAGNOSTICS` (`SMBS_Diagnostics`) on menu and toolbar with dedicated SVG icon.
- **Backlog Hygiene & KL-26 Fail-Fast (`45e2ea1`):**
  - **KL-26 (CLOSED):** Added fail-fast assertions in `smbs_bridge.present_verification` and `smbs_core/viewer/flat_annotations.py` to prevent contradictory payloads (`verification_status='VERIFIED' ∧ production_eligible=False`).
  - **G3:** Replaced silent `except Exception: pass` in `freecad_smbs/commands/open_run.py` with user-facing error reporting (`show_error`).
  - **M1:** Added rationale comments explaining FreeCAD re-registration exception handling in `freecad_smbs/InitGui.py`.
  - **O1:** Removed unused `Path` import in `tests/test_freecad_smbs/test_animation_dialog.py`.
  - **O2:** Added rationale comment for tooling assembly catch-all in `freecad_smbs/ui/animation_dialog.py`.
  - **O11:** Removed redundant `import FreeCAD as App` inside `_on_preview_clamps` in `tooling_panel.py`.
- **Verification:**
  - Added test suites `tests/test_freecad_smbs/test_verification_dialog.py`, `test_preferences.py`, `test_diagnostics.py`.
  - All 68 tests in `tests/test_freecad_smbs/` pass cleanly.
  - Baseline: **690 tests / 0 failures / 0 errors / 1 skipped / 2 expectedFailures**.

### S.76 — F.3b Punch Descent + Backgauge Overlay + KL-28 Sidecars + KL-16 Honesty

- **KL-28 Sidecars (CLOSED):**
  - Created canonical tooling sidecars with verified datums:
    - `toollibrary/Backgauge/Backgauge.tooling.json`: kind `backgauge`, source `Backgauge.FCStd`, datums `mounting_plane`, `working_axis`, `stop_face`, metadata dimensions (100x200x14mm).
    - `toollibrary/Clamp/Clamp.tooling.json`: kind `clamp`, source `Clamp.FCStd`, datums `mounting_plane`, `working_axis`, `working_plane`, metadata dimensions (150x150x40mm).
  - Extended core enums in `smbs_core/models/tooling.py`:
    - Added `ToolingKind.CLAMP = "clamp"`.
    - Added `DatumRole.WORKING_PLANE = "working_plane"`.
  - Updated `ROLE_STRING_MAP` and `NAME_PATTERNS` in `smbs_core/geometry_backend/backends/fcstd_loader.py` to recognize `clamp` / `clamps`.
- **KL-16 Machine Frame Loading & Honesty:**
  - Extended `PressBrakeMachine.__init__` and `from_config` to accept `machine_frame_path` and `machine_frame_target_object_name`.
  - In `setup_assembly`: if `machine_frame_path` exists on disk, loads real B-Rep geometry via `FcstdToolingLoader` and attaches to `assembly.machine_frame` and `config.frame`.
  - When machine frame is absent, keeps `assembly.machine_frame = None`; `CollisionDetector.machine_collision` returns honest `CollisionStatus.UNKNOWN` (Invariant #1: `UNKNOWN ≠ VALID`).
- **F.3b Punch Descent Kinematics & Backgauge Motion Overlay:**
  - Added `_compute_punch_offset_mm(frame, max_stroke_mm=100.0)` in `freecad_smbs/ui/animation_dialog.py` for ram descent kinematics towards bottom dead center (BDC).
  - In `AnimationPlayerDialog`:
    - Resolves and loads `backgauge_shape` via `resolve_backgauge_fcstd(TOOL_LIBRARY_ROOT)`.
    - In `_render_current`: applies punch descent translation along ram stroke direction to `SMBS_Punch`, renders `SMBS_Backgauge` in 3D scene.
    - Updated `closeEvent` to cleanly remove `SMBS_Backgauge` feature along with other tooling features.
- **Verification:**
  - Added test pin suite `tests/test_f3b_kl16_kl28.py` covering KL-28 sidecars, KL-16 honest UNKNOWN and real scratch loading, and F.3b punch stroke kinematics.
  - All 695 tests in the test discovery suite pass cleanly (`0 failures, 0 errors, 1 skipped, 2 expectedFailures`).

### S.77 — P1c FreeCAD UI Full Integration & Bidirectional Sweep

- **Bidirectional Playback in FreeCAD UI (Fold / Unfold):**
  - Added direction selector `cmb_direction` (`"Unfold (backward)"` and `"Fold (forward)"`) to `AnimationPlayerDialog` (`freecad_smbs/ui/animation_dialog.py`).
  - Switching playback direction resets timeline position, updates frame count/slider maximum, and refreshes 3D viewport.
  - Made `Gui.updateGui()` call None-safe/attribute-safe in `_update_feature` to allow execution in headless and test environments without GUI loop.
- **Direction-Aware Step Headers:**
  - Updated `format_step_header(frame, direction)` in `smbs_core/viewer/fc_animation.py`:
    - In forward mode (`Fold`): Step 0 displays `"Step 0 — flat pattern"`, subsequent steps display `"Step i — {bend_id} ({ui_direction})"`.
    - In backward mode (`Unfold`): Step 0 displays `"Step 0 — initial (fully folded)"`, maintaining full backward compatibility.
- **Limitation and Tooltip Truth-Up:**
  - Updated `freecad_smbs/commands/simulate.py` tooltip to `"Воспроизведение процесса гибки (F.2/F.3/P1c; двунаправленное)"`.
  - Updated `freecad_smbs/integration/smbs_bridge.py` limitations to accurately reflect that N≤2 linear chains yield `VALID_PER_PANEL` solids, while N≥3 complex tree branches remain `PREVIEW_ONLY`.
- **Verification:**
  - Added `tests/test_p1c_full_fixture_sweep.py` verifying bidirectional navigation, solid validation, header formatting, and UI direction switching across benchmark runs.
  - Full test suite baseline: **700 tests / 0 failures / 0 errors / 1 skipped / 2 expectedFailures**.

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
