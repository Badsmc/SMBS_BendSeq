# SMBS BendSeq demo models

This directory is the canonical in-repository home for **demo sheet-metal FCStd parts** used by native D6.3 diagnostics and regression runs.

## Structure

```text
demo/
  README.md
  parts/
    L-(ok).FCStd
    Z-(ok).FCStd
    135-(ok).FCStd
```

The three part files listed above are **required but not yet present in GitHub**. They must be copied from the original FreeCAD models and committed as binary files, without generating synthetic substitutes or changing their geometry. Tests requiring the parts must report a missing-input error until this is done.

## Existing CAD tooling

Machine and tooling FCStd assets remain in the existing production `toollibrary/` paths, including:
- `toollibrary/punch/puancon.FCStd`
- `toollibrary/die/matrica.FCStd`
- `toollibrary/Backgauge/Backgauge.FCStd`
- `toollibrary/Clamp/Clamp.FCStd`
- `toollibrary/machine_frame/machine_frame.FCStd`
- `toollibrary/presses/cncbender.FCStd`

Those files must **not be duplicated or silently relocated**: the existing tooling library resolves these locations and moving them without a coordinated migration would break working software. The purpose of `demo/parts/` is to version-control reference *workpieces* alongside the source.

## Cross-platform behavior

`smbs_core.demo_paths.find_demo_part(filename)` resolves model names relative to the installed repository. There are no macOS-specific absolute paths. The same layout works on Windows, Linux and macOS.

When you have obtained the three original FCStd parts:
1. Place them in `demo/parts/`.
2. Use `git add demo/parts/*.FCStd` and commit/push.
3. Run `tools/run_d634_adaptive_motion_collision.py` using native FreeCAD Python.
4. Run the graphical `tools/freecad_gui_d633_diagnostic.py` in FreeCAD GUI.

Do not claim native FCStd fixture acceptance until these files have been committed and executed.
