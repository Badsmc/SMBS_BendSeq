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
- **No auto-scaling.** `TEXT_HEIGHT_MM = 5.0` fixed; edit the macro for parts that are much larger/smaller than ~100mm.
- **No step filtering / highlighting.** All steps shown simultaneously. Fine for ≤10 bends; may overlap for larger parts.

## See also

- `docs/ROADMAP.md` § 3, Requirement 1
- `docs/PROGRESS.md` — KL-13 (old runs without `bend_line_flat_pattern`)
- `smbs_core/viewer/flat_annotations.py` — annotation computation (pure Python, testable)
