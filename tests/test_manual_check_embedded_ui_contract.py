"""Static GUI wiring regression: Manual Verify is a check, not JSON export.

Headless CI cannot instantiate PySide/FreeCAD; these AST contracts prevent
the previously observed file-dialog-only UX from silently reappearing.
"""
from __future__ import annotations

import ast
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / (
    "freecad_smbs/commands/pressbrake_2d.py"
)


def method(name):
    syntax = ast.parse(SOURCE.read_text(encoding="utf-8"))
    hits = [
        n for n in ast.walk(syntax)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == name
    ]
    if len(hits) != 1:
        raise AssertionError("Expected exactly one GUI method: " + name)
    return hits[0]


def called_names(node):
    names = set()
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            fn = call.func
            if isinstance(fn, ast.Name):
                names.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                names.add(fn.attr)
    return names


class ManualVerifyEmbeddedContractTests(unittest.TestCase):
    def test_manual_button_runs_3d_not_save_json_dialog(self):
        source = ast.get_source_segment(
            SOURCE.read_text(encoding="utf-8"),
            method("_validate_export_manual_order"),
        )
        calls = called_names(method("_validate_export_manual_order"))
        self.assertIn("check_manual_3d_sequence", calls)
        self.assertIn("evaluate_manual_planar_order", calls)
        self.assertIn("_persist_manual_check_to_fcstd", calls)
        self.assertIn("_show_manual_check_feedback", calls)
        self.assertNotIn("getSaveFileName", calls)
        self.assertNotIn("write_text", calls)
        self.assertNotIn("json.dumps", source)
        self.assertNotIn("smbs_manual_sequence_diagnostics.json", source)

    def test_native_fcstd_persistence_does_not_rename_or_export(self):
        calls = called_names(method("_persist_manual_check_to_fcstd"))
        self.assertIn("_bound_smbs_document", calls)
        self.assertIn("_write_embedded_smbs_json", calls)
        self.assertIn("save", calls)
        self.assertNotIn("saveAs", calls)
        self.assertNotIn("getSaveFileName", calls)
        self.assertNotIn("json", calls)
        snap_calls = called_names(method("_write_embedded_smbs_json"))
        self.assertIn("build_snapshot", snap_calls)
        self.assertIn("write_included_json", snap_calls)

    def test_planar_search_completion_trace_has_only_bound_values(self):
        """Regression: a dead CAD-search flag raised NameError after 2D search."""
        fn = method("calculate_planar_candidates")
        traces = [
            node for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_trace_event"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "PLANAR_2D_SEARCH_COMPLETED"
        ]
        self.assertEqual(len(traces), 1)
        supplied = {kw.arg for kw in traces[0].keywords}
        self.assertEqual(supplied, {
            "status", "evaluated_steps", "visited_nodes",
            "candidate_count", "limit_reason", "budget_seconds",
        })
        self.assertNotIn("cad_extended_search",
                         ast.get_source_segment(
                             SOURCE.read_text(encoding="utf-8"), fn
                         ))

    def test_manual_native_budget_extended_without_relaxing_collisions(self):
        fn = method("_validate_export_manual_order")
        configs = [
            call for call in ast.walk(fn)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "AdaptiveSamplingConfig"
        ]
        self.assertEqual(len(configs), 1)
        values = {
            kw.arg: kw.value.value
            for kw in configs[0].keywords
            if isinstance(kw.value, ast.Constant)
        }
        self.assertEqual(values["max_phase_time_s"], 300.0)
        self.assertEqual(values["max_evaluated_states_per_phase"], 64)
        self.assertNotIn("volume_tolerance_mm3", values)
        self.assertNotIn("near_tolerance_mm", values)
        calls = called_names(fn)
        self.assertIn("crosscheck_manual_planar_native_evidence", calls)
        self.assertIn("check_manual_3d_sequence", calls)

    def test_manual_panel_has_inline_state_and_historical_restore(self):
        text = SOURCE.read_text(encoding="utf-8")
        self.assertIn("self.manual_check_summary = QtWidgets.QLabel(", text)
        self.assertIn("self.manual_check_details = QtWidgets.QPlainTextEdit()", text)
        self.assertIn("HISTORICAL / UNVERIFIED", text)
        self.assertIn("self._manual_3d_verdicts[step[\"bend_id\"]]", text)


if __name__ == "__main__":
    unittest.main()
