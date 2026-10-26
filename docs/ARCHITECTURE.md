# smbs_core Architecture

> **Architecture scope (annotated 2026-10-25):** This document covers Layers 1–5 (Stages 1–4: geometry backend, core, physics/solvers, CLI/IO, viewer). It does NOT cover later stages (Stages 5–9, Req 6, Req 2 F.1–F.3a) — see footer and `docs/PROGRESS.md`. Current HEAD: `87d7c03` (stage-1-fix-correctness). Current release verdict: **READY** (qualified at HEAD `917eb71`; see `docs/AUDIT_FULL_2026-10-23.md` §Release Verdict).

```
┌──────────────────────────────────────────────────────┐
│  LAYER 5: Viewer / FreeCAD Adapter                   │
│  • run_dir → timeline (viewer/ modules)              │
│  • CLI: python -m smbs_core.viewer view <run_dir>    │
│  • Offscreen HLR+PIL renderer (PREVIEW_ONLY)         │
│  • FreeCAD addon GUI — S.69 (freecad_smbs/)          │
└──────────────────────────────────────────────────────┘
                        ↑↓ JSON / STEP
┌──────────────────────────────────────────────────────┐
│  LAYER 4: CLI + IO                                   │
│  • smbs-cli                                          │
│  • bendseq_writer (JSON v1.0)                        │
│  • step_reader, fcstd_reader, tooling_loader         │
└──────────────────────────────────────────────────────┘
                        ↑↓
┌──────────────────────────────────────────────────────┐
│  LAYER 3: Physics + Solvers + Geometry               │
│  • Validator, CollisionDetector, ToolingAssembly     │
│  • FeatureExtractor, PanelTopology                   │
│  • A*, DFS, Greedy                                   │
└──────────────────────────────────────────────────────┘
                        ↑↓
┌──────────────────────────────────────────────────────┐
│  LAYER 2: Core                                       │
│  • FoldState, BendTransform, StateCache              │
│  • BendGraph, PanelGraph                             │
│  • Orchestrator                                      │
└──────────────────────────────────────────────────────┘
                        ↑↓
┌──────────────────────────────────────────────────────┐
│  LAYER 1: GeometryBackend (ABC)                      │
│  • PythonOCCBackend, MockBackend                     │
│  • does NOT import FreeCAD                           │
└──────────────────────────────────────────────────────┘
```

## Core Architectural Rules:
1. Layers 1–4 MUST NOT import `FreeCAD`, `Part`, or `FreeCADGui`.
2. All geometric operations MUST go through `GeometryBackend`.
3. `UNKNOWN` status MUST NEVER be treated as `VALID`.
4. No hardcoded fallback values, path assumptions, or silent error swallowing.
5. **`confidence=CONFIRMED` is reserved** for parameters whose `source` is `SHEETMETAL_NATIVE` or `GEOMETRIC_VERIFIED`. Parameters derived from defaults or inference MUST carry `confidence=ESTIMATED` or `confidence=UNKNOWN`.
6. **`production_eligible=True` is a release gate**, not a functional flag. It requires: `verification_status=VERIFIED`, all metrics within tolerance, AND all P0 defects in `docs/AUDIT_FULL_2026-10-23.md` closed with reproducible evidence and a negative test case.
7. **Silent fallback on critical geometric parameters is prohibited.** Any missing or inferred value MUST be surfaced as `UNKNOWN` or `ESTIMATED` in `source`/`confidence`, never masked as `CONFIRMED`.

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
replayer.replay_backward()   → Iterator[StepView]     (PREVIEW_ONLY for N≥2; VALID_PER_PANEL for N=1 disk-state via P1a)
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
- Every `StepView` carries `geometry_precision="PREVIEW_ONLY"` and `geometry_warning` referencing KL-12 (for N≥2). For N=1 with valid `flat_pattern.brep`, P1a disk-state path yields `VALID_PER_PANEL`, no warning — genuine solid on both steps (`_is_valid_solid` gate via `BRepCheck_Analyzer`).
- Every rendered PNG carries a red banner `PREVIEW_ONLY (KL-12) - not a physical re-simulation` (unchanged — PNG renderer not yet updated; P1c scope).
- `render.py` fails loudly on visible-edge discretization loss (`RenderError`); hidden-edge loss surfaces in the PNG subtitle and in `manifest.json` diagnostics.

