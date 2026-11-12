# Backgauge.FCStd — visual-only mode

This feature continues from `feature/fcstd-embedded-smbs-json`.
It does not change the FCStd embedded SMBS JSON storage format or the
independent punch/die/frame verification paths.

## In FreeCAD

1. Open the SMBS 2.5D workbench, select a SheetMetal part and load its unfold.
2. Open **Задний упор**, enable **Показывать движущийся упор Backgauge.FCStd**, and choose automatic placement (or enter explicit S/Z targets).
3. Save settings. The simulation draws only the actual CAD cross-section from `toollibrary/Backgauge/Backgauge.FCStd`. If the model cannot be loaded, no synthetic rectangle is drawn.
4. In the bends table, **Полка, мм** shows the *local* flange pitch: from the midpoint of the active bend line to the nearest parallel neighbouring bend line or physical blank edge. This is independent of the gauge's visual position.
5. Switching bends moves the stop to each bend's target. During full-cycle `TRANSFER`, the target moves smoothly between operations. Text labels are not overlaid on the viewer.

## Dimension definition (operations table)

These dimensions are **local**, not cumulative distances to the same
original outside edge. Example, on a 100 mm-wide flat blank:

- Bend B01 is at X=10 mm → **Полка = 10 mm** (the outside edge at X=0).
- Bend B02 is at X=32 mm → **Полка = 22 mm** (the neighbouring B01 line
  at X=10), **NOT 32 mm** from the original outside edge.

Each candidate is measured from the midpoint of its own canonical bend
line, perpendicular to its axis, on both sides. The nearest true external
blank boundary or nearly parallel neighbouring bend axis (within ~5.7°
and covering the probe point) is taken. Sketch segments of other
orientations are not silently used as flange boundaries. The result
uses the flat SheetMetal XY frame, in millimetres with two decimals.
If a meaningful boundary cannot be resolved, the table shows **—**.

This is a nominal layout flange pitch, **not** a finished internal/external
flange dimension after bending allowance. It is geometric and does
not change when the operator rearranges the bend sequence.

### Separate visual gauge placement

The previously implemented absolute distance from a bend line to the
full unfolded outer contour on its rooted fixed-panel side is still
stored separately in `gauge_distances_mm`. It is *not* the table value.
The CAD gauge placement continues to use that earlier distance and is
still an unverified visual approximation; the placement issue seen
in real FreeCAD must be checked separately against the active machine
section. Neither value certifies backgauge contact or machine clearance.

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
