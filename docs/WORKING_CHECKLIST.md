# Working Checklist

> **Purpose:** Lightweight daily tracker. Not a roadmap (see `docs/ROADMAP.md`), not an architecture doc (see `docs/ARCHITECTURE.md`), not a release audit (see `docs/AUDIT_FULL_2026-10-23.md`).
> **Rule:** Move a task to **Done** only after the acceptance condition is met and a verification reference is linked (test name, commit SHA, or `cat -n` evidence). No "Done" without proof.
> **Snapshot:** HEAD `fda5abf`, 2026-10-24.

---

## Next — P0 (blocks release)

*(None — all P0 items CLOSED in S.40–S.50; see `docs/AUDIT_FULL_2026-10-23.md` §Release Verdict)*

## Next — P1 (hygiene, not a release blocker)

*(None — all P1-CODE items CLOSED in S.55; see §Done)*

## In Progress

*(None — all in-progress remediation tasks committed)*

## Blocked

| ID | Task | Blocked by | Note |
|---|---|---|---|
| KL-12-v3 | Forward fold (panel shell → solid) | Architecture: rigid-body model needs bend-region deformation | Investigated S.38; 7 recombine approaches probed; none produce solid |
| KL-16 | Machine frame (`machine_frame.FCStd`) | CAD session — file missing | Required for machine collision check |
| F.3b | Req 2 Phase F.3b (punch descent + backgauge + machine frame overlay) | KL-16 (`machine_frame.FCStd` missing) | Machine frame overlay requires machine frame geometry |

## Deferred (separate session, P1-docs)

