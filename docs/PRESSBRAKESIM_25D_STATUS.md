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

## Phase 5B debug: full motion recording (JSONL + event summary)

A read-only motion tracer is now available in the **Настройки** tab of the
PressBrakeSim workspace:

1. Load the part and tooling, choose `Full Cycle`.
2. Enable **Записывать все движения в JSONL (отладка)**.
3. Click **Play All**. The trace begins automatically and captures every
   rendered frame in the same 2.5D machine-space geometry actually shown in
   the viewer (no second physics/kinematics computation).
4. Reproduce the issue on L/Z/U or the 10-bend part. Pause/resume normally.
5. When playback completes, a `.summary.json` is written automatically;
   alternatively click **Завершить трассу и отчёт**, Reset, reload the part or
   close the workspace. The trace remains accessible on disk.

**Files:** under the existing logging directory,
`~/Library/Application Support/FreeCAD/v1-1/pressbrake_traces/` on the
current macOS FreeCAD setup. Each run gets a unique timestamped
`pressbrake_YYYYMMDDTHHMMSS_microsecondsZ.jsonl` and adjacent
`.summary.json`. The settings tab displays the exact path.

The trace captures a machine-coordinate position and full panel/bend-zone
polygons for each rendered frame, real recorded ram and tool datum coordinates,
all signed FoldState bend angles, canonical and user bend orders, cycle phase,
progress, actual WorkpiecePose fields (translation/rotation/roll), pose warnings,
time delta and scene rectangle. It logs `BEND_SWITCH`, `PHASE_CHANGE`,
`SCENE_RECT_CHANGED`, `POSE_UNSUPPORTED`, `POSITION_JUMP`, and user
pause/resume/reset events. `POSITION_JUMP` compares matching sheet landmark
positions between consecutive actual rendering frames; default >2 mm
(also >0.05 mm at an active-bend change), to flag suspicious discontinuities,
not to assert that all fast movement is impossible.

The recorder is opt-in and adds **no timer**, changes no FoldState, FreeCAD
document, tooling geometry or playback control. Logging exceptions should
be reported in settings and should not block the simulator. It is a debug
facility, not an inference of physical validity or a collision detector.
The original `pressbrake_2d.log` is retained alongside separate motion traces.

**Acceptance:** source + headless unit tests added and CI workflow extended,
but **real FreeCAD GUI and CI PASS remain unconfirmed**. Phase 5B remains open.

## Phase 5B stabilization checkpoint — immutable reference and continuous projected roll

**User authorization:** proceed with Phase 5B stabilization; the user has **not approved** the prior Phase 5B releases.

- GUI now constructs the complete 2.5D sheet using the **canonical graph bend** as a stable datum derived from the flat part. It no longer depends on editable `bend_order[0]`. This is a fixed datum in the original flat-model coordinates, **not yet** a generalized global frame for nonparallel bending axes.
- The 2.5D scene rectangle is frozen for the complete playback, including the final draw after `_stop_timer()`, and is released on Reset/reload. Automatic `fitInView` is disabled during Full Cycle PLAYING.
- WorkpiecePose2D now supports `roll_projection` in the *intermediate visualization only*. When adjacent machine placements differ by a sheet face reversal (`mirror_z`), TRANSFER smoothly projects the 3D roll through an edge-on state rather than applying an instantaneous binary reflection at its last frame.
- Pose interpolation endpoints remain exact `start` at t=0 and `end` at t=1, including pivot, orientation, translation and face choice. Each frame uses a common FoldState geometry; panel angles are never modified during transfer.
- The intermediate projected roll is **NOT a physical 2D cross-section** and **MUST NOT** be supplied to the Phase 5C collision oracle; actual 3D sheet rotation and clearance require 3D validation. Previously accepted 3D sheet-only fixed-isometric viewer is unchanged.
- Added regression tests for continuity of sample points across a changing sheet-face presentation and invariance of the canonical datum.
- Limitations: The fixed reference is derived from a canonical original bend line rather than a full global 3D part frame. Nonparallel bend axes still explicitly fail closed in the 2.5D view. Exact manipulator paths, contact mechanics, and safe punch/die clearance are unverified. GUI FreeCAD user acceptance and CI results are still outstanding.

**Phase 5B remains OPEN / NOT APPROVED.**

## Phase 5B Hotfix 9 — recover user-requested AUTO PLAY separately from unsafe Full Cycle

User feedback after Hotfix 8: automatic playback no longer usable; all frames were reportedly advanced manually.

