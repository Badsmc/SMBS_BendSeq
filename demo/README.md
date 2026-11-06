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

**Binary fixture status (working branch `feature/25d-physical-validation`):** 16 `.FCStd` models from `AutoMod-V0.0.1/SMBendTests` have been committed directly to `demo/parts/` with their exact source Git blobs. Their 22 associated JSON files and legacy `run_batch.py` are retained under `demo/SMBendTests/`; original file identities and Git blob hashes are documented in `demo/SMBendTests/IMPORT_INDEX.json`.

The 17-entry `demo/MANIFEST.json` describes a **different archive revision** (different file sizes and SHA-256 values). In particular, its `U-60deg-30deg-(ok)-variant2.FCStd` was **not present** in the source branch. The 17-file archive installer therefore remains a **separate optional workflow** and should not be used to overwrite or verify these 16 imported assets. The labels `(ok)`/`(false)` have not been independently validated.

## Layout and cross-platform support

`smbs_core.demo_paths.demo_part_path("L-(ok).FCStd")` resolves `demo/parts/L-(ok).FCStd` from the module's repository location, not the current working directory or the user's Desktop. The same code works on Windows, macOS and Linux. A missing fixture is an explicit error, never a generated stand-in.

`fixtures/freecad/load_fcstd.py` reads inputs from `demo/parts/` and produces *generated* fixture artifacts inside `fixtures/freecad/<fixture_slug>/`. Original sources remain unchanged.

Tool CAD assets remain in `toollibrary/` under their original paths; they are not duplicated into `demo/parts/` because the existing tooling library depends on them.

## Installing original binary demo models

For the historical 17-file revision, `tools/install_demo_models.py` expects `SMBS_demo_FCStd_17_files.zip` with file hashes matching `demo/MANIFEST.json`. This is **not the same revision** as the source branch copied above. Never silently substitute or overwrite either set with the other.

For Windows FreeCAD installations using a folder such as `%APPDATA%/FreeCAD/v1-1/Mod/SMBS_BendSeq-2D`, the repository-relative resolver works without changing any source paths.
