# Demo — FreeCAD SheetMetal workpieces

The clean `handmode` workbench includes **all 16 original active FreeCAD
`.FCStd` demo models** available in the Cascade source branch, without
modifying the CAD binaries.

## Directory

```text
demo/
  MANIFEST.json
  README.md
  parts/
    135-(ok).FCStd
    2-1-3-4-(ok).FCStd
    L-(ok).FCStd
    L-30-6-(false).FCStd
    SQin.200-25-10-(ok).FCStd
    SQout.200-25-10-(ok).FCStd
    U-10-10-20-(false).FCStd
    U-10-15-10-(punch2-OK).FCStd
    U-10-30-10-(OK).FCStd
    U-60deg-30deg-(ok).FCStd
    U-90deg-90deg-(ok).FCStd
    Z-(ok).FCStd
    Z-10-20-10-(false).FCStd
    Z-10-20-10-(ok).FCStd
    box.100in-50out-(false).FCStd
    box.100out-50in-(ok).FCStd
```

Open these files normally in FreeCAD, then use the SMBS workbench to
inspect unfold, bend order and preview results. File names containing
`(ok)` and `(false)` are historical labels, **not** independently
verified manufacturing or collision results.

`MANIFEST.json` records the authoritative 16 filenames, sizes and
SHA-256 hashes. The `handmode` CI verifies that every file in the
manifest exists unchanged and no unexpected `.FCStd` was added.

**Historical archive note:** Older documentation mentions a 17th model,
`U-60deg-30deg-(ok)-variant2.FCStd`. That file is **not present** in the
source Cascade branch and is not claimed to be included. The separate
17-file archive is not reconstructed by this release.

The original `demo/SMBendTests/` batch JSON tests and test runners are
intentionally excluded from `handmode`, which is an install-only branch.
The workpiece models themselves are all preserved.
