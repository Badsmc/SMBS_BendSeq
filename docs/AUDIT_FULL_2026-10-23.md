# Audit — SMBS/BendSeq (2026-10-23)

> **Purpose:** Authoritative post-S.38 audit capturing verified defects, rejected external claims, and the current release verdict. All other docs reference this file for P0 registry and release readiness.
> **Scope:** `Badsmc/SMBS_BendSeq` only. Branch `stage-1-fix-correctness`, HEAD `91d07c0` (audited baseline: 550 tests / 0F / 0E / 3xF). `SMBS_FreeCad` explicitly excluded (not inspected, referenced, or copied).
> **Verdict (historical 2026-10-23):** **NOT READY** for release.
> **Updated (S.50 / 2026-10-24):** HEAD `fda5abf`, baseline 577 tests (0F / 0E / 3xF). Release blockers: **CLEARED** (all P0-CODE-1..5 and P0-GEOM closed with negative regression pins).

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

## P0-GEOM Defects — B-Rep Restoration Discrepancies (CLOSED)

During the S.45–S.46 audit of baseline B-Rep reconstruction, canonical sheetmetal fixtures exhibited non-zero Hausdorff surface distances (up to 112mm) due to coordinate frame misalignment and folding kinematics discrepancies. All issues were investigated and resolved across sessions S.47–S.49-gate:

- **H1/H5 (Outward Normal):** FrameAligner failed on ambiguous face normal senses. Fixed in S.47 (`57663e9`) with orientation-aware outward normal logic.
- **H4b (Flat Pattern Input):** Direct flat pattern fold input bypassing synthetic hacks. Closed in S.48b (`0a34af8`).
- **H4a (Dynamic Origin Alignment):** Resolved translation offsets across 9/10 fixtures. Closed in S.48d (`74935bb`).
- **H4c (Leaf Reference Panel Kinematics):** Inverted fold on far-end leaf (`u_bracket`). Closed in S.48e (`bdca336`).
- **Gate Enforcement:** Hausdorff surface-distance tolerance (`<= 0.2mm`) re-enabled in baseline `is_pass`. Closed in S.49-gate (`fda5abf`).
- **Evidence:** All 10/10 canonical fixtures pass with `hausdorff = 0.0000mm` bit-identical (`tests/test_baseline_topology_canonical.py`).

### Errata (S.48c/d chamfer → Z-flip)

In session S.48c and S.48d commit messages, a remaining 1.0355mm discrepancy in `u_bracket` was hypothesized to be caused by CAD chamfer generation differences during folding.

In session S.48e root-cause investigation (E1/E2 probes), this hypothesis was **disproven**:
1. The 1.0355mm discrepancy was a measurement artifact of an inverted bracket (`rotate(Z, 180°)` + fold + `rotate(Z, -180°)` preserved +Z, resulting in inverted ∩ orientation vs reference ∪ orientation).
2. Fixing the reference panel selection to an unbranched far-end leaf (`ref_idx = 0`, Panel 0) eliminated the Z-inversion entirely.
3. Hausdorff distance dropped from 1.0355mm to **0.0000mm bit-identical** without touching CAD chamfer logic.
4. Commit `bdca336` (S.48e) and this section explicitly record this correction.

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

**Current verdict: READY FOR RELEASE (HEAD 917eb71).** All six conditions MET.

A release is READY only if **all** below hold:
1. Every P0-CODE-n has `VERIFIED` evidence (test + commit SHA + `cat -n` proof). — **MET** (S.40–S.49-gate: P0-CODE-1..5 and P0-GEOM closed with verified negative tests; S.51 exception safety).
2. Every P0-DOC-n is closed (this pass closes them). — **MET** (S.39/S.50: P0-DOC-1..4 resolved and annotated).
3. `docs/REGRESSION_REPORT.md` exists for the current HEAD with reproducible command. — **MET** (S.53, HEAD `917eb71`, 578 tests / 0F / 0E / 3xF).
4. `docs/TEST_MATRIX.md` reflects current test files; no `NOT RUN` in critical paths. — **MET** (S.53, 118 test files across 9 subsystems).
5. `docs/RELEASE_AUDIT.md` (or successor) explicitly states READY with audited SHA. — **MET** (S.53, HEAD `917eb71`).
6. No `HISTORICAL` report is used in the active evidence chain. — **MET** (active verification relies on living tests only).

As of 2026-10-24 (HEAD `917eb71`, baseline 578 tests / 0F / 0E / 3xF), **all six release conditions are fully MET**. Qualified for release under stage-1-fix-correctness pipeline scope.

---

## Cross-references

- `docs/PROGRESS.md` §Open P0 Defects — short registry.
- `docs/WORKING_CHECKLIST.md` §Next P0 — actionable tasks + acceptance.
- `docs/ARCHITECTURE.md` — Acceptance Principles (items 5–7 in Core Architectural Rules).
- `docs/ROADMAP.md` §11 addendum — progress since 2026-10-21.
- `docs/KNOWN_LIMITATIONS.md` — historical; canonical list in `PROGRESS.md`.
