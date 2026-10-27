# HISTORICAL — Known Limitations (pre-refactor, 2026-10-11, base commit d63eac2)
#
# Audited body date: 2026-10-11, pre-refactor legacy suite (SMBS baseline 7a0d87b)
# Deprecated at commit: 289daa3 (2026-10-17)
# Does NOT describe current HEAD: 91d07c0 (stage-1-fix-correctness)
# Canonical limitations list: docs/PROGRESS.md §Known Limitations (lines 395–534)
# Canonical P0 defect registry: docs/PROGRESS.md §Open P0 Defects + docs/AUDIT_FULL_2026-10-23.md
# Current release decision: NOT READY
#
> **DEPRECATED (2026-10-17).** This file predates the Stage 1/2/3 refactor.
> Canonical Known Limitations list: `docs/PROGRESS.md` §Known Limitations.
> Kept for historical reference only. Do not update.

---

# SMBS / BendSeq - Known Limitations

## 1. Zero-Thickness Simplification
Collision detection (`collision_detector.py`) occasionally relies on `TopoShape` bounding box overlaps or boolean intersections that assume perfect rigidity. Extreme near-misses (e.g., clearance < 0.1mm) might falsely register as a collision depending on OCC tolerance.

## 2. Flat Pattern Backgauge "X" Dimension
The calculation in `backgauge_calculator.py` uses an estimation (`max(20.0, bend_length * 0.5)`) for the backgauge depth `X`. True backgauge depth requires precise projection of the flat pattern's parallel rear edges. The output JSON safely marks `geometry_confirmed: False` and warns the operator.

## 3. Springback Compensation
Bend radii and angles assume static inputs. The database currently maps tooling strictly by basic rules (V = 6T). Complex springback coefficients and dynamic tool clearance (e.g. 30-degree deep V-dies for over-bending) are not yet implemented.

## 4. GUI & FreeCAD Sandbox
The add-on creates temporary document objects (`Simulation_Part`) that must be manually deleted by the user or ignored. There is no automated garbage collection of simulation features upon closing the task panel.

## 5. Symmetric Bend Matching
When two bends have identical lengths and exactly identical distance topologies relative to all other bends, they are marked as `AMBIGUOUS`. The addon will not arbitrarily bind them to sketch lines to prevent catastrophic machining errors.

---

## Current Status (annotated 2026-10-23)

This file is a HISTORICAL snapshot. The five limitations above (Zero-Thickness,
Backgauge "X", Springback, GUI Sandbox, Symmetric Bend Matching) reflect the
pre-refactor legacy state at SMBS baseline `7a0d87b`, deprecated in `289daa3`.

It does NOT describe:
- Current HEAD: 91d07c0 (stage-1-fix-correctness), baseline 550 tests.
- The canonical limitations list: see `docs/PROGRESS.md` §Known Limitations
  (KL-12 v3 forward fold, KL-16 machine frame, KL-23 cyclic N≥8, etc.).
- Open P0 defects P0-CODE-1..5 / P0-DOC-1..4: see `docs/PROGRESS.md`
  §Open P0 Defects and `docs/AUDIT_FULL_2026-10-23.md`.

**Current release decision: NOT READY.**
Evidence and open blockers: `docs/AUDIT_FULL_2026-10-23.md` §Release Verdict.