Latest real `pressbrake_20261104T143731_520624Z.jsonl` contains 64 frames at nominal 31ms tick cadence with `playback_status=PLAYING`, and `KINEMATIC_POSE_HALT` at next-bend preparation after B01 (`NONPARALLEL_BEND_AXES`). A trace frame with PLAYING status does not independently verify which UI controls the user operated. The decisive blocker is that full-cycle machine-pose simulation deliberately halts at unsupported nonparallel axes. That safety guard MUST remain and must not be silently bypassed.

Changes:
- Expose **two clearly differentiated automatic playback paths**:
  - `Play All (станок)`: Full Cycle, includes punch and die, halts safely on unsupported machine pose.
  - `Авто гибка (без пресса)`: explicit dedicated button in Simulation tab. Switches to Bend Only, resets to flat, invokes existing `BendSequenceController.play_all()` and single Qt timer to animate ALL signed bend angles without 2.5D machine-pose verification. This mode intentionally does not certify punch/die contact or collisions.
- `advance()` now surfaces AUTO PLAY state (mode, phase, active bend, angle and timer interval) on every Qt tick. Unexpected callback errors stop timer, log stack trace, finalize JSONL and display explicit `AUTOPLAY_ERROR`.
- A latched `MACHINE_PRESENTATION_UNSUPPORTED` fault prevents false Full Cycle Resume/Play until Reset or intentional mode change. The error text suggests the alternative automatic geometry playback.
- Added a FreeCAD GUI-only Qt-event-loop regression that clicks the new button and checks that multiple frame advances happen automatically, plus a pure-Python regression for BEND_ONLY completion of +90/-90. Both tests remain unverified in a live FreeCAD GUI.
- Neither `FoldState` nor the existing 3D sheet-only isometric preview has been replaced. No second animation timer or mirror solver was added.
- **Phase 5B remains OPEN**: true uninterrupted Full Cycle for nonparallel 8-bend parts requires 3D workpiece/machine placement, not a 2D fallback.

## Phase 5B Hotfix 8 — forensic motion trace safety correction (2026-11-04)

Two user-supplied real FreeCAD motion traces:
- `pressbrake_20261104T140039_280181Z.jsonl`: **864 frames**, **8 bends B01–B08**, 132 POSITION_JUMP flags, 6 POSE_UNSUPPORTED events, 7 BEND_SWITCH events.
- `pressbrake_20261104T140224_018166Z.jsonl`: **114 frames**, 33 POSITION_JUMP flags, 1 POSE_UNSUPPORTED event.
- 657/864 full-trace frames had `pose.mode=IDENTITY`; 630 frames specifically reported `NONPARALLEL_BEND_AXES` (including alternating operation groups). At B05/B07 `DIE_SUPPORT_MISSING` appeared around BOTTOM. This was a **dangerous visual fallback**: simulation kept playing an unpositioned sheet and showed jumps of 100+ mm.

Corrective changes (limited and fail-safe):
1. The GUI stores the last successfully transformed, machine-positioned sheet section and its WorkpiecePose. A future `WorkpiecePoseError` pauses the existing PressCycleController, stops the GUI timer and renders the **last valid frame** with an explicit STOP/unsupported warning; it no longer renders the active part in the `IDENTITY` pose and pretends that bending continued. If NO valid machine section exists, it hides the unplaced sheet entirely rather than displaying the local FoldState section as a valid machine pose.
2. The fault persists across paused redraws. Resume refuses a known-invalid Full Cycle. Reset, Play All, and intentional manual selection clear the latch so the user can retry or inspect another operation.
3. A distinct `KINEMATIC_POSE_HALT` event is recorded in JSONL; diagnostic `POSITION_JUMP` now carries `classification=FAST_FRAME_MOTION|BEND_SWITCH` and a separate `BEND_SWITCH_DISCONTINUITY` event is recorded when physically corresponding landmarks differ by more than 0.05 mm across an operation switch.
4. On Windows, log and trace files now use `%APPDATA%/FreeCAD/v1-1/` (or the available platform fallback) rather than a synthetic `C:\\Users\\...\\Library\\Application Support\\...` path. macOS continues using `~/Library/Application Support/FreeCAD/v1-1/`, Linux uses `$XDG_DATA_HOME/FreeCAD/v1-1/` or `~/.local/share/FreeCAD/v1-1/`.
5. Added headless regression for truly perpendicular axes and an optional FreeCAD GUI regression for freeze/stop. Extended motion-trace regression to distinguish rapid continuous movement from bend-switch discontinuity.
6. On an invalid machine pose, a running diagnostic JSONL recording is immediately finalized with `finished_reason=KINEMATIC_POSE_HALT` and an associated `*.summary.json`, so the failed sequence can be inspected without a separate manual Stop click.

