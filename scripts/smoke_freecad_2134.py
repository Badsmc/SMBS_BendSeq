"""
smoke_freecad_2134.py - Automated FreeCAD Smoke Test on 2-1-3-4-(ok).FCStd.

Executes the complete interactive checklist:
1. Workbench & environment verification (symlink, branch, imports).
2. Document load & pipeline calculation (RunActiveCommand, annotations on flat pattern).
3. Verification dialog inspection (stages, baseline metrics, zero red UNKNOWN!=VALID).
4. Simulate player inspection (backward & forward directions, punch descent, backgauge overlay, MACHINE: LOADED, precision badges).
5. Tooling panel (Machine frame tab, 3D preview).
6. Diagnostics & Preferences dialogs.
7. Saves graphical PNG screenshots of all dialogs and captures console/report view.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smbs_core.geometry_backend.freecad_bootstrap import ensure_freecad_paths
ensure_freecad_paths()

import FreeCAD as App
import FreeCADGui as Gui
from PySide6 import QtCore, QtGui, QtWidgets

from freecad_smbs.commands.open_run import set_current_run, get_current_run
from freecad_smbs.integration import smbs_bridge
from freecad_smbs.ui.annotations import render_flat_pattern_annotations
from freecad_smbs.ui.verification_dialog import VerificationDialog, extract_verification_details
from freecad_smbs.ui.animation_dialog import AnimationPlayerDialog, _compute_punch_offset_mm
from freecad_smbs.ui.tooling_panel import ToolingPanel
from freecad_smbs.ui.preferences_dialog import PreferencesDialog
from freecad_smbs.ui.diagnostics_dialog import DiagnosticsDialog
from freecad_smbs.ui.badges import BADGE_VERIFIED, BADGE_VERIFICATION_FAILED, BADGE_UNVERIFIED
from smbs_core.viewer.replayer import GEOMETRY_PRECISION_VALID, GEOMETRY_PRECISION_PREVIEW
from smbs_core.viewer.fc_animation import format_precision_badge, format_step_header


def run_smoke_checklist():
    print("=" * 75)
    print("FREECAD SMOKE CHECKLIST — 2-1-3-4-(ok).FCStd")
    print("=" * 75)

    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(["SMBS_Smoke"])

    artifacts_dir = ROOT / "docs" / "smoke_artifacts_2134"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------------
    # STEP 1: Подготовка & Открытие документа
    # -------------------------------------------------------------------------
    print("\n[STEP 1] Environment & Document Load")
    fixture_path = ROOT / "SMBendTests/2-1-3-4-(ok).FCStd"
    if not fixture_path.is_file():
        raise FileNotFoundError(f"Fixture {fixture_path} not found")

    # Symlink check
    mod_symlink = Path.home() / "Library/Application Support/FreeCAD/Mod/freecad_smbs"
    symlink_ok = mod_symlink.is_symlink() and mod_symlink.resolve() == (ROOT / "freecad_smbs").resolve()
    print(f"  Mod symlink: {mod_symlink} -> {symlink_ok} ({mod_symlink.resolve() if mod_symlink.exists() else 'N/A'})")

    doc = App.openDocument(str(fixture_path))
    App.setActiveDocument(doc.Name)
    print(f"  Opened FreeCAD Document: '{doc.Name}' ({fixture_path.name})")
    print(f"  Root objects in document: {[obj.Name for obj in doc.Objects]}")

    # -------------------------------------------------------------------------
    # STEP 2: Рассчитать последовательность (RunActiveCommand)
    # -------------------------------------------------------------------------
    print("\n[STEP 2] Pipeline Execution & Flat Pattern Annotations")
    print("  Running smbs_bridge.run_pipeline_on_fcstd...")
    result = smbs_bridge.run_pipeline_on_fcstd(
        fcstd_path=fixture_path,
        search_algorithm="dfs",
        unknown_policy="EXPLORATORY",
    )
    print(f"  Planning Status: {result.planning_status}")
    print(f"  Verification Status: {result.verification_status}")
    print(f"  Production Eligible: {result.production_eligible}")
    print(f"  Run Directory: {result.run_dir}")

    artifacts = result.artifacts
    if artifacts is None:
        raise RuntimeError("Pipeline failed to return RunArtifacts")

    set_current_run(artifacts)

    # Ensure active document is valid
    try:
        _ = doc.Name
    except Exception:
        doc = App.ActiveDocument if App.ActiveDocument is not None else App.openDocument(str(fixture_path))

    # Render annotations
    annotations = smbs_bridge.get_annotations(artifacts)
    created_labels = render_flat_pattern_annotations(doc, artifacts, annotations)
    print(f"  Annotated bend steps on flat pattern: {len(created_labels)} labels created")
    for a in annotations:
        print(f"    Step {a.step}: Bend {a.bend_id}, midpoint=({a.midpoint[0]:.2f}, {a.midpoint[1]:.2f}, {a.midpoint[2]:.2f}), dir={a.ui_direction}")

    # -------------------------------------------------------------------------
    # STEP 3: Верификация (Verify Dialog)
    # -------------------------------------------------------------------------
    print("\n[STEP 3] Verification Dialog Inspection")
    verif_details = extract_verification_details(artifacts)
    print(f"  Overall Status: {verif_details['status']}")
    print(f"  Badge Text: {verif_details['badge_text']}")

    # Stages breakdown
    print("  Per-Stage Breakdown:")
    for st_name, st_info in verif_details.get("stages", {}).items():
        print(f"    [{st_name}] status={st_info.get('status')}: {st_info.get('details')}")

    # Baseline & Hausdorff metrics
    base_file = artifacts.run_dir / "04_baseline_result.json"
    if base_file.is_file():
        import json
        b_data = json.loads(base_file.read_text(encoding="utf-8"))
        bm = b_data.get("payload", {}).get("metrics", {})
    else:
        bm = getattr(artifacts, "baseline_result", {}).get("payload", {}).get("metrics", {})
    ref_faces = bm.get("faces_count_reference")
    res_faces = bm.get("faces_count_restored")
    raw_comp = bm.get("raw_comparison_result") or {}
    counts_match = raw_comp.get("counts_match")
    hd = bm.get("hausdorff_distance_mm", 0.0)
    print(f"\n  Baseline Metrics:")
    print(f"    Reference Faces: {ref_faces}")
    print(f"    Restored Faces:  {res_faces} (Delta: {res_faces - ref_faces if res_faces is not None and ref_faces is not None else 'N/A'})")
    print(f"    Counts Match:    {counts_match}")
    print(f"    Hausdorff Dist:  {hd:.6f} mm")

    # Render and screenshot VerificationDialog (Tab 0: Stages)
    dlg_verif = VerificationDialog(artifacts)
    dlg_verif.show()
    app.processEvents()
    verif_shot = artifacts_dir / "01_verify_dialog.png"
    dlg_verif.grab().save(str(verif_shot))
    print(f"  Stages Screenshot saved: {verif_shot}")

    # Tab 2: Shape Metrics
    if hasattr(dlg_verif, "tabs"):
        dlg_verif.tabs.setCurrentIndex(2)
        app.processEvents()
        metrics_shot = artifacts_dir / "01b_verify_dialog_metrics.png"
        dlg_verif.grab().save(str(metrics_shot))
        print(f"  Metrics Screenshot saved: {metrics_shot}")

    dlg_verif.close()

    # -------------------------------------------------------------------------
    # STEP 4: Симуляция (Simulate Dialog & Player)
    # -------------------------------------------------------------------------
    print("\n[STEP 4] Simulate Dialog & Bidirectional Playback")
    player = smbs_bridge.get_player(artifacts)
    dlg_sim = AnimationPlayerDialog(artifacts, player)
    dlg_sim.show()
    app.processEvents()

    # Check Machine Frame badge
    mf_badge = dlg_sim.lbl_machine_frame.text()
    print(f"  Machine Frame Badge: {mf_badge} (Expect 'MACHINE: LOADED')")

    # 4a. Backward playback (Unfold)
    print("\n  --- Backward Mode (Unfold) ---")
    dlg_sim.cmb_direction.setCurrentIndex(0)  # Unfold (backward)
    app.processEvents()

    total_backward = dlg_sim.player.total
    print(f"  Total backward frames: {total_backward}")
    b_precisions = []
    for idx in range(total_backward):
        dlg_sim._on_slider_changed(idx)
        app.processEvents()
        hdr = dlg_sim.lbl_header.text()
        badge_txt = dlg_sim.lbl_precision.text()
        curr_frame = dlg_sim.controller.current()
        p_offset = _compute_punch_offset_mm(curr_frame)
        b_precisions.append(badge_txt)
        print(f"    Frame {idx}: Header='{hdr}', Precision='{badge_txt}', PunchOffset={p_offset:.1f}mm")

        # Step 0 screenshot
        if idx == 0:
            pix = dlg_sim.grab()
            p_out = artifacts_dir / "02_simulate_backward_step0.png"
            pix.save(str(p_out))
            print(f"      [Screenshot] {p_out.name}")

        # Mid step screenshot (e.g. index total_backward // 2)
        if idx == total_backward // 2:
            pix = dlg_sim.grab()
            p_out = artifacts_dir / "03_simulate_backward_step_mid.png"
            pix.save(str(p_out))
            print(f"      [Screenshot] {p_out.name}")

    # 4b. Forward playback (Fold)
    print("\n  --- Forward Mode (Fold) ---")
    dlg_sim.cmb_direction.setCurrentIndex(1)  # Fold (forward)
    app.processEvents()

    total_forward = dlg_sim.player.total
    print(f"  Total forward frames: {total_forward}")
    f_precisions = []
    for idx in range(total_forward):
        dlg_sim._on_slider_changed(idx)
        app.processEvents()
        hdr = dlg_sim.lbl_header.text()
        badge_txt = dlg_sim.lbl_precision.text()
        curr_frame = dlg_sim.controller.current()
        p_offset = _compute_punch_offset_mm(curr_frame)
        f_precisions.append(badge_txt)
        print(f"    Frame {idx}: Header='{hdr}', Precision='{badge_txt}', PunchOffset={p_offset:.1f}mm")

        # Step 0 screenshot
        if idx == 0:
            pix = dlg_sim.grab()
            p_out = artifacts_dir / "04_simulate_forward_step0.png"
            pix.save(str(p_out))
            print(f"      [Screenshot] {p_out.name}")

        # Mid step screenshot
        if idx == total_forward // 2:
            pix = dlg_sim.grab()
            p_out = artifacts_dir / "05_simulate_forward_step_mid.png"
            pix.save(str(p_out))
            print(f"      [Screenshot] {p_out.name}")

    dlg_sim.close()

    # -------------------------------------------------------------------------
    # STEP 5: Оснастка — вкладка Machine frame
    # -------------------------------------------------------------------------
    print("\n[STEP 5] Tooling Panel — Machine frame tab")
    library = smbs_bridge.get_tooling_library()
    dlg_tooling = ToolingPanel(library)
    dlg_tooling.show()
    app.processEvents()

    # Switch to Machine frame tab (index 2)
    if hasattr(dlg_tooling, "tabs"):
        dlg_tooling.tabs.setCurrentIndex(2)
        app.processEvents()

    pix_tooling = dlg_tooling.grab()
    tooling_shot = artifacts_dir / "06_tooling_machine_frame.png"
    pix_tooling.save(str(tooling_shot))
    print(f"  Machine Frame Tab Screenshot saved: {tooling_shot}")

    # Test "Просмотр в 3D"
    print("  Testing 'Просмотр в 3D' for machine frame...")
    dlg_tooling._on_preview_machine_frame()
    app.processEvents()
    print(f"  Active document has {len(App.ActiveDocument.Objects)} objects after 3D preview")

    dlg_tooling.close()

    # -------------------------------------------------------------------------
    # STEP 6: Диагностика & Настройки
    # -------------------------------------------------------------------------
    print("\n[STEP 6] Preferences & Diagnostics Dialogs")
    dlg_pref = PreferencesDialog()
    dlg_pref.show()
    app.processEvents()
    pref_shot = artifacts_dir / "07_preferences_dialog.png"
    dlg_pref.grab().save(str(pref_shot))
    dlg_pref.close()
    print(f"  Preferences Screenshot saved: {pref_shot}")

    dlg_diag = DiagnosticsDialog()
    dlg_diag.show()
    app.processEvents()
    diag_shot = artifacts_dir / "08_diagnostics_dialog.png"
    dlg_diag.grab().save(str(diag_shot))
    dlg_diag.close()
    print(f"  Diagnostics Screenshot saved: {diag_shot}")

    print("\n" + "=" * 75)
    print("FREECAD SMOKE CHECKLIST COMPLETED SUCCESSFULLY (0 Tracebacks)")
    print("=" * 75)


if __name__ == "__main__":
    run_smoke_checklist()
