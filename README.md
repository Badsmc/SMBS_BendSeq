# SMBS BendSeq · PressBrakeSim 0.1.0

**SMBS BendSeq** is a FreeCAD workbench for planning press-brake operations. Start from a SheetMetal unfold or an ASCII DXF flat pattern, set the bend order manually or search for candidates with **FAST**, and preview the part and tooling in 3D. Use **Mesh 3D** to check the selected sequence for sampled tool collisions.

**Version 0.1.0 — initial public preview.** This version has not completed full FreeCAD acceptance or manufacturing validation.

[Requirements](#requirements) · [Installation](#installation) · [Quick start](#quick-start) · [Collision checks](#collision-checks) · [Tooling](#tooling)

## Requirements

| Component | What it is used for |
| --- | --- |
| **FreeCAD 1.1.x desktop** | Run the **PressBrakeSim** workbench and open or save `.FCStd` files. |
| **Pivy with Coin3D (`pivy.coin`)** | Render the 3D simulation. Use a FreeCAD build that provides Pivy in its own Python environment. |
| **SheetMetal Workbench (optional addon)** | Load a SheetMetal unfold. Install **SheetMetal** from the Addon Manager and use its **Unfold** command to prepare the part. The SMBS ASCII DXF workflow does not require SheetMetal. |
| **TechDraw workbench** | Optional. Required to create a TechDraw page from the bend table. |

To check Pivy, run this in **FreeCAD's Python console**:

```python
from pivy import coin
```

If the import fails, check whether your FreeCAD distribution provides a matching Pivy package or use a FreeCAD build that includes it. Pivy must be available to the Python runtime that FreeCAD uses.

For a tooling-based collision check, select both a punch and a die. Punch and die models are not distributed. Import your own compatible models and sidecars. The parametric editor is currently available in develop.

## Installation

### Addon Manager

Open **Tools → Addon Manager**. If SMBS BendSeq is available in your configured addon sources, select it and install it. Restart FreeCAD and choose **PressBrakeSim** from the workbench selector.

### Manual installation

1. Download the [ZIP for the `main` branch (0.1.0)](https://github.com/Badsmc/SMBS_BendSeq/archive/refs/heads/main.zip). For ongoing development, use the [`develop` branch](https://github.com/Badsmc/SMBS_BendSeq/tree/develop).
2. Find your user `Mod` directory by running this in FreeCAD's Python console:

   ```python
   from pathlib import Path
   import FreeCAD as App

   print(Path(App.getUserAppDataDir()) / "Mod")
   ```

3. Extract the archive into `Mod/SMBS_BendSeq/`. Keep `InitGui.py`, `freecad_smbs/`, `smbs_core/`, and `toollibrary/` directly inside that folder.
4. Restart FreeCAD and select **PressBrakeSim**. SMBS displays English or Russian according to the FreeCAD language setting.

## Quick start

1. **Load a flat pattern.** Select a SheetMetal unfold in the model tree and open **Simulation**. For an ASCII DXF, open **DXF flat pattern**, enable the relevant layers, assign angles to the bend lines, and click **Calculate**.
2. **Select tooling.** Choose the punch and die under **Tooling**. For segmented tools, use **Calculate tool layout** to propose a layout from the available stock and clamp positions.
3. **Set the bend order.** Select bend lines or reorder operations in **Simulation**. To search for candidates, run **FAST**, select a sequence, and review the part orientation at each bend.
4. **Check and document the sequence.** Run **Mesh 3D**. Open **Information** to review the bend table or export it to TechDraw.

Sample models are available in [`demo/parts/`](demo/parts); see the [demo index](demo/README.md) for names and historical collision examples.

## Collision checks

Open **Mesh 3D** in FreeCAD's **Tasks** panel. Set the time limit in seconds and the minimum penetration volume in mm³, then click **Run Mesh**. If the time limit is reached, review which operations were checked and which are still pending.

**Save latest collision…** exports the most recent confirmed collision for the current setup to a separate `.FCStd`. The intersecting volume is shown in red and the colliding bodies are translucent.

FAST proposes bend sequences. Mesh checks sampled positions of the part against the selected punch and die. Its result applies to the reported samples and tool geometry. Continuous motion, the backgauge, clamps and the machine frame are outside the scope of this check.

## Tooling

- **Segments.** Enter the available lengths and quantities for each punch or die. The tool's `.tooling.json` stores the inventory for reuse across parts.
- **Punch rotation.** Save a 180° rotation for a punch to use that orientation in simulation and checks.
- **Tool layout.** Configure the clamps, calculate the sections for the selected bends, and apply a common layout if needed. Recalculate after changing the available stock or clamps.

## Saving and troubleshooting

For a session linked to a SheetMetal unfold, saving the document in FreeCAD with **Ctrl+S** embeds SMBS session data in the `.FCStd`. Keep the external tooling files available when moving the project to another installation. See [FCStd save and restore](docs/FCSTD_NATIVE_AUTOSAVE.md) for details.

To investigate an error, enable **Log SMBS activity and FreeCAD crashes** in SMBS settings and reproduce the problem. The desktop log is named `SMBS_debug_log.json`. Include the FreeCAD version, the SMBS version and commit, and the steps to reproduce the issue when reporting it. [Report an issue](https://github.com/Badsmc/SMBS_BendSeq/issues).

See the [tooling library notes](toollibrary/README.md) for local tooling setup.

## Development and licensing

`main` is the public release branch and `develop` is the active development branch. Both start from version 0.1.0. See [branch architecture](BRANCH_ARCHITECTURE.md) and [changelog](CHANGELOG.md). Historical experiment code is preserved in rewritten Git archive tags. Bundled punch/die assets and historical diagnostic data were removed from published branch and tag histories; demo sheet-metal parts remain under `demo/`. Existing Git clones must be replaced with fresh clones to avoid reintroducing old history.

Original source code is licensed under [LGPL-2.1-or-later](LICENSE-Code) and original non-code resources under [CC-BY-SA-4.0](LICENSE-Assets). See [licensing and attribution](LICENSE.md). Third-party CAD redistribution rights still require confirmation; official Addon Manager listing and full runtime acceptance remain pending.

## Dependencies and privacy

Use FreeCAD's own Python environment. Part, the FreeCAD-provided Qt wrapper and Pivy/Coin3D are host components, not packages to install separately with pip. SheetMetal Workbench is optional for DXF use, required for SheetMetal unfolds; TechDraw is optional for drawing export. The addon does not install Python packages itself. PythonOCC is a legacy optional offline geometry reader, not needed for the native FreeCAD workflow. Named tooling sets use standard-library validation. See [submission preparation](docs/ADDON_INDEX_SUBMISSION.md) and [privacy](PRIVACY.md).

To test through Addon Manager before official indexing, add `https://github.com/Badsmc/SMBS_BendSeq` with branch `main` as a custom repository in Addon Manager preferences. Remove other SMBS installations first. Restart FreeCAD after installation.
