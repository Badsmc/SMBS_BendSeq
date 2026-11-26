# Branch architecture and release policy

| Branch | Role |
| --- | --- |
| `main` | Public addon preview and reviewed releases |
| `develop` | Active integration and development |

Version 0.1.0 remains the public main preview. Version 0.2.0 integrates native FreeCAD improvements into develop.
Historical code remains reachable through the original main ancestry and
`archive/2026-11-25/` tags. New changes start from develop using
`feature/*`, `fix/*`, or `experiment/*`. Integrate reviewed changes into develop
before promoting to main.

## Release gates

- Preserve the FreeCAD entry point, unique command IDs and geometry contracts.
- Keep the Python version, package.xml and changelog consistent.
- Run deterministic unit tests and clean-install checks.
- Verify GUI registration, SheetMetal/DXF input, manual and staged ordering,
  playback, reopening, tooling, FCStd persistence and language switching.
- Check FAST and Mesh behavior using reference models; report sampled results
  separately from continuous physical validation.
- Never mark unverified sequences as production-ready.

The 0.2.0 development preview has not yet passed all acceptance gates for promotion to main.
