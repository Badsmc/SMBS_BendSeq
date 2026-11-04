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
- After Phase 5A (`3197983`, Custom Punch & Die Tooling System): `76 passed, 7 skipped, 6 collection errors, 10 subtests passed`
- After Phase 5A Hotfix 1 (`c1b4c4f`, Fix Missing Sheet Geometry, Separated Scene Bounds, Positive Clearance Alignment & Z-Layering): `74 passed, 7 skipped, 10 subtests passed` in pure-Python `pytest` (`17/17` in `tests/test_bend_zone.py`) and `17/17` in `freecadcmd` with real Qt6 `QGraphicsScene`
- After Phase 5B (Press Brake Working Cycle & Ram Motion): **`75 passed, 7 skipped, 10 subtests passed` in pure-Python `pytest` (`18/18` in `tests/test_bend_zone.py`) and `18/18` in `freecadcmd` (`QT_QPA_PLATFORM=offscreen`)**
- State of the 6 collection errors (recorded explicitly per Step 6):
  - 4 × `ModuleNotFoundError: No module named 'OCC'` in local non-conda Python venv (`tests/test_ambiguous_tree_returns_unknown.py`, `tests/test_end_to_end_pipeline.py`, `tests/test_feature_extractor_signed_angle.py`, `tests/test_honest_collision_and_verifier.py`).
  - 1 × `ImportError: dlopen(.../FreeCAD.so)` when importing compiled FreeCAD binary into system Python 3.14 (`tests/test_fold_multi_bend.py`).
  - 1 × `ImportError: cannot import name 'SELF_COLLISION_GAP_TOL_MM' from 'smbs_core.Physics.collision_detector'` (`tests/test_self_collision_panels.py`, defect D2 from `0d4c071`).

## Phase 5B contact-placement Hotfix 2 — reported GUI failures

User manual acceptance for `eed7944`: die shoulder support **FAIL**, symmetric bending **FAIL**, whole sheet 3D **PASS**, 3D animation **PASS**, source-model immutability **PENDING**.

Targeted changes (no 3D-renderer modification):
- V support now checks **both** verified machine-space V-shoulder datums against the actual lower outline of the two panels adjacent to the active bend. Other Z flanges are excluded from support contact; they need separate collision handling.
- A single vertical offset is not incorrectly reported as dual support if shoulder heights differ. A bounded rigid placement correction (maximum 6°) attempts to seat both shoulders without changing any FoldState angle; excessive imbalance and insufficient flange spans yield explicit `DIE_SUPPORT_...` diagnostic.
- On an unsupported contact geometry, both 2.5D and whole-sheet 3D remain in `SYMMETRIC_V_PREVIEW`, with GUI warning `DIE_CONTACT_UNVERIFIED`. Previously the 2.5D view silently returned to identity pose (one flange stationary), while 3D could show an error.
- Existing working 3D geometry, animation, GUI workspace, imports, bend sequence, punch and die profiles remain untouched.
- Regression tests updated for dual shoulder seating, missing shoulder, rigid preservation of relative bend angle.
- **Commit is not yet verified in interactive FreeCAD GUI. Do not mark this as resolved until physical GUI tests pass.** Source file unchanged does not establish user model immutability; pending user verification remains open.

## Phase 5B follow-up — die support + whole-sheet 3D preview

- User GUI feedback: prior symmetric motion worked, but the sheet floated above the die and the 3D tab showed only a static source-model reference.
- Changed `machine_pose_for_cycle` to derive an idealized support-height offset from verified die shoulder datums and actual folded-sheet lower surface intersections. At least one shoulder supports the sheet without forced penetration of either shoulder by the sampled exterior. If shoulder support cannot be established, explicitly marks `RAM_ONLY_SUPPORT_ASSUMED`.
- Important limit: support placement does NOT solve the actual ram stroke vs contact path, punch interference, springback, material forces, or 3D collisions. The existing `preview_stroke_mm` may be incompatible with geometric support; this is still `KINEMATIC_PREVIEW` and not physically validated.
- Added `preview_mesh_3d.py`: builds all panel top/bottom/side faces and all finite-radius BendZone surface strips in full-part 3D machine coordinates using authoritative `FoldState.transforms`, with one common WorkpiecePose.
- The 3D tab now updates during playback from the same timer and angles as the 2.5D tab; tools use projected working-profile outlines. This is an isometric **surface** visualization, not an OCC BRep solid 3D machining verifier, and camera rotation is not yet implemented.
- Removed unused static FreeCAD Shape snapshot to avoid duplicating or altering the source model; live 3D representation uses a headless mesh in the viewer only.
- Added pure-Python regression tests for V shoulder support and whole-sheet 3D motion plus dedicated GitHub Actions source/geometry checks.
- **Not yet verified in an interactive FreeCAD GUI. Do not close Phase 5B before user confirms proper die support and animated whole-part 3D preview.**

