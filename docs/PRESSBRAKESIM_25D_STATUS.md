# PressBrakeSim 2.5D — Status (evidence only)

Updated: 2026-11-03 · Branch `2D` · Baseline `e8f2ed1`.
Environment: macOS, FreeCAD 1.1.3 (`freecadcmd` headless), SheetMetal WB present; pytest in a
local venv (Python 3.14, no pythonocc). Level C (interactive GUI) NOT performed this iteration.

## Test results
- Baseline (`e8f2ed1`): `58 collected → 57 passed, 1 failed, 7 skipped, 6 collection errors`
- After Iteration 1 (`bd2166e`, D1 fix): `58 passed, 7 skipped, 6 collection errors`
- After Phase 2 implementation (`35a105f`): `67 passed, 7 skipped, 6 collection errors, 10 subtests passed`
- After Phase 3A implementation (`ac94016`): `70 passed, 7 skipped, 6 collection errors, 10 subtests passed`
- After Phase 3B implementation (`21c5eca`): `71 passed, 7 skipped, 6 collection errors, 10 subtests passed`
- After Phase 3B Hotfix (empty selection freeze): **`72 passed, 7 skipped, 6 collection errors, 10 subtests passed`**
- State of the 6 collection errors (recorded explicitly per Step 6):
  - 4 × `ModuleNotFoundError: No module named 'OCC'` in local non-conda Python venv (`tests/test_ambiguous_tree_returns_unknown.py`, `tests/test_end_to_end_pipeline.py`, `tests/test_feature_extractor_signed_angle.py`, `tests/test_honest_collision_and_verifier.py`).
  - 1 × `ImportError: dlopen(.../FreeCAD.so)` when importing compiled FreeCAD binary into system Python 3.14 (`tests/test_fold_multi_bend.py`).
  - 1 × `ImportError: cannot import name 'SELF_COLLISION_GAP_TOL_MM' from 'smbs_core.Physics.collision_detector'` (`tests/test_self_collision_panels.py`, defect D2 from `0d4c071`).

## Phase table

| Phase | Requirement | Impl. | Tests | FreeCAD verif. | Blockers | Next action |
|---|---|---|---|---|---|---|
| 0 | Baseline/audit | DONE | baseline recorded | headless only | – | – |
| 1 | Geometry contract (angle, r, t, K, inner_r, BA, axis) | IMPLEMENTED | TESTED (unit) | VERIFIED on 16/16 fixtures (headless) | – | – |
| 2 | Panels + explicit `BendZone` + tangent boundaries | IMPLEMENTED | TESTED (unit) | VERIFIED on 16/16 fixtures (0.000000 mm error) | – | – |
| 3 | Finite-radius 2.5D kinematics + intermediate section + atomic `FoldState` | IMPLEMENTED | TESTED (unit + headless FreeCAD) | VERIFIED on 16/16 fixtures (3D centroid/vertex/bbox = 0.000000 mm) | – | – |
| 3A / 3B / 4 | Real Geometry Viewer (`pressbrake_2d.py`) + 3D Cross-Section Exactness: load selected SheetMetal part, resolve `Body.Tip` reference face on stale unfolds (`135-(ok)`), render full coplanar multi-bend section (`Z-(ok)`, `135-(ok)`, `U-*`), finite-radius `BendZone`, both surfaces & neutral axis | IMPLEMENTED | TESTED (unit + headless FreeCAD 3D-slice comparison across 11 uniaxial models = 0.000000 mm contour/bbox error) | VERIFIED headless (`0.000000 mm` vs OpenCASCADE 3D slices); awaiting user GUI re-check on `135-(ok)` and `Z-(ok)` | User manual GUI check | Confirm remaining manual GUI checks |
| 5 | Tooling/collision 2.5D | NOT IMPLEMENTED in 2.5D path (legacy OCC exists) | – | – | – | Phase 5 |
| 6 | Sequence | NOT IMPLEMENTED in 2.5D path (legacy `Solvers/` exist) | – | – | – | Phase 6 |
| 7 | Release | NOT STARTED | – | – | – | Phase 7 |

