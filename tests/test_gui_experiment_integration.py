"""Headless integration contract: new GUI must not replace experimental engines."""
from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMMAND = ROOT / "freecad_smbs" / "commands" / "pressbrake_2d.py"


class GuiExperimentIntegrationContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = COMMAND.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source, filename=str(COMMAND))
        cls.classes = {
            n.name: n for n in ast.walk(cls.tree) if isinstance(n, ast.ClassDef)
        }

    def test_all_top_level_actions_registered(self):
        from freecad_smbs.InitGui import COMMANDS_MAP
        self.assertTrue({
            "SMBS_PressBrake2D", "SMBS_LoadPart", "SMBS_ToolingTab",
            "SMBS_Calculate", "SMBS_PlanarCandidates", "SMBS_Tooling",
        } <= set(COMMANDS_MAP))

    def test_both_experimental_search_handlers_survive(self):
        panel = self.classes["Panel"]
        methods = {n.name for n in panel.body if isinstance(n, ast.FunctionDef)}
        self.assertTrue({
            "calculate_sequence", "calculate_planar_candidates",
            "_show_planar_candidates_dialog",
            "check_current_physical_pose",
            "check_active_bend_physical_trajectory",
            "_physical_pose_signature",
        } <= methods)

    def test_handlers_delegate_to_original_search_engines(self):
        self.assertIn("search_bend_sequences_2d(", self.source)
        self.assertIn("search_bend_sequence_25d(", self.source)
        self.assertIn("workspace.calculate_planar_candidates()", self.source)
        self.assertIn("workspace.calculate_sequence()", self.source)
        self.assertIn("self.last_planar_search_report = report", self.source)
        self.assertIn("self.last_sequence_search_report = search_rep", self.source)

    def test_five_toolbar_commands_have_distinct_resources(self):
        from freecad_smbs.InitGui import COMMANDS_MAP
        for name in (
            "SMBS_PressBrake2D", "SMBS_LoadPart", "SMBS_ToolingTab",
            "SMBS_Calculate", "SMBS_PlanarCandidates",
        ):
            with self.subTest(command=name):
                resources = COMMANDS_MAP[name]().GetResources()
                self.assertTrue(resources["MenuText"])
                self.assertTrue(Path(resources["Pixmap"]).is_file())

    def test_viewer_is_separated_from_controls_without_second_timer(self):
        panel = self.classes["Panel"]
        methods = {n.name for n in panel.body if isinstance(n, ast.FunctionDef)}
        self.assertIn("_install_workspace_layout", methods)
        self.assertIn("_orbit_3d", methods)
        self.assertIn("_update_3d_preview", methods)
        self.assertIn("self._control_tabs = controls", self.source)
        self.assertIn("SMBS_CloseTaskPanel", self.source)
        self.assertNotIn("QtWidgets.QSplitter(", self.source)
        self.assertEqual(self.source.count("self.timer = QtCore.QTimer(self)"), 1)

    def test_selected_backgauge_variant_is_preserved_through_gui_replay(self):
        self.assertIn("backgauge_variant_indices=tuple(", self.source)
        self.assertIn("self._selected_planar_gauge_positions = {", self.source)
        self.assertIn("self._selected_planar_gauge_order", self.source)
        self.assertIn("SELECTED_DFS_CONTACT_SAMPLED_NOT_3D_CERTIFIED", self.source)

    def test_selected_loading_orientation_propagates_to_machine_cycle(self):
        self.assertIn("loading_orientation_variants=tuple(", self.source)
        self.assertIn("self._selected_planar_loading_orientation = {", self.source)
        self.assertIn(
            "self.press_cycle.loading_orientation_by_bend = (",
            self.source,
        )
        self.assertIn("self._effective_loading_orientation_map()", self.source)
        self.assertIn("selected_planar_loading_orientation.get(", self.source)

    def test_both_machine_views_share_selected_3d_placement(self):
        self.assertIn("def _current_preview_machine_placement(self):", self.source)
        self.assertIn("machine_placement=current_placement", self.source)
        self.assertIn("self._current_preview_machine_placement()", self.source)
        self.assertIn("def _effective_loading_orientation_map(self):", self.source)
        self.assertIn("plan_preview_loading_orientations(", self.source)

    def test_partial_full_cycle_finishes_retraction_before_stop(self):
        self.assertIn("partial_prefix_done", self.source)
        self.assertIn("partial_safe_to_stop", self.source)
        self.assertIn("CyclePhase.STEP_COMPLETE", self.source)

    def test_native_calculation_code_remains_present(self):
        # These components belong to the experimental branch, not the GUI.
        for path in (
            "smbs_core/kinematics2d/sequence_search.py",
            "smbs_core/kinematics2d/sequence_search_2d.py",
            "smbs_core/kinematics2d/native_collision_3d.py",
            "smbs_core/kinematics2d/multi_station_screening.py",
        ):
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file())


if __name__ == "__main__":
    unittest.main()
