# smbs_core Architecture

```
┌──────────────────────────────────────────────────────┐
│  СЛОЙ 5: Viewer / FreeCAD Adapter                    │
│  • run_dir → timeline (viewer/ modules)              │
│  • CLI: python -m smbs_core.viewer view <run_dir>    │
│  • Offscreen HLR+PIL renderer (PREVIEW_ONLY)         │
│  • FreeCAD addon GUI — deferred (post KL-12)         │
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

---

## Stage 4 Viewer (Layer 5)

**Purpose:** headless visualization of a pipeline run's `candidate_sequence`. Independent of `VerifierWorker`; does not call solver, verifier, or `PhysicalValidator`. Reads only pipeline artifacts (`04/07/09` + `geometry.brep`).

### Boundaries

```
run_dir/  (04, 07, 09, geometry.brep)
    │
    ▼
run_loader.load_run()        → RunArtifacts
    │
    ▼
graph_builder.build_graphs() → ViewerGraphs (BendGraph + PanelGraph + direction_by_bend)
    │
    ▼
replayer.replay_backward()   → Iterator[StepView]     (PREVIEW_ONLY, KL-12)
    │
    ▼
player.Player                → tuple[Frame, ...]      (production_eligible from 09 ONLY)
    │
    ├── cli.render_table / --json / --dump-frames
    └── render.render_frame_png  → PNG + manifest.json (HLR + PIL)
```

### Honest marking (C2)

- `production_eligible` is read from `09_verification_result.payload.production_eligible`. **Never computed** by viewer (invariant #9).
- Missing `09` → `verification_status="UNVERIFIED"`, `production_eligible=None`, `diagnostics=()`. (invariant #1: `UNKNOWN != VALID`).
- `diagnostics` are taken from `09_verification_result.diagnostics` (top-level; per L1 audit, `payload` has none).
- Every `StepView` carries `geometry_precision="PREVIEW_ONLY"` and `geometry_warning` referencing KL-12.
- Every rendered PNG carries a red banner `PREVIEW_ONLY (KL-12) - not a physical re-simulation`.
- `render.py` fails loudly on visible-edge discretization loss (`RenderError`); hidden-edge loss surfaces in the PNG subtitle and in `manifest.json` diagnostics.

### Backend

- `PythonOCCBackend` is used by default. `MockBackend` requires explicit `--allow-mock-backend` flag; without it, an unavailable OCC yields exit code 2 (invariant #4: no silent fallback).

### Deferred

- **Forward fold mode** — blocked on KL-12 (S.13b). `Player.switch_direction("forward")` raises `PlayerError`.
- **Per-panel kinematic unfold** (`PER_PANEL_KINEMATIC`) — blocked on KL-12.
- **FreeCAD addon GUI** — deferred; better aligned after S.13b so the 3D viewport shows honest per-panel kinematics, not whole-part rotation.

### Future extensions (Layer 6+)

See `docs/ROADMAP.md` for details. Layers 6+ cover FreeCAD GUI integration (bend annotations, tooling animation), tooling library (FCStd import), optimal tooling selection (with clamp constraints). Depend on KL-12 fix for honest per-panel kinematics.

Confirmed decisions: bend annotations will be **3D annotations** (not TechDraw); tooling animation will show **moving tooling** (punch descends, backgauge repositions). See `docs/ROADMAP.md` §11.

### Non-goals

- No 3D viewport in the headless CLI. No X11/Cocoa dependency. PNG batch + JSON metadata only.
- Viewer does not modify solver state, search tree, or verification results.
- Viewer is not a re-verification path; `production_eligible` cannot be recomputed client-side.

