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
