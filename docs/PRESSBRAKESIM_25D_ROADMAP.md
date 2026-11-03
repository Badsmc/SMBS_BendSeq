# PressBrakeSim 2.5D — Authoritative Roadmap

Branch: `2D` · Baseline commit: `e8f2ed1` · Target: FreeCAD 1.1.x, Python 3.11, SheetMetal WB.
Live evidence/status: see `docs/PRESSBRAKESIM_25D_STATUS.md`.

## 1. Objective and scope

A fast, deterministic, physically credible **2D/2.5D** press-brake simulator inside FreeCAD.
It is NOT a full 3D simulation engine.

In scope: rigid planar panels, finite-radius bend zones, thickness, signed angles, bend
axes, panel tree kinematics, tool profiles, 2D/2.5D collision checks, sequence search, UI.

Excluded: per-frame / per-candidate 3D OCC Booleans, FEM/springback, generic 3D collision.
A 2D collision-free result never implies 3D collision freedom; unsupported checks return
`UNKNOWN`/`UNVERIFIED`, never `PASS`.

## 2. Target architecture

```mermaid
flowchart LR
  SM["FreeCAD SheetMetal getUnfold"] --> R["sheetmetal_unfold_reader"]
  R --> AM["axis_matching"]
  AM --> PB["panel_builder"]
  PB --> PG["panel_graph"]
  PG --> PK["panel_kinematics FoldState"]
  PK --> CV["collision validation 2.5D"]
  CV --> SS["sequence solver"]
  PK --> UI["pressbrake_2d task panel"]
  DV["direction_validator (optional 3D check)"] -.-> UI
  PFI["physical_face_inventory (diagnostic only)"] -.-> DV
```

| Module | Responsibility | Runtime dependency |
|---|---|---|
| `freecad_smbs/integration/sheetmetal_unfold_reader.py` | Read `BendInfo` (angle, radius, axis, thickness, ANSI K-factor, inner radius, BA) from SheetMetal; fail closed on missing/invalid/CSV/spreadsheet K-factor | FreeCAD + SheetMetal |
| `smbs_core/kinematics2d/bend_zone.py` | Explicit finite-radius `BendZone` model (inner/neutral/outer radii, BA, tangent lines, intermediate section polylines, finite-radius moving transform) | Pure Python |
| `smbs_core/kinematics2d/axis_matching.py` | Unique geometric matching of bend axes to `_Sketch_Bends` lines | Pure Python |
| `freecad_smbs/integration/panel_builder.py` | Split flat outline by narrow separator strips for topology, then bound rigid panels by `BendZone` tangent lines; reject overlapping/consuming bend zones | FreeCAD Part (2D planar cut, once per build) |
| `smbs_core/kinematics2d/panel_graph.py` | Panel/bend tree, rooting at fixed panel, propagation of `BendZone` and moving side (`moving_left`), ambiguity rejection | Pure Python |
| `smbs_core/kinematics2d/panel_kinematics.py` | Drift-free absolute-angle rigid panel & finite-radius `BendZone` kinematics | Pure Python |
| `smbs_core/kinematics2d/fold_sequence.py`, `engine.py` | Sequence replay helpers | Pure Python |
| `freecad_smbs/commands/pressbrake_2d.py` | 2D task panel UI (slider, play, reset, fit, diagnostics) | FreeCAD GUI |
| `freecad_smbs/integration/direction_validator.py` | Conservative reference 3D sign validation | FreeCAD (validation only) |
| `freecad_smbs/integration/physical_face_inventory.py` | Read-only face pairing diagnostic. **Must stay optional.** | FreeCAD |
| `smbs_core/Geometry/feature_extractor.py`, `Core/bend_transform.py`, `Physics/*`, `Solvers/*`, `workers/*` | Legacy 3D/OCC pipeline (HandMod lineage). Reuse concepts, not the OCC dependency. | pythonocc |

## 3. Data contracts and coordinate systems

- **Sketch frame (SF):** SheetMetal `move_to_origin` frame; flat pattern in z=0. `BendInfo.line`
  and `_Sketch_Bends` lines live here. `getUnfold()[2]` (`trimmed_bend_lines`) is transformed
  back to the **source local frame** — never mix it with `BendInfo.line` without the transform.
- **Source local frame:** body shape with placement removed (`getUnfold` line 1488).
- **World frame:** document placement.
- `BendRecord2D`: `bend_id`, `source_angle_deg` (SheetMetal signed included-complement), `fold_angle_deg`
  = sign·(180−|angle|), `radius_mm` (reference-face cylinder radius), `length_mm`, `start_xyz`, `end_xyz`,
  `thickness_mm`, `k_factor_ansi`, `inner_radius_mm`, `bend_allowance_mm`.
