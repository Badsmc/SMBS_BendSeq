# SMBS BendSeq / PressBrakeSim

FreeCAD workbench for planning and simulating press-brake bends from a SheetMetal unfold or an ASCII DXF flat pattern.

## Installation

Use FreeCAD 1.1.x with `pivy.coin`. Install the SheetMetal workbench if you load SheetMetal unfolds.

Extract the release into a folder such as `SMBS_BendSeq` in FreeCAD's user `Mod` directory. The folder must contain `InitGui.py` and the `freecad_smbs`, `smbs_core`, and `toollibrary` directories. Restart FreeCAD and select **PressBrakeSim**.

## Workflow

1. Select a SheetMetal unfold and load it in **Simulation**, or open **DXF flat pattern**. For DXF, enable the relevant layers, assign angles to the bend lines, and click **Calculate**.
2. Select a punch and die in **Tooling**. Use **Tool layout** to calculate a segment layout using the available stock and configured clamps.
3. Use **Simulation** to set or review the bend order and preview the operations. Use **FAST** to search for candidate sequences. A FAST result is a candidate for further verification.
4. Open **Mesh 3D** to set the time limit and minimum penetration volume in FreeCAD's **Tasks** panel. Run the check and review the result. **Save latest collision…** writes the last confirmed overlap for the current setup to a separate FCStd file.
5. Open **Information** for the bend table and TechDraw export. Use **Settings** to manage saved FCStd data and enable diagnostic logging when needed.

Mesh checks sampled positions against the selected punch and die. A clear result covers the reported samples and tools.

## Tooling library

Select a punch or die and use **Segments** to enter lengths and available quantities. SMBS stores this stock with the selected tool in its `.tooling.json` and reuses it across projects. The punch rotation option is also stored with the selected tool.

Configure clamp dimensions and positions in the tooling library. In the layout report, inspect the sections for each bend or apply a common layout across the selected bends. If you change stock or clamps, calculate the layout again.

Example FreeCAD parts are available in [demo/parts](demo/parts). See [FCStd save and restore](docs/FCSTD_NATIVE_AUTOSAVE.md) for how workbench data is stored with the document.
