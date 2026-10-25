# Roadmap: SMBS/BendSeq FreeCAD Integration

> **Updated:** 2026-10-23 (HEAD `91d07c0`; baseline **550**/0F/0E/3xF; KL-12 v3 INVESTIGATED → BLOCKED; Req 2 F.1/F.2/F.3a COMPLETE).
> **Release verdict: NOT READY.** Open P0 defects: P0-CODE-1..5 + P0-DOC-1..4. See `docs/AUDIT_FULL_2026-10-23.md` §Release Verdict and `docs/PROGRESS.md` §Open P0 Defects.

## 1. Executive Summary

### What can be started now (without approvals or clarifications)
- **Requirement 1** — logical bend sequence numbers on the flat pattern in FreeCAD.
- **FreeCAD workbench scaffolding** (as part of Requirement 1) — reused by Requirements 2, 3, 3.1, 4.
- Read-only research spike on the FreeCAD Qt API for annotations (no code commits).

### What requires user clarifications (blockers)
- **Q1** (Requirement 3): how to designate object roles in FCStd (punch/die/frame/backgauge).
- **Q2** (Requirement 4.1): exact math of punch retention by frame clamps.
- **Q3** (Requirement 4): definition of "optimal" tooling set (objective function).
- **Q13** (Requirement 5): scope of "further visualization".

### What requires Invariant #7 approval
- `contracts/schemas/tooling_manifest.schema.json` — extension for library (3), sets (3.1), clamps (4.1).
- `contracts/schemas/tooling_selection.schema.json` — new schema, selection output (4).
- Possibly `tooling_set.schema.json` / `tooling_station.schema.json` — clarify packaging under #7.

### What depends on KL-8 / KL-12
- **Requirement 2** — requires KL-12 fix (honest per-panel kinematics for animation).
- **Requirement 4** — benefits from KL-8 fix (collision-aware selection); basic geometric selection is independent.
- Requirements 1, 3, 3.1, 4.1 — not blocked by KL-8/KL-12.

### Key decisions and recommended order
1. Requirement 1 starts immediately — touches no contracts and does not depend on KL.
2. Tooling library (3) — foundation for 3.1, 4, 4.1, 2; starts after #7 and Q1.
3. Clamp constraint checker (4.1) precedes selection algorithm (4).
4. Animation (2) — after library (3) and KL-12 fix.
5. Requirement 5 remains an open block pending answer to Q13.

---

## 2. Phases

### Phase A: Foundation + Requirement 1 — flat pattern sequence numbers
- **Requirements:** 1
- **Grouping rationale:** workbench scaffolding is needed by all subsequent phases; Requirement 1 touches no schemas and does not depend on KL.
- **Estimate:** 3–4 sessions, 10–20 commits.
- **Required clarifications:** Q4, Q5 (important, not blockers — can start with defaults).
- **Required approvals:** none.
- **Risks:** FreeCAD Qt API for annotations; manual GUI testing; mapping `bend_id` → visual line.

### Phase B: Tooling library (Requirement 3)
- **Requirements:** 3
- **Grouping rationale:** foundation for 3.1, 4, 4.1, 2.
- **Estimate:** 4–6 sessions, 14–28 commits.
- **Required clarifications:** Q1 (blocker), Q8 (important).
- **Required approvals:** `tooling_manifest.schema.json` (extend).
- **Risks:** heuristics for object role detection in FCStd; external FCStd import API; persistence.

### Phase C: Tooling sets (Requirement 3.1)
- **Requirements:** 3.1
- **Grouping rationale:** logical extension of the library; can run in parallel with Phase D.
- **Estimate:** 3–4 sessions, 10–18 commits.
- **Required clarifications:** Q9, Q10.
- **Required approvals:** `tooling_manifest.schema.json` (extend) or new `tooling_set.schema.json` — clarify under #7.
- **Risks:** semantics of incomplete set; permissible punch changeover moment; UI complexity.

### Phase D: Clamp constraints (Requirement 4.1)
- **Requirements:** 4.1
- **Grouping rationale:** constraint checker is a prerequisite for correct selection (4); smaller in scope than C.
- **Estimate:** 2–3 sessions, 7–14 commits.
- **Required clarifications:** Q2 (blocker), Q12 (important).
- **Required approvals:** `tooling_manifest.schema.json` (extend) or new `tooling_station.schema.json`.
- **Risks:** ambiguous retention math; machine-specific config; geometric approximation of physical clamps.

### Phase E: Optimal tooling selection (Requirement 4)
- **Requirements:** 4
- **Grouping rationale:** uses library (B), sets (C), and checker (D).
- **Estimate:** 4–6 sessions, 14–28 commits.
- **Required clarifications:** Q3 (blocker), Q11 (important).
- **Required approvals:** new `tooling_selection.schema.json`.
- **Risks:** undefined objective function; assignment complexity; integration with search (Stage 2); manual validation on real parts.

### Phase F: FreeCAD animation (Requirement 2)
- **Requirements:** 2
- **Grouping rationale:** requires library (B) for tooling visuals and KL-12 fix for honest kinematics.
- **Estimate:** 4–6 sessions, 14–28 commits.
- **Required clarifications:** Q6, Q7.
- **Required approvals:** no new ones (uses `tooling_manifest` from Phase B); depends on Phase B #7.
- **Risks:** KL-12 fix alters `rotate_subtree` semantics — animation code must conform; QTimer and GUI thread-safety; positioning tooling relative to bend line.

