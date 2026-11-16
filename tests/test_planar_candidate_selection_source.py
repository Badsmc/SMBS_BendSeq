"""Regression: selecting a complete 2D candidate must not reference missing locals.

Real Qt/FreeCAD still needs manual integration testing. This static
test catches the precise NameError from the macOS operator trace.
"""
import ast
from pathlib import Path
import unittest


FILE = Path(__file__).resolve().parents[1] / "freecad_smbs/commands/pressbrake_2d.py"


class PlanarCandidateSelectionSourceTests(unittest.TestCase):
    def test_mixed_orientation_assigned_before_use(self):
        tree = ast.parse(FILE.read_text(encoding="utf-8"))
        on_preview = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "on_preview"
        ]
        self.assertEqual(len(on_preview), 1)
        f = on_preview[0]
        assigned = [
            node.lineno
            for node in ast.walk(f)
            if isinstance(node, ast.Name)
            and node.id == "mixed_orientation"
            and isinstance(node.ctx, ast.Store)
        ]
        read = [
            node.lineno
            for node in ast.walk(f)
            if isinstance(node, ast.Name)
            and node.id == "mixed_orientation"
            and isinstance(node.ctx, ast.Load)
        ]
        self.assertEqual(len(assigned), 1)
        self.assertTrue(read)
        self.assertTrue(all(n > assigned[0] for n in read))

    def test_dialog_only_accepted_after_trace_and_pose_assignment(self):
        source = FILE.read_text(encoding="utf-8")
        begin = source.index("                def on_preview():")
        end = source.index("                def on_export():", begin)
        candidate = source[begin:end]
        self.assertEqual(candidate.count("dlg.accept()"), 1)
        self.assertLess(
            candidate.index("self._manual_orientation_by_bend = orientations"),
            candidate.index("dlg.accept()"),
        )
        self.assertLess(
            candidate.index('"PLANAR_2D_MANUAL_ORDER_SELECTED"'),
            candidate.index("dlg.accept()"),
        )


if __name__ == "__main__":
    unittest.main()
