# BendSeq — editable SVG action icons

The approved 24px transparent icon catalog is installed in this directory.
The workbench logo uses a 64px SVG with a 32×32 viewBox.

- `minimal24/light/btn_*.svg`: independent action assets for light FreeCAD themes.
- `minimal24/dark/btn_*.svg`: corresponding dark-theme variants.
- `minimal24/{light,dark}/status_{fast,mesh}_{running,done,warning,error}.svg`:
  separately replaceable state symbols, with distinct semantic colors.
- `gui_workbench.svg`: FreeCAD workbench and Addon Manager logo.
- `preferences-bendseq.svg`: preferences icon.
- Existing control and gui filenames are retained for compatibility.

To replace an icon, close FreeCAD, overwrite its SVG file with the same name
in the active theme folder (or both theme folders), then restart FreeCAD.
The icon lookup in `freecad/bendseq/icon_theme.py` uses the Qt palette.
Qt and FreeCAD still own button backgrounds, sizing and system dialogs.

Actions are bound explicitly in the command and task source. For example:

| Action | SVG filename |
| --- | --- |
| DXF flat pattern | btn_dxf_flat_pattern.svg |
| Simulation | btn_simulation.svg |
| FAST | btn_fast.svg |
| PDF | btn_bend_plan_pdf.svg |
| Mesh | btn_mesh.svg |
| Play / pause / resume | btn_play.svg / btn_pause.svg / btn_resume.svg |
| Import punch / die from simulation | btn_simulation_punch_import.svg / btn_simulation_die_import.svg |
| Import punch / die in library | btn_library_punch_import.svg / btn_library_die_import.svg |
| Create punch / die | btn_create_punch.svg / btn_create_die.svg |
| PDF export | btn_pdf_generate.svg |

Generic repeated dialog actions intentionally reuse `btn_dialog_cancel.svg`
or `btn_apply.svg`. Native FreeCAD task close controls, Qt-provided file dialogs
and other application-owned controls retain their system appearance.

Replacing installed icons is local; reinstalling/updating the addon may
overwrite the installed assets. Keep a backup of custom SVGs.

The automated tests verify resource presence, SVG structure, theme resolution
and independent bindings. Real FreeCAD rendering is a separate manual check.