**Critical limitation:** this is a *stability and truthfulness* hotfix, NOT a full fix for eight bends with nonparallel bend axes. A single fixed 2D cross-section cannot faithfully represent all nonparallel bends of a spatial sheet. Those require a true 3D workpiece placement/projection with explicit press-frame coordinates and physical handling between poses. This code stops unsupported motion rather than fabricating it. `DIE_SUPPORT_MISSING` likewise blocks claims of physically verified contact. Do not mark Phase 5B accepted. Phase 5C oracle remains isolated and pilot/unverified.

**Validation required:** Run CI on branch `2D` and manually reproduce B01→B02 of the 8-bend sample. Expected: visible stop with reason `NONPARALLEL_BEND_AXES`, retained last valid machine frame, no `IDENTITY` jumps after the fault, model unchanged. Real FreeCAD GUI verification still pending.

## Motion-trace JSONL recording hotfix (user reported missing folder/files)

- User reported that JSON/JSONL recording created neither output directory nor file.
- Identified UX cause: prior GUI did not explicitly start recording upon selecting its checkbox; recorder only started via `Play All` with the option already selected. Path was hidden in a short status label. The summary `*.summary.json` is created only on `stop()`, while per-frame data is a `*.jsonl` file.
- Added explicit Start/Stop actions, immediate start when checkbox enabled after a part is loaded, output-folder chooser and Open-folder button. Errors are visible both in main status and trace status; the absolute saved path is shown.
- `MotionTraceRecorder.start()` flushes the initial JSONL event immediately, so file appears with content as soon as recording begins. Stop writes the summary JSON. These changes are strictly diagnostics; FoldState, tool library and bend sequence remain unchanged.
- Default on the user's macOS setup remains `~/Library/Application Support/FreeCAD/v1-1/pressbrake_traces`; user can choose another writable directory from GUI.
- Added independent headless tests for immediate folder/JSONL creation, summary creation on stop, and optional FreeCAD GUI test for manual recording.
- **This is code committed, not yet validated on the user's actual FreeCAD GUI.** GitHub CI status not confirmed. No claim of corrected behavior until user verifies files.

## Phase 5C — cross-section collision oracle (pilot, NOT accepted)

Phase 5B stays **OPEN / NOT APPROVED**. The user explicitly withheld acceptance of its contact placement, negative-bend representation and transfer. Phase 5C implementation is isolated; it must not be used to certify the outstanding 5B machine pose.

**What was added (headless only):**
- `smbs_core/kinematics2d/contact_oracle_2d.py`: conservative sheet/punch/die *2D polygon-section* classifier, statuses `CLEAR`, `TOUCH`, `INTERFERENCE`, `UNVERIFIED`, a per-component/tool `ContactFinding` and aggregate `ContactReport`.
- Bounds are used only for fast obvious separation, followed by polygon boundary segment crossings and interior witnesses. Punch/die cutout rings (holes) represent empty space.
- Both positive-area penetration and coincident overlapping solid interiors are classified as `INTERFERENCE`, while boundary-only touch is not penetration.
- Missing tools/alignment or degenerate geometry are `UNVERIFIED`, not a simulated PASS.
- Read-only adapter `check_sheet_tooling_preview(section, tooling)` accepts an **already machine-positioned** FoldState cross-section and existing tool-profile polygons. It scans fixed/moving/other panels and all bend zones. It does not mutate the source FreeCAD object, the angles, or imported tool profiles.
- Tests `tests/test_contact_oracle_2d.py` cover disjoint, touch, partial/full penetration, coincident polygons, tool cavities, missing geometry and read-only adapter. Included in GitHub Actions `pressbrake-preview-2d.yml`.
- **Not enabled as automatic stop, PASS/FAIL safety certificate or 3D collision check in GUI**. Must first verify the new oracle independently and then integrate with valid Phase 5B machine poses, without hiding errors.

**Limitations and stop-criteria:**
- This is a polyline-discretized 2D slice, not OCC 3D volumetric collision. Contact tolerance depends on sampling; profiles with complex nonmanifold boundaries, crossing contours and variable cross-sections require additional validation.
- `CLEAR` means only that this tested section did not report interference under the current sampling/geometry; it does not mean that the part can be manufactured.
- Exact physically meaningful punch/blank contact, 3D tooling collisions, travel of sheet outside the tested cross-section, stroke reachability, machine frame, backgauge, clamps, and dynamic interference along the full transition remain outstanding.
- Real FreeCAD GUI checks and successful CI test run **not yet confirmed**. Phase 5C status: **IMPLEMENTED (pilot), UNVERIFIED**, not closed.

