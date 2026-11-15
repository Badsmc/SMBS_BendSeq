# Simulation smoothness and GUI polish — work plan

Status: IN PROGRESS (planning baseline); date: 2026-11-15
Branch: `development/simulation-gui-polish`
Base: `smbs` (contains `pose` changes at branch creation)
Repository: Badsmc/SMBS_BendSeq

## Goal
Make the existing FreeCAD SMBS add-on responsive, understandable and visually smooth while preserving the proven bending sequence/geometry/physical-validation pipeline. Changes must be validated in a real FreeCAD session; passing unit tests does not imply interactive acceptance.

## Non-negotiable invariants
- Single source of truth for bend identity, angle, tooling, machine geometry, workpiece pose, machine coordinate frame and press-cycle phase.
- Punch/die/backgauge/frame remain fixed in machine coordinates; only punch vertical ram stroke is animated. The workpiece is positioned and rotated relative to fixed tooling.
- Avoid recomputing expensive geometry, collision checks or unfolding on each viewer frame; visualization reads immutable/precomputed samples or interpolated transforms.
- Do not silently change collision verdicts, bend order, panel assignment, kinematics or model geometry.
- No fake pass, no mock results in production, no unused legacy or duplicate subsystems.
- Keep Windows 11 and macOS support; avoid hard-coded paths.
- Existing tools library and test assets remain available; avoid unnecessary Git history rewrites.
- `main` integration only after review and testing; `smbs` remains stable installation channel.

## Work packages, in order
### P0. Reproduce and measure
- Diagnose 3D and 2D rendering independently; never enable both animation redraw loops simultaneously during baseline profiling or regular playback.
- Default the visible animation mode to 3D; 2D section view is an optional alternative, not a second always-running animation.
- One shared simulation controller/timeline and model state drives either renderer; a mode switch pauses or deactivates the old renderer, releases its active timer/subscription, then attaches the new renderer at the exact current simulation time.
- Hidden/inactive views must not call scene redraw, geometry rebuild, frame callbacks or independent simulation ticking. Do not disable shared physical calculations or required validation as a side effect.
- Compare 3D-only, 2D-only and idle GUI frame timings; measure whether any duplicated Qt timers or redraw observers remain.
- Inventory real entry points for FreeCAD UI, task panel, scene update, press-cycle controller and simulation state.
- Collect baseline on sample FCStd with at least 8–10 bends, including manually selected sequence.
- Instrument: GUI frame durations (median/p95/max), scene updates, geometry/OCC calls during playback, Qt timer/slider events, allocation/mesh rebuilding, inter-bend pose delta and phase boundaries.
- Keep diagnosis separate from debug logging overhead. Write reproducible machine-readable diagnostics to a cross-platform location; never overwrite important existing desktop debug log accidentally.
- Do not assert the root cause before profiling.

### P1. Smooth playback and deterministic seeking
- Separate simulation timeline (simulation time and bend/phase) from rendering (Qt timer and 3D viewer draw).
- Prepare/cache part geometry per bend/phase; update transforms and ram displacement instead of recreating topology or rebuilding scene nodes each frame.
- Use elapsed monotonic time and time-based sampling/interpolation, not fixed-degree-per-tick stepping.
- Consistent seek(position): identical workpiece transform/angle and punch height for Play, Pause, Step, Reverse and slider scrubbing.
- No jump between consecutive bend states: verify world-space transform continuity, correct orientation and frame hand-off. If a discrete reposition is physically required, animate it as TRANSFER rather than teleporting.
- Throttle redraws to useful cadence; do not block GUI event loop with computation. Use safe main-thread scene mutations; do not run unsafe FreeCAD scene edits in worker threads.
- Handle large models and 3D screen updates without hiding physical geometry or weakening collision tests.

### P2. Polished task-panel experience
- Offer an explicit 3D / 2D view selector, with 3D selected by default. Exactly one renderer may update per frame. Preserve bend, phase, position and slider value when switching modes.
- One clear entry point called “Симуляция”; open/reopen panel without losing document or settings.
- Explicit modes: “Проверить последовательность” and “Симулировать без проверки” with an **unverified** marker, never misrepresented as safe.
- Compact visible controls: Play/Pause, previous/next bend, reset, time slider, playback speed, bend number/angle, phase, current status.
- Disable controls only when their prerequisites truly are missing; show actionable reason.
- Compact neutral GUI with responsive layout and legible hierarchy, consistent with FreeCAD dark/light themes. No oversized buttons or hard-coded white background.
- Wheel zoom and ordinary FreeCAD 3D camera rotation must continue working.
- Selected tooling visible in the scene; machine vs part movement visually comprehensible.

