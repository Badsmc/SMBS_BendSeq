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
- After Phase 3B Hotfix 1 (`914f9e0`): `72 passed, 7 skipped, 6 collection errors, 10 subtests passed`
- After Phase 3B Hotfix 2 (`5f1cb47`): `72 passed, 7 skipped, 6 collection errors, 10 subtests passed`
- After Phase 3B sequential bending restoration (`15ff5a6`): `73 passed, 7 skipped, 6 collection errors, 10 subtests passed`
- After Phase 4A (`a2d77f1`): `74 passed, 7 skipped, 6 collection errors, 10 subtests passed`
- After Phase 4B (`4b0df54`, Manual Bend Order Editor): `75 passed, 7 skipped, 6 collection errors, 10 subtests passed`
- After Phase 5A (Custom Punch & Die Tooling System): **`76 passed, 7 skipped, 6 collection errors, 10 subtests passed`** (plus `16/16` in `freecadcmd` including STEP & FreeCAD Shape tests C & D)
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
| 3A / 3B | Real Geometry Viewer + 3D Cross-Section Exactness + NotificationArea deadlock fix + flat initial state | IMPLEMENTED | TESTED (unit + headless FreeCAD 3D-slice comparison = 0.000000 mm) | VERIFIED in FreeCAD GUI by user (`15ff5a6`) | – | – |
| 4A | Automatic Bend Sequence Playback (`BendSequenceController`: `IDLE`, `PLAYING`, `PAUSED`, `COMPLETED`, `Play All`, `Pause/Resume`, `Next Step`, `Previous Step`, `Reset`, manual override compatibility) | IMPLEMENTED | TESTED (unit tests A–F in `test_bend_zone.py`) | VERIFIED in FreeCAD GUI by user (`a2d77f1`) | – | – |
| 4B | Manual Bend Order Editor (`set_bend_order`, `move_up`, `move_down`, `restore_canonical_order`, strict flat-state reorder rule, `KINEMATICALLY SIMULATED` status) | IMPLEMENTED | TESTED (unit tests A–G in `test_bend_zone.py`, reverse order `B02 -> B01` on Z and 135 matches 3D slices `< 1e-9 mm`) | VERIFIED in FreeCAD GUI by user (`4b0df54`) | – | – |
| 5A | Custom Punch & Die Tooling System (`ToolProfile`, DXF/STEP/FreeCAD-Shape importers, exact line/arc geometry, `PunchDatums2D`/`DieDatums2D`, coordinate transforms, `ToolingSetup2D`, persistent `ToolLibrary25D`, UI integration) | IMPLEMENTED | TESTED (Tests A–H in `test_bend_zone.py` + `freecadcmd` 3D STEP/Shape verification) | Awaiting user interactive GUI confirmation in FreeCAD | User manual GUI check | Confirm `Import Punch`, `Import Die`, `Tool Library`, `Align Tooling`, `Hide/Show Tooling` in FreeCAD GUI |
| 5B | Ram motion, contact & collision validation 2.5D | NOT IMPLEMENTED in 2.5D path (legacy OCC exists) | – | – | – | Phase 5B |
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
| D5 | FIXED in code (`NOT VERIFIED` in interactive GUI until user test) | macOS Hang Report confirmed deadlock in `Gui::NotificationArea` (`showInNotificationArea → createNSWindow → qt_message_output → FreeCAD messageHandler → ConsoleSingleton::notifyPrivate → NotificationAreaObserver::sendLog → NotificationArea::pushNotification → std::mutex::lock`) triggered by `FreeCAD.Console.PrintWarning`/`PrintError` during UI callbacks | Removed all `FreeCAD.Console.*` calls from `pressbrake_2d.py`; empty selection updates only `self.status` without invoking `FreeCAD.Console`, `NotificationArea`, scene redraw, or timer/tree changes; technical logs go to `~/Library/Application Support/FreeCAD/v1-1/pressbrake_2d.log` |
