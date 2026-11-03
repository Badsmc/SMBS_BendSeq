"""SMBS 2D v5 viewer: source-shape validation added to v4 playback."""
from __future__ import annotations
import traceback
from freecad_smbs.commands import fold_sequence_viewer as v4
from freecad_smbs.integration.sheetmetal_unfold_reader import read_unfold
from freecad_smbs.integration.panel_builder import build_panels
from freecad_smbs.integration.direction_validator import validate_directions
from smbs_core.kinematics2d.panel_graph import graph_from_panel_build

_WINDOW=None

def show():
    import FreeCAD as App
    QtCore,QtGui,QtWidgets=v4._qt()
    window=v4.show()
    doc=App.ActiveDocument
    unfolds=[o for o in doc.Objects if o.Name.endswith("_Unfold") and hasattr(o,"baseObject")]
    if len(unfolds)!=1:
        raise RuntimeError("Expected exactly one SheetMetal unfold object")
    unfold=unfolds[0]
    data=read_unfold(unfold.Name,doc)
    source=doc.getObject(data.source_object)
    if source is None or source.Shape.isNull():
        raise RuntimeError("Source 3D shape not found: "+data.source_object)
    outline=v4._sketch_name(doc,"_Unfold_Sketch")
    bends=v4._sketch_name(doc,"_Unfold_Sketch_Bends")
    panel_build=build_panels(unfold_name=unfold.Name,outline_name=outline,
                             bends_name=bends,document=doc)
    graph=graph_from_panel_build(panel_build)
    window.setWindowTitle("SMBS 2D v5 | Source 3D direction verification")
    bar=QtWidgets.QHBoxLayout()
    validate_button=QtWidgets.QPushButton("Validate Directions")
    apply_button=QtWidgets.QPushButton("Apply Verified")
    apply_button.setEnabled(False)
    bar.addWidget(validate_button)
    bar.addWidget(apply_button)
    window.layout().insertLayout(2,bar)
    state={"result":None}
    def invalidate(*args):
        state["result"]=None
        apply_button.setEnabled(False)
    window.fixed.currentTextChanged.connect(invalidate)
    def validate():
        apply_button.setEnabled(False)
        state["result"]=None
        try:
            window.log.appendPlainText("Validating against source 3D shape: "+source.Name)
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            try:
                result=validate_directions(graph,window.fixed.currentText(),
                                           panel_build,source.Shape,source.Name)
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
            state["result"]=result
            window.log.appendPlainText(
                "3D fit: %s | RMS %.3f mm | next %.3f mm" %
                (result.status,result.rms_mm,result.runner_up_mm))
            for item in result.directions:
                window.log.appendPlainText(
                    "%s: SheetMetal %+.1f -> candidate %+.1f | %s" %
                    (item.bend_id,item.original_deg,item.suggested_deg,item.status))
            window.log.appendPlainText(result.message)
            apply_button.setEnabled(result.status=="VERIFIED")
        except Exception:
            window.log.appendPlainText(traceback.format_exc())
    def apply():
        result=state["result"]
        if result is None or result.status!="VERIFIED":
            return
        window.timer.stop()
        window.play_btn.setText("Play")
        for item in result.directions:
            window.controller.targets[item.bend_id]=item.suggested_deg
        window.draw()
        window.log.appendPlainText(
            "Verified signs applied to preview only. Source CAD unchanged.")
    validate_button.clicked.connect(validate)
    apply_button.clicked.connect(apply)
    global _WINDOW
    _WINDOW=window
    return window
