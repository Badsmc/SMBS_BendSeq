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

*(None — all in-progress remediation tasks committed)*

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

| ID | Task | Commit | Evidence | Date |
|---|---|---|---|---|
| P0-DOC-pass | Post-audit documentation standardization | `af87274` | S.39 docs pass (all P0-DOC closed) | 2026-10-23 |
| P0-CODE-1-fix | Strict origin/axis guards; direction formula unchanged (triple product confirmed) | `9dd2249` | `tests/test_topology_worker_direction.py` (554 tests) | 2026-10-24 |
| P0-CODE-2-fix | Strict radius/length guards, no silent defaults | `2fcd8ab` | `tests/test_topology_worker_radius_length.py` (558 tests) | 2026-10-24 |
| P0-CODE-3a,b-fix | Real Hausdorff metric + area_delta gate | `e95aac3` | `tests/test_baseline_worker_hausdorff.py` (561 tests) | 2026-10-24 |
| P0-CODE-4-fix | Wire status_worker to result_payload status | `bb75ef1` | `tests/test_geometry_worker_unfold_failure.py` (564 tests) | 2026-10-24 |
| P0-CODE-5-fix | Strict confidence provenance in SheetMetalAdapter | `bc495aa` | `tests/test_sheetmetal_adapter_confidence.py` (571 tests) | 2026-10-24 |
| P0-GEOM-H1/H5 | Orientation-aware outward normal in FrameAligner | `57663e9` | `tests/test_frame_aligner_orientation.py` (573 tests) | 2026-10-24 |
| P0-GEOM-H4b | Direct flat pattern fold input | `0a34af8` | `tests/test_baseline_fold_flat_input.py` (575 tests) | 2026-10-24 |
| P0-GEOM-H4a | Dynamic origin alignment in baseline | `74935bb` | 9/10 fixtures h=0.0000mm (575 tests) | 2026-10-24 |
| P1-perf | Deduplicate BRep vertices in Hausdorff computation | `7611c5f` | `test_04` in `tests/test_baseline_worker_hausdorff.py` (576 tests; 259.4s -> 137.2s) | 2026-10-24 |
| P0-GEOM-H4c | Far-end leaf reference fold Z-inversion fix | `bdca336` | 10/10 fixtures h=0.0000mm bit-identical (576 tests) | 2026-10-24 |
| P0-CODE-3c/GEOM | Enable hausdorff surface-distance gate in is_pass | `fda5abf` | `test_hausdorff_gate_enforcement` in `tests/test_baseline_topology_canonical.py` (577 tests) | 2026-10-24 |
| S.50-docs | Canonical docs clearance, P0-GEOM, Errata | `ce68906` | `docs/AUDIT_FULL_2026-10-23.md`, `docs/WORKING_CHECKLIST.md` (577 tests) | 2026-10-24 |

## Upcoming Tasks (Post-P0 Remediation)

| ID | Task | Priority | Note / Location |
|---|---|---|---|
| P0-CODE-4-adjacent | Exception handling in `geometry/worker.py:189–201` | P1 | STEPControl write / Part.read / adapter.unfold not wrapped in try/except; if raised, `02_geometry_result.json` is missing (S.43 commit note) |
| P1-hygiene-aligned-flat | Clean up dead aligned_flat references and stale comments | P1 | `smbs_core/workers/baseline/worker.py:589, 592–599` |
| P1-hygiene-verifier-dup | Deduplicate `_find_largest_planar_face` | P1 | `smbs_core/workers/verifier/worker.py:85` vs `smbs_core/workers/baseline/worker.py:153` |
| P1-hygiene-baseline-metrics | Contract audit for `res["baseline_metrics"]` | P1 | Flag 66: top-level pipeline return on baseline INVALID |
| P1-hygiene-test-05-workaround | Clean up direct json read in `test_05` | P1 | Flag 67: `tests/test_baseline_topology_canonical.py:188` |
| P1-hygiene-angle-override | Add comment annotation to `angle_override` | P1 | `tests/test_baseline_topology_canonical.py:65` |
| P1-hygiene-proposal-2 | Re-evaluate search test caching / runner opt | P1 | Investigated during S.46 audit |
