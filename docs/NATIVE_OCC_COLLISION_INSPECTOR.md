# Native FreeCAD OCC collision inspector

Branch: `feature/native-occ-collision-inspector`
Parent: `fix/restore-legacy-manual-2d-check`.

## Purpose

Show **where and what intersects** in the actual FreeCAD 3D model used by
the Manual native D6 sampled collision checker. The old Qt "Показать коллизию
в 3D" preview with red XYZ bounding boxes remains available. The new button
"Открыть точное пересечение OCC" is shown only after an independently verified
blocking sheet–punch or sheet–die collision, not after a merely advisory 2D
finding or a timeout.

The inspector reconstructs a detached FoldState with all completed bends
and the first blocking sampled bend angle, applies the exact logged 3×3
machine orientation and translation, reconstructs native SheetMetal
panels/bend zones and selected FCStd punch/die (including virtual length
or selected sections), and runs a **new actual OCC Common** boolean. It
requires the replayed overlap volume to agree with the recorded native D6
sample (within 2%, at least 0.05 mm³); otherwise it fails and logs the
mismatch rather than showing possibly wrong geometry.

A separate unsaved document `SMBS_OCC_Bxx_<id>` opens in FreeCAD.
It contains these tree groups:

- **Машинная установка — контекст**: translucent full workpiece, real
  punch and die in the sampled machine frame;
- **Задетые панели и зоны**: actual panel or bend-zone solids highlighted
  amber, labelled `panel:P09`, `bend_zone:B07`, etc.;
- **ТОЧНЫЕ пересечения OCC**: opaque red full sheet × punch/die Common
  solids. Optional per-panel exact common solids are hidden initially
  to avoid double rendering; users can toggle visibility.

Camera attempts FreeCAD's `ViewSelection` command to frame the actual
contact solid; View → Standard views → Fit all (`V` then `F`) frames the
whole machine installation. Mouse wheel zoom and 3D orbit remain native.

## User acceptance in FreeCAD 1.1.x on macOS

1. Update/install **this branch only** under FreeCAD Mod; close FreeCAD first.
   Confirm `SMBS_RUNTIME_READY.module_file` points to this version.
2. Open `2-1-3-4-(ok).FCStd`, select the SheetMetal unfold, choose the
   same AP1017 punch / AT3015 V12 die and virtual-length/end-zone settings.
3. On Manual, enter `B07 → B03 → B05 → B01 → B08` and enable the 180°
   operator setup. Click **Проверить 3D**, not the fast **Проверить 2D**.
   For the previously observed B08 collision, the new exact OCC button
   should become enabled after D6 reports the first blocking sample
   (previously at ~22.5°; actual result can differ with setup).
4. Click **Открыть точное пересечение OCC**. A **separate unsaved FreeCAD
   document** should open. Rotate the camera, toggle context and affected
   panels through tree visibility, and inspect red Common geometry. The
   displayed contact volume is the volume of the *first blocking sample*,
   not the maximum overlap later in the trajectory.
5. Close only this temporary inspection document. Verify the source FCStd
   is still open, contains the original model, and can be used normally.
6. Change the punch/die identity or virtual-length setting after verifying.
   The native collision inspector must reject stale tooling and request
   a fresh "Проверить 3D" run. Try an insufficient or missing FCStd tool:
   it must fail visibly and add `MANUAL_EXACT_OCC_INSPECTION_FAILED` to
   the Desktop JSON developer journal.
7. Confirm that the quick **Проверить 2D** still uses only Stage A/B
   and does not open documents or run the native OCC inspector.

## Scope and limitations

- The report is a view of **sampled collisions** at known machine poses;
  it is not a continuous safety certificate or a valid production sequence.
- TRANSFER, backgauge, clamps, and press frame remain outside the native
  punch/die collision scope.
- The listed tool geometry can be virtual/extruded along the active bend
  length. That is indicated in the event log and does not certify installed
  physical tool section coverage.
- No geometry from this temporary document is saved into the source FCStd.
- GitHub Actions can test code and data contracts but cannot validate the
  native OCC visuals or their macOS FreeCAD camera; physical acceptance in
  FreeCAD is required before merging to `main` / installable `smbs`.
