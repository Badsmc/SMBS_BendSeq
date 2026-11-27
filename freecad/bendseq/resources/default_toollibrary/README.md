# Local tooling library

The distributed punch and die directories are empty. No ready-made punch or die DXF, FCStd or JSON metadata files are bundled.

Create original demonstration tools using the [parametric editor](../../../../docs/PARAMETRIC_TOOL_GENERATOR.md), or import your own compatible models.

Use `punch/` and `die/` for local models and matching metadata. Writable user libraries are stored outside the installation under `FreeCAD.getUserAppDataDir()/BendSeq/`. Set real segment lengths and quantities for your own stock. Existing saved projects may need replacement tooling selected.

Backgauge, clamps and machine-frame files remain separate. Geometry validation does not certify machine safety or manufacturing feasibility.