### Backend

- `PythonOCCBackend` is used by default. `MockBackend` requires explicit `--allow-mock-backend` flag; without it, an unavailable OCC yields exit code 2 (invariant #4: no silent fallback).

### Deferred

- **Forward fold mode** — blocked on KL-12 (S.13b). `Player.switch_direction("forward")` raises `PlayerError`.
- **Per-panel kinematic unfold** (`PER_PANEL_KINEMATIC`) — blocked on KL-12.
- **FreeCAD addon GUI** — S.69 scaffold + menu + read-only commands + «Расчёт» (3D annotations); S.70 «Симуляция» (F.2+F.3a playback, PREVIEW_ONLY, `MACHINE: SIMPLIFIED (KL-16 pending)` badge); S.71 «Оснастка» (5-tab Task Panel: Punches/Dies/Machine Frame/Backgauge/Clamps, β1 honesty, case-insensitive resolver for real `.FCStd`, no sidecar → direct display, KL-28). F.3b (punch descent + backgauge motion + machine frame geometry) blocked on KL-16. Headless `python -m smbs_core.viewer view <run_dir>` unchanged. **P1a (2026-10-26):** `replayer` N=1 disk-state path yields `VALID_PER_PANEL` — both states genuine solids (`_is_valid_solid` gate). N≥2 remains PREVIEW_ONLY (P1b). KL-12-v3 remains OPEN (multi-bend).

### Future extensions (Layer 6+)

See `docs/ROADMAP.md` for details. Layers 6+ cover FreeCAD GUI integration (bend annotations, tooling animation), tooling library (FCStd import), optimal tooling selection (with clamp constraints). Depend on KL-12 fix for honest per-panel kinematics.

Confirmed decisions: bend annotations will be **3D annotations** (not TechDraw); tooling animation will show **moving tooling** (punch descends, backgauge repositions). See `docs/ROADMAP.md` §11.

### Non-goals

- No 3D viewport in the headless CLI. No X11/Cocoa dependency. PNG batch + JSON metadata only.
- Viewer does not modify solver state, search tree, or verification results.
- Viewer is not a re-verification path; `production_eligible` cannot be recomputed client-side.

---

## Current Status (annotated 2026-10-25)

This architecture document describes Layers 1–5 (Stages 1–4). It predates:

- Stage 5 (Tooling library, Phase B).
- Stage 6 (Req 4.1 Clamp constraints).
- Stage 8 (KL-8 tooling collision, APPROACH scope).
- Stage 9 / Req 4 Phase E (Optimal tooling selection).
- Req 6 — Search SLA (Phase 6a, 2026-10-21).
- Req 2 Phases F.1 / F.2 / F.3a (per-panel kinematics, FreeCAD Timeline UI, tooling overlay).

The Stage 4 `### Deferred` section records forward-fold as "blocked on
KL-12 (S.13b)" — this remains TRUE as of 2026-10-23. The architectural
forward-fold track is tracked in `docs/ROADMAP.md` under KL-12 v3.

**Not described here:** P0-CODE-1..5 / P0-DOC-1..4 history (all closed S.39–S.51;
see `docs/AUDIT_FULL_2026-10-23.md`, `docs/PROGRESS.md` S.40–S.50). Acceptance
Principles 13–15 now in Core Architectural Rules above, items 5–7.

**Full content refresh (Stages 5–9, Req 6, Req 2):** stale current-state
facts truth-upped S.63 (this pass); full content addition of Stages 5–9,
Req 6, Req 2 tracked separately in `docs/WORKING_CHECKLIST.md` §Deferred.

**Current release decision: READY** (qualified at HEAD `917eb71`; see `docs/AUDIT_FULL_2026-10-23.md` §Release Verdict).
