# Backgauge.FCStd — visual-only mode

This feature continues from `feature/fcstd-embedded-smbs-json`.
It does not change the FCStd embedded SMBS JSON storage format or the
independent punch/die/frame verification paths.

## In FreeCAD

1. Open the SMBS 2.5D workbench, select a SheetMetal part and load its unfold.
2. Open **Задний упор**, enable **Показывать движущийся упор Backgauge.FCStd**, and choose automatic placement (or enter explicit S/Z targets).
3. Save settings. The simulation draws only the actual CAD cross-section from `toollibrary/Backgauge/Backgauge.FCStd`. If the model cannot be loaded, no synthetic rectangle is drawn.
4. In the bends table, **До края, мм** shows the geometric distance, regardless of manual gauge positioning.
5. Switching bends moves the stop to each bend's target. During full-cycle `TRANSFER`, the target moves smoothly between operations. Text labels are not overlaid on the viewer.

## Distance definition

The distance is measured in the unfolded SheetMetal XY coordinate frame:
- Start: midpoint of the *canonical bend line*, not a bend tangent or flange corner.
- Direction: perpendicular to that line, toward the rooted fixed panel's centroid.
- End: first intersection with the *full external blank contour*. Artificial cuts between topology panels are not treated as the workpiece edge.
- Units: millimeters; two decimal places in the operations table.

The algorithm uses an ordered discretization of the real outline wire
(0.05 mm deflection). It computes each dimension once on part load.
Ambiguous/missing geometry is reported as **—**. The application does
not guess a value or use the bounding box as a substitute.

In automatic placement, the CAD stop's datum front face is placed at
`distance + configured clearance` in machine S; the Z coordinate is
a *preview* estimate from the active flat bend-zone support and thickness.
Manual S/Z targets override the visual position, but not the measurement
displayed in the table.

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
