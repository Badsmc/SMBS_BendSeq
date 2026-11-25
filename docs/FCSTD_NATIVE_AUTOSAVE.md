# FCStd: native saving and automatic SMBS restoration

## Behavior

1. Open a SheetMetal model with an unfold and start SMBS.
2. After loading the part, SMBS configuration changes remain in the open session.
3. **FreeCAD → File → Save / Ctrl+S** automatically updates the embedded
   `SMBS_Data.SessionJSON` *before* native FCStd serialization. A separate JSON
   export or a "Save to FCStd" button is not required.
4. When reopening an FCStd while PressBrakeSim is active, the addon finds
   `SMBS_Data`, reads the **exact name** of the associated unfold, and loads it
   automatically. If the file was opened in another workbench, switching to
   PressBrakeSim starts the same safe process.
5. If the simulator is already open, the Simulation command can restore the
   saved part without requiring manual selection in the model tree.
6. Closing the simulator stores the current JSON in the document. Native
   FreeCAD Save is still required to write it to disk.

## Stored data

- Geometry identifier and SHA256, SheetMetal unfold, and all bends.
- Operation order, pre-bends and finishing bends, angles, and independent 0°/180° rotations.
- Selected punch and die (**references with profile checksums**).
- Simulation settings, speeds, tolerances, weights, and archived candidates.
- **Document-specific backgauge settings:** AUTO/MANUAL mode, X stations, S/Z
  coordinates, clearance, and feed.
- **Document-specific clamp configuration:** quantity, position, dimensions,
  clearances, and segmentation rules.
- Limited historical diagnostic results.

JSON size is limited to 4 MiB. Missing `tooling.backgauge` and
`tooling.clamps` fields remain valid for older FCStd files.

## Safety

- Automatic loading requires valid `SMBS_Data` and its specified unfold.
  An arbitrary CAD object is not selected in place of a missing unfold.
- The geometry is compared again using SHA256. If it differs, automatic
  overwriting of the old JSON is disabled pending manual resolution.
- A JSON generation error does not cancel saving the CAD model itself.
  Report View shows the reason, and previously saved SMBS data may remain unchanged.
- Restoring clamp settings **does not overwrite the global**
  `toollibrary/config.json`. Clamp-dialog changes for the loaded part are
  written to its FCStd on the next save.
- External FCStd files for punches, dies, and the backgauge, and inventory
  `.tooling.json` files, **are not embedded** in the document. They must remain
  separately available on another FreeCAD installation.
- Restored 3D/OCC check results are historical, not newly verified results.
  Pre-bend/finish sequences remain `UNVERIFIED` until compatible collision
  checking is implemented.
- This embeds data automatically **during native FreeCAD saving**; it is not
  a periodic background FCStd autosave every N minutes.

## Manual verification in FreeCAD

Create a part with 3–4 bends → change its order, set B01.1/B01.2 and their
independent 180° checkboxes, select a punch and die, configure the backgauge
and clamps → Ctrl+S → close the document/FreeCAD → reopen the FCStd with
PressBrakeSim active → verify restoration without selecting the unfold,
including the table and the clamp/backgauge settings dialogs.

Repeat with a document without JSON, two different documents, a broken tooling
reference, and modified CAD geometry.