## Phase 2 & 3 FreeCAD verification across all 16 real FCStd models
Executed via `freecadcmd` against the native `Body_Unfold`, `*_Unfold_Sketch`, and `*_Unfold_Sketch_Bends`
objects inside each of the 16 FCStd models in `~/Desktop/bend_Seq/BendSeq_Fixtures_Source/`.
Each model was built with `build_panels`, rooted at its canonical `source_face` panel, and folded
to target angles through `FoldState` using `BendZone.moving_transform`. Every folded rigid panel was
transformed back to the 3D source frame (`T.inverse()`) and compared against the source 3D planar faces:

| Model | Panels | Bends | Max 3D Centroid Error (mm) | Max 3D Vertex Distance (mm) | Max 3D BBox Dim Error (mm) | Max Area Error (mm²) |
|---|---:|---:|---:|---:|---:|---:|
| `135-(ok).FCStd` | 3 | 2 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| `2-1-3-4-(ok).FCStd` | 11 | 10 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| `L-(ok).FCStd` | 2 | 1 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| `L-30-6-(false).FCStd` | 2 | 1 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| `SQin.200-25-10-(ok).FCStd` | 9 | 8 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| `SQout.200-25-10-(ok).FCStd` | 9 | 8 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| `U-10-10-20-(false).FCStd` | 3 | 2 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| `U-10-15-10-(punch2-OK).FCStd` | 3 | 2 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| `U-10-30-10-(OK).FCStd` | 3 | 2 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| `U-60deg-30deg-(ok).FCStd` | 3 | 2 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| `U-90deg-90deg-(ok).FCStd` | 3 | 2 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| `Z-(ok).FCStd` | 3 | 2 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| `Z-10-20-10-(false).FCStd` | 3 | 2 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| `Z-10-20-10-(ok).FCStd` | 3 | 2 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| `box.100in-50out-(false).FCStd` | 9 | 8 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| `box.100out-50in-(ok).FCStd` | 9 | 8 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |

## Defects
| ID | Status | Description | Evidence |
|---|---|---|---|
| D1 | FIXED (`bd2166e`) | `match_bend_axes` measured overlap against `min(bend, sketch)` length | `test_reject_short_overlap` passes; 16/16 fixtures match |
| D2 | OPEN | `test_self_collision_panels` imports constants missing on `2D` (`SELF_COLLISION_GAP_TOL_MM`) | collection ImportError |
| S1 | RESOLVED | `2-1-3-4-(ok)` P06–P11 area residuals (0.14–0.85 mm²) | Cutting rigid panels at exact `BendZone` tangent lines yields 0.000000 mm² error on all 11 panels |
| L1 | RESOLVED | Sharp-hinge kinematics around BA centerline | Replaced by `BendZone.moving_transform` (exact inverse of SheetMetal `compute_unbend_transform` at full angle, neutral-length-preserving intermediate section at partial angles) |
| D3 | FIXED | Multi-bend cross-section in `cross_section_2d` & `LoadedPartSession` omitted non-active `BendZone` arcs and held non-active bends at 0° (`Z-(ok)` had 13.5370 mm error) | `cross_section_2d` projects all coplanar parallel bend zones and panels using `FoldState.transforms`; `LoadedPartSession` holds non-active bends at target angles (`0.000000 mm` vs 3D slice) |
| D4 | FIXED | `135-(ok).FCStd` `Body_Unfold.baseObject` referenced stale feature `Bend.Face5` (1 bend) instead of `Body.Tip = Bend001` (2 bends, `+45°/-45°`, 12.9878 mm error) | `_resolve_tip_source` in `sheetmetal_unfold_reader.py` resolves coplanar reference face on `Body.Tip`; `panel_builder.py` uses `Body.Tip` extracted outline & bend lines (`0.000000 mm` vs 3D slice) |
| D5 | FIXED (awaiting GUI confirmation) | Clicking «Деталь» with empty selection raised an exception inside `load_part()`, reset `self.session = None`, triggered unblocked `self.operations.clear()` (`currentItemChanged` signal) and `self.draw()` (`scene.clear()` + `setSceneRect`), causing FreeCAD error & UI freeze | Added early `validate_selection_preconditions` & `execute_load_part` before any pipeline/widget/scene mutation; logs warning via `PrintWarning` without traceback and preserves previous `LoadedPartSession` |
