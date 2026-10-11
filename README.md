# SMBS / BendSeq

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

## Runtime Environment
- Python 3.11+
- OpenCASCADE (pythonocc-core 7.7+) / FreeCAD 1.1.3
