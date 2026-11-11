"""End-to-end headless DFS contract: alternative CAD gauge contacts.

These tests mock the costly FreeCAD adapter; real machine eligibility still
requires the sampled Stage B and native runtime validation.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from smbs_core.kinematics2d.backgauge_2d import (
    BackgaugeModel2D, BackgaugePosition2D,
)
from smbs_core.kinematics2d.operation_cost import WorkpieceMachineSetup
from smbs_core.kinematics2d.preliminary_screening import (
    PreliminaryScreeningReport, ScreeningDecision,
)
from smbs_core.kinematics2d.sequence_search_2d import (
    PlanarSearchStatus, search_bend_sequences_2d,
)
from tests.test_sequence_search_2d import DummyState, stage_b_result


class BackgaugeDFSVariantTests(unittest.TestCase):
    def test_rejected_primary_contact_falls_back_to_checked_alternative(self):
        state = DummyState(("B01", "B02"))
        for bend in state.bends.values():
            bend.bend_zone = SimpleNamespace(outer_radius_mm=1.5)
        session = SimpleNamespace(
            fold_state=state,
            bend_order=("B01", "B02"),
            panel_vertices_xy={"P": [(0, 0), (1, 0), (1, 1)]},
            part_label="",
        )
        model = BackgaugeModel2D(
            width_s_mm=10.0, height_z_mm=10.0,
            positions={}, automatic=True, cad_geometry_enabled=True,
        )
        contact = SimpleNamespace(station_xm_mm=0.0, second_station_xm_mm=None)

        def resolve(_state, _vertices, _bend_id, _model, **_kwargs):
            return (
                BackgaugePosition2D(105, 0), 100.0, "PRIMARY",
                {
                    "contact_s_mm": 100.0,
                    "contact_z_mm": 0.0,
                    "selected_datum_is_contact": True,
                    "alternative_contacts": [
                        {
                            "contact_s_mm": 102.0,
                            "contact_z_mm": 5.0,
                            "support_panel_id": "P",
                            "support_panel_matches_fixed": False,
                            "face_gap_mm": 0.0,
                        }
                    ],
                },
            )

        contact.resolve_contact_for_bend = resolve
        z_samples = []

        def screen(_state, bend_id, _tooling, _verts, **kwargs):
            z = kwargs.get("backgauge_contact_z_mm")
            z_samples.append((bend_id, z))
            if bend_id == "B01" and z == 0.0:
                return PreliminaryScreeningReport(
                    ScreeningDecision.CONTINUE_TO_3D,
                    "STAGE_B_SECTION_REQUIRES_3D", "primary_collision",
                    sampled_phases=("BENDING",),
                    contact_statuses=("INTERFERENCE",),
                )
            return stage_b_result()

        with patch(
            "smbs_core.kinematics2d.backgauge_cad_3d.BackgaugeCAD3D",
            return_value=contact,
        ), patch(
            "smbs_core.kinematics2d.sequence_search_2d.screen_candidate_stage_a",
            return_value=PreliminaryScreeningReport(
                ScreeningDecision.CONTINUE_TO_3D, "STAGE_A_PASSED", "ok",
            ),
        ), patch(
            "smbs_core.kinematics2d.sequence_search_2d.screen_candidate_stage_b",
            side_effect=screen,
        ), patch(
            "smbs_core.kinematics2d.sequence_search_2d.compute_workpiece_machine_setup",
            side_effect=lambda _state, bid, **kw: WorkpieceMachineSetup(
                bend_id=bid, rotation_deg=0.0, flipped=False,
                shift_xm_mm=0.0, bend_length_mm=100.0,
                operator_reach_mm=200.0, rear_reach_mm=40.0,
            ),
        ):
            report = search_bend_sequences_2d(
                session, object(), backgauge_model=model,
                group_first=False, max_nodes=25,
                max_search_time_s=3.0, max_complete_candidates=1,
            )

        self.assertEqual(report.status, PlanarSearchStatus.SAMPLED_2D_CANDIDATES)
        self.assertEqual(report.candidates[0].sequence, ("B01", "B02"))
        first = report.candidates[0].steps[0]
        self.assertEqual(first.backgauge_variant_index, 1)
        self.assertEqual(first.setup.backgauge_s_mm, 107.0)
        self.assertEqual(first.setup.backgauge_z_mm, 5.0)
        self.assertEqual(first.to_dict()["backgauge_variant_index"], 1)
        self.assertIn(("B01", 0.0), z_samples)
        self.assertIn(("B01", 5.0), z_samples)


    def test_full_density_replay_rechecks_selected_stop_variant(self):
        from smbs_core.kinematics2d.sequence_search_2d import (
            validate_partial_planar_prefix,
        )
        state = DummyState(("B01", "B02"))
        session = SimpleNamespace(
            fold_state=state, bend_order=("B01", "B02"),
            panel_vertices_xy={"P": [(0, 0), (1, 0), (1, 1)]},
        )
        model = BackgaugeModel2D(
            width_s_mm=10.0, height_z_mm=10.0,
            positions={}, automatic=True, cad_geometry_enabled=True,
        )
        resolved_orientations = []
        def resolve(*_args, **kwargs):
            resolved_orientations.append(bool(kwargs.get("reverse_axis", False)))
            return (
                BackgaugePosition2D(105.0, 0.0), 100.0, "PRIMARY",
                {
                    "contact_s_mm": 100.0, "contact_z_mm": 0.0,
                    "selected_datum_is_contact": True,
                    "alternative_contacts": [{
                        "contact_s_mm": 102.0, "contact_z_mm": 5.0,
                        "support_panel_id": "P", "face_gap_mm": 0.0,
                    }],
                },
            )
        checker = SimpleNamespace(resolve_contact_for_bend=resolve)
        seen_z, seen_orientation = [], []

        def sampled_screen(*_args, **kwargs):
            seen_z.append(kwargs.get("backgauge_contact_z_mm"))
            seen_orientation.append(bool(kwargs.get("reverse_axis", False)))
            return stage_b_result()

        with patch(
            "smbs_core.kinematics2d.backgauge_cad_3d.BackgaugeCAD3D",
            return_value=checker,
        ), patch(
            "smbs_core.kinematics2d.sequence_search_2d.screen_candidate_stage_a",
            return_value=PreliminaryScreeningReport(
                ScreeningDecision.CONTINUE_TO_3D, "STAGE_A_PASSED", "ok",
            ),
        ), patch(
            "smbs_core.kinematics2d.sequence_search_2d.screen_candidate_stage_b",
            side_effect=sampled_screen,
        ):
            valid, reason = validate_partial_planar_prefix(
                session, object(), ("B01", "B02"),
                backgauge_model=model, backgauge_variant_indices=(1, 0),
                loading_orientation_variants=(
                    "REVERSE_MACHINE_Z_180_GAUGE_ALTERNATIVE_1", "NORMAL",
                ),
            )
            invalid, invalid_reason = validate_partial_planar_prefix(
                session, object(), ("B01", "B02"),
                backgauge_model=model, backgauge_variant_indices=(2, 0),
            )
        self.assertTrue(valid, reason)
        self.assertEqual(seen_z, [5.0, 0.0])
        self.assertEqual(seen_orientation, [True, False])
        self.assertEqual(resolved_orientations[:2], [True, False])
        self.assertFalse(invalid)
        self.assertIn("backgauge_variant_no_longer_available", invalid_reason)

    def test_operator_long_reversed_orientation_is_searched_and_selected(self):
        state = DummyState(("B01", "B02"))
        for bend in state.bends.values():
            bend.bend_zone = SimpleNamespace(outer_radius_mm=1.5)
        session = SimpleNamespace(
            fold_state=state, bend_order=("B01", "B02"),
            panel_vertices_xy={"P": [(0, 0), (1, 0), (1, 1)]},
            part_label="",
        )
        model = BackgaugeModel2D(
            width_s_mm=10.0, height_z_mm=10.0,
            positions={}, automatic=True, cad_geometry_enabled=True,
        )
        resolver_orientations, screened_orientations = [], []

        def resolve(_state, _verts, _bid, _model, **kwargs):
            reverse = bool(kwargs.get("reverse_axis", False))
            resolver_orientations.append(reverse)
            stop_s = 12.0 if reverse else 800.0
            return (
                BackgaugePosition2D(stop_s + 5, 0.0), stop_s, "CAD",
                {
                    "contact_s_mm": stop_s, "contact_z_mm": 0.0,
                    "selected_datum_is_contact": True,
                    "alternative_contacts": (),
                },
            )

        def screen(_state, _bid, *_args, **kwargs):
            reverse = bool(kwargs.get("reverse_axis", False))
            screened_orientations.append(reverse)
            if not reverse:
                return PreliminaryScreeningReport(
                    ScreeningDecision.CONTINUE_TO_3D,
                    "STAGE_B_SECTION_REQUIRES_3D",
                    "wrong_loading_side_punch_collision",
                    sampled_phases=("BENDING",),
                    contact_statuses=("INTERFERENCE",),
                )
            return stage_b_result()

        checker = SimpleNamespace(
            station_xm_mm=0.0, second_station_xm_mm=None,
            resolve_contact_for_bend=resolve,
        )
        with patch(
            "smbs_core.kinematics2d.backgauge_cad_3d.BackgaugeCAD3D",
            return_value=checker,
        ), patch(
            "smbs_core.kinematics2d.sequence_search_2d.screen_candidate_stage_a",
            return_value=PreliminaryScreeningReport(
                ScreeningDecision.CONTINUE_TO_3D, "STAGE_A_PASSED", "ok",
            ),
        ), patch(
            "smbs_core.kinematics2d.sequence_search_2d.screen_candidate_stage_b",
            side_effect=screen,
        ), patch(
            "smbs_core.kinematics2d.sequence_search_2d.compute_workpiece_machine_setup",
            side_effect=lambda _state, bid, **kw: WorkpieceMachineSetup(
                bend_id=bid,
                rotation_deg=180.0 if kw.get("reverse_axis") else 0.0,
                flipped=False, shift_xm_mm=0.0,
                bend_length_mm=100.0,
                operator_reach_mm=800.0 if kw.get("reverse_axis") else 12.0,
                rear_reach_mm=12.0 if kw.get("reverse_axis") else 800.0,
            ),
        ):
            report = search_bend_sequences_2d(
                session, object(), backgauge_model=model,
                max_nodes=25, max_search_time_s=3.0,
                max_complete_candidates=1, group_first=False,
            )
        self.assertEqual(report.status, PlanarSearchStatus.SAMPLED_2D_CANDIDATES)
        candidate = report.candidates[0]
        for step in candidate.steps:
            self.assertEqual(step.orientation_variant, "REVERSE_MACHINE_Z_180")
            self.assertEqual(step.setup.operator_reach_mm, 800.0)
            self.assertEqual(step.setup.rear_reach_mm, 12.0)
            self.assertEqual(step.setup.backgauge_s_mm, 17.0)
        self.assertIn(True, screened_orientations)
        self.assertIn(True, resolver_orientations)

    def test_precheck_blocks_punch_interference_before_cad_resolution(self):
        from unittest.mock import Mock
        state = DummyState(("B01", "B02"))
        for bend in state.bends.values():
            bend.bend_zone = SimpleNamespace(outer_radius_mm=1.5)
        session = SimpleNamespace(
            fold_state=state, bend_order=("B01", "B02"),
            panel_vertices_xy={"P": [(0, 0), (1, 0), (1, 1)]},
            part_label="",
        )
        model = BackgaugeModel2D(
            width_s_mm=10.0, height_z_mm=10.0,
            positions={}, automatic=True, cad_geometry_enabled=True,
        )
        resolver = Mock(side_effect=AssertionError("CAD must be skipped"))
        checker = SimpleNamespace(
            station_xm_mm=0.0, second_station_xm_mm=None,
            resolve_contact_for_bend=resolver,
        )
        precheck_args = []

        def tool_collision(*_args, **kwargs):
            precheck_args.append(kwargs)
            return PreliminaryScreeningReport(
                ScreeningDecision.CONTINUE_TO_3D,
                "STAGE_B_SECTION_REQUIRES_3D", "punch_interference",
                sampled_phases=("BENDING",),
                contact_statuses=("INTERFERENCE",),
            )

        with patch(
            "smbs_core.kinematics2d.backgauge_cad_3d.BackgaugeCAD3D",
            return_value=checker,
        ), patch(
            "smbs_core.kinematics2d.sequence_search_2d.screen_candidate_stage_a",
            return_value=PreliminaryScreeningReport(
                ScreeningDecision.CONTINUE_TO_3D, "STAGE_A_PASSED", "ok",
            ),
        ), patch(
            "smbs_core.kinematics2d.sequence_search_2d.screen_candidate_stage_b",
            side_effect=tool_collision,
        ), patch(
            "smbs_core.kinematics2d.sequence_search_2d.compute_workpiece_machine_setup",
            side_effect=lambda _state, bid, **kw: WorkpieceMachineSetup(
                bend_id=bid, rotation_deg=0.0, flipped=False,
                shift_xm_mm=0.0, bend_length_mm=100.0,
            ),
        ):
            report = search_bend_sequences_2d(
                session, object(), backgauge_model=model,
                group_first=False, max_nodes=25, max_search_time_s=3.0,
            )

        resolver.assert_not_called()
        self.assertFalse(report.candidates)
        self.assertTrue(precheck_args)
        self.assertTrue(all(
            p["enforce_rear_frame"] is True for p in precheck_args
        ))
        self.assertGreater(
            dict(report.rejection_counts).get("2D_INTERFERENCE", 0), 0
        )


if __name__ == "__main__":
    unittest.main()
