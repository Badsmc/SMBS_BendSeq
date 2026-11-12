"""Headless safety regressions for native panel attribution & onset search."""
from __future__ import annotations

import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from smbs_core.kinematics2d.motion_trajectory_3d import (
    AdaptiveSamplingConfig, AdaptiveTrajectoryValidator3D,
    MotionState3D, TrajectoryEvidenceStatus,
)
from smbs_core.kinematics2d.native_collision_3d import CollisionClass
from smbs_core.kinematics2d.native_collision_attribution import component_hits
from smbs_core.kinematics2d.manual_sequence_replay import off_section_prior_fold_risk
from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from smbs_core.kinematics2d.panel_kinematics import FoldState


def mock_state(u, phase="BENDING", threshold=0.4):
    collides = u >= threshold
    status = (CollisionClass.PENETRATION if collides else CollisionClass.CLEAR)
    punch = SimpleNamespace(
        status=status, distance_mm=0.0 if collides else 3.0,
        overlap_volume_mm3=400.0 if collides else 0.0,
    )
    die = SimpleNamespace(
        status=CollisionClass.CLEAR, distance_mm=3.0,
        overlap_volume_mm3=0.0,
    )
    return MotionState3D(
        phase=phase, active_bend_id="B03",
        signed_angle_deg=90.0*u, path_u=u, ram_z_mm=1.5 - 5*u,
        support_plane_zm=0.0,
        workpiece_placement=None, completed_angles={"B07": 90.0},
        collision_report=SimpleNamespace(
            status=status, is_safe=not collides,
            punch_result=punch, die_result=die,
        ),
        is_safe=not collides,
    )


class NativeOnsetTests(unittest.TestCase):
    def make_validator(self, config):
        obj = AdaptiveTrajectoryValidator3D(
            fold_state=None, panel_build=None, punch_base=None,
            die=SimpleNamespace(v_opening_mm=12.0,
                                effective_v_opening_mm=12.0),
            config=config,
        )

        def make_snapshot(**kwargs):
            obj.total_states_evaluated += 1
            obj.total_collision_queries += 1
            obj.state_evaluation_requests += 1
            return mock_state(kwargs["path_u"], kwargs["phase"])
        obj.create_state_snapshot = make_snapshot
        return obj

    def test_endpoint_hit_is_refined_not_assumed_at_90_degrees(self):
        validator = self.make_validator(AdaptiveSamplingConfig(
            onset_refinement_max_samples=6,
            onset_refinement_max_s=30,
        ))
        result = validator.validate_phase(
            "BENDING", "B03", {"B07": 90.0},
            lambda u: (90*u, 1.5-5*u, 0.0),
        )
        self.assertEqual(
            result.evidence_status,
            TrajectoryEvidenceStatus.DISCRETE_SAMPLES_REJECTED,
        )
        bracket = result.first_collision_bracket
        self.assertIsNotNone(bracket)
        self.assertLess(
            bracket["last_sampled_nonblocking_angle_deg"], 36.0,
        )
        self.assertGreaterEqual(
            bracket["first_sampled_blocking_angle_deg"], 36.0,
        )
        self.assertEqual(bracket["refinement_samples"], 6)
        self.assertEqual(len(result.states), 8)
        self.assertTrue(result.uninspected_intervals)
        self.assertFalse(bracket["continuous_interval_certified"])
        self.assertTrue(bracket["nonmonotone_collision_not_excluded"])

    def test_first_blocking_state_remains_proven_geometric_collision(self):
        validator = self.make_validator(AdaptiveSamplingConfig(
            onset_refinement_max_samples=4,
        ))
        r = validator.validate_phase(
            "BENDING", "B03", {"B07": 90.0},
            lambda u: (90*u, 1.5-5*u, 0.0),
        )
        first = next(st for st in r.states if not st.is_safe)
        self.assertGreaterEqual(first.signed_angle_deg, 36.0)
        self.assertLess(first.signed_angle_deg, 90.0)
        self.assertEqual(first.collision_report.punch_result.overlap_volume_mm3,
                         400.0)

    def test_zero_budget_returns_uninspected_full_interval(self):
        validator = self.make_validator(AdaptiveSamplingConfig(
            onset_refinement_max_samples=0,
        ))
        r = validator.validate_phase(
            "BENDING", "B03", {"B07": 90.0},
            lambda u: (90*u, 1.5-5*u, 0.0),
        )
        self.assertEqual(len(r.states), 2)
        self.assertEqual(r.first_collision_bracket["refinement_samples"], 0)
        self.assertEqual(r.uninspected_intervals, [(0.0, 1.0)])


