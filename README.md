# SMBS / BendSeq

> **Status (annotated 2026-10-23):** advanced prototype, **NOT READY for release**. HEAD `91d07c0`, baseline 550 tests. See §Current Status and `docs/AUDIT_FULL_2026-10-23.md`.

Sheet Metal Bending Sequence Planner & Verification Engine.

## Specification
Built according to **ТЗ v1.2** (11.10.2026), incorporating:
- 24 architectural principles (headless-first, file-based isolation, no silent defaults, provenance tracking).
- 3 execution stages:
  1. **Baseline**: Unfold -> Align -> Fold -> Compare (B-Rep Hausdorff & invariant check).
  2. **Search**: Pre-filter + A*/DFS backward planning with PhysicalValidator on each state transition.
  3. **Verification**: Independent Verifier process without access to solver cache, validating physical transitions and producing canonical `part_bendseq.json` (v1.1).

## Architecture
```
smbs_core/
├── contracts/
│   └── schemas/       # JSON Schema specifications for inter-worker data contracts
├── coordinator/       # Coordinator orchestrating file flows across runs/<run_id>/
├── protocols/         # File store, canonicalization, and SHA-256 content hashing
├── workers/           # Isolated worker processes communicating via versioned files:
│   ├── sheetmetal/    # FreeCAD SheetMetal adapter (native unfolds & bend lines)
│   ├── geometry/      # OpenCASCADE B-Rep feature extraction & neutral unfold
│   ├── topology/      # Panel & bend topological graph construction
│   ├── validator/     # Physical collision & kinematic transition validator
│   ├── solver/        # Deterministic search algorithms (A*, DFS, Greedy)
│   ├── verifier/      # Independent verifier worker
│   └── export/        # Canonical part_bendseq.json v1.1 generator
├── Core/              # Bend transform, FoldState, state cache
├── Geometry/          # Feature extraction, bend matcher, panel topology
├── Physics/           # Collision detector, tooling assembly, machine kinematics
├── Solvers/           # Backward planning search implementations
└── IO/                # Tooling loaders, step readers
```

## Documentation Index

| Document | Purpose |
|---|---|
| `docs/PROGRESS.md` | Current snapshot: HEAD, baseline, open P0 defects |
| `docs/ROADMAP.md` | Authoritative plan: phases, dependencies, acceptance |
| `docs/ARCHITECTURE.md` | Implemented Layers 1–5 (Stages 1–4); Stages 5+ pending |
| `docs/KNOWN_LIMITATIONS.md` | Historical; canonical list in `PROGRESS.md` §Known Limitations |
| `docs/WORKING_CHECKLIST.md` | Active task tracking (Next / In Progress / Done) |
| `docs/TEST_MATRIX.md` | Historical; current matrix in `PROGRESS.md` §Test Metrics |
| `docs/REGRESSION_REPORT.md` | Historical Stage 1 baseline evidence |
| `docs/RELEASE_AUDIT.md` | Historical; current verdict in `AUDIT_FULL` |
| `docs/AUDIT_FULL_2026-10-23.md` | Full audit: P0 registry, evidence, scope |

## Runtime Environment
- Python 3.11+
- OpenCASCADE (pythonocc-core 7.7+) / FreeCAD 1.1.3

---

## Current Status (annotated 2026-10-23)

**HEAD:** `91d07c0` (stage-1-fix-correctness). Baseline 550 / 0F / 0E / 3xF.  
**Release verdict: NOT READY.** See `docs/AUDIT_FULL_2026-10-23.md` §Release Verdict.

- **Open P0 defects:** P0-CODE-1..5 (integrity) + P0-DOC-1..4 (historical annotation). See `docs/PROGRESS.md` §Open P0 Defects.
- **KL-12 v3 (forward fold, panel shell disconnection):** INVESTIGATED → **BLOCKED**. Root cause: rigid-body kinematic model without bend-region deformation.
- **KL-16 (machine frame):** missing `machine_frame.FCStd`; CAD session pending.
- **Verified (by 550-test baseline):** worker contracts, SheetMetal native unfold, backward search + validator, independent verifier with strict `UNKNOWN != VALID` gate, export gate `production_eligible`.
- **Not verified:** physical fold for arbitrary parts (only L / U / 4-bend fixtures), forward-fold animation, `confidence=CONFIRMED` integrity (see P0-CODE-1..5).

---

*README body (§Specification, §Architecture, §Runtime Environment) reflects intended design from commit `d63eac2` (2026-10-11). Status and Documentation Index were added 2026-10-23 during the post-audit standardization pass. For verified state see §Current Status and `docs/PROGRESS.md`.*
