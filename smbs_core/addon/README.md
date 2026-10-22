# SMBS FreeCAD Addon

FreeCAD macro for visualizing bend sequence numbers on the flat pattern.
Part of SMBS/BendSeq Stage 4 (requirement 1 — see `docs/ROADMAP.md`).

## Install

1. Copy `SMBS_AnnotateBends.FCMacro` to your FreeCAD macros directory
   (FreeCAD → Macro → Macros... → Add).
2. Open `SMBS_AnnotateBends.FCMacro` in any text editor and set `SMBS_REPO_PATH`
   at the top to point to your SMBS_BendSeq checkout (e.g. `/Users/badmc/Desktop/SMBS_BendSeq`).
3. Restart FreeCAD or reload macros (Macro → Macros... → Refresh).

## Usage

1. Open FreeCAD (any workbench).
2. Macro → Macros... → `SMBS_AnnotateBends` → Execute.
3. In the file dialog, select a run directory, e.g. `runs/run_20261017_203828_511545/`.
4. The flat pattern loads with numbered bend annotations (1, 2, 3, ...).
5. A summary dialog shows verification status, step list, and diagnostics.

## Color legend

| Color | Meaning |
|---|---|
| 🟢 Green | `VERIFIED` — sequence verified, `production_eligible=true` |
| 🟠 Orange | `VERIFICATION_FAILED` — e.g. tooling collision (KL-8). Order is correct, but safety cannot be confirmed. |
| ⚫ Gray | `UNVERIFIED` — no `09_verification_result.json`. Not "verified" in any sense. |

## Requirements for run directory

Must contain:
- `04_canonical_part.json`
- `07_search_result.json`
- `flat_pattern.brep`

Optional:
- `09_verification_result.json` — if absent, annotations are gray (UNVERIFIED).

**Note:** `bend_line_flat_pattern` in `04_canonical_part.json` is required. Runs generated before 2026-10-17 lack this field and will be rejected with a clear error. Regenerate old runs with the current pipeline (`smbs-cli part.FCStd`) to enable annotations. See `docs/PROGRESS.md` KL-13 for details.

## Known limitations (S.18b, honest)

- **No "Calculate" button.** The macro reads an existing run directory. To generate a run, use `smbs-cli part.FCStd` in a terminal first. "Calculate in FreeCAD" is a separate future step (see `docs/ROADMAP.md` requirement 1).
- **Geometry is a flat pattern only.** No folded 3D model, no tooling. Those require requirements 2–4 (see roadmap).
- **Fixed font size.** `ANNOTATION_FONT_SIZE = 15` (config at top of macro); edit for parts much larger/smaller than ~150mm.
- **No step filtering / highlighting.** All steps shown simultaneously. Fine for ≤10 bends; may overlap for larger parts.
- **Uses `App::AnnotationLabel` with `DisplayMode="Object"`.** In FreeCAD 1.1 this is the only combination that renders text without a hardcoded anchor dot or leader line. `Draft.make_text` (screen=True) is invisible in FreeCAD 1.1; do not substitute.

## Manual GUI test status (S.18b)

Verified on FreeCAD 1.1.3 / PySide6 / Qt 6.8.3:
- **Synthetic VERIFIED fixture** → green label '1' rendered at Z=4.5 mm above flat pattern, no anchor dot.
- **Old run without `bend_line_flat_pattern`** → red error dialog with actionable message, no annotations created.
- **Synthetic VERIFICATION_FAILED fixture** → orange label '1', honest marking confirms FAILED status.

Real production runs (with non-empty `candidate_sequence` + `bend_line_flat_pattern`) are blocked until KL-8 and/or KL-13 are resolved. See `docs/PROGRESS.md`.

## See also

- `docs/ROADMAP.md` § 3, Requirement 1
- `docs/PROGRESS.md` — KL-13 (old runs without `bend_line_flat_pattern`)
- `smbs_core/viewer/flat_annotations.py` — annotation computation (pure Python, testable)

## Tooling Library (Phase B, S.22f)

Macro: `SMBS_ToolingLibrary.FCMacro`

Install: same as `SMBS_AnnotateBends` (copy to FreeCAD macros dir, edit `SMBS_REPO_PATH` + `SMBS_LIBRARY_PATH` at top).