### Phase G: Open block (Requirement 5)
- **Requirements:** 5
- **Grouping rationale:** open block, planning not possible before Q13.
- **Estimate:** TBD.
- **Required clarifications:** Q13.
- **Required approvals:** TBD.
- **Risks:** TBD.

---

## 3. Requirements in Detail

### Requirement 1: Flat pattern sequence numbers

- **What already exists:**
  - `smbs_core/viewer/run_loader.py` — loading `04/07/09.json` + `geometry.brep`.
  - `smbs_core/viewer/graph_builder.py` — `BendGraph` + `PanelGraph` from `04`.
  - `smbs_core/viewer/player.py` — reads `production_eligible` from `09`.
  - `07_search_result.json` — `candidate_sequence[i].bend_id` gives the logical sequence.
  - `04_canonical_part.json` — `bend_line_flat_pattern.{start_mm,end_mm}`, `axis_point`, `axis_direction`.
  - CLI pipeline (`smbs-cli part.FCStd`) — full run in `runs/run_XXX/`.
- **What to add:**
  - FreeCAD workbench: `smbs_workbench/` (or in existing structure) — toolbar + "Compute" command.
  - Command: launching the pipeline (or reading an existing run) + loading `04/07/09`.
  - Mapping `bend_id` → geometric flat pattern line via `bend_line_flat_pattern`.
  - Rendering numbers 1..N in FreeCAD 3D view (Draft/Annotation) or in TechDraw — per answer to Q4.
  - Honest marking: when `production_eligible=false` or missing `09` — `UNVERIFIED` badge (per answer to Q5, default — display with badge).
- **Dependencies:** none.
- **Estimate:** 3–4 sessions, 10–20 commits.
- **Risks:** FreeCAD Qt API; manual GUI testing; correctness of `bend_line_flat_pattern` mapping onto displayed geometry.
- **Definition of Done:**
  - User opens `part.FCStd` with SMBS workbench loaded.
  - Clicks "Compute" → numbers 1..N appear near bend lines on the flat pattern.
  - Order matches `candidate_sequence` from `07`.
  - Explicit badge on `UNVERIFIED`; without `09` — `UNVERIFIED`.
  - No crash when files are missing.
- **Required user clarifications:**
  - Q4: Where to display numbers — 3D flat pattern or 2D TechDraw? → defines API and UX. → Expected: 3D annotations on unfolded part, or TechDraw view.
  - Q5: What to display when `production_eligible=false`? → Invariant #1. → Expected: show with `UNVERIFIED` badge, or do not show — user decision; default — with badge.
