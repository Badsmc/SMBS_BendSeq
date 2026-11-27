# Development and releases

## Branches

| Branch | Purpose | Version |
| --- | --- | --- |
| `main` | Public preview and reviewed releases | 0.2.0 |
| `develop` | Current integration and development | 0.2.0 |

Start feature and fix branches from `develop`. Promote reviewed versions to `main` and document outstanding acceptance checks accurately. Keep the package version, date, runtime version and changelog consistent. Historical work is available in Git history and archive tags.

## Local checks

Run from the repository root with Python 3.11 or newer:

```sh
python scripts/addon_preflight.py
python scripts/build_qt_translations.py --check
python -m compileall -q freecad scripts tests Init.py InitGui.py
python -m unittest discover -s tests -v
```

CI repeats these checks on Python 3.11 and 3.12 and validates the compiled Qt translation catalog. `scripts/check_core_migration.py` additionally checks the historical migration commits; it requires full Git history, as configured in CI.

## Release acceptance

Automated checks do not exercise the FreeCAD GUI or establish physical clearance. Before promoting a release, verify clean Addon Manager installation, workbench startup, DXF and SheetMetal input, manual/staged operations, FAST, Mesh, simulation, tooling, PDF and FCStd save/reopen. Record platform, FreeCAD version and actual results in the [migration and acceptance checklist](NATIVE_PACKAGE_MIGRATION.md).

See [Addon Index preparation](ADDON_INDEX_SUBMISSION.md), [changelog](../CHANGELOG.md), [licenses](../LICENSE.md) and [security policy](../SECURITY.md).

## Installation archives

`.gitattributes` excludes `tests/`, `scripts/` and `.github/` from Git export archives. These development files remain in the repository for validation and maintenance. Runtime files, user documentation, licenses, demo parts and README media remain included. A Git clone includes development files.
