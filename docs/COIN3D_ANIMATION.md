# SMBS Coin3D animation — implementation and acceptance

Branch: `development/coin3d-animation` (forked from `smbs` after fast-forward merge of `development/simulation-gui-polish` on 2026-11-15).

## Goal

Render a sheet-metal press-brake simulation in the same machine frame and
from the same FoldState/PressCycleController as the existing 2.5D viewer.
The GPU draws the scene; it does not decide how the sheet folds, whether a
bend is physically possible, or what constitutes a collision.

## Runtime design

- `freecad_smbs/ui/coin3d_viewer.py`: Coin3D/Quarter widget. One persistent
  SoSeparator scene with independent sheet, tooling and native OCC blocker
  sections. It does **not** create a FreeCAD document object or its own timer.
- `freecad_smbs/commands/pressbrake_2d.py`: the original controller owns the
  25ms playback timer. Its former 2D-projected 3D QGraphicsScene renderer was
  removed. The default selected graphics tab is the Coin3D widget.
- Sheet panel and bend face topology is reused while its vertex-count and
  part/bend identity remain consistent. Bending updates SoCoordinate3 arrays,
  **not** QPainter polygons, painter-path subtraction, depth sorting or a new
  scene graph every frame.
- A punch and a die are cached by selected tool geometry, axial setup, and
  bend length. Punch Z movement modifies SoTranslation only. The die remains
  in a static Coin subgraph. Display-only punch length equals the active bend
  for a simple non-sectioned punch, and it does not alter OCC tool length.
- A 30 Hz viewport ceiling is independent of the 40 Hz state timer.
  Rendering still may be slower on complex models. The sheet
  `build_sheet_preview_faces` calculation remains per displayed frame;
  this is a known next optimization target.
- Native collision inspection uses an independent sampled machine placement
  and red **bounding-box wires**; these are not collision contact surfaces.
- No invented geometry for unverified punch/die holes: end caps containing
  inner contours are omitted rather than rendered with filled-in holes.
  Profile side walls remain.
- Coin camera is initially fitted once after the first mesh; after that the
  operator owns orbit, pan, zoom and Fit. No automatic camera reset on bend,
  transfer or tool change.
- 2D stays optional, and unchanged as a diagnostic view. When JSONL motion
  tracing is enabled, the existing 2D geometry tracing may incur extra CPU
  work even if Coin3D is visible; do not misrepresent that as a normal GUI
  frame cost.

## Mandatory FreeCAD acceptance (macOS and Windows)

1. Only one installed SMBS add-on copy under the active FreeCAD Mod directory.
2. Import `pivy.coin` and `pivy.quarter.QuarterWidget` using the same
   Python embedded in the target FreeCAD build. If unavailable, report it as
   a missing dependency rather than silently resuming the old 3D renderer.
3. Start FreeCAD. Verify no `Path`, `SyntaxError`, `IndentationError`,
   `StandardButton` or Quarter scenegraph errors in the FreeCAD Report View.
4. Load a SheetMetal Unfold with at least two different bend lengths.
   Verify all panels, bent strips, die and correct-length punch render.
5. Play, pause, step, scrub, change order and run Full Cycle. The punch must
   translate along machine Z; the die remains stationary. The part moves and
   rotates to the tooling, not the other way round.
6. Orbit, pan and zoom the Coin camera; perform at least five TRANSFER stages
   and reopen Tasks. Confirm the camera does not reset until explicit Fit.
7. Switch 3D -> 2.5D -> 3D. Confirm the hidden renderer does not redraw
   on normal Coin3D timer ticks.
8. Open a real native OCC collision in the 3D viewer, verify the red blocker
   bounding box and that the geometry remains independent of the simulation.
9. Change punch and die, length, axial sections and visibility. Check that
   mesh cache invalidates and updates the correct tools, without changing
   physical collision settings.
10. Run with and without JSONL tracing. Save / reload FCStd with its embedded
    saved selections and bend orders; verify restored checks remain UNVERIFIED.
11. Collect timings via `SMBS_debug_log.json` entries `COIN3D_FRAME`
    (render_ms, frame_interval_ms, face_count). Compare with old 3D
    `PREVIEW_3D_FRAME` data from the same saved model and same hardware.
12. No user-visible claims of 60 FPS or zero lag before measurements on
    the user's 2016 MacBook Pro and Windows FreeCAD 1.1.

## Testing / safety

`.github/workflows/coin3d-source-checks.yml` runs Python compileall,
headless FCStd-JSON tests and source guard tests. They are not a substitute
for an installed FreeCAD + OpenGL test. Keep `smbs` untouched during this
development phase; only merge back when macOS and Windows behavior is
verified in FreeCAD.

### Remaining development

- Confirm Quarter interactions/zoom and camera clipping in the target
  FreeCAD runtime. Ensure Pivy SWIG and Qt bindings agree.
- Cache already-folded zone meshes and panel-local vertices by angle state
  to reduce `build_sheet_preview_faces` CPU work.
- Add true triangulated end caps around holes if needed in the visual model.
- Optional per-step frame-time percentile telemetry and adaptive GUI redraw
  cadence based on measured render time, without changing controller time.