Usage:
1. Macro -> Macros... -> SMBS_ToolingLibrary -> Execute.
2. Dialog shows punches and dies from library.
3. **Add Tool...** — pick `<name>.FCStd` with sidecar `<name>.tooling.json` next to it. Kind auto-detected via Q1=D (property -> name -> sidecar). Missing sidecar -> error with instructions.
4. **Remove** — delete selected tool from library.
5. **Preview** — load tool shape into active FreeCAD document.

Requirements:
- Library root must contain `punch/` and `die/` subdirectories (or use Bootstrap button).
- Every added tool requires `<stem>.tooling.json` sidecar (datums: mounting_plane, working_axis, tip_point / v_center).

Not in this version:
- Sidecar creation wizard (create manually in text editor with datums).
- "Use in pipeline" (needs Coordinator integration, S.22g).

**Preview behavior note:** Preview merges the FCStd document into the active FreeCAD document in its *original modeling coordinates*. Tools are NOT aligned to each other or to a machine — this is intentional. Alignment happens in `ToolingAssembly.place()` (pipeline side, via datums from sidecar). Backlog: "Preview assembled tooling" feature (show punch+die aligned as they would be on the machine).

## Animation Player (Req 2 Phase F.2, S.35)

Macro: `SMBS_AnimationPlayer.FCMacro`

Install: same as `SMBS_AnnotateBends` — copy to FreeCAD macros dir, edit `SMBS_REPO_PATH` at top. Optional `TICK_INTERVAL_MS` config (default 500 ms = 2 fps).

Usage:
1. Macro → Macros... → SMBS_AnimationPlayer → Execute.
2. In the file dialog, select a run directory (must contain `04_canonical_part.json`, `07_search_result.json`, `geometry.brep`).
3. Timeline dialog opens: header (step / bend_id / direction), verification badge, precision badge, warning text (if any), slider, controls (Reset / Step ◀ / Play-Pause / Step ▶).
4. Shape in active document updates per frame. Play iterates at `TICK_INTERVAL_MS`, stops at end of sequence. Slider seeks by reset + advance.

Honest marking (badges, read-only from `StepView`, never computed):
- **Verification** (from `09_verification_result.json`): `VERIFIED` (green) / `FAILED` (orange) / `UNVERIFIED` (gray).
- **Precision** (from `StepView.geometry_precision`): `VALID_PER_PANEL` (green) / `PREVIEW_ONLY` (yellow).
- Warning text below badges when `StepView.geometry_warning` is not `None` (e.g. partial per-panel kinematics with unassigned bridge caps — see KL-12 v3 in `docs/PROGRESS.md`).

Not in this version:
- **Tooling visualization** (punch / die / machine frame) — deferred to Req 2 Phase F.3, pending `tooling_placement` design (item #7).
- **Reverse Play loop** — `AnimationController.step_backward()` exists, but Play always advances forward from step 0; reverse via user pressing Step ◀.
- **Persistent viewport optimization** — each frame removes and re-adds the `SMBS_Animation` object (`Part.show`); not optimized for very long sequences.

**Manual GUI test status: NOT YET PERFORMED.** The FCMacro GUI is not unit-testable in CI. Headless logic (`smbs_core/viewer/fc_animation.py`: controller state machine + badge mapping) is covered by 7 tests in `tests/test_viewer_fc_animation.py`. User must run the macro in FreeCAD and confirm: dialog opens, badges render correctly for both VERIFIED and VERIFICATION_FAILED fixtures, Play advances frames, Step buttons work, slider seeks, shape updates visibly, dialog close stops the timer. **Do NOT claim "verified in FreeCAD" until this is performed** — honest marking extends to our own test status.

**Known follow-up (not blocker):** `smbs_core/viewer/render.py` PNG fallback has `BANNER_TEXT = "PREVIEW_ONLY (KL-12) - not a physical re-simulation"` hardcoded (`cli.py:213` calls `render_frame_png` without precision / banner_text arguments). PNG output therefore still marks PREVIEW_ONLY even when `StepView.geometry_precision=VALID_PER_PANEL`. Does not affect the viewport path (this FCMacro is the source of truth for DoD Req 2). Deferred to F.3 or a separate S.37 fix.
