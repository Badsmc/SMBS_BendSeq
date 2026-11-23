# SMBS Minimal 24px icon theme

24 original hand-coded transparent SVG pictograms, supplied in *light* and
*dark* variants. The 24 symbols are taken from the user-approved
SMBS_Minimal_24px icon pack; no image-generation sprite or decorative
background is used.

The FreeCAD UI resolves each icon with freecad_smbs.icon_theme.icon_path().
Unprovided icons use the existing resources/icons files. The Qt application
palette determines which variant is selected when an icon path is requested.

Display targets: native workbench toolbar 24 px; task-panel button icons
24 px; views and result indicators 24 px. No action handlers or algorithms
are changed by the icon update.
