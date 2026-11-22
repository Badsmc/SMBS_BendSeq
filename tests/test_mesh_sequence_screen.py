from types import SimpleNamespace
from unittest import TestCase

from smbs_core.kinematics2d.mesh_sequence_screen import (
    _bounds, _crosses_tool, _tree, screen_mesh_sequence,
)


class MeshSequenceScreenTests(TestCase):
    def test_crossing_follows_punch_ram_and_ignores_coplanar_touch(self):
        tool = ((0., 0., 0.), (3., 0., 0.), (0., 3., 0.))
        sheet = ((1., 1., 1.), (1., 1., -1.), (1., 2., -1.))
        check_budget = lambda: None
        tree = _tree([(_bounds(tool), tool)], check_budget)
        self.assertTrue(_crosses_tool(sheet, tree, 0., check_budget))
        self.assertFalse(_crosses_tool(sheet, tree, 4., check_budget))
        self.assertFalse(_crosses_tool(tool, tree, 0., check_budget))

    def test_budget_preserves_pending_operations(self):
        from unittest.mock import patch

        sequence = SimpleNamespace(fold_state=object(), reset=lambda: None)
        preview = SimpleNamespace(
            sequence=sequence, tooling=object(), config=None,
            loading_orientation_by_bend={}, assisted_retract_preview_bends=set(),
        )
        with patch("smbs_core.kinematics2d.mesh_sequence_screen.PressCycleController"):
            with patch("smbs_core.kinematics2d.mesh_sequence_screen._panel_meshes", return_value={}):
                report = screen_mesh_sequence(None, preview, ("B01", "B02"), budget_seconds=0.)
        self.assertEqual(report["reason"], "TIME_BUDGET")
        self.assertEqual(report["completed"], [])
        self.assertEqual(report["pending"], ["B01", "B02"])

    def test_contact_sliver_only_applies_to_punch_on_active_bend_during_forming(self):
        from smbs_core.kinematics2d.mesh_sequence_screen import _is_forming_contact_sliver
        from smbs_core.kinematics2d.press_cycle import CyclePhase

        bounds = {"zmin": -2.224919, "zmax": -2.224784}
        args = ("punch", CyclePhase.BENDING, "B05", "B05", 0.00033, bounds)
        self.assertTrue(_is_forming_contact_sliver(*args))
        for index, other in ((0, "die"), (1, CyclePhase.RETRACT),
                             (2, "P11"), (4, 0.05297), (5, None)):
            case = list(args)
            case[index] = other
            self.assertFalse(_is_forming_contact_sliver(*case))
