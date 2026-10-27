"""
dialogs.py - User dialog wrappers for SMBS FreeCAD Addon.

Headless-safe: PySide6 imports are strictly lazy inside functions.
Falls back to FreeCAD.Console if running without GUI display.
"""
from __future__ import annotations

import sys


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

    if planning_status == "SUCCESS":
        steps_lines = []
        for s in seq:
            step_no = s.get("step", "?")
            bid = s.get("bend_id", "?")
            direction = s.get("direction", "")
            ang = s.get("angle", s.get("angle_deg", ""))
            ang_str = f" {ang}°" if ang != "" else ""
            steps_lines.append(f"  Шаг {step_no}: {bid} ({direction}{ang_str})")
        steps_text = "\n".join(steps_lines) if steps_lines else "  (нет шагов)"

        elig_str = "Готово к производству (production_eligible=true)" if prod_elig else "Не готово к производству (production_eligible=false)"
        msg = (
            f"Последовательность успешно рассчитана!\n\n"
            f"Порядок гибки ({len(seq)} шагов):\n"
            f"{steps_text}\n\n"
            f"Верификация: {verif_status}\n"
            f"Статус: {elig_str}\n"
            f"Директория запуска: {run_dir}\n\n"
            f"Метки шагов добавлены на развёртку детали.\n"
            f"Команды Симуляция и Верификация готовы к работе."
        )
        show_info("SMBS: Расчёт завершён", msg)
    elif planning_status == "NO_SOLUTION":
        msg = (
            f"Последовательность гибки не найдена (NO_SOLUTION).\n\n"
            f"Причина:\n{err_msg or 'Все траектории гибки приводят к столкновению или недопустимы'}\n\n"
            f"Директория запуска: {run_dir}\n"
            f"Подробные логи доступны в 11_run_report.json."
        )
        show_unverified_warning("SMBS: Решение не найдено", msg)
    else:
        msg = (
            f"Расчёт завершился с ошибкой ({planning_status}).\n\n"
            f"Детали:\n{err_msg or 'Неизвестная ошибка'}\n\n"
            f"Проверьте корректность модели листового металла в верстаке SheetMetal "
            f"и вывод в панели отчётов FreeCAD (Report view)."
        )
        show_error("SMBS: Ошибка расчёта", msg)


class ProgressContext:
    """Progress dialog context manager, headless-safe."""

    def __init__(self, title: str = "SMBS", text: str = "Выполняется расчёт..."):
        self.title = title
        self.text = text
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
