# Working Checklist

> **Purpose:** Lightweight daily tracker. Not a roadmap (see `docs/ROADMAP.md`), not an architecture doc (see `docs/ARCHITECTURE.md`), not a release audit (see `docs/AUDIT_FULL_2026-10-23.md`).
> **Rule:** Move a task to **Done** only after the acceptance condition is met and a verification reference is linked (test name, commit SHA, or `cat -n` evidence). No "Done" without proof.
> **Snapshot:** HEAD `fda5abf`, 2026-10-24.

---

## Next — P0 (blocks release)

*(None — all P0 items CLOSED in S.40–S.50; see `docs/AUDIT_FULL_2026-10-23.md` §Release Verdict)*

## Next — P1 (hygiene, not a release blocker)

| ID | Task | Acceptance | Ref |
|---|---|---|---|
| P1-CODE-1-remove | `sheetmetal_folder.py:1310–1388`: remove dead fallback (`40, 30, 50, 30, 40` + `NotImplementedError`) or annotate `LEGACY_FALLBACK_UNUSED`. | No production caller reaches this branch. | P1-CODE-1 |
| P1-CODE-2-surface | `sheetmetal_folder.py:1214, 1236, 1286, 1313–1315, 1337–1339, 1371–1373`: replace silent `or 1.5/50.0/90.0/1.0/0.44` with explicit raise or `UNKNOWN`. | Missing param → explicit fail or `UNKNOWN`. | P1-CODE-2 |
| P1-CODE-3-fixture | `sheetmetal_folder.py:1323, 1356` + `pythonocc_backend.py:286, 318–319, 323`: remove or annotate `95.5/47.5/50.0` (fixture_l overfit). | Comment `# FIXTURE_DEFAULT` or removal. | P1-CODE-3 |

## In Progress

| ID | Task | Status | Note |
|---|---|---|---|
| P0-DOC-pass | Post-audit documentation standardization | IN PROGRESS | Steps 1–8 applied (uncommitted); Step 9 (this file), Step 10 (`AUDIT_FULL`), commit pending |

## Blocked

| ID | Task | Blocked by | Note |
|---|---|---|---|
| KL-12-v3 | Forward fold (panel shell → solid) | Architecture: rigid-body model needs bend-region deformation | Investigated S.38; 7 recombine approaches probed; none produce solid |
| KL-16 | Machine frame (`machine_frame.FCStd`) | CAD session — file missing | Required for machine collision check |
| KL-23 | Cyclic N≥8 tie-break | Scheduling | P1; after P0-CODE fixes |

## Deferred (separate session, P1-docs)

| ID | Task | Note |
|---|---|---|
| P1-DOC-translate-arch | Translate `docs/ARCHITECTURE.md` ASCII diagram (RU → EN) | Deferred from Step 6 |
| P1-DOC-translate-roadmap | Translate `docs/ROADMAP.md` §1–§3 (RU → EN) | Deferred from Step 8 |
| P1-DOC-term-tz | Standardize `ТЗ v1.2` → `technical specification v1.2` | Cross-file term |
| P1-DOC-refresh-arch | Full ARCHITECTURE refresh (Stages 5–9, Req 6, Req 2) | Separate track |
| P1-DOC-refresh-roadmap | ROADMAP §1–§7 refresh (post-Stage 4) | Separate track |

## Done

| ID | Task | Evidence | Date |
|---|---|---|---|
| *(empty — populated after P0-DOC-pass commit)* | | | |
