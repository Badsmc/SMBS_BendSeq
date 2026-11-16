"""FreeCAD search toolbar must show the mode chooser, not auto-run old 2D.

Compile the actual command class with injected host objects to exercise
Activated() without requiring Qt, Pivy or FreeCAD in CI.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


SOURCE_FILE = (
    Path(__file__).resolve().parents[1]
    / "freecad_smbs/commands/pressbrake_2d.py"
)


class SearchToolbarRoutingTests(unittest.TestCase):
    def _isolated_search_command(self):
        parsed = ast.parse(SOURCE_FILE.read_text(encoding="utf-8"))
        target = next(
            node for node in parsed.body
            if isinstance(node, ast.ClassDef)
            and node.name == "PlanarCandidatesCommand"
        )
        module = ast.fix_missing_locations(
            ast.Module(body=[target], type_ignores=[])
        )
        fake = SimpleNamespace(
            planar_calculate=SimpleNamespace(isEnabled=lambda: True),
            fast_feasible_btn=SimpleNamespace(isEnabled=lambda: True),
        )
        fake.calculate_planar_candidates = lambda: self.fail(
            "Toolbar must not automatically start legacy 2D"
        )
        fake.calculate_fast_feasible_sequence = lambda: self.fail(
            "Toolbar must not automatically start OCC"
        )
        events = []
        fake._trace_event = lambda *args, **kwargs: events.append(
            ("event", args, kwargs)
        )
        namespace = {
            "get_workspace_widget": lambda: fake,
            "show_workspace_controls": lambda obj, idx: events.append(
                ("show", obj, idx)
            ),
        }
        exec(compile(module, str(SOURCE_FILE), "exec"), namespace)
        return namespace["PlanarCandidatesCommand"], fake, events

    def test_toolbar_opens_search_page_without_running_any_solver(self):
        command_cls, workspace, events = self._isolated_search_command()
        command_cls().Activated()
        self.assertIn(("show", workspace, 3), events)
        self.assertIn(
            ("event", ("SEQUENCE_SEARCH_PAGE_OPENED",), {
                "available_modes": (
                    "FAST_FEASIBLE_SEARCH", "PLANAR_2D_ONLY"
                ),
            }),
            events,
        )

    def test_search_task_has_independent_explicit_actions(self):
        source = SOURCE_FILE.read_text(encoding="utf-8")
        self.assertIn('"SMBS_FastFeasibleSearch"', source)
        self.assertIn('"SMBS_PlanarOnlySearch"', source)
        self.assertIn(
            "self.fast_feasible_btn.clicked.connect(\n"
            "                    self.calculate_fast_feasible_sequence", source
        )
        self.assertIn(
            "self.planar_only_btn.clicked.connect(\n"
            "                    self.calculate_planar_candidates", source
        )
        self.assertIn("self._planar_search_running = True", source)
        self.assertIn("self._planar_search_running = False", source)
        self.assertIn('self._trace_event(\n                    "PLANAR_2D_ONLY_SEARCH_REQUESTED"', source)
        self.assertIn('"FAST_FEASIBLE_SEARCH_REQUESTED"', source)
        self.assertIn('"FAST_FEASIBLE_SEARCH_PRECHECK_REJECTED"', source)
        self.assertIn("self.fast_feasible_info.setText(message)", source)

    def test_prevents_search_reentry_and_disables_both_buttons(self):
        source = SOURCE_FILE.read_text(encoding="utf-8")
        self.assertIn('or getattr(self, "_planar_search_running", False)', source)
        self.assertIn('self.planar_only_btn.setEnabled(False)', source)
        self.assertIn('self.fast_feasible_btn.setEnabled(False)', source)
        self.assertIn('self.planar_only_btn.setEnabled(can_search)', source)


if __name__ == "__main__":
    unittest.main()
