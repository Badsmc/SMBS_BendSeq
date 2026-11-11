# PressBrakeSim GUI icon system — v0.0.1

Custom engineering-style SVG icons for the FreeCAD **PressBrakeSim 2.5D** interface.

All icons are **32 × 32**, include their own dark rounded tile for contrast in
light and dark FreeCAD themes, and use vector-only shapes. No external fonts,
images, links or third-party artwork are needed.

## Main toolbar and workbench

| Preview | File | Meaning |
|---|---|---|
| ![Workbench](gui_workbench.svg) | `gui_workbench.svg` | Press-brake workbench identity |
| ![Simulation](gui_simulation.svg) | `gui_simulation.svg` | Open bending simulation |
| ![Part](gui_part.svg) | `gui_part.svg` | Load selected SheetMetal part |
| ![Tooling](gui_tooling.svg) | `gui_tooling.svg` | Punch and die setup |
| ![Calculation](gui_calculate.svg) | `gui_calculate.svg` | Full sequence calculation |
| ![2D variants](gui_planar_candidates.svg) | `gui_planar_candidates.svg` | Fast 2D candidate search |
| ![Catalog](gui_sequence_catalog.svg) | `gui_sequence_catalog.svg` | Saved sequence catalog |
| ![Backgauge](gui_backgauge.svg) | `gui_backgauge.svg` | Real rear stop and contact position |
| ![Tool library](tooling.svg) | `tooling.svg` | Tooling library and model assets |

## Playback and diagnostics controls

| Preview | File | Meaning |
|---|---|---|
| ![Play](control_play.svg) | `control_play.svg` | Play / Resume |
| ![Pause](control_pause.svg) | `control_pause.svg` | Pause |
| ![Reset](control_reset.svg) | `control_reset.svg` | Reset or restore order |
| ![Fit](control_fit.svg) | `control_fit.svg` | Fit entire part |
| ![Previous](control_prev.svg) | `control_prev.svg` | Previous bend |
| ![Next](control_next.svg) | `control_next.svg` | Next bend |
| ![Record](control_record.svg) | `control_record.svg` | Start JSONL recording |
| ![Stop](control_stop.svg) | `control_stop.svg` | Stop recording |
| ![Folder](control_folder.svg) | `control_folder.svg` | Open or select output folder |
| ![Manual](control_manual.svg) | `control_manual.svg` | Manually select bend order |
| ![Validate](control_validate.svg) | `control_validate.svg` | Validate and export |

## Design tokens

- Background: graphite navy `#233B4D` with desaturated border `#56788A`.
- Primary: silver blue `#E7F3F7` for press structure, geometry and lists.
- Active geometry: aqua `#55CFC0`, e.g., sheet metal and motion.
- Mechanical/action accent: warm amber `#FFC272`.
- Recording state: coral red `#F37377`.

Buttons retain their original labels, keyboard behavior, signals and enabled
states; the SVG icons are informative visual identifiers only. Disabled
appearance is handled by Qt. The Pause/Resume icon updates with playback state.

The files are used by `freecad_smbs/InitGui.py`,
`freecad_smbs/commands/pressbrake_2d.py` and
`freecad_smbs/commands/tooling.py`.

## Verification

`.github/workflows/gui-icon-contract.yml` checks FreeCAD command resource
paths, SVG XML validity and runtime import contracts. **Real FreeCAD 1.1
light/dark toolbar rendering and icon scaling must still be checked locally.**

This icon work is isolated to the `GUI/v0.0.1` branch; `handmode` and
the search algorithms are unchanged.
