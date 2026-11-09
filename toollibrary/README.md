# SMBS Tool Library

This is the canonical shared equipment library for the integrated development branch `main`.

## Inventory (2026-11-09)

| Role | CAD model | Metadata |
|---|---|---|
| Punch | `punch/puancon.FCStd` | `punch/puancon.tooling.json` |
| Die | `die/matrica.FCStd` | `die/matrica.tooling.json` |
| Backgauge | `Backgauge/Backgauge.FCStd` | `Backgauge/Backgauge.tooling.json` |
| Clamp | `Clamp/Clamp.FCStd` | `Clamp/Clamp.tooling.json` |
| Machine frame | `machine_frame/machine_frame.FCStd` | `machine_frame/machine_frame.tooling.json` |
| Press | `presses/cncbender.FCStd` | none (same Git blob as `machine_frame/machine_frame.FCStd`) |

The `presses/cncbender.FCStd` file and the machine frame are byte-for-byte identical in Git. Do not independently treat them as two validated machine models.

## Named sets

`sets/standard_90deg_v12.json` was restored from historical `AutoMod-V0.0.1` and is also present in `HandMod-V.0.0.1`.

- Set ID: `standard_90deg_v12`
- Punch: `puancon` (sidecar R1 mm, 88°)
- Die: `matrica` (sidecar V12 mm, 88°)
- Clamp positions: 75 mm and 275 mm
- Recommended thickness in metadata: 2.0 mm — informational, **not a validated machine limit**

The setup is a named configuration, not a guarantee of successful or collision-free bending.

## Integration

The FreeCAD UI uses `freecad_smbs.integration.smbs_bridge.get_tooling_library()` which resolves this folder relative to the repository. `ToolingLibrary` loads punch/die catalogs and named sets using `smbs_core.geometry_backend.io`. The canonical backgauge and clamp paths in `freecad_smbs/resources/tooling_paths.py` now match the actual case-sensitive filenames.

Keep these FCStd files and JSON sidecars paired. Do not change datums, tool dimensions or file IDs without validating in real FreeCAD and updating tests. On `main` the files have been checked for existence, reference IDs and Git blob consistency; **geometry and machine collision verification in FreeCAD has not been performed**.

The `smbs` install branch is not automatically updated when `main` changes. A validated release promotion is required.