- **Bend line semantics (confirmed from SheetMetal source `unroll_cylinder`, L1208–1232):** the bend
  line is the **centerline of the bend-allowance zone** (offset BA/2 from each tangent line).
- **Rigid panel boundaries:** `PanelBuilder` retains the 0.02 mm separator strips strictly for topology
  identification, then cuts each topological island along the `BendZone` tangent lines (±BA/2) to produce
  exact rigid panels and unfolded bend-zone faces. Verified across all 16 FCStd models with 0.000000 mm
  error in 3D centroids, vertices, bounding boxes, and areas.

## 4. Phases

Maturity levels: `DECLARED → IMPLEMENTED → TESTED → VERIFIED → QUALIFIED → PRODUCTION_READY`.

| Phase | Goal | Acceptance |
|---|---|---|
| 0 Baseline | Audit, baseline tests, actual architecture | Baseline SHA, tests run, failures documented |
| 1 Geometry contract | Signed angles, r, t, K, stable IDs, frames, reject ambiguity | Consistent bend data on all fixtures; no silent fallback |
| 2 Panels + bend zones | Separate rigid panels (tangent-line bounded) from finite bend zones (width BA) | Panel dims match planar source faces with no unexplained constants; L/U/multi-bend validated |
| 3 Kinematics | Finite-radius bend-zone kinematics, signed, subtree, reversible | Flat→Folded→Flat repeatable; folded matches source within tolerance |
| 4 UI/animation | Load, Build, Fixed Panel, Bend List, Slider, Play/Pause/Prev/Next/Reset/Fit, Collision, Find Sequence, Export | Controls enabled only when prerequisites hold; no placeholders |
| 5 Tooling/collision | Punch/die profiles, contact, clearances, backgauge, broad phase | PASS/FAIL/UNKNOWN; known valid pass, invalid fail |
| 6 Sequence | Manual validation, greedy, DFS backtracking, optional A* (reuse `smbs_core/Solvers`) | Every sequence replayable; failures carry reasons |
| 7 Release | Regression, real models, perf, install, docs | Milestones v0.6…v1.0 (planned, not claims) |

### Phase 2 & 3 geometry/kinematics execution status
1. ✅ Trace flat→panel path; confirm bend-line semantics (BA centerline).
2. ✅ Explain contour diagnostic mismatches on L, U, and 2-1-3-4 (flat island included BA/2).
3. ✅ Add `thickness_mm`, `k_factor_ansi`, `inner_radius_mm`, `bend_allowance_mm` to `BendRecord2D` (from SheetMetal calculator, no defaults, cross-checked against SheetMetal BA).
4. ✅ Implement explicit `BendZone` (`smbs_core/kinematics2d/bend_zone.py`): inner/neutral/outer radii, tangent lines at ±BA/2, intermediate section polylines preserving neutral length BA, and finite-radius `moving_transform`.
5. ✅ Bound rigid panels in `PanelBuilder` by `BendZone` tangent lines while preserving 0.02 mm separator strips for topology; reject overlapping or panel-consuming bend zones.
6. ✅ Adapt `FoldState` to recompute deterministically from flat state (zero cumulative drift) and apply `BendZone.moving_transform` when present; verified across all 16 FCStd models (79 panels, 63 bends) with 0.000000 mm 3D deviation.

## 5. UI control activation rules
- Build 2.5D: requires active doc with SheetMetal unfold available.
- Fixed panel / Bend list / Slider / Play / Reset / Fit: require successful build.
- Collision Check / Find Sequence: require build + tooling selected; return UNKNOWN when unsupported.
- Export Report: requires at least one computed result.

## 6. Collision validation limits
2D section checks prove nothing outside the modelled section planes. Longitudinal extents and
out-of-section features → `UNKNOWN` unless explicitly modelled.

## 7. Test strategy
- Level A: pure Python (`tests/`, venv + pytest). Level B: `freecadcmd` scripts on real fixtures
  (`~/Desktop/bend_Seq/BendSeq_Fixtures_Source`, 16 FCStd). Level C: interactive FreeCAD (manual).
- CI (`.github/workflows/ci.yml`) runs Level A with pythonocc via micromamba.

## 8. Known defects and risks
See STATUS file §Defects. Key risks: legacy OCC pipeline duplicates geometry concepts; sharp-hinge
kinematics; K-factor tables unsupported (fail closed); fixtures are outside the repository.
