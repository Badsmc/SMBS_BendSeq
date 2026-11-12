# Backgauge.FCStd — visual-only mode

This feature continues from `feature/fcstd-embedded-smbs-json`.
It does not change the FCStd embedded SMBS JSON storage format or the
independent punch/die/frame verification paths.

## In FreeCAD

1. Open the SMBS 2.5D workbench, select a SheetMetal part and load its unfold.
2. Open **Задний упор**, enable **Показывать движущийся упор Backgauge.FCStd**, and choose automatic placement (or enter explicit S/Z targets).
3. Save settings. The simulation draws only the actual CAD cross-section from `toollibrary/Backgauge/Backgauge.FCStd`. If the model cannot be loaded, no synthetic rectangle is drawn.
4. In the bends table, **До гиба, мм** is the nominal LOCAL line-to-line (or edge-to-line) flat-pattern dimension, while **Упор S, мм** is the current visual machine setting from bend axis to the gauge's working face, incorporating prior signed bends, 1.5 mm (or actual source) thickness and finite bend radius. In manual mode the displayed S is the entered target.
5. Switching bends moves the stop to each bend's target. During full-cycle `TRANSFER`, the target moves smoothly between operations. Text labels are not overlaid on the viewer.

## Dimension definitions and sign/sequence behavior

**До гиба, мм** is a nominal local distance in the SheetMetal flat XY
sketch. For example, with line positions at X=10 and X=32 mm:
- B01: 10 mm from the outer edge to the line.
- B02: 22 mm from B01 to B02, NOT the cumulative 32 mm.

The local value is measured perpendicular to the current bend axis from
its midpoint to the nearest parallel neighbouring bend axis or external
blank boundary. It is NOT a finished outer flange dimension. That
finished dimension can include sheet thickness and the radius geometry.

**Упор S, мм** is the rear face position relative to the active bend
axis in the 2.5D machine coordinate frame. This is calculated from the
actual **previously completed signed bends**, not the flat local dimension
plus a hard-coded 1.5 mm compensation:

1. Create an isolated `FoldState` from the active session's panel graph;
   never edit the running playback state.
2. For each bend in the **current user sequence**, apply all previous
   bends to their final signed target angles. The active and later bends
   remain flat before their respective operations.
3. Compute exactly the pre-bend machine placement using the same
   `WorkpiecePlacement3D` geometry and `BendZone` parameters
   (thickness, inner radius, K-factor, bend allowance).
4. Compute the machine cutting-plane section at the configured
   first CAD backgauge X-station. Include the thickness-bearing panel
   sections **and** all finite-radius bend-zone polygons.
5. Take the furthest positive-S projected sheet envelope, with
   corresponding Z, and place the real stop working face at that S
   plus user clearance. In manual mode the user-specified S/Z overrides
   the automatic target.

This is a **geometric silhouette projection for visualization**, not
proof of an accessible contact point. It does not run any OCC overlap,
penetration, contact, or travel-limit checks. If the machine section
cannot be constructed unambiguously, the table shows **—** rather than
inventing a measurement. The physical reference frame and bend
orientation are important; the user must check the setup in FreeCAD.

A 1.5 mm sheet can produce a value near 23.5 mm after a 90° preceding
bend with a 22 mm local pitch, but the exact value depends on inner
radius and allowance; it is **not always exactly 22 + 1.5 mm**. In the
headless synthetic fixture (1.5 mm sheet, inner radius 1 mm,
K=0.31, 10/22 flat pitches), the projected value is 23.34939 mm.

The sign of a bend that has **not yet been formed** must not change
the backgauge setting *before* that bend. Its sign changes the next
setup only **after** it is formed. A three-bend headless fixture
demonstrated B03 projected S of 48.067 mm following B01=+45°,
B02=−45°, versus 38.667 mm following B01=+45°, B02=+45°. Both use
the same flat bend line distances.

**The dimensions in the operations table are not additive.** The
machine S setup is measured anew from the current bend axis and
the actual signed, previously formed geometry; it is not the sum
of previous table entries.

## Deliberately disabled for this feature

- All backgauge-to-sheet, backgauge-to-tool, and backgauge-to-backgauge collision and penetration tests in the GUI workflow.
- OCC contact solving, contact-height discovery, first-contact checks, backgauge search penalties, and travel-limit checks.
- Backgauge-derived rejection of automated or manually entered bend sequences.
- Backgauge status/error annotations in the 2.5D viewer.

Other machine and tooling collision checks remain in their existing
separate workflows. The gauge is an illustrative fixture, **not evidence
of manufacturability or machine-safe positioning**.

## Quality gates

`tests/test_gauge_preview.py` runs headlessly. The existing GUI contract
workflow also compiles the modified Python sources and tests punch coupling
and embedded FCStd JSON storage. Actual FreeCAD GUI geometry must still be
tested manually with representative bent parts, including multiple bends,
manual order, two CAD fingers, and full-cycle `TRANSFER`.
