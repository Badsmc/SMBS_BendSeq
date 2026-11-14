# PressBrakeSim — native FreeCAD icon assets

This folder contains the addon-specific 32 × 32 SVG artwork for FreeCAD's
standard toolbar, menus, viewer tabs and task controls. The functions and
command IDs are unchanged.

## Appearance

- All icons use a neutral, low-saturation grey (`#888888`).
- Icons have **transparent backgrounds**. The old colored, rounded tile is
  removed: FreeCAD/Qt supplies the button background, hover, pressed, disabled,
  and focus states.
- No local toolbar color, background, global style or fixed light/dark theme is
  installed by PressBrakeSim.
- The workbench toolbar uses FreeCAD's `QToolBar` with native text-beside-icon
  actions at 18 px. Task buttons use the native widget style.
- A neutral SVG is a compromise for both light and dark FreeCAD themes.
  Verify glyph visibility at the user's display scale; colors of 2.5D machine
  geometry are independent and are not modified.

## Main toolbar

| File | Command |
|---|---|
| `gui_part.svg` | Деталь |
| `gui_tooling.svg` | Оснастка |
| `gui_calculate.svg` | Расчёт |
| `gui_planar_candidates.svg` | 2D варианты |
| `gui_manual.svg` | Manual |
| `gui_settings.svg` | Настройки |

`gui_simulation.svg`, `gui_sequence_catalog.svg`,
`gui_backgauge.svg`, `gui_workbench.svg`, and `tooling.svg`
serve the viewer, secondary menus and workbench identity.

`control_*.svg` are neutral icons for playback and task actions. Existing
signal connections and command enablement are preserved.

## Implementation and checks

- `freecad_smbs/InitGui.py`: the FreeCAD workbench toolbar commands.
- `freecad_smbs/commands/pressbrake_2d.py`: task layout, playback and icon binding.
- `freecad_smbs/ui/pressbrake_theme.py`: no local color overrides;
  section headers and dimensions only.
- `.github/workflows/gui-icon-contract.yml`: Python compile, SVG validity,
  native-theme and command-structure contracts.

**Important:** the GitHub workflow does not render FreeCAD. Visually verify
in FreeCAD 1.1.3 on Windows 11, both dark and light appearance, including
TaskPanel scrolling, button text, Manual, tooling forms, and dock reopening.
