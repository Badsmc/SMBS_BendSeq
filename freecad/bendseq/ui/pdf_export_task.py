"""All PDF settings are hosted inside the existing native Tasks stack."""
from dataclasses import asdict
from pathlib import Path
import json
from time import perf_counter
from traceback import format_exc

from freecad.bendseq.core.kinematics2d.developer_debug_log import debug_event

from freecad.bendseq.ui.native_preferences import NativePreferences
from freecad.bendseq.ui.localization import tr, tr_message
from freecad.bendseq.ui.pdf_export_options import PdfOptions, LAYERS, CONTENT, current_collision_hits


def show_pdf_export_task(workspace, embed):
    from freecad.bendseq.commands.pressbrake_2d import _qt
    from freecad.bendseq.ui.bend_instructions_pdf import export_bend_instructions_pdf, suggested_pdf_path
    from freecad.bendseq.ui.bend_information import bend_rows
    core, gui, qt = _qt()
    prefs = NativePreferences(core)
    try:
        stored = prefs.value("pdf/options_v2","",type=str)
        saved = (PdfOptions(**json.loads(stored)) if stored else
                 PdfOptions(positions_per_page=max(1,min(10,prefs.value("pdf/positions_per_page",1,type=int))),
                            large_first=prefs.value("pdf/large_first",False,type=bool)))
    except (ValueError,TypeError):
        saved = PdfOptions()
    dialog = qt.QDialog(workspace)
    layout = qt.QVBoxLayout(dialog)
    path = qt.QLineEdit(suggested_pdf_path(workspace))
    if not Path(path.text()).is_absolute():
        path.setText(str(Path.home() / path.text()))
    path.setAccessibleName(tr("pdf_output_path"))
    layout.addWidget(qt.QLabel(tr("pdf_output_path")))
    layout.addWidget(path)
    browse = qt.QPushButton(tr("pdf_choose_file"))
    layout.addWidget(browse)
    def choose():
        filename,_ = qt.QFileDialog.getSaveFileName(workspace,tr("save_bend_instructions_as_pdf"),path.text(),"PDF (*.pdf)")
        if filename:
            path.setText(filename)
    browse.clicked.connect(choose)
    page_box = qt.QGroupBox(tr("page_layout"))
    form = qt.QFormLayout(page_box)
    count = qt.QSpinBox()
    count.setRange(1,10)
    large = qt.QCheckBox(tr("large_first_operation_remaining_operations_in_two_columns"))
    landscape = qt.QCheckBox(tr("pdf_landscape"))
    fit = qt.QCheckBox(tr("pdf_fit_machine"))
    form.addRow(tr("operations_per_page"),count)
    for widget in (large,landscape,fit):
        form.addRow(widget)
    right_view = qt.QComboBox()
    right_view.addItem("3D", "3d")
    right_view.addItem("2D — " + tr("cross_section"), "section")
    form.addRow(tr("pdf_content_part_inset"), right_view)
    layout.addWidget(page_box)
    layers_box = qt.QGroupBox(tr("pdf_scene_layers"))
    layers = qt.QGridLayout(layers_box)
    layers.addWidget(qt.QLabel(tr("pdf_visibility")),0,0)
    layers.addWidget(qt.QLabel(tr("pdf_transparency")),0,1)
    checks, alpha = {}, {}
    for i,key in enumerate(LAYERS,1):
        check = qt.QCheckBox(tr("pdf_layer_"+key))
        spin = qt.QSpinBox()
        spin.setRange(0,100)
        spin.setSuffix(" %")
        spin.setToolTip(tr("pdf_transparency_hint"))
        check.toggled.connect(spin.setEnabled)
        checks[key],alpha[key] = check,spin
        layers.addWidget(check,i,0)
        layers.addWidget(spin,i,1)
    punch_segments = qt.QCheckBox(tr("pdf_punch_segments"))
    die_segments = qt.QCheckBox(tr("pdf_die_segments"))
    layers.addWidget(punch_segments,len(LAYERS)+1,0,1,2)
    layers.addWidget(die_segments,len(LAYERS)+2,0,1,2)
    layout.addWidget(layers_box)
    grouped = qt.QCheckBox(tr("pdf_group_collisions"))
    layout.addWidget(grouped)
    content_box = qt.QGroupBox(tr("pdf_document_content"))
    content_layout = qt.QVBoxLayout(content_box)
    content = {}
    for key in CONTENT:
        check = qt.QCheckBox(tr("pdf_content_"+key))
        content[key] = check
        content_layout.addWidget(check)
    layout.addWidget(content_box)
    notice = qt.QLabel()
    notice.setWordWrap(True)
    layout.addWidget(notice)
    reset = qt.QPushButton(tr("reset"))
    export = qt.QPushButton(tr("pdf_generate"))
    layout.addWidget(reset)
    layout.addWidget(export)
    feedback = qt.QLabel()
    feedback.setWordWrap(True)
    layout.addWidget(feedback)

    def values():
        return PdfOptions(visible={k:w.isChecked() for k,w in checks.items()},
            transparency={k:w.value() for k,w in alpha.items()},
            content={k:w.isChecked() for k,w in content.items()},
            positions_per_page=count.value(),large_first=large.isChecked(),
            punch_segments=punch_segments.isChecked(),die_segments=die_segments.isChecked(),
            fit_machine=fit.isChecked(),landscape=landscape.isChecked(),group_collisions=grouped.isChecked(),
            right_view=right_view.currentData())

    def load(options):
        right_view.setCurrentIndex(right_view.findData(options.right_view))
        count.setValue(options.positions_per_page)
        large.setChecked(options.large_first)
        landscape.setChecked(options.landscape)
        grouped.setChecked(options.group_collisions)
        fit.setChecked(options.fit_machine)
        punch_segments.setChecked(options.punch_segments)
        die_segments.setChecked(options.die_segments)
        for k,w in checks.items():
            w.setChecked(options.visible[k])
            alpha[k].setValue(options.transparency[k])
            alpha[k].setEnabled(w.isChecked())
        for k,w in content.items():
            w.setChecked(options.content[k])

    def update_notice(*_):
        grouped.setEnabled(checks["collision"].isChecked())
        hits = current_collision_hits(workspace)
        notice.setText(tr("pdf_collision_notice",count=len(hits)))
        for k in ("punch","die"):
            (punch_segments if k=="punch" else die_segments).setEnabled(checks[k].isChecked())
        for k in ("flat_pattern","schedule"):
            content[k].setEnabled(content["overview"].isChecked())
        content["segment_text"].setEnabled(content["tool_details"].isChecked())
        content["part_inset"].setEnabled(checks["part"].isChecked() or right_view.currentData()=="section")

    def save(*_):
        if workspace.session is None or workspace._task_progress_busy or workspace.timer.isActive():
            feedback.setText(tr("mesh_load_a_part_and_stop_the_simulation"))
            return
        if not workspace._apply_typed_manual_order():
            return
        options = values()
        target = path.text().strip()
        if not target:
            feedback.setText(tr("pdf_output_path"))
            return
        destination = Path(target).expanduser()
        if destination.suffix.lower() != ".pdf":
            destination = destination.with_suffix(".pdf")
        if not destination.parent.is_dir():
            feedback.setText(tr("pdf_folder_missing"))
            return
        if destination.exists():
            # Keep overwrite confirmation within Tasks, no modal dialogs.
            if getattr(dialog,"_confirmed_path",None) != str(destination):
                dialog._confirmed_path = str(destination)
                feedback.setText(tr("pdf_confirm_overwrite"))
                debug_event("PDF_EXPORT_OVERWRITE_CONFIRMATION", path=str(destination))
                return
        dialog._confirmed_path = None
        if workspace.tooling_setup.punch is None or workspace.tooling_setup.die is None:
            feedback.setText(tr("select_a_punch_and_die_first"))
            return
        export.setEnabled(False)
        started = perf_counter()
        debug_event("PDF_EXPORT_STARTED", path=str(destination),
                    document=getattr(workspace, "_smbs_document_name", None),
                    options=asdict(options))
        try:
            prefs.setValue("pdf/options_v2",json.dumps(asdict(options)))
            export_bend_instructions_pdf(workspace,str(destination),core,gui,bend_rows(workspace),options=options)
            size = destination.stat().st_size
            if not size:
                raise OSError("PDF output is empty")
            with destination.open("rb") as stream:
                if stream.read(5) != b"%PDF-":
                    raise OSError("Output does not contain a PDF header")
        except Exception as exc:
            debug_event("PDF_EXPORT_FAILED", path=str(destination),
                        elapsed_seconds=perf_counter()-started,
                        error=str(exc), traceback=format_exc())
            feedback.setText(tr_message(exc))
            workspace.status.setText(tr_message(exc))
        else:
            debug_event("PDF_EXPORT_COMPLETED", path=str(destination),
                        elapsed_seconds=perf_counter()-started, size_bytes=size)
            feedback.setText(tr("bend_instructions_saved_as_pdf")+" · "+str(destination))
            workspace.status.setText(tr("bend_instructions_saved_as_pdf"))
        finally:
            export.setEnabled(True)
            update_notice()
    def changed(*_):
        dialog._confirmed_path = None
    path.textChanged.connect(changed)
    reset.clicked.connect(lambda: (load(PdfOptions()),update_notice()))
    for w in (*checks.values(),content["overview"],content["tool_details"]):
        w.toggled.connect(update_notice)
    def right_view_selected(*_):
        if right_view.currentData() == "section":
            content["part_inset"].setChecked(True)
            checks["collision"].setChecked(True)
            if alpha["collision"].value() == 100:
                alpha["collision"].setValue(30)
        update_notice()
    # activated is user-driven: loading stored options must not override them.
    right_view.activated.connect(right_view_selected)
    export.clicked.connect(save)
    load(saved)
    update_notice()
    embed(workspace,dialog,tr("save_bend_instructions_as_pdf"),"pdf_export")