### P3. Persistence, performance, regression
- Save/recover user preferences and simulation position; avoid unrequested document edits.
- If project data is embedded in FCStd, define a versioned schema and safe migration, rather than writing arbitrary JSON into its ZIP internals.
- Add automated tests for timeline mapping, seek/play equivalence, state continuity, fixed tool frames, ram movement, UI lifecycle, no geometry recompute per viewer frame, manual path without collision validation.
- Manual acceptance on macOS and Windows FreeCAD; log reproducible measurements against P0 baseline.
- Check GUI reopen, document switch, sequence switch, cancel, scrub, reverse, stop/play and graceful invalid state handling.

## Acceptance checklist
- [ ] Actual code locations and baseline performance documented
- [ ] Exactly one of the 2D/3D renderers is active; switching views does not restart playback or create a second timer
- [ ] Smooth 3D playback without visible jumps or avoidable topology rebuilds
- [ ] Workpiece moves to fixed punch/die; only punch moves vertically
- [ ] Seeking/slider shows the same state as playback at a matching time
- [ ] Manual simulation without collision validation is possible, clearly marked unverified
- [ ] Task panel is usable, compact and reopens reliably
- [ ] No regressions in search, tooling or collision result correctness
- [ ] Automated tests and real FreeCAD validation on target systems recorded

## Engineering workflow
1. Inspect existing implementation and log evidence; write down suspected causes and their profiling proof.
2. Make small commits per independently testable change (timeline, caching, render loop, UI, persistence).
3. Run targeted tests and smoke-test the workbench in FreeCAD after each change.
4. Document reproducible issues, before/after timing and screenshots where helpful.
5. Merge verified work to `main`, then deliberately publish stable changes to `smbs` via release process.

## Initial user-reported issues
- Choppy/lagging 3D animation, especially transitions between bends.
- Workpiece orientation can cause a false practical obstruction; alternate part rotation should be explored as a distinct kinematic/search task, not concealed by visual interpolation.
- Punch must follow vertical stroke, while tooling is not relocated to the bend line.
- Keep all geometry/animation data consistent in one source.
- UI should allow immediate simulation of manually chosen order without collision checking.
- Panel reopen and debug JSON have previously failed in some builds; explicitly regression-test.

## P0/P1 first code inspection and patch (2026-11-15)
- Inspected `freecad_smbs/commands/pressbrake_2d.py` from this branch. The task panel creates a single 25 ms Qt timer connected to `advance()`. The timer path already chooses 3D rather than calling the 2D `draw()` in normal non-recording playback.
- Found unnecessary hidden 2D redraw at the start of `play_all()`, and on completion of regular 3D playback; also found a potential second 3D attempt when the first 3D render fails and calls `draw()`, which previously called `_update_3d_preview()` again.
- Patched selected-view rendering at start and completion, and added `draw(update_3d=False)` for the failed-3D diagnostic fallback. No changes to physical validation, state progression, 2D cutting-plane calculation or bend geometry.
- **Not yet measured in running FreeCAD**: frame p95, scene items, remaining geometry rebuild per 3D tick, effect of recording mode, and user-visible stutter. These remain open; do not claim FPS improvement without the measured baseline.
- Remaining higher-cost path: `_update_3d_preview()` calls `build_sheet_preview_faces()` and `build_tool_preview_faces()` every visible 3D tick, then reprojects/rebuilds painter paths per face. Profile and cache immutable geometry before attempting a broader change.

## P1 preview projection optimization (2026-11-15)
- Commit `1b0db2a`: in the live 3D preview, project each outer face vertex only once per frame and reuse the projected 2D polygon and depth for painter ordering. Previously sorting by depth and building the path each projected the same vertex independently.
- The camera remains anchored at physical die origin; camera angle, hole outlines, scene item reuse, original painter order and physical face generation remain unchanged.
- Bounds radius is now computed only when initializing a fixed 3D view rectangle, not on every frame. It still uses every face vertex for initial sizing.
- This optimization removes redundant camera projection work but **does not yet cache** `build_sheet_preview_faces` or `build_tool_preview_faces`. Do not claim to have eliminated face/geometry rebuilding.
- Verification still required: syntax smoke check in installed FreeCAD, headless preview regression tests, and real FPS / p95 timing comparison including view rotation, tool changes, switching bends, recording mode and repeated panel reopen.
