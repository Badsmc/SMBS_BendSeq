# PressBrakeSim 2.5D — Status (evidence only)

Updated: 2026-11-03 · Branch `2D` · Baseline `e8f2ed1`.
Environment: macOS, FreeCAD 1.1.3 (`freecadcmd` headless), SheetMetal WB present; pytest in a
local venv (Python 3.14, no pythonocc). Level C (interactive GUI) NOT performed this iteration.

## Baseline test result (`e8f2ed1`)
`58 total collected → 57 passed, 1 failed, 7 skipped, 6 collection errors`
- FAIL `test_axis_matching::test_reject_short_overlap` → confirmed defect D1 (fixed).
- Collection errors (environment): 4 × `No module named 'OCC'` (`test_ambiguous_tree_returns_unknown`,
  `test_end_to_end_pipeline`, `test_feature_extractor_signed_angle`, `test_honest_collision_and_verifier`),
  1 × FreeCAD.so not loadable from non-FreeCAD Python (`test_fold_multi_bend`).
- Collection error (code): `test_self_collision_panels` imports `SELF_COLLISION_GAP_TOL_MM` etc. which
  exist on `HandMod-V.0.0.1` but not on `2D` → defect D2 (open).

After D1 fix: `58 passed, 7 skipped, 6 errors` (same 6 collection errors).

## Phase table

| Phase | Requirement | Impl. | Tests | FreeCAD verif. | Blockers | Next action |
|---|---|---|---|---|---|---|
| 0 | Baseline/audit | DONE | baseline recorded | headless only | – | – |
| 1 | Geometry contract | IMPLEMENTED (angle, r, axis) | TESTED (unit) | VERIFIED axis matching on 16/16 fixtures (headless) | t, K, BA not in contract | add t/K/BA fields |
| 2 | Panels + bend zones | IMPLEMENTED (separator strips) | TESTED (unit) | L, U explained exactly; 2-1-3-4 partially | explicit bend-zone model missing | see roadmap §Phase 2 |
| 3 | Kinematics | IMPLEMENTED (sharp hinge) | TESTED (unit) | not verified vs source | finite-radius kinematics | after Phase 2 |
| 4 | UI | IMPLEMENTED partially (`pressbrake_2d.py`) | – | v4 manual notes only | Level C pending | manual check list |
| 5 | Tooling/collision 2.5D | NOT IMPLEMENTED in 2.5D path (legacy OCC exists) | – | – | – | – |
| 6 | Sequence | NOT IMPLEMENTED in 2.5D path (legacy `Solvers/` exist) | – | – | – | – |
| 7 | Release | NOT STARTED | – | – | – | – |

## Phase 2 iteration 1 — evidence
Scripts are not committed (scratch). Method: `SM.getUnfold` (K=0.4, ANSI) on the largest planar face,
split the flat face with `panel_builder._strip` (w=0.02 mm), subtract (BA−w)/2·L per adjacent bend,
compare with planar source-face areas.

| Model | Panels | Exact match (≤1e-3 mm²) |
|---|---|---|
| L-(ok) | 2 | 2/2 (9749.3274 − 249.3274 = 9500.0000) |
| U-10-15-10-(punch2-OK) | 3 | 3/3 |
| 2-1-3-4-(ok) | 11 | 5/11; residuals P06 +0.280, P07 −0.851, P08/P10 +0.140, P09/P11 −0.426 mm² |

L: t=1.5, r_in=1.0, K=0.4 → BA=π/2·(1+0.6)=2.51327; (BA−w)/2 = 1.24664 mm = the reported v0.5.2 L
discrepancy. **Conclusion:** the L/U contour FAILs are validation mismatches (flat panel includes half
the bend allowance), not PanelBuilder geometry defects. 2-1-3-4 residuals are unexplained (likely
the area-correction approximation for partially adjacent bend lines); the 29.025 mm OCC edge value
was not reproduced. The v0.5.2 diagnostic script and its output are not in the repo/workspace.

## Defects
| ID | Status | Description | Evidence |
|---|---|---|---|
| D1 | FIXED | `match_bend_axes` measured overlap against `min(bend, sketch)` length, accepting a short fragment inside a long axis | failing unit test; fix keeps 16/16 real fixtures matching (min ratio 0.9992) |
| D2 | OPEN | `test_self_collision_panels` imports constants missing on `2D` | collection ImportError |
| S1 | SUSPECTED | 2-1-3-4 panel residuals P06–P11 | see table |
| L1 | LIMITATION | Sharp-hinge kinematics around BA centerline | code review `panel_kinematics.hinge_transform` |

## Commits
- `docs(2d)` roadmap/status + `fix(2d)` axis matching — see git log on `origin/2D`.
