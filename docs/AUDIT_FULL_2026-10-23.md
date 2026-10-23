# Audit — SMBS/BendSeq (2026-10-23)

> **Purpose:** Authoritative post-S.38 audit capturing verified defects, rejected external claims, and the current release verdict. All other docs reference this file for P0 registry and release readiness.
> **Scope:** `Badsmc/SMBS_BendSeq` only. Branch `stage-1-fix-correctness`, HEAD `91d07c0`. `SMBS_FreeCad` explicitly excluded (not inspected, referenced, or copied).
> **Verdict:** **NOT READY** for release.

---

## Scope and Method

- **Repository:** `Badsmc/SMBS_BendSeq` only.
- **Branch / HEAD:** `stage-1-fix-correctness` at `91d07c0`.
- **Baseline:** 550 tests / 0F / 0E / 3xF (`python -m unittest discover -s tests -p test_*.py`, timeout 300s).
- **Method:** Every claim verified against raw `cat -n` of the listed files, not against summary or prior reports. Claims that could not be verified are marked PENDING/UNKNOWN.
- **Non-claims:** this audit does not re-run tests. Baseline is referenced as reported in S.38 pre-check.

---

## P0 Code Defects

All P0 defects are **integrity defects**, not functional regressions. Baseline remains green because no current test asserts the missing invariant. Each fix must add a **negative test** that fails today and passes after the fix.

### P0-CODE-1 — Direction derived from sign only, marked CONFIRMED
- **File:** `smbs_core/workers/topology/worker.py:534–565`
- **Defect:** `direction_str = "UP" if angle_val >= 0 else "DOWN"`; axis/normal not used; result marked `confidence=CONFIRMED`.
- **Impact:** downstream cannot distinguish real UP/DOWN from sign-guess.
- **Acceptance:** orientation-aware derivation; `CONFIRMED` only if axis/normal verified.
- **Ref:** `docs/WORKING_CHECKLIST.md` §Next P0 (P0-CODE-1-fix).

### P0-CODE-2 — Silent defaults for critical bend fields, marked CONFIRMED
- **File:** `smbs_core/workers/topology/worker.py:552, 557, 571, 572`
- **Defect:** `radius=1.0`, `length=50.0`, `axis_point=[0,0,0]`, `axis_direction=[1,0,0]` — all `confidence=CONFIRMED`.
- **Impact:** fabricated geometry becomes canonical.
- **Acceptance:** `raise IncompleteBendError` or `confidence=ESTIMATED`.
- **Ref:** `docs/WORKING_CHECKLIST.md` §Next P0 (P0-CODE-2-fix).

### P0-CODE-3 — Baseline hausdorff = max(bbox_delta); area_delta not gated
- **File:** `smbs_core/workers/baseline/worker.py:700, 702, 704–712, 760`
- **Defect:** `hausdorff = max(bbox_delta)` published as `hausdorff_distance_mm`; `area_delta` computed but not in `is_pass`.
- **Impact:** baseline can mark `VALID` for surface-level divergence.
- **Acceptance:** real surface/vertex Hausdorff; `area_delta` in `is_pass`.
- **Ref:** `docs/WORKING_CHECKLIST.md` §Next P0 (P0-CODE-3-fix).

### P0-CODE-4 — Dead status_worker; literal SUCCESS on unfold failure
- **File:** `smbs_core/workers/geometry/worker.py:179, 249, 273`
- **Defect:** `status_worker = RunStatus.BACKEND_UNAVAILABLE` (line 179) is never read; line 249 emits `"status": RunStatus.SUCCESS.value` (literal) even when `unfold_res.status != SUCCESS`.
- **Impact:** `02_geometry_result.json` reports SUCCESS while unfold failed.
- **Acceptance:** wire `status_worker`; unfold failure → `WORKER_ERROR`.
- **Ref:** `docs/WORKING_CHECKLIST.md` §Next P0 (P0-CODE-4-fix).

### P0-CODE-5 — FreeCAD preference defaults propagated as CONFIRMED
- **File:** `smbs_core/workers/sheetmetal/adapter.py:127–128, 164–165, 214–215, 249`
- **Defect:** `params.GetFloat("manualKFactor", 0.44)` / `GetString("kFactorStandard", "ansi")` silently default; `NormalizedBendLine(confidence=CONFIRMED)` (line 249); `EstimateThickness` → `thickness_confidence=CONFIRMED` (line 165).
- **Impact:** FreeCAD defaults indistinguishable from real parameters downstream.
- **Acceptance:** `source=DEFAULT_POLICY, confidence=ESTIMATED` for defaults.
- **Ref:** `docs/WORKING_CHECKLIST.md` §Next P0 (P0-CODE-5-fix).

---

## P0 Documentation Defects