## Phase 5B Hotfix 7 — unsupported 2.5D placement on selected bends

User feedback: some operations display the scene warning `2.5D: НЕТ ПРОВЕРЕННОГО ПОЛОЖЕНИЯ ДЕТАЛИ`.

Code review confirmed an overly restrictive orientation model: `symmetric_v_pose` accepted only one fixed/moving flange handedness with no roll. Actual Z-part bend sections after previous operations can require the workpiece to be turned face-up. Instead of requiring the source panel vectors to match one preset orientation, the solver now tries fixed-on-left/right and a possible *assumed physical sheet roll* (180° about its longitudinal axis, appearing as `mirror_z` in a 2D section). Each candidate must preserve the true included angle and point both adjacent flanges upward. It chooses the lowest-error geometry-valid candidate, favoring a roll for negative bends on ties.

The drawing now includes the **specific underlying warning reason** (e.g. `NONPARALLEL_BEND_AXES`, `BEND_PRESENTATION_UNSUPPORTED`, die support error) rather than a generic message. Nonparallel bends are still unsupported by the current single-plane 2.5D representation; this is a genuine limitation and must not be hidden.

Regression tests were updated for +45/+90/-45/-90 and upstream panel rotations. User GUI acceptance and CI results are not yet confirmed. Transferring from unrolled to rolled poses may require an explicit 3D roll/handling animation rather than the existing planar pose interpolation; do not claim physical continuity or collision freedom.

## Phase 5B Hotfix 6 — screenshot-driven correction: fixed part frame + rigid transfer

**Observed in user's 2.5D FreeCAD screenshots (2026-11-04):**
- Long sheet/flange appears horizontal and an isolated short upstand appears near the punch, even when full-cycle animation is active.
- The active bend/punch may visibly miss one another.
- This is not accepted as valid V-bending.

**Identified code problems:**
1. `symmetric_v_pose` rejected previously folded panel directions unless they were already left/right horizontal. For the next bend of a Z-part, FoldState vectors are generally not aligned that way.
2. The GUI rebuilt the whole 2.5D view in the currently active bend's local `s` frame. That frame's origin and direction change between B01 and B02; the visible sheet therefore jumped or appeared to become horizontal.
3. The old `transfer_motion` and new rigid interpolation represented two different trajectories. Keeping both would lead to contradictory UI status.

**Changes:**
- Compute symmetric up-bend placement from current actual outward flange vectors, without imposing a horizontal starting orientation; unsupported handedness explicitly fails rather than silently inverting a tool.
- Keep the full 2.5D sheet in the **first operation's fixed cross-section reference** throughout a sequence. Convert later bend sections into that reference using the documented axis directions and original bend origins. Reject nonparallel axes, which cannot share an honest single 2.5D plane.
- Draw all FoldState panels, zones, inner/outer surfaces, and completed operations from that fixed section and apply a single workpiece-to-machine pose.
- In TRANSFER, interpolate rigid poses between actual previous and next bend placements, with identical start/end endpoint mappings. Every panel follows the same transformation; no fabricated change in bend angles.
- Eliminate the superseded staged `transfer_motion` rendering/diagnostic path. The controller retains only transition progress, duration, nominal feed between original bend axes and clearance, which are used by the unified rigid-pose renderer.
- Keep punch and die static during sheet transport, and preserve read-only fixed-isometric full-part 3D animation.

**Limits and acceptance:** This is a geometry *kinematic preview*, not a verified contact/collision solver or a real physical manipulator trajectory. The current design deliberately fails closed for nonparallel bends. New source/tests were committed and workflow configured, but runtime test results and the real FreeCAD GUI display are still **UNVERIFIED**. Do not mark Hotfix 6 accepted without checking the screenshot case and true B01/B02 transitions.

## Phase 5B Hotfix 5 — continuous 2.5D sheet transfer between bends

User issue: negative-bend workpiece repositioning still appeared instantaneous; after each operation the sheet jumped back to a level pose. Requested smoother playback with visible lifting, flipping and feeding the workpiece to the next bend line.