## Phase 5B symmetric workpiece hotfix — implementation pending GUI acceptance

- Added `smbs_core/kinematics2d/workpiece_pose_2d.py`: a headless, rigid `WorkpiecePose2D` with `machine_pose_for_cycle` and `transform_sheet_section`.
- `FULL_CYCLE` now applies one machine transform to all sheet panels, finite-radius zones, inner/outer surfaces, neutral curves and tangents; `BEND_ONLY` continues to render the original `FoldState` section unchanged.
- Supported preview symmetry uses current neighboring flange vectors and an explicit assumed flip on negative bends; the installed V-groove datum is the horizontal machine anchor.
- During APPROACH and CONTACT the sheet stays stationary. During BENDING its active zone follows the programmed *preview* ram stroke; during RETRACT it holds the completed pose.
- The configured punch stroke is **not** derived from physical tooling contact. Exact punch/die interference checks and automatic feasible stroke remain unimplemented (future Phase 5C). No claim of physically valid machine bending.
- Added pure-Python regression tests `tests/test_workpiece_pose_2d.py` for symmetric flange elevation, +/- bends, inverse round trips, motion phases, full cross-section propagation, and Bend Only identity.
- **This hotfix has not yet been run in the user's interactive FreeCAD GUI.** Remote CI has no reported statuses for these commits; do not mark Phase 5B verified or closed.

To run headless tests:
```sh
PYTHONPATH=. python -m unittest tests.test_workpiece_pose_2d -v
PYTHONPATH=. python -m pytest -q tests/test_workpiece_pose_2d.py tests/test_bend_zone.py
```

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
| 5A | Custom Punch & Die Tooling System + Hotfix 1 (`ToolProfile`, DXF/STEP/FreeCAD-Shape importers, `App.ActiveDocument` preservation during `.FCStd` tool scan, separated `SceneBounds2D` with tall-tool sheet focus, positive initial clearance `5.0 mm` in `align_to_cross_section`, explicit `QGraphicsScene` Z-layers `0..50`, full compatibility with `Play All` & manual bend order) | IMPLEMENTED | TESTED (`18/18` in `test_bend_zone.py` + `18/18` in `freecadcmd` with real Qt6 `QGraphicsScene`) | VERIFIED baseline `c1b4c4f` | – | – |
| 5B | Press Brake Working Cycle & Ram Motion (`PressCycleController`, `CycleConfig`, `CyclePhase`: `IDLE -> APPROACH -> CONTACT -> BENDING -> BOTTOM -> RETRACT -> STEP_COMPLETE -> COMPLETED`, `Bend Only / Full Cycle` mode, explicit `KINEMATIC_PREVIEW`, `GEOMETRIC_CONTACT` vs `ASSUMED_CONTACT`, `ASSUMED_WORKPIECE_FLIP_FOR_OPPOSITE_BEND`, single `QTimer`) | IMPLEMENTED | TESTED (Tests A–K in `test_bend_zone.py` + `18/18` in `freecadcmd` with real Qt6 `QGraphicsScene`) | Awaiting user interactive GUI confirmation in FreeCAD | User manual GUI check | Confirm `Full Cycle` (`APPROACH -> BENDING -> RETRACT`) in FreeCAD GUI |
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
| D6 | FIXED (Phase 5A Hotfix 1) | Opening PressBrakeSim 2.5D panel in `3197983` called `ToolLibrary25D.list_tools()`, which opened and closed `puancon.FCStd` and `matrica.FCStd` via `App.openDocument`/`App.closeDocument`, resetting `FreeCAD.ActiveDocument` to `None` and preventing `load_part()` from loading the selected SheetMetal part; additionally, tall tools (>120 mm) dwarfed the 2 mm sheet in `sceneRect` and painted over the sheet without Z-layering, and `align_to_cross_section` used `clearance_mm=0.0` | `_load_legacy_fcstd_tool` now saves and restores `App.ActiveDocument`; `validate_selection_preconditions` falls back to `selection[0].Document`; `compute_scene_bounds_2d` separates `sheet_bounds`, `punch_bounds`, `die_bounds`, `combined_bounds` and keeps `view_rect` focused on the sheet + working zone; `align_to_cross_section` uses positive `clearance_mm=5.0 mm`; `draw()` assigns explicit `setZValue` (`10/15` for tools, `20..50` for sheet) |
