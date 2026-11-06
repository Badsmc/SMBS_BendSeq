# PressBrakeSim 2.5D — release roadmap

Authoritative code branch: **`2D`**.
Source: `freecad_smbs/commands/pressbrake_2d.py` and
`smbs_core/kinematics2d/`. The old PythonOCC orchestration and solver
are not part of this branch after cleanup.

## Architecture

```text
FreeCAD SheetMetal native unfold
 -> sheetmetal_unfold_reader
 -> panel_builder + axis_matching
 -> PanelGraph / BendZone / FoldState
 -> BendSequenceController + PressCycleController
 -> 2.5D preview + optional 3D surface preview
 -> explicit 2D tooling/contact checks and trace/report
 -> (planned) bounded sequence planner and selective native 3D validator
```

## Phases and acceptance gates

| Stage | Deliverable | Current state |
|---|---|---|
| R0 | Clean self-contained 2D source, one workbench, testable install | Headless CI verified; native GUI pending |
| R1 | Stable 2.5D playback, manual order, 2D/3D viewer, trace files | Implemented; real FreeCAD acceptance pending |
| R2 | Trusted tooling, 2.5D contact/collision bounds and honest warnings | Partially implemented; physical qualification pending |
| R3 | Bounded automatic manufacturing sequence search, verified replay | Not yet implemented in active 2D path |
| R4 | Native model regression, UI acceptance on OS targets, release artifacts | Pending |

## Technical boundaries

- SheetMetal is the only source for unfold geometry, thickness, signed angles,
  bend allowances and K-factor; missing/ambiguous input fails closed.
- All runtime state changes occur through one 2.5D FoldState and press-cycle
  controller; UI and diagnostic views do not write independent bend states.
- Rigid planar 2D transforms and single-cutting-plane contact checks cannot
  validate out-of-plane tools, frame or clamp interference.
- A projected 3D sheet turn is for visualization only and cannot be passed to
  a physical 2D collision oracle as a real geometric section.
- Native 3D validation may be connected selectively by an explicit adapter
  when 2.5D lacks evidence. Do not run expensive OCC booleans per animation
  frame or reintroduce the old duplicate orchestrator.
- An automatic order is returned as physically valid only with replayable
  evidence; otherwise UNKNOWN, UNSUPPORTED or resource-limited must be shown.

## Exact to-do list

Use `PRESSBRAKESIM_25D_RELEASE_GATES.md` for the prioritized acceptance
checklist. `PRESSBRAKESIM_25D_STATUS.md` contains evidence and open blockers.
`PRESSBRAKESIM_UI_CONTROLS.md` defines the user-facing controls.