Implementation on branch `2D`:
- Add opt-in `CyclePhase.TRANSFER` to the **existing** headless `PressCycleController`. Activated only for the FreeCAD 2.5D Full Cycle GUI; original headless tests and Bend Only retain their no-transfer behavior.
- After the punch completes RETRACT, the existing Qt timer drives a 1.8-second easing curve: (1) lift/retract workpiece clear of die, (2) rotate 180° when the next bend sign differs, (3) feed the sheet by the **signed distance** between actual adjacent bend axes, toward the next working location. No FoldState angle is modified during this transfer.
- The die remains in its machine position when switching B01 to B02; the punch is retracted and re-based to the new active bend surface.
- The GUI uses one 25 ms Qt timer (~40 Hz target). The existing 2.5D view draws the complete active section through a common rigid `WorkpiecePose2D`; real bend geometry and `FoldState` are preserved. `Pause/Resume` freezes/resumes the transfer fraction.
- Punch position now follows the geometric active-zone top at **every** BENDING tick in optional GUI mode (previously only at BOTTOM, creating a visual jump).
- The 3D preview is intentionally unchanged: fixed isometric camera, the entire deforming part without punch/die or machine reorientation.
- Added regression tests for transfer progression, rigid sheet motion, pause/resume, signed feed, reset, and disabled legacy mode. Extended the dedicated GitHub Actions workflow to run these.
- **Limitations:** The transfer animation is a staged `KINEMATIC_PREVIEW`, not a verified industrial manipulator trajectory. Actual machine backgauge clearance, flange support, flipping around the bend axis in 3D, tool and frame collisions are not verified. At the instant the next bend becomes active, its own local cross-section is used; further persistent-global-frame work may be needed if a discontinuity remains visible. Do not claim a physically continuous, collision-free movement until real FreeCAD testing.
- GUI acceptance and CI success remain **unconfirmed**; Phase 5B is still open.

## Phase 5B Hotfix 4 — user validation and targeted corrections

Manual result on `e498655`: Full Cycle PASS, symmetric flanges PASS, die support PASS, punch fully reaches sheet FAIL, 3D changes/bend-switch stability FAIL.

Corrections:
- **2.5D negative bend** uses a rigid 180° workpiece turn about the press bend axis. Previous `mirror_z` was a non-rigid reflection that made negative bending appear upside-down (die above, punch below). All supported bends now present both flanges upward toward the upper punch in machine view. The original FoldState signed angles stay unchanged.
- **2.5D punch contact preview** optionally follows the machine-space upper material surface at the *centerline of the selected punch*, not max height anywhere in BendZone and not an assumed 4mm stroke. This tracking is enabled in the FreeCAD GUI only; previous pure headless fixed-stroke contract remains the default. The controller records `GEOMETRIC_ZONE_TIP_PREVIEW` or a documented assumed-stroke fallback. Do not interpret tracking as force/contact/collision validation.
- **3D viewer** now draws only the complete FoldState part geometry; no die/punch at all, no machine-space reorientation/flip. The isometric frame is anchored to the first canonical bend rather than the active bend, and the fit camera does not recenter at each animation frame. Source document is not changed by rendering.
- Added new regression coverage for rigid 180-degree negative bends, upward flange elevation and punch-axis zone sampling.
- In-person FreeCAD GUI and new CI check results have not yet been confirmed; Phase 5B remains open.

## Phase 5B Hotfix 3 — 2.5D playback mode, symmetry, and real shoulder support

User GUI feedback for Hotfix 2: `PASS` shoulder positioning, `FAIL` symmetric 2.5D machine cycle, Z-detail and original-model integrity `PENDING`.

**Root cause in the interface:** `_PreviewPanel` still initialized `execution_mode = BEND_ONLY`, and its mode combo defaulted to Bend Only. Clicking Play All used `LoadedPartSession.advance_time`, bypassing the ram controller and whole-workpiece machine pose. The prior 3D scene could animate through FoldState independently, making the 2D viewer appear to use the old fixed-flange logic.

Targeted changes:
- Workspace GUI initializes Full Cycle and its mode combo consistently. Bend Only remains explicitly selectable, unchanged in its existing calculation path.
- V-die support places the **exact symmetric** workpiece with one vertical translation to actual shoulder geometry. An extra +/-6-degree global correction that sacrificed visible symmetry was removed.
- When only one shoulder can be reached, label `PARTIAL_SHOULDER_SUPPORTED_PREVIEW` and seat that shoulder rather than falling back to a floating identity pose or claiming verified two-point support. No physically valid two-shoulder contact is invented.
- `PressCycleController` rebases the approach/contact/return ram coordinate at the start of a Full Cycle to the actual **die-seated bend zone**, keeping Punch and the sheet aligned in Machine Z. The die remains stationary.
- Existing 3D whole-part animation, source model, DXF/STEP import and geometry contracts are unchanged.
- Added GUI default-mode regression, rigid symmetric-angle and shoulder-support tests.

Known limitation: this is `KINEMATIC_PREVIEW`, not a punch/sheet/die contact mechanics or collision solver. A configurable stroke is not guaranteed to be physically feasible. Source-model integrity and the real FreeCAD GUI still require user acceptance before closing Phase 5B.

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