class ComponentAttributionTests(unittest.TestCase):
    def test_actual_positive_brep_hit_attributed_to_P09_not_axis_guess(self):
        components = [
            ("panel:P05", SimpleNamespace(BoundBox="P05")),
            ("panel:P09", SimpleNamespace(BoundBox="P09")),
            ("bend_zone:B07", SimpleNamespace(BoundBox="B07")),
        ]
        calls = []
        def check(component, tool, tool_label):
            calls.append(component.BoundBox)
            hit = component.BoundBox == "P09"
            return SimpleNamespace(
                status=(CollisionClass.PENETRATION if hit else CollisionClass.CLEAR),
                overlap_volume_mm3=4120.24 if hit else 0.0,
                overlap_bbox_xyz_mm=(
                    {"xmin": -200., "xmax": 200.,
                     "ymin": -13., "ymax": -7.,
                     "zmin": 10., "zmax": 16.} if hit else None
                ),
                reason="positive_common_volume" if hit else "clear",
            )
        result = component_hits(
            components, SimpleNamespace(BoundBox="tool"),
            check_pair=check, get_gap=lambda a,b: 0.0,
        )
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["checked_components"], 3)
        self.assertEqual(calls, ["P05", "P09", "B07"])
        self.assertEqual(
            result["positive_overlap_components"][0]["component_id"],
            "panel:P09",
        )
        self.assertAlmostEqual(
            result["positive_overlap_components"][0]["overlap_volume_mm3"],
            4120.24,
        )

    def test_missing_or_failed_kernel_evidence_cannot_be_diagnosed_as_clear(self):
        fake = [("panel:P09", SimpleNamespace(BoundBox=1))]
        result = component_hits(
            fake, SimpleNamespace(BoundBox=2),
            check_pair=lambda a,b,tool_label: SimpleNamespace(
                status=CollisionClass.INVALID_GEOMETRY,
            ),
            get_gap=lambda a,b: 0,
        )
        self.assertEqual(result["status"], "PARTIAL")
        self.assertFalse(result["positive_overlap_components"])
        self.assertTrue(result["errors"])

    def test_off_plane_prior_fold_is_flagged_before_expensive_3d(self):
        graph = PanelGraph(("P05", "P08", "P09"), (
            GraphEdge("B03", "P05", "P08", 90,
                      ((0, 0, 0), (0, 100, 0))),
            GraphEdge("B07", "P08", "P09", 90,
                      ((10, 0, 0), (10, 100, 0))),
        ))
        state = FoldState(graph, "P05")
        risk = off_section_prior_fold_risk(state, ("B07",))
        self.assertEqual(risk["status"],
                         "UNVERIFIED_OUT_OF_SECTION_PANELS")
        self.assertIn("P09", risk["previously_folded_panels"])
        self.assertTrue(risk["native_full_span_3d_required"])
        self.assertFalse(
            off_section_prior_fold_risk(state, ())["native_full_span_3d_required"]
        )

    def test_3d_preview_reconstructs_native_pose_not_ergonomic_reversal(self):
        from pathlib import Path
        gui = (Path(__file__).resolve().parents[1]
               / "freecad_smbs/commands/pressbrake_2d.py"
               ).read_text(encoding="utf-8")
        self.assertIn('"SMBS_ShowNativeCollision"', gui)
        self.assertIn('def _show_first_collision_in_3d(self):', gui)
        self.assertIn('inspection_state = FoldState(', gui)
        self.assertIn('current_placement = PlacementTransform3D(', gui)
        self.assertIn('obstacle.get("intersection_bbox_xyz_mm")', gui)
        self.assertIn('"MANUAL_FLAT_BEND_CLICK"', gui)


if __name__ == "__main__":
    unittest.main()
