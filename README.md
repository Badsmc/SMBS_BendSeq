# SMBS_BendSeq

Headless bend-sequence planner for sheet metal (FreeCAD / PythonOCC).

## Status (2026-10)

**FreeCAD Runtime Release** (Development on branch `stage-1-fix-correctness`).

What works:
- Unfold via native SheetMetal
- Planning (A* / DFS backward search)
- Fold constructive path for N=1 and N=2 linear (`VALID_PER_PANEL`)
- Relief cut boundary detection and clamped box-hub folding (KL-9 closed)
- Real press brake machine frame B-Rep collision geometry & sidecar (`machine_frame.FCStd`, KL-16 closed)
- FreeCAD workbench `freecad_smbs`:
  - 1-click calculation for active document (**Рассчитать последовательность**) with auto-annotations on flat pattern
  - OpenRun, Calculate, Simulate (bidirectional unfold/fold with punch descent), Tooling, Verify, Info
- Dynamic PNG banners from `geometry_precision` (green `VALID_PER_PANEL`, red `PREVIEW_ONLY`)
- Cross-platform FreeCAD bootstrap (`FREECAD_LIB`, `FREECAD_MOD_SHEETMETAL`) with in-memory BREP conversion

Known limits:
- N≥3 tree intermediate steps may fall back to `PREVIEW_ONLY` (KL-12-v3 residual)

Full project documentation and developmental progress are tracked on branch [stage-1-fix-correctness](https://github.com/Badsmc/SMBS_BendSeq/tree/stage-1-fix-correctness).

## Quick start

### 1. In FreeCAD (Primary User Experience)

Install the workbench via symlink:
```bash
# macOS
ln -s /path/to/SMBS_BendSeq/freecad_smbs \
  ~/Library/Application\ Support/FreeCAD/Mod/freecad_smbs

# Linux
# ln -s /path/to/SMBS_BendSeq/freecad_smbs ~/.local/share/FreeCAD/Mod/freecad_smbs
```

1. Open FreeCAD and switch to the **SMBS** workbench.
2. Open or create your sheet metal model (`.FCStd`) and save it.
3. Click **Рассчитать последовательность** (Calculate Sequence) on the toolbar.
4. The pipeline automatically calculates the sequence, attaches numbered bend annotations to the unfolded pattern, and prepares **Симуляция** (Simulate) and **Верификация** (Verify).

### 2. Headless CLI (for CI / Batch Runs)

```bash
export PYTHONPATH=/path/to/SMBS_BendSeq:$PYTHONPATH
# optional overrides:
# export FREECAD_LIB=/path/to/FreeCAD/lib
# export FREECAD_MOD_SHEETMETAL=/path/to/Mod/SheetMetal

# Run planning pipeline
python -m smbs_core.cli path/to/part.FCStd

# View and render replay frames to PNG
python -m smbs_core.viewer.cli view runs/run_<id> --render-png /tmp/frames
```

## Architecture & Workflows

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
├── Geometry/          # Feature extraction, bend matcher, panel topology, sheetmetal folder
├── Physics/           # Collision detector, tooling assembly, machine kinematics
├── Solvers/           # Backward planning search implementations
├── viewer/            # Replayer, render (HLR), CLI viewer
└── IO/                # Tooling loaders, step readers
```

## Development & Test Suite

All development history, unit test suites (`tests/`), test fixtures (`SMBendTests/`), and design documentation (`docs/`) are maintained on the development branch:
- [stage-1-fix-correctness](https://github.com/Badsmc/SMBS_BendSeq/tree/stage-1-fix-correctness)
