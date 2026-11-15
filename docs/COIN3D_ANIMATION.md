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
- Sheet panel and bend faces are batched by material into a handful of
  persistent SoIndexedFaceSet GPU nodes instead of one graphics item per
  face. Bending updates a few SoCoordinate3 arrays, **not** QPainter polygons,
  painter-path subtraction, software depth sorting or a new scene graph per
  frame.
- A cached, already-folded world-space surface mesh is reused across
  APPROACH, RETRACT and TRANSFER when bend angles have not changed. Each
  frame applies the current authoritative rigid machine placement once;
  bending-angle changes correctly invalidate that mesh.
- A punch and a die are cached by selected tool geometry, axial setup, and
  bend length. Punch Z movement modifies SoTranslation only. The die remains
  in a static Coin subgraph. Display-only punch length equals the active bend
  for a simple non-sectioned punch, and it does not alter OCC tool length.
- The 25 ms timer targets 40 Hz for both state progression and the native
  GPU view. Duplicate render requests within 15 ms are coalesced; no 33 ms
  gate that inadvertently limits a 40 Hz timer to 20 FPS.
  Rendering can still be slower than 40 FPS on complex models.
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
- For a subsequent optimization, update rigidly moving sheets using one
  GPU scenegraph transform instead of reprojecting world faces to new arrays
  on every machine transfer; verify the FreeCAD/Coin matrix convention first.
- Add true triangulated end caps around holes if needed in the visual model.
- Optional per-step frame-time percentile telemetry and adaptive GUI redraw
  cadence based on measured render time, without changing controller time.

## macOS FreeCAD 1.1 fallback (2026-11-15)

The user's macOS package imports the SMBS workbench, but `from pivy.quarter
import QuarterWidget` raised ImportError when pressing Detail. FreeCAD's
Quarter is compiled into its GUI and need not be exposed as Python Pivy.

Fixes in this branch:
- `pivy.coin` and `pivy.quarter` are tested separately. When `pivy.coin`
  is present but Python Quarter is absent, SMBS uses the **existing FreeCAD
  document Coin3D view** via `Gui.getDocument(name).activeView().getSceneGraph()`.
  It attaches one temporary SoSeparator containing our cached GPU meshes,
  without modifying the native FCStd objects or resuming the QPainter viewer.
- The 3D tab becomes a navigation/status pane for the native document viewer.
  The workspace MDI window and existing FreeCAD model view are tiled so the
  latter is actually visible. Camera Fit operates on the native FreeCAD view.
- If even `pivy.coin` is absent, the 3D tab is disabled; Detail, the main
  workspace, 2.5D playback, search and FCStd storage still work. This is an
  explicit degraded mode, **not** a silent substitute renderer.
- The temporary Coin node is detached when the workspace closes. It is not
  serialized in the FCStd file.
- The unrelated startup `NameError: Path` was caused by FreeCAD `exec`
  using separate globals and locals. `InitGui.py` and `Init.py`
  now import `Path` and `sys` inside `_checkout_root`; a regression test
  reproduces the split-namespace execution.

Limitations: when running native mode, the FreeCAD document view may show
its original part together with the simulation overlay; the operator can
hide the original CAD object using FreeCAD's visibility control without
changing the underlying geometry. Neither a guaranteed full GUI integration
nor measured FPS on the user's machine has been established yet. Native
scenegraph lifecycle, camera and tiling must be tested in FreeCAD.

User acceptance: reboot FreeCAD after installing the branch, click Detail,
verify both MDI windows appear, play a bend, confirm the scene updates in
FreeCAD's original 3D window, then close SMBS and confirm the temporary mesh
disappears without removing/altering the CAD part.

## Dedicated native 3D simulation window (2026-11-15)

On macOS FreeCAD without the Python `pivy.quarter` module, Coin3D now
renders in a **separate, temporary FreeCAD document window** named
`SMBS_Animation_NN`, rather than placing our scene graph on top of the
source FCStd document's 3D view. The original FCStd geometry, view provider,
visibility settings and saved archive are left alone. Simulation surface
geometry remains an ephemeral Coin3D separator and creates no document
objects. Source document identity is restored after opening the animation
document so normal SMBS operations and FCStd persistence remain bound to it.

- A new simulation window is created lazily on first 3D render, not on each
  timer tick; the same window and mesh batches are reused through playback.
- Loading another valid part clears old sheet/tool meshes and reapplies
  first-frame Fit, but does not create an additional animation document
  if the source document is unchanged.
- When SMBS is closed the ephemeral Coin3D node is detached and **only**
  the SMBS-owned animation document is closed. Closing/reopening the
  animation document during playback should recreate it on the next render.
- The native FreeCAD MDI layout initially tiles the animation and SMBS
  controls with other open document windows. This is a separate FreeCAD
  document/view, **not** an independent macOS top-level application window.
- On systems exposing Python Quarter, the existing embedded Quarter widget
  is still used; the macOS native fallback is the one changed here.
- Source guard test added to CI for separate-document creation,
  binding, active-document restoration and safe cleanup.

Runtime manual verification required: create, play, pause, scrub, rotate,
zoom and close SMBS with an unsaved FCStd; confirm no unwanted save prompt,
no changes to the source scenegraph, no stale temporary document, and
that clicking a new document window does not redirect saved SMBS state.

## Native macOS GUI simplification (2026-11-15)
The original SMBS workspace no longer creates a redundant embedded
`3D · Просмотр` tab when `pivy.quarter` is unavailable. The external
`SMBS_Animation_NN` FreeCAD document window owns the 3D presentation.
The SMBS workspace retains only `2.5D · Гибка` and
`Развёртка · Порядок`. FreeCAD builds with a genuine Quarter Python
widget may continue using the embedded 3D tab.

All view routing now uses QWidget identity instead of hard-coded tab
numbers. Native-mode 3D frames are driven even when there is no 3D tab,
and collision-inspection / assisted-retract commands update the external
window directly. Existing 2.5D rendering and physical OCC validation are
not deleted. Before merging to `smbs`, manually check 2D interaction and
whether background 3D plus 2D painting meets performance targets.
