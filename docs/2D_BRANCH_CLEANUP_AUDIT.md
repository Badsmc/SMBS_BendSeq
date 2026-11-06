# Branch 2D cleanup audit — 2026-11-06

**Canonical checkout:** `Badsmc/SMBS_BendSeq`, branch `2D`.
**Reversible snapshot:** `archive/2d-pre-cleanup-20261106` at
`c221c0b08fa450b6e36ebc5ce75de4389524d293`.

## Removal scope

Commit `9374cc3a7fca7a5ed280b974ea1fde096dbf78bb`
removed **142 tracked files** from the 2D branch. This only changes 2D;
no files were deleted from Git history, `main` or `d6-native-3d`.

Removed:
- Deprecated `smbs_core/Core`, `Geometry`, `IO`, `Physics`, `Solvers`,
  `workers`, `viewer`, `addon`, `api`, `contracts`, `coordinator`,
  `protocols`, `tooling`, `testing` and legacy PythonOCC backend.
- Old SMBS calculation, run, replay, annotation, preferences and verifier
  command handlers and redundant GUI dialogs/icon resources.
- Tests exclusively tied to the removed OCC orchestration and unavailable
  `SELF_COLLISION_GAP_TOL_MM` legacy contract.
- Old OCT/PythonOCC `.github/workflows/ci.yml` *on branch 2D only*,
  replaced by the authoritative pure 2.5D CI.
- Stale historical 2D v4 and HandMod transition plans and generated reports.

Preserved:
- `Init.py`, `InitGui.py` and `freecad_smbs/InitGui.py`, with exactly two
  exposed workbench commands: active 2.5D workspace and tooling library.
- `freecad_smbs/commands/pressbrake_2d.py`, `smbs_core/kinematics2d/`
  and the SheetMetal extraction / panel graph integration.
- Real tooling assets and tooling catalog, including FreeCAD metadata
  reader and configuration / clamp validation.
- `demo/`, `fixtures/freecad/load_fcstd.py`, motion trace analysis,
  workbench installation helper and 2.5D regression tests.
- The 2.5D UI contract, master specification and explicit release gates.

## Key structural corrections

- The actual FreeCAD runtime no longer imports unexposed legacy commands.
- Tooling library command loads the retained tooling catalog directly,
  without the removed `smbs_bridge`.
- Machine frame badge no longer imports old 3D animation viewer.
- The tooling backend `__init__.py` modules no longer trigger PythonOCC
  imports when only FCStd metadata is needed.
- `pyproject.toml` describes the 2.5D addon; pythonocc is no longer a
  runtime dependency.
- 2D workbench CI compiles every retained source file and runs currently
  portable regression tests. Full GUI native tests are separate release gates.

## Not ready for a production release

- The original 17 demo FCStd binary parts are **not yet in GitHub**.
  Only `demo/MANIFEST.json` and a SHA-256 checked local ZIP installer exist.
- A *fresh* clean-install acceptance test on real Windows FreeCAD GUI is
  required for the two-command menu, tooling, Play All, Full Cycle, recording,
  model integrity and animation continuity.
- The 2D solver has a playable manual bend order, **not** a fully physically
  verified automatic manufacturing sequence search.
- 2.5D cannot certify full machine/3D clearance; the native D6.3.4 evaluator
  remains separately in `d6-native-3d`.
- Qt GUI tests requiring FreeCAD are not executed by the Linux pure-Python CI.

The word `PASS` on a Linux headless job must never be mistaken for FreeCAD
GUI acceptance or press-brake manufacturing qualification.

## Workflow for all future work

1. Checkout `2D`, not main or the experimental D6 branch.
2. Reproduce and test with minimal changes in active modules.
3. Add a targeted regression test and run the 2D CI.
4. Verify in FreeCAD on original fixtures before marking a user-facing
   feature VERIFIED.
5. Never copy back the old pipeline wholesale. If a 3D calculation is needed,
   introduce a narrow adapter and evidence contract.