| ID | Task | Note |
|---|---|---|
| P1-DOC-refresh-arch | Full ARCHITECTURE content refresh (Stages 5–9, Req 6, Req 2) — truth-up of stale facts completed S.63 (`bc9acad`); content addition = separate epic track | Separate track |
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
| P0-CODE-4-adjacent | Exception handling in native unfold (`geometry/worker.py`) | `917eb71` | `tests/test_geometry_worker_unfold_failure.py` (578 tests) | 2026-10-24 |
| P1-CODE-1 | Replace dead fallback in `fold_from_canonical_part` with explicit precondition check | `4761aef` | `smbs_core/Geometry/sheetmetal_folder.py` (578 tests) | 2026-10-24 |
| P1-CODE-2 | Eliminate silent fallback defaults across `fold_from_canonical_part`, `fold_from_flat_pattern`, `fold_multi_bend_from_flat_pattern`, `fold_tree_from_flat_pattern` | `4a2b0e3`, `a28c71e`, `bc5b92e` | `smbs_core/Geometry/sheetmetal_folder.py` (578 tests) | 2026-10-25 |
| P1-CODE-3 | Annotate fixture defaults in `fold_one_bend`/`fold_u_bracket`; reject silent fabrication in `measure_planar_flange_lengths` | `16a6685`, `1f3178d` | `smbs_core/Geometry/sheetmetal_folder.py`, `smbs_core/geometry_backend/backends/pythonocc_backend.py` (578 tests) | 2026-10-25 |
| P1-hygiene-aligned-flat | Dead `aligned_flat` and `transformer` removed from baseline worker | `21902db` | `smbs_core/workers/baseline/worker.py` (578 tests) | 2026-10-24 |
| P1-hygiene-verifier-dup | `_find_largest_planar_face` deduplicated into `smbs_core/Geometry/face_utils.py` | `5a02f72` | `smbs_core/Geometry/face_utils.py` (578 tests) | 2026-10-24 |
| P1-hygiene-baseline-metrics | `baseline_metrics` surfaced on baseline INVALID (contract symmetry) | `c41263d` | `smbs_core/coordinator/run_pipeline.py` (578 tests) | 2026-10-24 |
| P1-hygiene-test-05-workaround | Direct `json.load` workaround in `test_hausdorff_gate_enforcement` removed | `c41263d` | `tests/test_baseline_topology_canonical.py` (578 tests) | 2026-10-24 |
| P1-hygiene-angle-override | Annotate `angle_override_deg` as DEBUG_OVERRIDE in `fold_from_flat_pattern`/`fold_multi_bend_from_flat_pattern` signatures | `941ae36` | `smbs_core/Geometry/sheetmetal_folder.py` (578 tests) | 2026-10-25 |
| P1-hygiene-proposal-2 | S.46 caching investigation closed: `StateCache` already integrated (Phase 7 COMPLETE), no further runner optimization required | — (S.46 audit; no code change) | `docs/PROGRESS.md` Phase 7; `smbs_core/workers/solver/worker.py:183-201` (578 tests) | 2026-10-25 |
| KL-23 | Cyclic N≥8 tie-break — investigated, closed as KNOWN_LIMITATION | — (no code change; cyclic topology rejected at `panel_topology.py:13`; already accepted in `RELEASE_AUDIT.md:26`) | `docs/PROGRESS.md:504-508`; `docs/RELEASE_AUDIT.md:26` (578 tests) | 2026-10-25 |
| P1-DOC-term-tz | Standardize ТЗ → technical specification across docs, smbs_core, tests | `4b4d065`, `4d50858`, `53a613e` | 23 files / 30 line replacements (R1: 1/7, R2: 13/14, R3: 9/9); `docs/history/*` excluded per S.39 урок 9 (historical archive) | 2026-10-25 |
| P1-DOC-translate-arch | Translate `docs/ARCHITECTURE.md` ASCII diagram (RU → EN) | `90860aa` | 6 lines translated (lines 7, 15, 22, 29, 36, 38); 56-char box alignment preserved; zero Cyrillic remaining | 2026-10-25 |
| P1-DOC-translate-roadmap | Translate `docs/ROADMAP.md` §1–§3 (RU → EN) | `d1eba1f`, `6edd5b4`, `c37b7ae`, `e79fc61`, `965d8ae` | 5 atomic commits (§1 / §2 / §3 Req1 / §3 Req2-3.1 / §3 Req4-5); 639 lines stable; zero Cyrillic in §1-§3 (lines 1-271); §4+ (lines 272+) untranslated as out-of-scope | 2026-10-25 |
| A1-A2 | Eliminate fabrication in TechnologicalUnfolder (`_compute_analytic_flat_length`, `_create_synthetic_flat_pattern`) | `cdc5327` | +2 tests in `test_technological_unfold_single_bend.py`; baseline 580/OK | 2026-10-25 |
| A3-A3ext | Eliminate fabrication in PythonOCCBackend (`get_surface_area`, `get_face_center`) | `344f598` | +4 tests in new `test_pythonocc_backend.py`; baseline 584/OK | 2026-10-25 |
| KL-18-fix | Strict `thickness` in BendSeqOrchestrator; documented `k_factor` policy default | `d8577d0` (fixes `8973bc4` regression) | +3 tests in new `test_orchestrator_strict_params.py`; baseline 587/OK | 2026-10-25 |
| KL-25+KL-10 | Eliminate `inspect.signature` reflection in solvers; align mock validators | `4686d24` | Removed try/except inspect block from dfs/greedy/astar; aligned MockValidator in stage 11/18/19; +2 tests in `test_kl25_unknown_policy.py`; baseline 589/OK | 2026-10-25 |
| KL-24 | Authoritative `g_scores` lookup in A* pending-pop branch | `4dd9a0e` | `astar_backward.py:205` uses `g_scores.get(current_fp, ...)` aligning with non-pending branch; +1 test in `test_kl24_pending_g_scores.py`; baseline 590/OK | 2026-10-25 |
| KL-20 | Reject fabricated `tooling_status` in SolverWorker | `180f6c5` | `solver/worker.py:298` replaced `or` fallback with explicit None check + rank-based fidelity check (REAL > SIMPLIFIED > MISSING); +2 tests in `test_solver_worker_honest.py`; baseline 592/OK | 2026-10-25 |
| KL-8d | Actionable diagnostic on NO_SOLUTION + SIMPLIFIED + PRODUCTION | `9763c1d` | `solver/worker.py:441` appends explicit diagnostic explaining missing tooling and how to resolve; +1 test in `test_solver_worker_honest.py`; baseline 593/OK | 2026-10-25 |

## Upcoming Tasks (Post-P0 Remediation)

*(None — all P1-hygiene items CLOSED in S.56; see §Done)*
