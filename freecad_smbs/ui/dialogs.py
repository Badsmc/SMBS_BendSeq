"""
dialogs.py - User dialog wrappers for SMBS FreeCAD Addon.

Headless-safe: PySide6 imports are strictly lazy inside functions.
Falls back to FreeCAD.Console if running without GUI display.
"""
from __future__ import annotations

import sys

from freecad_smbs.ui.localization import tr, tr_message


def _print_fallback(prefix: str, title: str, msg: str) -> None:
    try:
        import FreeCAD
        if hasattr(FreeCAD, "Console"):
            FreeCAD.Console.PrintMessage(f"SMBS [{prefix}] {title}: {msg}\n")
            return
    except ImportError:
        pass
    print(f"SMBS [{prefix}] {title}: {msg}", file=sys.stderr)


def show_info(title: str, msg: str) -> None:
    """Display an informative message dialog."""
    title, msg = tr_message(title), tr_message(msg)
    try:
        from PySide6 import QtWidgets
        app = QtWidgets.QApplication.instance()
        if app is not None:
            QtWidgets.QMessageBox.information(None, title, msg)
            return
    except (ImportError, Exception):
        pass
    _print_fallback("INFO", title, msg)


def show_error(title: str, msg: str) -> None:
    """Display an error message dialog."""
    title, msg = tr_message(title), tr_message(msg)
    try:
        from PySide6 import QtWidgets
        app = QtWidgets.QApplication.instance()
        if app is not None:
            QtWidgets.QMessageBox.critical(None, title, msg)
            return
    except (ImportError, Exception):
        pass
    _print_fallback("ERROR", title, msg)


def show_unverified_warning(title: str, msg: str) -> None:
    """Display a warning dialog regarding unverified or failed sequence."""
    title, msg = tr_message(title), tr_message(msg)
    try:
        from PySide6 import QtWidgets
        app = QtWidgets.QApplication.instance()
        if app is not None:
            QtWidgets.QMessageBox.warning(None, title, msg)
            return
    except (ImportError, Exception):
        pass
    _print_fallback("WARNING", title, msg)


def show_pipeline_result(result: Any) -> None:
    """Display comprehensive results of run_pipeline_on_fcstd."""
    planning_status = getattr(result, "planning_status", "UNKNOWN")
    verif_status = getattr(result, "verification_status", "UNVERIFIED")
    prod_elig = getattr(result, "production_eligible", False)
    seq = getattr(result, "candidate_sequence", [])
    run_dir = getattr(result, "run_dir", "")
    err_msg = getattr(result, "error_message", None)

    if planning_status in ("SUCCESS", "PARTIAL") and seq:
        steps_lines = []
        for s in seq:
            step_no = s.get("step", "?")
            bid = s.get("bend_id", "?")
            direction = s.get("direction", "")
            if isinstance(direction, dict):
                direction = direction.get("value", "")
            ang = s.get("angle", s.get("angle_deg", ""))
            if isinstance(ang, dict):
                ang = ang.get("value", "")
            ang_str = f" {ang}°" if ang != "" else ""
            steps_lines.append(tr("step", step=step_no, bend=bid, direction=direction, angle=ang_str))
        steps_text = "\n".join(steps_lines) if steps_lines else tr("no_steps")

        if planning_status == "SUCCESS":
            elig_str = "ready_for_production_production_eligible_true" if prod_elig else "not_approved_for_production_production_eligible_false"
            msg = tr("sequence_calculated_successfully_bending_order_steps_verification_status_run", count=len(seq), steps=steps_text, verification=verif_status, eligibility=tr(elig_str), directory=run_dir)
            show_info(tr("smbs_calculation_complete"), msg)
        else:  # PARTIAL (EXPLORATORY / without physical tooling)
            msg = tr("a_geometric_bending_sequence_was_found_bending_order_steps", count=len(seq), steps=steps_text, verification=verif_status, directory=run_dir)
            show_info(tr("smbs_geometric_calculation_exploratory"), msg)
    elif planning_status == "NO_SOLUTION":
        msg = tr("no_bending_sequence_found_no_solution_reason_run_directory", reason=err_msg or tr("all_bending_paths_collide_or_are_invalid"), directory=run_dir)
        show_unverified_warning(tr("smbs_no_solution_found"), msg)
    else:
        msg = tr("calculation_failed_details_check_the_sheet_metal_model_in", status=planning_status, details=err_msg or tr("unknown_error"))
        show_error(tr("smbs_calculation_error"), msg)


class ProgressContext:
    """Progress dialog context manager, headless-safe."""

    def __init__(self, title: str = "SMBS", text: str = "calculating"):
        self.title = title
        self.text = tr(text)
        self._dlg = None

    def __enter__(self) -> ProgressContext:
        try:
            from PySide6 import QtWidgets, QtCore
            app = QtWidgets.QApplication.instance()
            if app is not None:
                dlg = QtWidgets.QProgressDialog(self.text, None, 0, 0)
                dlg.setWindowTitle(self.title)
                dlg.setWindowModality(QtCore.Qt.ApplicationModal)
                dlg.setCancelButton(None)  # Disallow cancel during atomic run
                dlg.setMinimumDuration(0)
                dlg.show()
                app.processEvents()
                self._dlg = dlg
        except Exception:
            pass
        return self

    def update_text(self, text: str) -> None:
        if self._dlg is not None:
            try:
                from PySide6 import QtWidgets
                self._dlg.setLabelText(text)
                app = QtWidgets.QApplication.instance()
                if app is not None:
                    app.processEvents()
            except Exception:
                pass
        else:
            try:
                import FreeCAD
                if hasattr(FreeCAD, "Console"):
                    FreeCAD.Console.PrintMessage(f"SMBS: {text}\n")
            except Exception:
                pass

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._dlg is not None:
            try:
                self._dlg.close()
            except Exception:
                pass
            self._dlg = None
