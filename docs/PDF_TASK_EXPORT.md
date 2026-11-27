# Configurable PDF operator instructions (feature/pdf-task-export)

Click Bend plan PDF with a loaded part and selected tooling. The settings are
a specialist page of the existing native FreeCAD Tasks stack. Close section
returns to the previous task; the MDI and simulation remain independent.
The destination is editable directly; the optional Choose file button invokes
the native file picker. Existing files require a second explicit Generate click.

- A4 portrait/landscape; 1–10 operations per page; optional large first card.
- Overview, flat pattern, bend table, tool sections/names, segment quantities,
  before/after images, separate workpiece inset, bend axis and gauge information.
- Independent visibility and 0–100% transparency for part, punch, die,
  machine frame/ram, clamps, gauge and collision evidence. 100% hides a layer.
- Punch and die segment highlighting is independent: disabling it preserves
  the real section geometry and removes distinct segment colors/seam strokes.
- Working-area crop (default) or full-machine fit. Settings persist in QSettings;
  export does not change live tooling, visibility or motion.
- Collision evidence is optional and comes only from a matching Mesh result.
  A separate page reconstructs the recorded sampled workpiece pose and ram Z.
  The last retained exact confirmed intersection can be tessellated; other
  findings show the reported overlap bounding box, explicitly labeled.
  Conditional findings are labeled. No new collision test is run. No matching
  findings is not a clearance verdict. Backgauge is omitted on these pages
  because its exact sample position is not saved.
- The document stays English; UI uses the FreeCAD language.

Headless tests cover configuration, alpha/visibility, crop, AABB coordinates,
stale evidence and legacy startup. Real FreeCAD/Qt PDF rendering, transparency
appearance, and TaskPanel lifecycle still need interactive acceptance.

## Grouped collision evidence

Mesh Tasks offers By bend (default) or Exact sample. Each staged operation is
separate. The grouped reference is the largest confirmed finding, or largest
conditional finding if none confirmed. Recorded AABB corners are transferred
through source and reference panel rigid transforms; changed deformable bend
zones are marked as whole components, not invented exact overlaps. Red is
confirmed, orange conditional. The map is not simultaneous collision geometry.
Part and tool transparency controls affect collision viewing only. PDF export
uses the same grouping by default, lists each finding, and can be switched to
individual samples. The Mesh checking algorithm is unchanged.
