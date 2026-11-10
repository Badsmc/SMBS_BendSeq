"""Focused tests for conditional, non-certified RETRACT-only diagnostics."""
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from smbs_core.kinematics2d.preliminary_screening import (
    PreliminaryScreeningReport, ScreeningDecision,
)
from smbs_core.kinematics2d.sequence_search_2d import _classify_step
from smbs_core.kinematics2d.manual_sequence_replay import evaluate_manual_planar_order


class FakeState:
    def __init__(self):
        self.bends = {
            "B01": SimpleNamespace(angle_deg=45.0),
            "B02": SimpleNamespace(angle_deg=90.0),
        }
        self.angles = {"B01": 0.0, "B02": 0.0}
        self.transforms = {}

    def reset_all(self):
        self.angles = {"B01": 0.0, "B02": 0.0}

    def set_angle(self, bid, angle):
        self.angles[bid] = angle


class RetractWarningTests(unittest.TestCase):
    def setUp(self):
        self.state = FakeState()
        self.session = SimpleNamespace(
            fold_state=self.state,
            canonical_order=("B01", "B02"),
            panel_vertices_xy={"P01": ((0, 0), (1, 0), (1, 1))},
        )

    def test_warning_distinct_from_clear_and_interference(self):
        base = dict(
            decision=ScreeningDecision.CONTINUE_TO_3D,
            reason="retract_punch_interference_requires_3d_review",
            sampled_phases=("APPROACH", "BENDING", "BOTTOM", "RETRACT"),
            contact_statuses=("TOUCH", "TOUCH", "TOUCH", "INTERFERENCE"),
            diagnostic_findings=(
                ("RETRACT", "PUNCH", "P03", "INTERFERENCE",
                 "proper_material_boundary_crossing"),
            ),
        )
        self.assertEqual(
            _classify_step(PreliminaryScreeningReport(
                stage="STAGE_B_RETRACT_WARNING", **base)),
            "2D_RETRACT_WARNING_NOT_3D_VALIDATED",
        )
        self.assertEqual(
            _classify_step(PreliminaryScreeningReport(
                stage="STAGE_B_SECTION_REQUIRES_3D", **base)),
            "2D_INTERFERENCE",
        )

    def test_manual_optional_warning_continues_and_keeps_finding(self):
        ok_a = PreliminaryScreeningReport(
            ScreeningDecision.CONTINUE_TO_3D, "STAGE_A_PASSED", "ok",
        )
        retract = PreliminaryScreeningReport(
            ScreeningDecision.CONTINUE_TO_3D,
            "STAGE_B_RETRACT_WARNING",
            "retract_punch_interference_requires_3d_review",
            sampled_phases=("APPROACH", "BENDING", "BOTTOM", "RETRACT"),
            contact_statuses=("TOUCH", "TOUCH", "TOUCH", "INTERFERENCE"),
            diagnostic_findings=((
                "RETRACT", "PUNCH", "P03", "INTERFERENCE",
                "proper_material_boundary_crossing",
            ),),
        )
        clear = PreliminaryScreeningReport(
            ScreeningDecision.CONTINUE_TO_3D,
            "STAGE_B_PREVIEW", "all_sampled_2d_sections_clear_or_touch",
            sampled_phases=("APPROACH", "BENDING", "BOTTOM", "RETRACT"),
            contact_statuses=("TOUCH", "TOUCH", "TOUCH", "CLEAR"),
        )
        with patch(
            "smbs_core.kinematics2d.manual_sequence_replay.screen_candidate_stage_a",
            return_value=ok_a,
        ), patch(
            "smbs_core.kinematics2d.manual_sequence_replay.screen_candidate_stage_b",
            side_effect=(retract, clear),
        ) as stage_b:
            report = evaluate_manual_planar_order(
                self.session, object(), ("B01", "B02"),
                allow_retract_warning=True,
            )
        self.assertEqual(report["accepted_prefix"], ["B01", "B02"])
        self.assertEqual(report["status"],
                         "CONDITIONAL_RETRACT_WARNING_NOT_3D_VALIDATED")
        self.assertEqual(report["physical_certification"], "NOT_CERTIFIED_3D")
        self.assertEqual(report["retract_warnings"][0]["bend_id"], "B01")
        self.assertEqual(report["retract_warnings"][0]["diagnostic_findings"][0]["tool"],
                         "PUNCH")
        self.assertTrue(stage_b.call_args_list[0].kwargs["allow_retract_warning"])
        self.assertEqual(self.state.angles, {"B01": 0.0, "B02": 0.0})

    def test_default_mode_stops_on_warning(self):
        ok_a = PreliminaryScreeningReport(
            ScreeningDecision.CONTINUE_TO_3D, "STAGE_A_PASSED", "ok",
        )
        retract = PreliminaryScreeningReport(
            ScreeningDecision.CONTINUE_TO_3D, "STAGE_B_RETRACT_WARNING",
            "retract_punch_interference_requires_3d_review",
        )
        with patch(
            "smbs_core.kinematics2d.manual_sequence_replay.screen_candidate_stage_a",
            return_value=ok_a,
        ), patch(
            "smbs_core.kinematics2d.manual_sequence_replay.screen_candidate_stage_b",
            return_value=retract,
        ):
            report = evaluate_manual_planar_order(
                self.session, object(), ("B01", "B02"),
            )
        self.assertEqual(report["status"], "MANUAL_REPLAY_STOPPED")
        self.assertEqual(report["accepted_prefix"], [])


if __name__ == "__main__":
    unittest.main()
