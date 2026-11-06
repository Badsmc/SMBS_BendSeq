# PressBrakeSim 2.5D — release acceptance gates

Target: release a **truthful, functional FreeCAD 2.5D workbench** before building a general-purpose full 3D press-brake simulator.

Branch: `2D`. Native D6.3.4 in `d6-native-3d` is an optional scoped validation backend, not a reason to replace the complete 2.5D engine.

## Release definition

The release is a stable FreeCAD 1.1.x addon for interactive SheetMetal bending preview, manual order validation, and honest *bounded* machine-space feasibility results. For unsupported cases, explicitly show `UNKNOWN`/`UNVERIFIED`/`UNSUPPORTED`. Never represent an animation-only sequence or a projected 2D face flip as machine-qualified.

Maturity convention: IMPLEMENTED != TESTED != VERIFIED != QUALIFIED != PRODUCTION_READY.

## Priority P0 — install and baseline (blocker)

- [x] Repository root has FreeCAD `Init.py` / `InitGui.py` forwarding to canonical `freecad_smbs` workbench
- [x] Cross-platform `demo/parts` path resolver and SHA-256 verified archive installer
- [ ] Original demo FCStd binaries committed (17 unique model filenames; both U variants preserved)
- [ ] One active workbench registration / no duplicated old SMBS menu in a fresh installation
- [ ] Repeat Windows FreeCAD GUI smoke test after a clean install
- [ ] Fix legacy CI import failure `SELF_COLLISION_GAP_TOL_MM` through a correct compatibility contract or quarantined legacy test with documented rationale, not an invented collision tolerance
- [ ] All required CI jobs green; no hidden failures

## Priority P1 — one complete 2.5D user workflow (blocker)

- [ ] Select/open FreeCAD SheetMetal model -> click Simulation -> identify all bends, signed directions, thickness and radii from SheetMetal
- [ ] Single `Play All` with `Mode=Bend Only` animates the entire model and finishes, preserving previous bends
- [ ] `Mode=Full Cycle` runs approach/contact/bend/bottom/retract only when machine placement is supported; otherwise fail closed with clear reason, no frozen/unresponsive UI
- [ ] Slider, Pause/Resume, Next/Previous, Reset, reorder, Fit, and close/reopen remain functional on real Windows GUI
- [ ] No frame-to-frame position jumps for supported parallel-axis transfers; nonparallel-axis transfers must not be deceptively shown as physically verified
- [ ] Recording opt-in produces nonempty JSONL with a valid summary; playback remains usable when recording is off
- [ ] Original FCStd files and current FreeCAD user documents are not changed or auto-saved by simulation

## Priority P2 — geometry and tooling validity (blocker for machine-qualification claims)

- [ ] Full fold and unfold match original SheetMetal source within documented numerical tolerances on the 16 canonical named parts; evaluate 17th U variant separately
- [ ] Validate punch/die profile orientation, length coverage, seat/contact and clearance, including opposite bend signs
- [ ] 2.5D collision oracle rejects known collisions, passes truly supported profile-section cases and returns UNKNOWN for out-of-section 3D ambiguity
- [ ] Backgauge/clamps/frame are not silently claimed cleared; either qualified with the required 3D backend or explicitly marked unverified
- [ ] Collision results and manual sequence results include specific reasons and sampled/evidence status, never a fabricated universal PASS

## Priority P3 — sequence planning (blocker if release promises automatic valid sequences)

- [ ] Separate visually playable canonical bend order from physically verified manufacturing order
- [ ] Implement deterministic manual-order replay plus explicit legality checks in the 2.5D oracle
- [ ] Connect 2.5D oracle to bounded greedy/DFS-backtracking (reverse search where physically valid); no duplicated solver or false success
- [ ] Exhaustive/limited search results are distinguished: FOUND / NO_VALID_SEQUENCE_PROVEN / UNKNOWN_LIMIT_REACHED / UNSUPPORTED_GEOMETRY
- [ ] Replay each reported valid order from flat FoldState with physical check evidence at each step

## P4 — release package (blocker)

- [ ] Install/uninstall test on Windows; basic compatibility on macOS/Linux
- [ ] GUI smoke test for L, Z, 135, U, box and 8/10-bend fixtures, including both labelled-OK and labelled-false negative examples
- [ ] Deterministic report, machine setup, warning reasons, logs and source-hash audit
- [ ] Clear limitations, user help, changelog and semantic release tag
- [ ] Do not call a 2.5D preview a certified production bending sequence

## Where native 3D is required

Use native FreeCAD BRep geometry *as a selective validator* for unsupported 2.5D candidates: full-sheet/tool collision, nonparallel-axis placement, out-of-plane interference and optionally transport. Reuse `d6-native-3d` through an explicit adapter returning evidence/status. Do **not** run expensive 3D boolean checks on every 2.5D animation frame, do not replace the FoldState/SheetMetal data contracts, do not remove safe-stop on unsupported paths.

## Release gate evidence

For every gate record: branch+SHA, fixture filename+SHA256, FreeCAD/SheetMetal version, input configuration, command/UI action, observed result, report path and decision. GitHub CI without actual FreeCAD GUI is not sufficient for GUI acceptance.
