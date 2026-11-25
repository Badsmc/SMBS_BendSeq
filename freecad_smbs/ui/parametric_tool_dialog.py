"""Native Qt dialog for independent generic tool creation."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from freecad_smbs.ui.localization import tr
from smbs_core.kinematics2d.parametric_tool import ToolRecipe, starter_recipes
from freecad_smbs.integration.parametric_tool_native import create_tool


def show_generator(parent, library_root, kind):
    from freecad_smbs.commands.pressbrake_2d import _qt
    _, _, qt = _qt()
    dialog = qt.QDialog(parent)
    dialog.setWindowTitle(tr("parametric_tool_generator"))
    layout = qt.QVBoxLayout(dialog)
    note = qt.QLabel(tr("parametric_tool_notice"))
    note.setWordWrap(True)
    layout.addWidget(note)
    presets = qt.QComboBox()
    recipes = [recipe for recipe in starter_recipes() if recipe.kind == kind]
    for recipe in recipes:
        presets.addItem(recipe.tool_id, recipe)
    layout.addWidget(presets)
    form = qt.QFormLayout()
    layout.addLayout(form)
    name = qt.QLineEdit()
    form.addRow(tr("parametric_tool_id"), name)
    fields = {}
    dimensions = [
        ("height","parametric_height",1,5000),
        ("width","parametric_width",1,5000),
        ("angle","parametric_angle",15,120),
        ("radius","parametric_radius",0.1,100),
        ("length","parametric_length",1,5000),
    ]
    if kind == "punch":
        dimensions += [("shank_width","parametric_shank_width",1,5000),
                       ("shank_height","parametric_shank_height",1,5000),
                       ("relief_depth","parametric_relief_depth",0,5000),
                       ("relief_height","parametric_relief_height",1,5000)]
    else:
        dimensions += [("opening","parametric_opening",1,5000)]
    for key,label,lo,hi in dimensions:
        field = qt.QDoubleSpinBox()
        field.setDecimals(3)
        field.setRange(lo,hi)
        field.setSuffix("°" if key == "angle" else " mm")
        fields[key] = field
        form.addRow(tr(label), field)
    quantity = qt.QSpinBox()
    quantity.setRange(0,10000)
    form.addRow(tr("parametric_quantity"), quantity)
    def load_preset(*_):
        recipe = presets.currentData()
        name.setText(recipe.tool_id)
        for key, field in fields.items():
            field.setValue(getattr(recipe,key))
        quantity.setValue(recipe.quantity)
    presets.currentIndexChanged.connect(load_preset)
    load_preset()
    buttons = qt.QDialogButtonBox(qt.QDialogButtonBox.Ok | qt.QDialogButtonBox.Cancel)
    layout.addWidget(buttons)
    buttons.rejected.connect(dialog.reject)
    created = []
    def generate():
        recipe = replace(presets.currentData(), tool_id=name.text().strip(),
                         quantity=quantity.value(), **{k:v.value() for k,v in fields.items()})
        try:
            created.append(create_tool(recipe, library_root))
        except Exception as exc:
            qt.QMessageBox.warning(dialog, tr("parametric_tool_generator"), str(exc))
            return
        dialog.accept()
    buttons.accepted.connect(generate)
    run = getattr(dialog, "exec", None) or dialog.exec_
    run()
    dialog.deleteLater()
    return created[0] if created else None
