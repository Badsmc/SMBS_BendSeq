# SMBS BendSeq — FreeCAD demo fixtures

`demo/parts/` is the **only canonical repository location** for the original demo workpiece `.FCStd` files used by the 2D/2.5D FreeCAD fixture loader and native 3D diagnostics.

The source files are never overwritten by test runners. The names `(ok)` and `(false)` are supplied fixture labels, **not** independently verified collision results.

## Expected original CAD models

```text
demo/parts/
  L-(ok).FCStd
  L-30-6-(false).FCStd
  Z-(ok).FCStd
  Z-10-20-10-(ok).FCStd
  Z-10-20-10-(false).FCStd
  135-(ok).FCStd
  U-90deg-90deg-(ok).FCStd
  U-10-30-10-(OK).FCStd
  U-10-10-20-(false).FCStd
  U-60deg-30deg-(ok).FCStd
  U-60deg-30deg-(ok)-variant2.FCStd
  U-10-15-10-(punch2-OK).FCStd
  box.100out-50in-(ok).FCStd
  box.100in-50out-(false).FCStd
  SQin.200-25-10-(ok).FCStd
  SQout.200-25-10-(ok).FCStd
  2-1-3-4-(ok).FCStd
```

**Binary fixture status (working branch `feature/25d-physical-validation`):** 16 `.FCStd` models are active in `demo/parts/`. 15 are from `AutoMod-V0.0.1/SMBendTests`, and `box.100out-50in-(ok).FCStd` was replaced from `BendSeq_Fixtures_Source/box.100out-50in-(ok).FCStd` (size: 86,662 bytes, SHA-256: `6510d62cdf6427cfac18d936243c39f8aafa4e31718248124105642ad9e52db3`, 18 objects) to supply a functional SheetMetal `Body_Unfold` object required by native Part session builders.

The active 16 models and their authoritative sizes and SHA-256 checksums are documented in `demo/MANIFEST.json` (schema v2). The separate historical 17-model archive (`SMBS_demo_FCStd_17_files.zip`) remains a separate optional workflow and is **not declared restored**.

## Layout and cross-platform support

`smbs_core.demo_paths.demo_part_path("L-(ok).FCStd")` resolves `demo/parts/L-(ok).FCStd` from the module's repository location, not the current working directory or the user's Desktop. The same code works on Windows, macOS and Linux. A missing fixture is an explicit error, never a generated stand-in.

`fixtures/freecad/load_fcstd.py` reads inputs from `demo/parts/` and produces *generated* fixture artifacts inside `fixtures/freecad/<fixture_slug>/`. Original sources remain unchanged.

Tool CAD assets remain in `toollibrary/` under their original paths; they are not duplicated into `demo/parts/` because the existing tooling library depends on them.

## Installing original binary demo models

For the historical 17-file revision, `tools/install_demo_models.py` expects `SMBS_demo_FCStd_17_files.zip` with file hashes matching `demo/MANIFEST.json`. This is **not the same revision** as the source branch copied above. Never silently substitute or overwrite either set with the other.

For Windows FreeCAD installations using a folder such as `%APPDATA%/FreeCAD/v1-1/Mod/SMBS_BendSeq-2D`, the repository-relative resolver works without changing any source paths.