| ID | File | Defect | Status |
|---|---|---|---|
| P0-DOC-1 | `docs/RELEASE_AUDIT.md` | Stage 3 "READY FOR RELEASE" without SHA/scope; snapshot since 2026-10-10 | Annotated HISTORICAL 2026-10-23 |
| P0-DOC-2 | `docs/REGRESSION_REPORT.md` | 32/32 PASS at commit `c76e95f` (main); not current | Annotated HISTORICAL 2026-10-23 |
| P0-DOC-3 | `docs/TEST_MATRIX.md` | ">70 (74 typically)" pre-refactor matrix | Annotated HISTORICAL 2026-10-23 |
| P0-DOC-4 | `docs/PROGRESS.md` | No P0 registry; stale HEAD `87cdbad` | Added §Open P0 Defects + HEAD fix in this pass |

---

## P1 Code Hygiene

| ID | File | Defect |
|---|---|---|
| P1-CODE-1 | `sheetmetal_folder.py:1310–1388` | Dead fallback (`40, 30, 50, 30, 40` literal + `NotImplementedError`); no production caller. |
| P1-CODE-2 | `sheetmetal_folder.py:1214, 1236, 1286, 1313–1315, 1337–1339, 1371–1373` | Silent geometry defaults (`or 1.5/50.0/90.0/1.0/0.44`) in active and fallback branches. |
| P1-CODE-3 | `sheetmetal_folder.py:1323, 1356`; `pythonocc_backend.py:286, 318–319, 323` | Hardcoded `95.5/47.5/50.0` (fixture_l overfit). |

---

## Rejected Audit Claims

Claims below were made in the external 2026-10-23 audit. Each was disproven by raw `cat -n` of the current branch. They remain here so future readers do not re-open them without evidence.

| ID | Claim (audit) | Raw disproof (branch) | Verdict |
|---|---|---|---|
| AUD-01 | `fold_from_canonical_part` ignores `aligned_flat` | `sheetmetal_folder.py:1221–1255`: `aligned_flat` passed to `fold_from_flat_pattern` / `fold_multi_bend_from_flat_pattern` / `fold_tree_*_from_flat_pattern`. | Rejected |
| AUD-02 | "Hardcoded templates for N∈{1,2,4}" | True only for dead fallback (`aligned_flat=None` never reached); demoted to **P1-CODE-1**, not P0. | Demoted |
| AUD-05 | Verifier assigns VERIFIED without validator | `verifier/worker.py:289–305` calls `validator.validate_step` per step; `442–476` honest final gate (`unknown_steps → VERIFICATION_FAILED`, `production_eligible=False`). | Rejected |
| AUD-06 (verifier) | `hausdorff = bbox` in verifier | `verifier/worker.py:102–123`: real two-way vertex Hausdorff via `BRepExtrema_DistShapeShape`. | Rejected |
| AUD-09 | Export fallback `0.44` without marker | `export/worker.py:86`: `{"value": 0.44, "source": "DEFAULT_POLICY", "confidence": "ESTIMATED"}`; `71–72, 99, 111, 149` strict gate. | Rejected |

**Note:** AUD-06 was **correct for `baseline/worker.py`** (see P0-CODE-3) but **incorrect for `verifier/worker.py`**. Split verdict is intentional.

---

## Pending Candidates (P1, not blocking release)

| ID | Candidate | Needs |
|---|---|---|
| P1-DOC-A | verifier vertex Hausdorff ≠ surface Hausdorff | Decide: acceptable approximation or upgrade to surface |
| P1-DOC-B | verifier `if ext.IsDone()` without else (silent skip) | Document as KNOWN_LIMITATION |
| P1-DOC-C | topology `angle` vs `signed_angle` unsignedness | Review `FeatureExtractor` |
| P1-DOC-D | topology `axis_point`/`axis_direction` without wrapper | Verify against canonical schema |
| P1-DOC-E | geometry `diagnostics` do not gate status | Document + cover in P0-CODE-4 fix |
| P1-DOC-F | `metrics_ok` / `fold_metrics_ok` composition | Full raw review in next session |

---

## Release Verdict

**Current verdict: NOT READY.**

A release is READY only if **all** below hold:
1. Every P0-CODE-n has `VERIFIED` evidence (test + commit SHA + `cat -n` proof).
2. Every P0-DOC-n is closed (this pass closes them).
3. `docs/REGRESSION_REPORT.md` exists for the current HEAD with reproducible command.
4. `docs/TEST_MATRIX.md` reflects current test files; no `NOT RUN` in critical paths.
5. `docs/RELEASE_AUDIT.md` (or successor) explicitly states READY with audited SHA.
6. No `HISTORICAL` report is used in the active evidence chain.

As of 2026-10-23, **condition 1 is unmet** (5 P0-CODE open). Other conditions may become satisfiable independently.

---

## Cross-references

- `docs/PROGRESS.md` §Open P0 Defects — short registry.
- `docs/WORKING_CHECKLIST.md` §Next P0 — actionable tasks + acceptance.
- `docs/ARCHITECTURE.md` — Acceptance Principles (items 5–7 in Core Architectural Rules).
- `docs/ROADMAP.md` §11 addendum — progress since 2026-10-21.
- `docs/KNOWN_LIMITATIONS.md` — historical; canonical list in `PROGRESS.md`.
