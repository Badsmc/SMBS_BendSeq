# Native package migration

## Baseline
- Source: develop, commit 67a9482 (2026-11-26), version 0.2.0.
- Branch: refactor/native-package-layout. Never merge without user confirmation.
- 70 unittest headless tests pass; addon_preflight passes. Full output: native-package-baseline-tests.txt.
- No FreeCAD GUI validation has been performed in this environment.

## References and decisions
- https://freecad.github.io/Addon-Academy/Topics/Structuring/
- https://freecad.github.io/Addon-Academy/Demos/Minimal-Workbench/
- https://github.com/FreeCAD/FreeCAD (startup source to audit)
- https://github.com/FreeCAD/FreeCAD-addons (legacy index; modern index is FreeCAD/Addons)
- All implementation moves into freecad.bendseq; shared freecad namespace stays open.
- Preserve SMBSWorkbench, command IDs, ParamGet keys, session schema and calculations.
- Keep release at 0.2.0 until manual acceptance; target 0.3.0.
- Keep root entrypoints until actual FreeCAD 1.1 GUI verification, as requested.

## Progress
Baseline captured. Migration in progress.
