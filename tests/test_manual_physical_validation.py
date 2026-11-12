"""Headless Manual 3D orchestration tests; FreeCAD native kernel is mocked.

These cover selected FCStd identities, completed-bend history, user-set
virtual length, conditional end zones, early collision stop, failures,
partial orders and nonmutation of the original fold state.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from smbs_core.kinematics2d.manual_physical_validation import (
    check_manual_3d_sequence,
)
from smbs_core.kinematics2d.physical_validation_25d import PhysicalEvidence
from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.tooling_25d import ValidationStatus


def part():
    graph = PanelGraph(("P01", "P02", "P03"), (
        GraphEdge("B01", "P01", "P02", 90.0,
                  ((10, 0, 0), (10, 50, 0))),
        GraphEdge("B02", "P02", "P03", -45.0,
                  ((32, 0, 0), (32, 50, 0))),
    ))
    state = FoldState(graph, "P01")
    return SimpleNamespace(
        fold_state=state, panel_build=SimpleNamespace(),
        bend_order=("B01", "B02"),
    )


def evidence(status, *, conditional=False):
    return SimpleNamespace(
        status=status, native_status=status.value, reason="test_" + status.value,
        checked_scope=("3D_PUNCH_DIE_SAMPLED_BEND",),
        tool_length_info={"active_bend_length_mm": 50.0},
        is_conditional=conditional,
    )


class ManualToolingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.punch_file = root / "selected_punch.FCStd"
        self.die_file = root / "selected_die.FCStd"
        self.punch_file.write_bytes(b"test-tool")
        self.die_file.write_bytes(b"test-tool")
        self.tooling = SimpleNamespace(
            punch=SimpleNamespace(
                tool_id=self.punch_file.stem,
                name="Chosen Punch",
                source_file=str(self.punch_file),
                validation_status=ValidationStatus.GEOMETRY_VERIFIED,
            ),
            die=SimpleNamespace(
                tool_id=self.die_file.stem,
                name="Chosen Die",
                source_file=str(self.die_file),
                validation_status=ValidationStatus.GEOMETRY_VERIFIED,
            ),
            punch_section_offsets_xm=(),
            die_section_offsets_xm=(),
        )
        self.session = part()

    def test_all_steps_use_selected_tools_and_signed_prior_geometry(self):
        original = dict(self.session.fold_state.angles)
        calls = []

        def fake(pseudo_session, selected_tools, **kwargs):
            bid = pseudo_session.active_bend_id
            calls.append((bid, dict(pseudo_session.fold_state.angles), kwargs))
            self.assertIs(selected_tools, self.tooling)
            self.assertIsNot(pseudo_session.fold_state, self.session.fold_state)
            self.assertEqual(pseudo_session.fold_state.angles[bid], 0.0)
            return evidence(PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS)

        with patch(
            "smbs_core.kinematics2d.manual_physical_validation"
            ".check_active_25d_bend_trajectory", side_effect=fake,
        ):
            report = check_manual_3d_sequence(
                self.session, self.tooling, ("B01", "B02"),
                match_active_bend_length=True,
            )
        self.assertEqual(report["status"], "SAMPLED_3D_CLEAR_NOT_CONTINUOUS")
        self.assertEqual(report["checked_bends"], 2)
        self.assertFalse(report["continuous_motion_certified"])
        self.assertFalse(report["backgauge_collisions_checked"])
        self.assertEqual(report["punch"], self.punch_file.stem)
        self.assertEqual(report["die"], self.die_file.stem)
        self.assertEqual(calls[0][1], {"B01": 0.0, "B02": 0.0})
        self.assertEqual(calls[1][1], {"B01": 90.0, "B02": 0.0})
        self.assertTrue(all(call[2]["match_active_bend_length"] for call in calls))
        self.assertEqual(self.session.fold_state.angles, original)

    def test_collision_stops_at_first_proven_hit(self):
        def fake(*args, **kwargs):
            return evidence(PhysicalEvidence.COLLISION)
        with patch(
            "smbs_core.kinematics2d.manual_physical_validation"
            ".check_active_25d_bend_trajectory", side_effect=fake,
        ) as check:
            report = check_manual_3d_sequence(
                self.session, self.tooling, ("B01", "B02"),
            )
        self.assertEqual(report["status"], "COLLISION")
        self.assertEqual(report["checked_bends"], 1)
        check.assert_called_once()

    def test_end_zone_option_survives_and_never_becomes_clear(self):
        def fake(*args, **kwargs):
            self.assertTrue(kwargs["allow_end_zone_collision"])
            self.assertEqual(kwargs["end_zone_length_mm"], 10.0)
            return evidence(PhysicalEvidence.CONDITIONAL, conditional=True)
        with patch(
            "smbs_core.kinematics2d.manual_physical_validation"
            ".check_active_25d_bend_trajectory", side_effect=fake,
        ):
            report = check_manual_3d_sequence(
                self.session, self.tooling, ("B01", "B02"),
                allow_end_zone_collision=True, end_zone_length_mm=10.0,
            )
        self.assertEqual(report["status"], "CONDITIONAL")
        self.assertTrue(all(s["is_conditional"] for s in report["steps"]))

    def test_incomplete_manual_prefix_is_unverified(self):
        with patch(
            "smbs_core.kinematics2d.manual_physical_validation"
            ".check_active_25d_bend_trajectory",
            return_value=evidence(
                PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS
            ),
        ):
            report = check_manual_3d_sequence(
                self.session, self.tooling, ("B01",),
            )
        self.assertEqual(report["status"], "UNVERIFIED")
        self.assertEqual(report["checked_bends"], 1)
        self.assertFalse(report["complete_part_order_requested"])

    def test_missing_real_fcstd_is_not_approved(self):
        self.tooling.die.source_file = "missing.FCStd"
        with patch(
            "smbs_core.kinematics2d.manual_physical_validation"
            ".check_active_25d_bend_trajectory",
        ) as check:
            report = check_manual_3d_sequence(
                self.session, self.tooling, ("B01", "B02"),
            )
        self.assertEqual(report["status"], "UNVERIFIED")
        self.assertEqual(report["checked_bends"], 0)
        check.assert_not_called()

    def test_multi_section_and_virtual_length_are_incompatible(self):
        self.tooling.punch_section_offsets_xm = (-50, 50)
        with patch(
            "smbs_core.kinematics2d.manual_physical_validation"
            ".check_active_25d_bend_trajectory",
        ) as check:
            report = check_manual_3d_sequence(
                self.session, self.tooling, ("B01", "B02"),
                match_active_bend_length=True,
            )
        self.assertEqual(report["status"], "UNVERIFIED")
        check.assert_not_called()

    def test_occt_exception_does_not_mutate_original_or_prove_clear(self):
        originals = (dict(self.session.fold_state.angles),
                     dict(self.session.fold_state.transforms))
        with patch(
            "smbs_core.kinematics2d.manual_physical_validation"
            ".check_active_25d_bend_trajectory",
            side_effect=RuntimeError("OCC boolean timeout"),
        ):
            report = check_manual_3d_sequence(
                self.session, self.tooling, ("B01", "B02"),
            )
        self.assertEqual(report["status"], "UNVERIFIED")
        self.assertEqual(report["checked_bends"], 1)
        self.assertIn("OCC boolean timeout", report["steps"][0]["reason"])
        self.assertEqual(self.session.fold_state.angles, originals[0])
        self.assertEqual(self.session.fold_state.transforms, originals[1])

    def test_unknown_or_misordered_manual_prefix_fails(self):
        for order in (("B02",), ("B01", "B01"), ("B99",)):
            with self.subTest(order=order):
                with self.assertRaises(ValueError):
                    check_manual_3d_sequence(self.session, self.tooling, order)


if __name__ == "__main__":
    unittest.main()
