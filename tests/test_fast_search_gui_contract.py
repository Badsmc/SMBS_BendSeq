"""Pure AST contracts for the explicit, opt-in SMBS FAST UI.

Not a substitute for exercising Qt in a real FreeCAD installation.
"""
from __future__ import annotations

import ast
from pathlib import Path
import unittest


SOURCE = (Path(__file__).resolve().parents[1] /
          "freecad_smbs" / "commands" / "pressbrake_2d.py").read_text("utf-8")
TREE = ast.parse(SOURCE)


def function(name):
    matches = [n for n in ast.walk(TREE)
               if isinstance(n, ast.FunctionDef) and n.name == name]
    if len(matches) != 1:
        raise AssertionError(f"Expected one {name}, got {len(matches)}")
    return ast.get_source_segment(SOURCE, matches[0])


class FastSearchGUIContracts(unittest.TestCase):
    def test_visible_button_and_explicit_user_action(self):
        self.assertIn('setObjectName("SMBS_FastSequenceSearch")', SOURCE)
        self.assertIn(
            "self.fast_search_btn.clicked.connect(self._run_fast_sequence_search)",
            SOURCE,
        )
        self.assertIn("playback_layout.addWidget(self.fast_search_btn)", SOURCE)

    def test_search_requires_stopped_editable_session(self):
        body = function("_run_fast_sequence_search")
        self.assertIn("not self.session.can_reorder()", body)
        self.assertIn("self.timer.isActive()", body)
        self.assertIn("limit_seconds=1.0", body)
        self.assertIn("beam_width=32", body)
        self.assertIn("max_candidates=5", body)
        self.assertIn("self._last_fast_candidates_report = report", body)

    def test_never_auto_apply_candidate(self):
        body = function("_run_fast_sequence_search")
        dialog = body.index("result = dialog.exec()")
        acceptance = body.index("if result != QtWidgets.QDialog.Accepted:")
        applying = body.index("controller.reorder_operations(selected.operation_ids)")
        self.assertLess(dialog, acceptance)
        self.assertLess(acceptance, applying)
        self.assertIn("UNVERIFIED_3D_NOT_CHECKED", body)
        self.assertIn("_on_staged_plan_changed(", body)
        self.assertNotIn("_validate_export_manual_order(", body)

    def test_preserves_per_stage_180_degree_choices(self):
        body = function("_run_fast_sequence_search")
        self.assertIn("self._manual_operation_orientation_overrides", body)
        self.assertIn("self._manual_orientation_overrides", body)
        self.assertIn("orientation = selected.orientations()", body)


if __name__ == "__main__":
    unittest.main()
