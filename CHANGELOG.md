# Changelog

## 0.1.0 — 2026-11-25

Initial public preview of the current PressBrakeSim implementation.

- Manual bend ordering, staged pre-bends and finishing operations.
- FAST candidate sequence search and orientation preview.
- Coin3D simulation with selected tooling and backgauge representation.
- Sampled Mesh 3D collision checks, collision export and bend information.
- SheetMetal unfold and ASCII DXF input.
- Tool segmentation, clamp settings and FCStd session persistence.
- English source identifiers and separate English/Russian interface catalogs.
- Addon metadata, runtime version diagnostics and a version consistency test.
- Historical reference data retained separately from the active runtime.

Limitations: full FreeCAD acceptance, continuous machine collision verification,
manufacturing certification and official Addon Manager listing are pending.

- Addon Index preparation: canonical SheetMetal dependency, native Part, FreeCAD Qt wrappers, host-managed import paths and standard-library tooling-set validation.

Licensing: LGPL-2.1-or-later for original code and CC-BY-SA-4.0 for original assets, with full license texts and a separate CAD provenance review.

## Development updates (not yet released)

- Parametric punch and die editor with live dimensioned drawing, DXF and FCStd export, centered punch tip and shank with adjustable body offset.
- Coin3D machine frame: fixed lh/rh/down/up bodies; moving z layer and clamps follow the existing ram trajectory.
