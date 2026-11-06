"""Native FreeCAD GUI acceptance for Stage 1: 2.5D Full Cycle stabilization.

Run in the FreeCAD Python Console after installing branch 2D and the original
demo FCStd models:
    from tools.run_25d_stage1_gui_acceptance import run
    run()

This is deliberately NOT a headless test. Any skip or failure means NO PASS.
"""
from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run():
    import FreeCAD as App
    from PySide6 import QtWidgets
    from smbs_core.demo_paths import demo_part_path

    if QtWidgets.QApplication.instance() is None:
        raise RuntimeError("This acceptance runner requires a real FreeCAD GUI session")
    model = demo_part_path("box.100out-50in-(ok).FCStd")
    if not model.is_file():
        raise FileNotFoundError(f"Required original 8-bend fixture missing: {model}")

    fixture_sha_before = hashlib.sha256(model.read_bytes()).hexdigest()

    from tests.test_press_cycle_phase_d_regression import TestFullCyclePhaseDRegression
    from tests.test_pressbrake_workspace_gui import WorkspaceGuiTests

    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestFullCyclePhaseDRegression))
    for name in (
        "test_full_cycle_tick_failure_pauses_controller_and_latches_reason",
        "test_manual_record_button_creates_jsonl_and_summary",
        "test_separate_workspace_layout_and_single_timer",
    ):
        suite.addTest(WorkspaceGuiTests(name))

    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
    fixture_sha_after = hashlib.sha256(model.read_bytes()).hexdigest()
    if fixture_sha_after != fixture_sha_before:
        raise RuntimeError(
            "STAGE1_SOURCE_CHANGED: original FCStd changed during GUI test; "
            "do not mark acceptance PASS"
        )
    if result.skipped or not result.wasSuccessful():
        raise RuntimeError(
            "STAGE1_GUI_ACCEPTANCE_FAILED: ran={} failures={} errors={} skipped={}. "
            "Do not mark Phase 5B verified.".format(
                result.testsRun, len(result.failures),
                len(result.errors), len(result.skipped)
            )
        )
    print("STAGE1_GUI_ACCEPTANCE_PASS: {} tests; fixture={}".format(
        result.testsRun, model
    ))
    return result


if __name__ == "__main__":
    run()
