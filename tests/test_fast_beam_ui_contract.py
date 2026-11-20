"""Headless GUI contract for explicit experimental FAST search.

Native PySide and FreeCAD need separate Windows/macOS smoke testing.
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


def source_for(name):
    methods = [
        node for node in ast.walk(TREE)
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    if len(methods) != 1:
        raise AssertionError(f"Expected one {name}, found {len(methods)}")
    return ast.get_source_segment(SOURCE, methods[0])


class FastGuiContracts(unittest.TestCase):
    def test_explicit_only_not_automatic_trigger(self):
        self.assertIn('QPushButton("FAST — найти варианты (1 с)")', SOURCE)
        self.assertIn("self.fast_beam_search_btn.clicked.connect(", SOURCE)
        self.assertIn("self._run_fast_beam_search", SOURCE)
        self.assertIn('setObjectName("SMBS_FastBFMBeamSearch")', SOURCE)

    def test_no_native_occ_or_full_2d_in_fast_callback(self):
        runner = source_for("_run_fast_beam_search")
        self.assertIn("search_fast_beam(", runner)
        self.assertIn("screen_candidate_stage_a(", runner)
        self.assertIn("time_budget_s=1.0", runner)
        self.assertNotIn("screen_candidate_stage_b(", runner)
        self.assertNotIn("native_collision", runner)
        self.assertNotIn("processEvents(", runner)

    def test_applied_plan_is_explicitly_unverified(self):
        runner = source_for("_run_fast_beam_search")
        self.assertIn("if not accepted", runner)
        self.assertIn("UNVERIFIED", runner)
        self.assertIn("self.session.controller.reorder_operations(", runner)
        self.assertIn("self.session.set_bend_order(", runner)
        self.assertIn("self._manual_operation_orientation_overrides", runner)
        self.assertIn("self._on_staged_plan_changed(", runner)


if __name__ == "__main__":
    unittest.main()
