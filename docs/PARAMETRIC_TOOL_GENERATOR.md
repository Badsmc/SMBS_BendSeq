# Parametric Tool Generator (experimental)

Open the full Tooling library, select Punches or Dies, then Generate tool.
Select a preset, set dimensions and the ID, then confirm. The generator writes
an exact line/arc ASCII DXF, a native extruded FCStd solid and tooling.json
into the selected active library. Existing entries are never overwritten.
Stock quantity defaults to zero (unknown). A length is reference geometry,
not evidence of real stock.

Original starter presets: P85, P88, P90, P30, gooseneck P88, and V6, V8, V10,
V12, V16, V20, V25, V32, V40, V50, V80. Use the preset dropdown to create
individual models. No commercial profile is copied. They are generic
demonstration geometry, not tooling drawings or certified machine configurations.

Headless tests verify each DXF round-trip with the existing geometry importer.
FCStd generation requires FreeCAD Part and validates the actual native solid
and existing loader's extracted datums before publishing it. Verify in FreeCAD
1.1.x, including 2D preview, Coin3D, FAST and Mesh, before adopting as default.
The prior tooling and provenance gate are unchanged; the generator alone does
not clear the redistribution rights of existing third-party models.

## CNC-style live editor

The resizable dialog shows the parameter form on the left and a proportional
cross-section with dimensions, centerline and active-field highlighting on the
right. Every edit rebuilds the same geometric contour used by DXF and FCStd.
Invalid dimensions clear the drawing and disable Create; no stale geometry is
saved. H1 is derived from H2 minus the mounting shank height. V is the nominal
opening at the die top plane. L and quantity refer to the longitudinal stock,
not the transverse drawing. Two independent generic T-base die presets (V10,
V25) supplement the rectangular dies. B and T control base width and height;
set both to zero for a rectangular die. Dimension symbols are SMBS conventions,
not a claim of compatibility with a specific CNC controller's L1–L9 schema.
No controller safety or force settings are copied. The diagrams are screen
previews; the exported circular arcs are exact.

## Extended editor controls

H1 and H2 are independently editable; the mounting shank height is derived.
The punch relief has editable starting position, height, transition and depth,
and can be mirrored left/right. The die supports independent left/right upper
widths, base widths and base heights. L7 groove depth and L8/L9 upper heights
are derived dimensions, not independent constraints. The SMBS dimension codes
are not a direct import format for the photographed CNC controller. Reset
restores the current preset. DXF export is explicit and refuses to overwrite
an existing file; Save new tool uses the native validated library export.
Controller force and safety fields are intentionally not simulated.

## Punch transverse offset and drawing contrast

The signed ΔX parameter moves the body and mounting shank horizontally:
positive right, negative left. The working tip stays at (0,0); the original
tangent flanks retain the included angle and radius, and intersect the body
sides at different heights. The relief dimensions reference the chosen side.
Offsets that leave insufficient flank geometry or violate closure are rejected.
The mounting datum follows the shank; the working axis remains unchanged.
The technical drawing uses a dedicated dark background, yellow dimensions
and white active dimension, independently of the host UI theme.
