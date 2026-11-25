"""Native Qt live drawing editor for independent generic tool creation."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from freecad_smbs.ui.localization import tr
from freecad_smbs.ui.parametric_tool_preview import make_preview
from smbs_core.kinematics2d.parametric_tool import starter_recipes, dxf_text
from smbs_core.kinematics2d.parametric_tool_drawing import drawing_model
from freecad_smbs.integration.parametric_tool_native import create_tool


def show_generator(parent, library_root, kind):
    from freecad_smbs.commands.pressbrake_2d import _qt
    core, gui, qt = _qt()
    dialog = qt.QDialog(parent)
    dialog.setWindowTitle(tr("parametric_create_punch" if kind=="punch" else "parametric_create_die"))
    dialog.setWindowFlags(dialog.windowFlags() | core.Qt.WindowMaximizeButtonHint)
    screen = qt.QApplication.primaryScreen()
    bounds = screen.availableGeometry() if screen else None
    dialog.resize(min(1000,bounds.width()-60) if bounds else 1000,
                  min(720,bounds.height()-80) if bounds else 720)
    layout = qt.QVBoxLayout(dialog)
    note = qt.QLabel(tr("parametric_tool_notice"))
    note.setWordWrap(True)
    layout.addWidget(note)
    splitter = qt.QSplitter(core.Qt.Horizontal)
    layout.addWidget(splitter,1)
    scroll = qt.QScrollArea()
    scroll.setWidgetResizable(True)
    controls = qt.QWidget()
    left = qt.QVBoxLayout(controls)
    scroll.setWidget(controls)
    splitter.addWidget(scroll)
    presets = qt.QComboBox()
    recipes = [recipe for recipe in starter_recipes() if recipe.kind == kind]
    for recipe in recipes:
        presets.addItem(recipe.tool_id, recipe)
    left.addWidget(presets)
    reset = qt.QPushButton(tr("reset"))
    left.addWidget(reset)
    form = qt.QFormLayout()
    left.addLayout(form)
    name = qt.QLineEdit()
    form.addRow(tr("parametric_tool_id"), name)
    fields = {}
    dimensions = [
        ("height", "H2" if kind=="punch" else "H", "parametric_height",1,5000),
        ("angle","α","parametric_angle",15,120),
        ("radius","R","parametric_radius",0.1,100),
    ]
    if kind == "punch":
        dimensions += [("working_height","H1","parametric_working_height",1,5000),
                       ("width","L6","parametric_width",1,5000),
                       ("shank_width","L5","parametric_shank_width",1,5000),
                       ("relief_start","L2","parametric_relief_start",0,5000),
                       ("relief_height","L3","parametric_relief_height",1,5000),
                       ("relief_transition","L4","parametric_relief_transition",0.1,5000),
                       ("relief_depth","L7","parametric_relief_depth",0,5000)]
    else:
        dimensions += [("opening","V","parametric_opening",1,5000),
                       ("left_width","L1","parametric_left_width",0.1,2500),
                       ("right_width","L2","parametric_right_width",0.1,2500),
                       ("base_left","L3","parametric_base_left",0,2500),
                       ("base_right","L4","parametric_base_right",0,2500),
                       ("base_height","L5","parametric_base_height",0,5000),
                       ("base_right_height","L6","parametric_right_base_height",0,5000)]
    dimensions += [("length","L","parametric_length",1,5000)]
    for key,code,label,lo,hi in dimensions:
        field = qt.QDoubleSpinBox()
        field.setDecimals(3)
        field.setRange(lo,hi)
        field.setSuffix("°" if key == "angle" else " mm")
        field.setKeyboardTracking(True)
        fields[key] = field
        form.addRow(f"{code} · {tr(label)}", field)
    mirror = qt.QCheckBox(tr("parametric_mirror_relief"))
    if kind == "punch":
        form.addRow(mirror)
    derived = qt.QLabel()
    derived.setWordWrap(True)
    form.addRow(derived)
    quantity = qt.QSpinBox()
    quantity.setRange(0,10000)
    form.addRow(tr("parametric_quantity"), quantity)
    left.addStretch(1)
    right_panel = qt.QWidget()
    right = qt.QVBoxLayout(right_panel)
    right.addWidget(qt.QLabel(tr("parametric_live_drawing")))
    preview = make_preview(core, gui, qt, right_panel)
    right.addWidget(preview,1)
    details = qt.QLabel()
    details.setWordWrap(True)
    right.addWidget(details)
    splitter.addWidget(right_panel)
    splitter.setStretchFactor(0,0)
    splitter.setStretchFactor(1,1)
    splitter.setSizes([360,640])
    status = qt.QLabel()
    status.setWordWrap(True)
    layout.addWidget(status)
    buttons = qt.QDialogButtonBox(qt.QDialogButtonBox.Ok | qt.QDialogButtonBox.Cancel)
    buttons.button(qt.QDialogButtonBox.Ok).setText(tr("parametric_save_new"))
    export = buttons.addButton(tr("parametric_export_dxf"), qt.QDialogButtonBox.ActionRole)
    layout.addWidget(buttons)
    buttons.rejected.connect(dialog.reject)
    created = []
    loading = False
    active_key = ""

    def current_recipe():
        values = {k:v.value() for k,v in fields.items()}
        if kind == "punch":
            values["shank_height"] = values["height"]-values.pop("working_height")
            values["relief_side"] = "left" if mirror.isChecked() else "right"
        else:
            l,r = values.pop("left_width"),values.pop("right_width")
            bl,br = values.pop("base_left"),values.pop("base_right")
            values.update(width=l+r,body_offset=(r-l)/2,base_width=bl+br,base_offset=(br-bl)/2)
        return replace(presets.currentData(), tool_id=name.text().strip(),
                       quantity=quantity.value(), **values)

    def refresh(key=""):
        nonlocal active_key
        if loading:
            return
        if key:
            active_key = key
        recipe = current_recipe()
        if kind == "punch":
            derived.setText(f"H2-H1 = {recipe.shank_height:g} mm")
        else:
            derived.setText(f"W = {recipe.width:g} mm  ·  B = {recipe.base_width:g} mm")
        details.setText(tr("parametric_preview_stock",length=f"{recipe.length:g}",quantity=recipe.quantity))
        try:
            model = drawing_model(recipe)
        except (ValueError, RuntimeError) as exc:
            preview.set_model(None)
            status.setText(tr("parametric_invalid_profile",error=str(exc)))
            buttons.button(qt.QDialogButtonBox.Ok).setEnabled(False)
            export.setEnabled(False)
        else:
            preview.set_model(model, active_key)
            status.setText(tr("parametric_valid_profile"))
            buttons.button(qt.QDialogButtonBox.Ok).setEnabled(True)
            export.setEnabled(True)

    class FieldFocus(core.QObject):
        def eventFilter(self, obj, event):
            if event.type() == core.QEvent.FocusIn:
                for key, field in fields.items():
                    if obj is field or obj is field.lineEdit():
                        refresh(key)
                        break
            return False

    focus = FieldFocus(dialog)
    for key, field in fields.items():
        field.valueChanged.connect(lambda value,k=key: refresh(k))
        field.installEventFilter(focus)
        field.lineEdit().installEventFilter(focus)
    quantity.valueChanged.connect(lambda *_: refresh())
    mirror.toggled.connect(lambda *_: refresh("relief_depth"))
    name.textChanged.connect(lambda *_: refresh())

    def load_preset(*_):
        nonlocal loading, active_key
        loading = True
        try:
            recipe = presets.currentData()
            name.setText(recipe.tool_id)
            special = dict(working_height=recipe.height-recipe.shank_height,
                           left_width=recipe.width/2-recipe.body_offset,
                           right_width=recipe.width/2+recipe.body_offset,
                           base_left=recipe.base_width/2-recipe.base_offset,
                           base_right=recipe.base_width/2+recipe.base_offset,
                           base_right_height=recipe.base_height if recipe.base_right_height is None else recipe.base_right_height)
            for key, field in fields.items():
                field.setValue(special[key] if key in special else getattr(recipe,key))
            mirror.setChecked(recipe.relief_side=="left")
            quantity.setValue(recipe.quantity)
            active_key = ""
        finally:
            loading = False
        refresh()

    presets.currentIndexChanged.connect(load_preset)
    reset.clicked.connect(load_preset)
    load_preset()

    def generate():
        try:
            recipe = current_recipe()
            drawing_model(recipe)
            created.append(create_tool(recipe, library_root))
        except Exception as exc:
            qt.QMessageBox.warning(dialog,tr("parametric_tool_generator"),str(exc))
            return
        dialog.accept()

    def export_dxf():
        try:
            recipe = current_recipe()
            contour,_ = drawing_model(recipe)
            filename,_ = qt.QFileDialog.getSaveFileName(dialog,tr("parametric_export_dxf"),
                                                         recipe.tool_id+".dxf","DXF (*.dxf)")
            if not filename:
                return
            path = Path(filename)
            if path.suffix.lower() != ".dxf":
                path = path.with_suffix(".dxf")
            with path.open("x",encoding="ascii") as output:
                output.write(dxf_text(contour))
        except Exception as exc:
            qt.QMessageBox.warning(dialog,tr("parametric_export_dxf"),str(exc))

    export.clicked.connect(export_dxf)
    buttons.accepted.connect(generate)
    run = getattr(dialog,"exec",None) or dialog.exec_
    run()
    dialog.deleteLater()
    return created[0] if created else None
