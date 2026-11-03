# SMBS 2D v4 — cumulative folding preview

Branch: `2D`. Status: **implemented, pending live FreeCAD verification**.

## What is new

- `smbs_core/kinematics2d/fold_sequence.py`: stateless seekable sequence controller; previous bends remain applied. Rebuilds from the flat state for each requested timeline position.
- `freecad_smbs/commands/fold_sequence_viewer.py`: independent Qt window with Play/Pause, Reset/End, slider, fixed-panel selector, Fit, and ISO/XY/XZ/YZ projections.
- `tests/test_fold_sequence.py`: regression tests for cumulative angles, seeking, negative angles and invalid orders.

The default order follows the panel graph rooted at the selected fixed panel. It is **not** a verified press-brake sequence. The 3D rigid rotations are projected to 2D. This does not model material thickness, bend radius, collision, springback, or tooling.

## Run in FreeCAD

1. Update the local `SMBS_BendSeq` checkout from branch `2D`. Ensure the checkout root is on FreeCAD's Python path.
2. Open the saved 2134 model, with one SheetMetal unfold and its generated outline and bend sketches.
3. Paste this **single line** into FreeCAD's Python Console:

```python
from freecad_smbs.commands.fold_sequence_viewer import show; w = show()
```

4. Press **Play**: all 10 bends should be applied cumulatively. Select ISO, XZ and YZ to inspect the pose. **End** should display all 10 bends; **Reset** should show the flat state.
5. Save the console traceback if loading fails. Do not treat a visually plausible pose as physically validated.

## Known limitations

- Requires one uniquely named unfold and matching sketches in the open document.
- Uses conservative PanelBuilder; unsupported outlines or axis mismatches are rejected.
- Panels are separated by thin numerical cutting strips. The visual preview has artificial narrow gaps at hinges.
- Direction/sign is taken from SheetMetal angle and directed bend axis. Its physical orientation relative to the actual formed part has not been independently verified.
- Sequence order, collisions, thickness and machine constraints are not validated.
- This is a new isolated viewer; it does not replace the older demo UI or macro.
