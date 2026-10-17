# smbs_core Architecture

```
┌──────────────────────────────────────────────────────┐
│  СЛОЙ 5: FreeCAD Adapter (последний этап)            │
│  • Импорт детали из документа                        │
│  • Визуализация JSON                                 │
│  • Симуляция                                         │
└──────────────────────────────────────────────────────┘
                        ↑↓ JSON / STEP
┌──────────────────────────────────────────────────────┐
│  СЛОЙ 4: CLI + IO                                    │
│  • smbs-cli                                          │
│  • bendseq_writer (JSON v1.0)                        │
│  • step_reader, fcstd_reader, tooling_loader         │
└──────────────────────────────────────────────────────┘
                        ↑↓
┌──────────────────────────────────────────────────────┐
│  СЛОЙ 3: Physics + Solvers + Geometry                │
│  • Validator, CollisionDetector, ToolingAssembly     │
│  • FeatureExtractor, PanelTopology                   │
│  • A*, DFS, Greedy                                   │
└──────────────────────────────────────────────────────┘
                        ↑↓
┌──────────────────────────────────────────────────────┐
│  СЛОЙ 2: Core                                        │
│  • FoldState, BendTransform, StateCache              │
│  • BendGraph, PanelGraph                             │
│  • Orchestrator                                      │
└──────────────────────────────────────────────────────┘
                        ↑↓
┌──────────────────────────────────────────────────────┐
│  СЛОЙ 1: GeometryBackend (ABC)                       │
│  • PythonOCCBackend, MockBackend                     │
│  • НЕ импортирует FreeCAD                            │
└──────────────────────────────────────────────────────┘
```

## Core Architectural Rules:
1. Layers 1–4 MUST NOT import `FreeCAD`, `Part`, or `FreeCADGui`.
2. All geometric operations MUST go through `GeometryBackend`.
3. `UNKNOWN` status MUST NEVER be treated as `VALID`.
4. No hardcoded fallback values, path assumptions, or silent error swallowing.

---

## Stage 3 Verifier (Independent Verification)

**Purpose:** Independently verify a solver-produced `candidate_sequence` against the reference 3D model. Independence from `SolverWorker` is a first-order rule: `VerifierWorker` MUST NOT read solver cache, search tree, or pruned states.

### Verification Pipeline

1. **Precheck** — reject if `search_result.payload.planning_status != SUCCESS` or empty `candidate_sequence` (`diagnostic: SOLVER_FAILED`).
2. **Re-simulation (unfold correctness)** — walk the sequence step-by-step via `PhysicalValidator.validate_step` on a fresh `FoldState.initial(...)`. No solver involvement. Fails on first invalid step (`BEND_NOT_FOUND`, `STEP_VALIDATION_FAILED`).
3. **Fold correctness (KL-11, closed in S.9.3)** — take `flat_pattern.brep`, align it to `ref_shape` via `FrameAligner.align_full_frame`, call `SheetMetalFolder.fold_from_canonical_part(canonical_part, aligned_flat=...)`, compare folded shape to `ref_shape` by `vol_delta` and `bbox_delta`. Failures: `ALIGNMENT_FAILED`, `FOLD_RECONSTRUCTION_FAILED`, `FOLD_SHAPE_MISMATCH`, `FOLD_CHECK_SKIPPED_NO_FLAT_SHAPE`. Fold step is skipped if re-simulation already failed.

### production_eligible
```text
production_eligible = unfold_ok AND fold_ok AND not unknown_steps AND has_all_bends
```

- **INVALID is never bypassed** — not under `PRODUCTION`, not under `EXPLORATORY`.
- **UNKNOWN** under both policies yields `VERIFICATION_FAILED` (`verification_result` schema has no `PARTIAL`; `UNKNOWN != VALID` invariant #1).
- `verification_result.schema.json` is fixed; fold metrics go into `diagnostics[]`, not `independent_metrics`.

### Inputs / Outputs

- **Inputs:** `07_search_result.json`, `04_canonical_part.json`, `flat_pattern.brep`, `geometry.brep` (from `canonical_part.payload.geometry_artifact`), optional `06_search_request.json` (for `machine_config`).
- **Output:** `09_verification_result.json` (schema v1.1).

### Non-goals

- `VerifierWorker` does not call `SolverWorker` — ever.
- `VerifierWorker` does not modify the search tree or solver state.
- `VerifierWorker` does not silently default missing flat/geometry — missing artifacts must produce explicit diagnostics and `VERIFICATION_FAILED`.
