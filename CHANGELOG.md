# Changelog

## 0.2.0 — 2026-11-27

Public preview published from `main`; version remains 0.2.0.

- Native FreeCAD Tasks integration, document lifetime management and task restoration after minimizing.
- FreeCAD ParamGet preferences, Qt translation catalogs and native toolbar sizing.
- FAST shortlist, pre-bend editing, Mesh controls and configurable PDF instructions in the shared Tasks surface.
- Nonblocking DXF and parametric tooling editors; DXF version and code-page-aware decoding.
- Modern namespace discovery with legacy entrypoint compatibility and duplicate-installation guards.
- Runtime startup and installed interface fingerprints, PDF export and lifecycle diagnostics.
- Regression tests and Windows FreeCAD 1.1.3 smoke checks.

The public `main` branch is now 0.2.0. macOS acceptance, clean Addon Manager installation and manufacturing validation remain pending. Sampled collision checks are not continuous safety certification.

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

## Additional 0.2.0 changes

- Confirm maintainer authorship and CC-BY-SA-4.0 licensing for all 20 bundled CAD models and active fixture settings.
- Add DXF, Bend and PDF demonstration media to the README.
- Consolidate documentation, correct resource links, and remove obsolete test transcripts, unused historical fixture settings and the completed one-off media publishing workflow.
- Exclude tests, CI configuration and development scripts from installation ZIP exports.
- Clarify that calculated sequences and collision results may be incorrect and require independent verification.

- Parametric punch and die editor with live dimensioned drawing, DXF and FCStd export, centered punch tip and shank with adjustable body offset.
- Coin3D machine frame: fixed lh/rh/down/up bodies; moving z layer and clamps follow the existing ram trajectory.

- Remove distributed punch/die CAD profiles, sidecars and legacy tooling sets; retain local tooling workflows.
