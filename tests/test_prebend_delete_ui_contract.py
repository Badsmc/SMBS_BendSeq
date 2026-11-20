"""Headless GUI contract: a split bend can be deleted from two visible places.

PySide/FreeCAD is not installed in GitHub Actions, so the widget itself must
still be checked in FreeCAD. These AST contracts protect the GUI wiring.
"""
from __future__ import annotations

import ast
from pathlib import Path
import unittest


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "freecad_smbs" / "commands" / "pressbrake_2d.py"
).read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def method_source(name: str) -> str:
    functions = [
        node for node in ast.walk(TREE)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    if len(functions) != 1:
        raise AssertionError(f"Expected exactly one method {name}, got {len(functions)}")
    return ast.get_source_segment(SOURCE, functions[0])


class PrebendDeleteGuiContractTests(unittest.TestCase):
    def test_dialog_contains_explicit_visible_delete_action(self):
        code = method_source("_edit_prebend_dialog")
        self.assertIn("Удалить предгиб и вернуть обычный гиб", code)
        self.assertIn('setObjectName("SMBS_DeletePrebend")', code)
        self.assertIn("if current is not None:", code)
        self.assertIn("self._remove_prebend(bend_id, parent=dialog)", code)
        self.assertNotIn("QDialogButtonBox.DestructiveRole", code)

    def test_prebend_row_has_independent_quick_delete(self):
        code = method_source("_update_operations_visual_state")
        self.assertIn('if op.kind == "PREBEND":', code)
        self.assertIn('setObjectName("SMBS_DeletePrebendRow")', code)
        self.assertIn("QtCore.QTimer.singleShot", code)
        self.assertIn("self._remove_prebend(bid)", code)

    def test_deletion_requires_confirmation_then_resets_preview(self):
        code = method_source("_remove_prebend")
        self.assertIn("QMessageBox.question", code)
        self.assertIn("QMessageBox.No", code)
        self.assertIn("controller.remove_prebend(bend_id)", code)
        self.assertIn('"STAGED_PREBEND_REMOVED"', code)
        self.assertIn("self._on_staged_plan_changed", code)
        self.assertIn("self.session.can_reorder()", code)


if __name__ == "__main__":
    unittest.main()