- **Required approvals (Invariant #7):** none. Numbering is computed on the fly from `04`+`07`; no new artifact is created.
- **Relation to KL-8/KL-12:** not blocked. KL-8 affects `production_eligible`, but not numbering; KL-12 does not affect `bend_id` mapping.

---

### Requirement 2: Bending animation in FreeCAD

- **What already exists:**
  - `smbs_core/viewer/replayer.py` — step-by-step backward unfold (via `backend.rotate_subtree`).
  - `smbs_core/viewer/player.py` — timeline (reset/next/prev).
  - `smbs_core/viewer/render.py` — HLR + PIL → PNG.
  - `smbs_core/viewer/cli.py` — `view <run_dir>`.
- **What to add:**
  - FreeCAD animation: object movement along timeline (QTimer), synchronized with `candidate_sequence`.
  - Tooling visualization: punch, die, frame, backgauge — from library (Phase B).
  - Timeline UI in FreeCAD.
  - Integration with KL-12 fix — per-panel kinematics.
- **Dependencies:** Phase B (tooling library), KL-12 fix.
- **Estimate:** 4–6 sessions, 14–28 commits.
- **Risks:** KL-12 fix alters `rotate_subtree` semantics; GUI thread-safety; positioning tooling relative to bend line; performance on complex parts.
- **Definition of Done:**
  - User loads a run with tooling from the library.
  - Animation plays bend steps; punch, die, frame, backgauge visible and moving coherently.
  - Each step honest-marked; `UNVERIFIED` badge when applicable.
  - No `PREVIEW_ONLY` caused by KL-12 (KL-12 fix completed).
- **Required user clarifications:**
  - Q6: Animation speed/duration? → affects timeline. → Expected: e.g. 1 s per bend, configurable.
  - Q7: Should tooling move (punch descends, backgauge repositions) or be static? → complexity and realism. → Expected: punch descends, backgauge repositions between bends.
- **Required approvals (Invariant #7):** no new ones; uses `tooling_manifest` from Phase B.
- **Relation to KL-8/KL-12:** **requires KL-12 fix** (per-panel kinematics). KL-8 affects `production_eligible`, but animation plays with badge.

---

### Requirement 3: Tooling library (FCStd)

- **What already exists:**
  - `contracts/schemas/tooling_manifest.schema.json` — simple tooling contract.
  - `place_part_on_tool_station` — existing placement logic.
- **What to add:**
  - Library store: FCStd directory + metadata index (per #7).
  - FCStd import module: role detection (punch/die/frame/backgauge) — per Q1.
  - FreeCAD command to add tooling to library.
  - Persistence: location — per Q8.
- **Dependencies:** none (but requires #7 and Q1 before code).
- **Estimate:** 4–6 sessions, 14–28 commits.
- **Risks:** object role heuristics; external FCStd import API; store permissions/locality; geometry versioning.
- **Definition of Done:**
  - User adds FCStd to library via workbench.
  - Roles identified (auto + confirmation) — per Q1.
  - Library persistent across sessions.
  - Tooling list with dimensions.
  - Re-import updates existing record.
- **Required user clarifications:**
  - Q1: How to designate object role in FCStd? → defines import. → Expected: naming convention + UI confirmation, or object properties.
  - Q8: Where to store library — project / user config / both? → persistence and sharing. → Expected: user config by default, project override.
- **Required approvals (Invariant #7):** `tooling_manifest.schema.json` — extend: roles, geometry (FCStd path, object name), dimensions.
- **Relation to KL-8/KL-12:** not blocked.

---

### Requirement 3.1: Set from library

- **What already exists:** library from Phase B.
- **What to add:**
  - Notion of "set" — named collection of tools.
  - Selection UI: specific set or entire library.
  - Punch changeover logic: flag + proposed changeover point (per Q10).
- **Dependencies:** Phase B.
- **Estimate:** 3–4 sessions, 10–18 commits.
- **Risks:** incomplete-set semantics; permissible changeover moment; UI complexity.
- **Definition of Done:**
  - User creates named set from library.
  - Can select set or "entire library" for a run.
  - System flags bends requiring punch changeover.
- **Required user clarifications:**
  - Q9: "Set" — persisted artifact or temporary selection? → storage. → Expected: persisted artifact.
  - Q10: When punch changeover is needed — auto-planning or flag-only? → scope. → Expected: flag + proposed point, user confirms.
- **Required approvals (Invariant #7):** `tooling_manifest.schema.json` (extend) or new `tooling_set.schema.json` — clarify in #7.
- **Relation to KL-8/KL-12:** not blocked.

---

### Requirement 4: Tooling selection

- **What already exists:**
  - Stage 2 search — `candidate_sequence`.
  - `tooling_manifest` (after Phase B).
- **What to add:**
  - Selection algorithm: input — available punches (type, length, count) and bends; output — per-bend assignment (per Q3).
  - `smbs_core/tooling/selection.py` module (proposed path).
  - Integration with search (post-process or inside A*/DFS).
  - Use of constraint checker from Phase D.
- **Dependencies:** Phase B, Phase D.
- **Estimate:** 4–6 sessions, 14–28 commits.
- **Risks:** undefined objective function; assignment complexity; search integration; manual validation.
- **Definition of Done:**
  - For bend sequence and available punches, system produces per-bend assignment.
  - Assignment satisfies constraint checker (4.1).
  - Changeover report.
  - Result saved in `tooling_selection`.
- **Required user clarifications:**
  - Q3: What "optimal" means — minimum punches / changeovers / cost? → objective function. → Expected: minimum changeovers while satisfying all bends.
  - Q11: Length selection — per-bend or per-sequence? → algorithm. → Expected: per-bend considering neighboring bends.
- **Required approvals (Invariant #7):** new `tooling_selection.schema.json`.
- **Relation to KL-8/KL-12:** benefits from KL-8 fix (collision-aware selection); basic geometric selection independent.

---

### Requirement 4.1: Frame clamp consideration

- **What already exists:** nothing specific.
- **What to add:**
  - Clamp model: length, gap, count.
  - Constraint checker: punch must be reliably retained (per Q2).
  - Integration into selection (Phase E).
- **Dependencies:** Phase B (frame/clamp model in library).
- **Estimate:** 2–3 sessions, 7–14 commits.
- **Risks:** ambiguous math; machine-specific config; geometric approximation.
- **Definition of Done:**
  - Checker validates punch retention by clamps for given configuration.
  - Selection (4) uses the checker.
  - Invalid assignment rejected with clear reason.
- **Required user clarifications:**
  - Q2: Exact retention math. Gap 50 mm, punch 10 mm — "not retained". Need overlap ≥ X mm on each side? Or full containment in clamp zone? → constraint. → Expected: working length of punch fully supported by clamp zones (overlap ≥ X mm).
  - Q12: Can clamps be repositioned between bends? → solver. → Expected: fixed per sequence; repositioning = "changeover" event.
- **Required approvals (Invariant #7):** `tooling_manifest.schema.json` (extend) or new `tooling_station.schema.json`.
- **Relation to KL-8/KL-12:** not blocked.

---

### Requirement 5: Further visualization (open block)

- **What already exists:** TBD.
- **What to add:** TBD until Q13.
- **Dependencies:** TBD.
- **Estimate:** TBD.
- **Risks:** TBD.
- **Definition of Done:** TBD.
- **Required user clarifications:**
  - Q13: What's included in "further visualization"? (collisions, bending forces, export to machine formats, reporting?) → without this, planning is impossible. → Expected: list of prioritized features.
- **Required approvals (Invariant #7):** TBD.
- **Relation to KL-8/KL-12:** TBD.

---

## 4. Матрица зависимостей

| Требование | Зависит от требований | Зависит от KL | Можно параллельно с |
|---|---|---|---|
| 1 | — | — | 3 (разработка библиотеки) |
| 2 | 3 | KL-12 | — |
| 3 | — | — | 1 |
| 3.1 | 3 | — | 4.1 |
| 4 | 3, 4.1 | KL-8 (желательно) | — |
| 4.1 | 3 | — | 3.1, 1 |
| 5 | TBD | TBD | TBD |

---

## 5. Матрица согласований

| Требование | Требуемые уточнения (кол-во) | Требуемые согласования #7 (файлы) | Что можно начинать без них |
|---|---|---|---|
| 1 | 2 (важные, не blocker) | нет | ✅ можно начинать сейчас |
| 2 | 2 (важные) | нет новых (использует `tooling_manifest` из 3) | ❌ требует KL-12 + Phase B |
| 3 | 2 (1 blocker: Q1) | `tooling_manifest.schema.json` (extend) | ❌ сначала #7 + Q1 |
| 3.1 | 2 (важные) | `tooling_manifest.schema.json` (extend) или `tooling_set.schema.json` (new) | ❌ требует Phase B + #7 |
| 4 | 2 (1 blocker: Q3) | `tooling_selection.schema.json` (new) | ❌ требует Phase B, D + #7 |
| 4.1 | 2 (1 blocker: Q2) | `tooling_manifest.schema.json` (extend) или `tooling_station.schema.json` (new) | ❌ требует Phase B + #7 |
| 5 | 1 (blocker для 5: Q13) | TBD | ❌ открытый блок |

---

## 6. Открытые вопросы к пользователю

### Blocker (без ответа нельзя начинать код)

- **Q1** [требование 3]: Как обозначать роль объекта в FCStd (пуансон/матрица/станина/упор)? Почему blocker: импорт библиотеки и определение ролей невозможны без соглашения. Ожидается: соглашение об именовании + подтверждение в UI, либо свойства объекта.
- **Q2** [требование 4.1]: Точная математика удержания пуансона лапками. Почему blocker: constraint checker нельзя написать корректно. Ожидается: рабочая длина пуансона полностью поддерживается зонами лапок (перекрытие ≥ X мм).
- **Q3** [требование 4]: Что значит «оптимальный» набор? Почему blocker: целевая функция определяет алгоритм. Ожидается: минимум замен при удовлетворении всех гибов. **RESOLVED 2026-10-21 (Q3=A)** — implemented in `select_tooling` DP (S.30b-core).
- **Q13** [требование 5]: Что входит в «дальнейшую визуализацию»? Почему blocker для 5: без scope нельзя планировать. Ожидается: список приоритетных функций.

### Важные (влияют на дизайн, но можно начать с допущения)

- **Q4** [требование 1]: Где отображать цифры — 3D-развёртка или TechDraw? Дефолт: 3D-аннотации.
- **Q5** [требование 1]: Что показывать при `production_eligible=false`? Дефолт: цифры с бейджем `UNVERIFIED` (соответствует инварианту #1).
- **Q6** [требование 2]: Скорость/длительность анимации? Дефолт: 1 с на гиб, настраиваемо.
- **Q7** [требование 2]: Должна ли оснастка двигаться? Дефолт: пуансон опускается, упор переставляется.
- **Q8** [требование 3]: Где хранить библиотеку? Дефолт: пользовательский конфиг + проектный override.
- **Q9** [требование 3.1]: «Сэт» — сохраняемый артефакт? Дефолт: да.
- **Q10** [требование 3.1]: Замена пуансона — авто или флаг? Дефолт: флаг + предложенная точка, пользователь подтверждает.
- **Q11** [требование 4]: Подбор длины — per-bend или per-sequence? Дефолт: per-bend с учётом соседних гибов. **RESOLVED 2026-10-21 (Q11=C, sectional punch)** — supersedes "per-bend" default; implemented as `DEFAULT_SEGMENT_CATALOG = (10, 15, 20, 50, 100)` + `_compose_segments` greedy in `run_pipeline.py` / `selection.py` (S.30b-core / S.30c-integration).
- **Q12** [требование 4.1]: Могут ли лапки переставляться между гибами? Дефолт: фиксированы; перестановка = «замена».

### Nice-to-have (можно уточнить позже)

- Детали визуализации оснастки (цвета, подписи), детали UI библиотеки (поиск/фильтры), формат отчёта о заменах.

---

## 7. Суммарные оценки

- **До MVP (только требование 1):** 3–4 сессии, 10–20 коммитов.
- **До полного roadmap (1–4.1, 2; без 5):** 20–29 сессий, 69–136 коммитов.
- **До production-grade (с KL-12 fix и KL-8 fix):** +4–7 сессий → 24–36 сессий.

---

## 8. Рекомендованный порядок (если отличается от пользовательского)

> **Revised order (2026-10-23, Req 2 F.3a close):**
> KL-12 v1 ✅ → KL-8 ✅ → KL-22 ✅ → Req 6a ✅ → Req 4 ✅ → KL-12 v2 ✅ (S.31) →
> Req 2 F.1 ✅ (S.33) → Req 2 F.2 ✅ (S.35) → **Req 2 F.3a ✅ (S.37)** →
> **Req 2 F.3b (punch descent + backgauge + machine frame; after KL-16) или
> KL-12 v3 (planar self-collision + bridge caps + panel shell connection)** →
> Req 6b (only if 6a misses SLA).
> KL-12 v2 (side edges) CLOSED in S.31 (3eb4376): fixture_l 8/10, box 78/78.
> Req 2 F.1 (headless per-panel kinematics) CLOSED in S.33 (1724748):
> replayer calls `resolve_panel_faces` before unfold; 3-state honest
> precision (PREVIEW_ONLY / VALID_PER_PANEL, conditional warning); forward
> direction = honest reversal. Baseline 532 → 537. Bridge caps (fixture_l
> faces 1,4) deferred to v3 — box-like parts remain `production_eligible=False`
> until v3 lands, but this does NOT block Req 2. Correction (to S.32):
> bridge caps are NOT universally absent on L-family —
> `run_20261012_034042_cf4c34` (real L-part) has them (8/10); 3-state warning
> marks such fixtures honestly.
> Req 2 F.2 (FreeCAD Timeline UI) CLOSED in S.35 (8d0ce71): headless
> AnimationController (`smbs_core/viewer/fc_animation.py`) + QDialog/QTimer
> `smbs_core/addon/SMBS_AnimationPlayer.FCMacro`. Baseline 537 → 544.
> Req 2 F.3a (tooling visualization, punch/die) CLOSED in S.37a-f
> (814eb19..87cdbad): PressBrakeMachine.with_default_tooling() +
> ToolingAssembly.place(); punch/die as persistent Part::Feature; machine
> frame badge SIMPLIFIED (KL-16 pending); tooling_placement (a) on-the-fly,
> schemas unchanged (#7 clean); baseline 544 → 550. First real GUI smoke
> revealed 4 pre-existing F.2 bugs (part_shape, Part.Shape(occ) C++ abort,
> initial render missing, GUI refresh after .Shape update) — all fixed.
> Manual GUI smoke: PERFORMED (S.37 smoke 7 — PASS on tooling overlay;
> shell disconnection at Step ≥1 is pre-existing KL-12 v3, see below).
> Known follow-up: render.py BANNER_TEXT hardcoded — PNG fallback still
> marks PREVIEW_ONLY even when `geometry_precision=VALID_PER_PANEL`;
> deferred to S.37 (separate fix); does not block DoD Req 2 (viewport is
> source of truth).
> KL-12 v3 (planar self-collision + bridge caps + panel shell disconnection)
> tracked under KL-8 SELF / Req 4 enforcement. Extended scope (S.37 smoke):
> after first unfold, single solid → TopoDS_Compound of disconnected shells
> (L/U/Z, diagnostics: solids 1→0, shells 1→2→3→4, 22 faces preserved).
> Pre-existing replayer behaviour, visible at PREVIEW_ONLY. Not F.3a
> regression; F.3b visual polish may also touch this.
> Original §8 order (requirement 4 → animation → KL-12/KL-8 backlog) superseded.
>
> Rationale: KL-22 uncovered during Req 6a profiling — depth 2+ unfolds failed
> on all BRep fixtures because resolver v1 leaves 37/78 faces unassigned.
> Fix preserves face identity (unassigned faces attached to fixed side).
> Without KL-22 fix, Req 6a metrics cannot be measured (SOLVED required,
> not faster failure). Post-fix baseline on box N=8: 2149.85s, 274 nodes,
> SLA p95 target 240s → ×8.95 over budget.
>
> Phase 6a scope refinement (2026-10-21): all 4 items mandatory:
> 1. Lazy A* (defer 3D to pop) — necessary (~8× speedup implied by profile)
> 2. Admissible heuristic + ε tie-breaker — required for node count
> 3. DFS Prefix Pruning + Move Ordering — required
> 4. DFBnB upper bound — required
> Lazy A* alone brings box N=8 to ~270s — on SLA boundary, not under it.
> Phase 6b (precedence DAG, ARA*, Beam, Reverse Search) only if 6a misses.

**Пользовательский:** 1 → 2 → 3 → 3.1 → 4 → 4.1 → 5.

**Рекомендованный:** 1 → 3 → 4.1 → 3.1 → 4 → 2 → 5.

**Обоснование:**
- 1 — без зависимостей и согласований, даёт ценность немедленно.
- 3 — фундамент для 3.1, 4, 4.1, 2.
- 4.1 — constraint checker нужен для корректного 4; меньше по объёму, чем 3.1.
- 3.1 и 4.1 можно менять местами.
- 4 — после 3 и 4.1.
- 2 — после 3 и KL-12 fix; может стартовать параллельно с 3.1/4, если KL-12 fix готов.
- 5 — остаётся открытым до Q13.

---

## 9. Что можно начинать прямо сейчас без единого согласования

- **Требование 1** — целиком.
- **FreeCAD workbench scaffolding** (в рамках требования 1) — будет переиспользован требованиями 2, 3, 3.1, 4.
- **Research spike** по FreeCAD Qt API для аннотаций (read-only, без коммитов кода).

---

## 10. Интеграция в проект

- **Файл:** `docs/ROADMAP.md` — living document.
- **Связь с `docs/PROGRESS.md`:**
  - В `PROGRESS.md` после Stage 4 detail, перед Known Limitations, добавляется раздел `## Future Stages` со строкой:
    `See docs/ROADMAP.md for the full integration roadmap (FreeCAD UI, tooling library, optimal tooling selection). Requirements 1–5 with dependencies, estimates, and open questions.`
  - Краткое резюме: MVP (требование 1) можно начинать немедленно; требования 2–5 требуют #7 и/или KL-12.
- **Связь с `docs/ARCHITECTURE.md`:**
  - Расширение секции Stage 4 Viewer (Layer 5) или добавление `### Future extensions (Layer 6+)` со строкой:
    `See docs/ROADMAP.md for details. Layers 6+ cover FreeCAD GUI integration (bend annotations, tooling animation), tooling library (FCStd import), optimal tooling selection (with clamp constraints). Depend on KL-12 fix for honest per-panel kinematics.`
- **Правила обновления:**
  - Когда требование закрыто — строка перемещается из Roadmap в `PROGRESS.md` как новый Stage (или часть Stage). Roadmap уменьшается, PROGRESS растёт.
  - Roadmap не дублирует PROGRESS; он хранит только будущее и открытые вопросы.
- **Что НЕ входит в интеграцию:**
  - Roadmap не меняет `contracts/schemas/*` — любые изменения проходят через отдельные #7-согласования, перечисленные в матрице.
  - Roadmap не добавляет тесты — тесты добавляются в code-коммитах.
  - Roadmap не планирует KL-8/KL-12 fix — только фиксирует зависимость.

---

*Статус: черновик на review. После принятия — интегрируется аддитивно в `docs/` без изменения схем и тестов. Baseline 350/5F/0E/3xF не сдвигается.*

---

## 11. Coordinator addendum (post-review, decisions recorded)

This section records user answers and coordinator notes added after the initial roadmap draft. It does not change phases, dependencies, or estimates in sections 1–10; it resolves open questions and records deferred #7 decisions.

### Resolved user questions

- **Q4 (requirement 1)** — **RESOLVED: 3D annotations** on the unfolded part, not TechDraw. Rationale: matches user mental model ("на развёртке появятся цифры"), faster to implement, honest marking visible in 3D viewport.
- **Q7 (requirement 2)** — **RESOLVED: tooling moves** during animation — punch descends on bend, backgauge repositions between bends. Rationale: user expects "показ оснастки" as motion, not static props.

These answers override the "Важные, можно с допущением" classification of Q4/Q7 in Section 6. Q4/Q7 are now **closed** and should not be re-asked in future sessions.

### Resolved user questions (Q1 added 2026-10-18)

- **Q1 (requirement 3)** — **RESOLVED: fallback chain (option D)**:
  1. Read FCStd object property `SMBS_Role` (`punch`/`die`/`frame`/`backgauge`).
  2. If absent, read object name (`PUNCH`/`DIE`/`FRAME`/`BACKGAUGE`).
  3. If neither, prompt user with dropdown in import dialog.
  Rationale: property (explicit) overrides name (implicit); UI fallback prevents automation from blocking. Q1 is no longer a blocker for Phase B.

### Pending #7 decisions (deferred to their phases)

- **`tooling_selection.json` location** (Phase E) — run-dir artifact (`runs/run_XXX/tooling_selection.json`) vs separate library store (`library/selections/<name>.json`). Decision deferred to Phase E start.
- **`tooling_placement` for animation** (Phase F) — (a) compute on-the-fly from `tooling_manifest` + `candidate_sequence` + `bend_line_flat_pattern` (no new schema, no #7) vs (b) persist to `tooling_placement.json` per run (new #7). Recommendation: (a) on-the-fly for MVP. Decision deferred to Phase F start.
- **`tooling_set.schema.json` vs extending `tooling_manifest.schema.json`** (Phase C) — new file vs extend existing. Decision deferred to Phase C start.

### Coordinator note on estimates

Requirement 1's estimate in Section 2 (Phase A: 3–4 sessions, 10–20 commits) is a **risk-adjusted upper bound**. Coordinator's narrower MVP scope (3D annotations only, workbench scaffolding shared with later phases) estimates **~1.5 sessions / 3–5 commits**. Treat Phase A's number as a buffer for FreeCAD Qt API surprises, not as a floor.

### Coordinator decision log (2026-10-18)

- **KL-12 (per-panel kinematics)** — deferred to backlog. H2 approach (runtime face_indices enrichment) tested and rejected: `rotate_subtree` loses cylinder faces on multi-step unfold, invalidates indices after step 1, and `intersect_volume` on planar compounds = 0. Fix requires either bbox approximation (Variant A, ~1 session, honest approximation marking) or solid-preserving split+fuse (Variant B, ~2–3 sessions, full KL-12 closure). **KL-12 does not block requirements 3, 3.1, 4, 4.1** — only requirement 2 (animation). Roadmap §8 order honored: requirements 3 → 4.1 → 3.1 → 4 → 2 → 5.
- **KL-8 (real tooling collision)** — blocked by KL-12. Approach-stage die collision fixed in S.19a/S.19a2 (`place_part_on_tool_station` now uses die V-groove axis + mounting_plane normal, tries both axis signs). Next stage `SELF` returns UNKNOWN because `face_indices` is empty → step rejected under PRODUCTION → NO_SOLUTION. KL-8 fix resumes when KL-12 is closed.
- **Current focus:** **requirement 3 (Phase B — Tooling Library)**. Q1=D resolved (fallback chain: property → name → UI). Q8 pending user answer (library storage location). #7 agreement required for `tooling_manifest.schema.json` extension before Phase B code.

### Phase B agreement (2026-10-18)

**#7 — APPROVED with minimal scope (3 optional fields):**

```diff
--- a/smbs_core/contracts/schemas/tooling_manifest.schema.json
+++ b/smbs_core/contracts/schemas/tooling_manifest.schema.json
@@ -62,6 +62,9 @@  (payload.punch)
             "tooling_status": { "type": "string", "enum": ["REAL", "SIMPLIFIED", "MISSING"] },
             "model_path": { "type": ["string", "null"] },
             "model_hash": { "type": ["string", "null"] },
+            "target_object_name": { "type": ["string", "null"] },
+            "smbs_role": { "type": ["string", "null"], "enum": ["punch", "die", "frame", "backgauge", "ram", "bed", null] },
+            "smbs_role_source": { "type": ["string", "null"], "enum": ["property", "name", "sidecar", "inferred", null] },
             "segment_layout_mm": {
@@ -80,6 +83,9 @@  (payload.die)
             "tooling_status": { "type": "string", "enum": ["REAL", "SIMPLIFIED", "MISSING"] },
             "model_path": { "type": ["string", "null"] },
             "model_hash": { "type": ["string", "null"] },
+            "target_object_name": { "type": ["string", "null"] },
+            "smbs_role": { "type": ["string", "null"], "enum": ["punch", "die", "frame", "backgauge", "ram", "bed", null] },
+            "smbs_role_source": { "type": ["string", "null"], "enum": ["property", "name", "sidecar", "inferred", null] },
             "segment_layout_mm": {
```

**Rationale:**
- `target_object_name` — already used in `run_pipeline.py:184,198`, missing from schema. Real contract bug.
- `smbs_role` + `smbs_role_source` — needed to record Q1=D fallback chain outcome in the artifact (otherwise role resolution is invisible to downstream consumers).
- All three fields are **optional** and `type: ["string", "null"]` → backward-compatible with all 41 existing manifests.
- `mounting_type` (PROMECAM/AMADA/TRUMPF/WILA/...) — **REMOVED from proposal**: speculative, no consumer code, no UI justification. Backlog if needed later.

**Q8 — APPROVED with MVP scope (2 levels):**

- **Level 1:** `<repo>/toollibrary/` — built-in, demo, tests (default fallback).
- **Level 2:** `--library-path <path>` CLI argument — explicit override.
- **Deferred:** `~/.smbs/tooling/` user-global path (cross-platform complexity; add when real user has >10 tools).

**Q1=D — 4-level fallback chain confirmed (property API works in headless):**

Test verified: `App.openDocument()` + `obj.PropertiesList` + `obj.addProperty("App::PropertyString", "SMBS_Role", ...)` all work headless. Chain:
1. `property` — read `SMBS_Role` from FCStd object property.
2. `name` — read object name (`PUNCH`, `DIE`, `FRAME`, `BACKGAUGE`, `RAM`, `BED`).
3. `sidecar` — read `.tooling.json` (existing behavior).
4. `inferred` — heuristic BBox + `confirmed=False`.

**Phase B revised estimate (honest, post-KL-12 lessons):**

Original roadmap: 4–6 sessions / 14–28 commits. **Coordinator revision: ~5 sessions / ~9 commits.** Sub-steps:

- **S.22a** — `ToolingCatalog` (scan + index sidecar files) — 2 commits
- **S.22b** — Q1=D 4-level fallback in `FcstdToolingLoader` — 2 commits
- **S.22c** — `ToolingLibrary` manager (add/get/list, headless) — 2 commits
- **S.22d** — `SMBS_ToolingLibrary.FCMacro` (FreeCAD UI, manual test) — 1 commit
- **S.22e** — Coordinator integration (`punch_id`/`die_id` + `--library-path`) — 2 commits

**Non-goals for Phase B MVP:**
- `mounting_type` field
- `~/.smbs/tooling/` user-global storage
- Datum editing UI in macro
- Auto-detection of datums (sidecar remains manual)
- Import wizard beyond basic role resolution

### Phase D — Requirement 4.1 (Clamp constraints) — COMPLETE (2026-10-19)

**Status:** DONE. All sub-steps (S.24a–S.24c) committed and pushed. Requirement 4.1 from roadmap §3 is functionally complete.

**Delivered:**
- `tooling_library_config.schema.json` — контракт для `<library>/config.json` (#7 approved, S.24a)
- `clamp_validator.py` — `ClampConfig`, `PunchSegment`, `validate_punch_chain` (5 Q2 rules), `load/save_clamp_config`, `validate_config_dict`
- `ToolingLibrary.get_clamp_config / set_clamp_config / ensure_clamp_config`
- `smbs-cli --clamps-config PATH` + `Coordinator.start_run(machine_config["clamps_config"])` plumbing
- Baseline: 449 tests / 5F / 0E / 3xF (+27 tests from Stage 6)

**Deferred (backlog):**
- UI-редактор раскладки лапок в FCMacro (JSON-файл достаточно для MVP).
- Auto-repositioning clamps (requirement 4).
- Enforcement of validate_punch_chain during planning (requirement 4, Phase E).

**Next:** requirement 3.1 (Tooling sets) per roadmap §8 order (3 → 4.1 → 3.1 → 4 → 2 → 5). KL-12 / KL-8 remain in backlog.

### Q2 — Clamp constraint spec (RESOLVED 2026-10-18)

**Status:** Spec accepted, implementation deferred to requirement 4.1 start.

**Machine model (`<library>/config.json`):**
- `clamps.width_mm` — clamp block width (user-configurable, default 150mm).
- `clamps.gap_between_mm` — gap between blocks (user-configurable, default 50mm).
- `clamps.positions_mm` — block centers.
- `clamps.repositionable` — bool (operator can move/remove blocks between bends).

**Punch chain validation rules:**
1. At least 2 clamp blocks under the punch chain.
2. All punch-to-punch joints must fall inside a clamp zone `[center - W/2, center + W/2]` (tolerance 0.1mm).
3. No gaps between punches: `end_i == start_{i+1}`.
4. End punches (first, last) may overhang by ≤ 30% of their own length (user-configurable ratio).
5. Punch total length = bend line length − 10..15mm (selection rule, belongs to requirement 4).

**If no valid configuration exists:** operator moves/removes blocks. Constraint checker only validates a given configuration (does NOT auto-reposition). Auto-repositioning is backlog (requirement 4 optimal selection).

**Example (valid):**
- Blocks at `[75, 275, 475]` centers, W=150, gap=50 → zones `[0..150], [200..350], [400..550]`.
- Punch chain: `[0..200]` (200mm) + `[200..400]` (200mm) + `[400..475]` (75mm).
- Joint at 200 → in zone `[200..350]` ✓. Joint at 400 → in zone `[400..550]` ✓.
- Left edge 0 = on block 1 (overhang 0, OK). Right edge 475 < block 3 end 550 → punch end is INSIDE block 3, overhang = 0, OK.
- Chain length = 475mm (target bend line = 485-490mm after -10..15mm tolerance). At least 3 blocks under chain (block 1, 2, 3) ≥ 2 required ✓.
- **Result: VALID.**

**Example (invalid — overhang fail):**
- Same blocks/zones. Punch chain: `[0..200]` (200mm) + `[200..400]` (200mm) + `[400..700]` (300mm).
- Right edge 700 > last block end 550 → overhang = 150mm. 30% of last punch (300mm) = 90mm → **150 > 90 ✗ FAIL**.
- Fix: shrink last punch to end ≤ 550 (e.g. `[400..640]`, overhang = 90mm = 30% of 240mm → OK) or add block at `[650..800]` center=725.

### Phase B — COMPLETE (2026-10-18)

**Status:** DONE. All sub-steps (S.22a–S.22h) committed and pushed. Requirement 3 (Tooling Library) from roadmap §3 is functionally complete.

**Delivered:**
- `ToolingCatalog` (scan + index), `ToolingLibrary` (add/remove/list/get/has_tool), Q1=D 4-level role resolution in `FcstdToolingLoader`, FreeCAD UI macro (`SMBS_ToolingLibrary.FCMacro`), Coordinator + CLI integration (`--punch-id`/`--die-id`/`--library-path`).
- 3 new schema fields in `tooling_manifest.schema.json`: `target_object_name`, `smbs_role`, `smbs_role_source` (all optional, backward-compatible).
- Baseline: 415 tests / 5F / 0E / 3xF (was 407 before Phase B tests, 407+8=S.22g; total Stage 5 +48 tests from 367 baseline).

**Deferred / backlog:**
- INFERRED heuristic (honest error instead — see KL-13 and Q1=D).
- `~/.smbs/tooling/` user-global path (Q8 MVP = 2 levels).
- Datum editing UI, sidecar wizard.
- "Use in pipeline" button in macro.
- KL-15 (target_object_name disambiguation, P2) — see PROGRESS.md.

**Next:** requirement 4.1 (Clamp constraints) per roadmap §8 order (3 → 4.1 → 3.1 → 4 → 2 → 5). KL-12 / KL-8 remain in backlog.

### Current order of requirements (updated 2026-10-21)

```
Req 1 (Phase A)   ✅ COMPLETE
Req 3 (Phase B)   ✅ COMPLETE
Req 4.1 (Phase D) ✅ COMPLETE
Req 3.1 (Phase C) ✅ COMPLETE
Stage 8 (KL-8)    ✅ COMPLETE (5F -> 0F)
KL-22             ✅ COMPLETE (face identity)
Req 6a (Phase 6a) ✅ COMPLETE (SLA 49.26s / 9 nodes)
Req 4 (Phase E)   ✅ COMPLETE (S.30a/b/c, clamp enforcement + DP min changes)
```

Next: Req 2 (Phase F, FreeCAD animation) — requires KL-12 v2.
After: Req 5 (Phase G, Open block) — Q13 pending.

### Status of sections 1–10

Sections 1–10 of this document are the original roadmap and remain authoritative for phases, dependencies, matrices, and detailed requirement descriptions. This addendum is a **delta**, not a replacement.

---

### Current Status (annotated 2026-10-23)

Snapshot: HEAD `91d07c0` (stage-1-fix-correctness), baseline 550 / 0F / 0E / 3xF.

**Progress since 2026-10-21 (§11 entry above):**
- Req 2 Phase F.1 CLOSED in S.33 (`1724748`).
- Req 2 Phase F.2 CLOSED in S.35 (`8d0ce71`).
- Req 2 Phase F.3a CLOSED in S.37 (6 commits: `814eb19` feature + 4 hotfixes + `91d07c0` docs).
- **KL-12 v3** (planar self-collision + bridge caps + shell disconnection) INVESTIGATED S.38 → **BLOCKED**. Root cause: rigid-body kinematic model without bend-region deformation; 7 recombine approaches probed, none produce solid. Forward-fold track reframed as separate architecture item (see `docs/PROGRESS.md` §Open P0 Defects).
- KL-16 (machine frame) still OPEN — no `machine_frame.FCStd`.

**Not described in §1–§11:** post-2026-10-21 P0 code defects (see `docs/AUDIT_FULL_2026-10-23.md`), release-readiness reclassification of §8 (was `P2 before Req 2`, now `BLOCKED pending architecture`).

**Release verdict: NOT READY.** See `docs/AUDIT_FULL_2026-10-23.md` §Release Verdict.
