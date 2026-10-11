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
