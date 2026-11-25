"""Native Qt live drawing editor for independent generic tool creation."""
from __future__ import annotations

from dataclasses import replace

from freecad_smbs.ui.localization import tr
from freecad_smbs.ui.parametric_tool_preview import make_preview
from smbs_core.kinematics2d.parametric_tool import starter_recipes
from smbs_core.kinematics2d.parametric_tool_drawing import drawing_model
from freecad_smbs.integration.parametric_tool_native import create_tool


def show_generator(parent, library_root, kind):
    from freecad_smbs.commands.pressbrake_2d import _qt
    core, gui, qt = _qt()
    dialog = qt.QDialog(parent)
    dialog.setWindowTitle(tr("parametric_tool_generator"))
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
    form = qt.QFormLayout()
    left.addLayout(form)
    name = qt.QLineEdit()
    form.addRow(tr("parametric_tool_id"), name)
    fields = {}
    dimensions = [
        ("height", "H2" if kind=="punch" else "H", "parametric_height",1,5000),
        ("width","W","parametric_width",1,5000),
        ("angle","α","parametric_angle",15,120),
        ("radius","R","parametric_radius",0.1,100),
    ]
    if kind == "punch":
        dimensions += [("shank_width","S","parametric_shank_width",1,5000),
                       ("shank_height","H2-H1","parametric_shank_height",1,5000),
                       ("relief_depth","D","parametric_relief_depth",0,5000),
                       ("relief_height","C","parametric_relief_height",1,5000)]
    else:
        dimensions += [("opening","V","parametric_opening",1,5000),
                       ("base_width","B","parametric_base_width",0,5000),
                       ("base_height","T","parametric_base_height",0,5000)]
    dimensions += [("length","L","parametric_length",1,5000)]
    for key,code,label,lo,hi in dimensions:
        field = qt.QDoubleSpinBox()
        field.setDecimals(3)
        field.setRange(lo,hi)
        field.setSuffix("°" if key == "angle" else " mm")
        field.setKeyboardTracking(True)
        fields[key] = field
        form.addRow(f"{code} · {tr(label)}", field)
    working_height = qt.QLabel()
    if kind == "punch":
        form.addRow("H1 · "+tr("parametric_working_height"), working_height)
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
    buttons.button(qt.QDialogButtonBox.Ok).setText(tr("parametric_tool_generator"))
    layout.addWidget(buttons)
    buttons.rejected.connect(dialog.reject)
    created = []
    loading = False
    active_key = ""

    def current_recipe():
        return replace(presets.currentData(), tool_id=name.text().strip(),
                       quantity=quantity.value(), **{k:v.value() for k,v in fields.items()})

    def refresh(key=""):
        nonlocal active_key
        if loading:
            return
        if key:
            active_key = key
        recipe = current_recipe()
        if kind == "punch":
            working_height.setText(f"{recipe.height-recipe.shank_height:g} mm")
        details.setText(tr("parametric_preview_stock",length=f"{recipe.length:g}",quantity=recipe.quantity))
        try:
            model = drawing_model(recipe)
        except (ValueError, RuntimeError) as exc:
            preview.set_model(None)
            status.setText(tr("parametric_invalid_profile",error=str(exc)))
            buttons.button(qt.QDialogButtonBox.Ok).setEnabled(False)
        else:
            preview.set_model(model, active_key)
            status.setText(tr("parametric_valid_profile"))
            buttons.button(qt.QDialogButtonBox.Ok).setEnabled(True)

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
    name.textChanged.connect(lambda *_: refresh())

    def load_preset(*_):
        nonlocal loading, active_key
        loading = True
        try:
            recipe = presets.currentData()
            name.setText(recipe.tool_id)
            for key, field in fields.items():
                field.setValue(getattr(recipe,key))
            quantity.setValue(recipe.quantity)
            active_key = ""
        finally:
            loading = False
        refresh()

    presets.currentIndexChanged.connect(load_preset)
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

    buttons.accepted.connect(generate)
    run = getattr(dialog,"exec",None) or dialog.exec_
    run()
    dialog.deleteLater()
    return created[0] if created else None
