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
from smbs_core.kinematics2d.native_collision_attribution import (
    component_hits, collision_preview_boxes,
)
from smbs_core.kinematics2d.manual_sequence_replay import (
    off_section_prior_fold_risk, evaluate_manual_planar_order,
    crosscheck_manual_planar_native_evidence,
)
from smbs_core.kinematics2d.preliminary_screening import (
    PreliminaryScreeningReport, ScreeningDecision,
)
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


class ManualEvidenceCrosscheckTests(unittest.TestCase):
    def test_reversed_B03_planar_interference_vs_sampled_native_clear(self):
        planar = {"steps": [
            {"bend_id": "B07", "evidence": "SAMPLED_2D_CLEAR_OR_TOUCH",
             "reverse_axis": True},
            {"bend_id": "B03", "evidence": "2D_INTERFERENCE",
             "reason": "BENDING:P09:proper_material_boundary_crossing",
             "reverse_axis": True},
        ]}
        native = {"steps": [
            {"bend_id": "B07",
             "status": "SAMPLED_3D_CLEAR_NOT_CONTINUOUS",
             "reverse_axis": True},
            {"bend_id": "B03",
             "status": "SAMPLED_3D_CLEAR_NOT_CONTINUOUS",
             "reverse_axis": True},
            {"bend_id": "B05", "status": "UNVERIFIED",
             "reverse_axis": True},
        ]}
        conflicts = crosscheck_manual_planar_native_evidence(planar, native)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["bend_id"], "B03")
        self.assertEqual(conflicts[0]["kind"],
                         "2D_SECTION_INTERFERENCE_NOT_REPRODUCED_IN_SAMPLED_NATIVE_3D")
        self.assertTrue(conflicts[0]["reverse_axis_2d"])
        self.assertTrue(conflicts[0]["reverse_axis_3d"])
        self.assertFalse(conflicts[0]["continuous_clearance_certified"])

    def test_native_collision_missed_by_2d_is_preserved(self):
        planar = {"steps": [{"bend_id": "B03",
                             "evidence": "SAMPLED_2D_CLEAR_OR_TOUCH"}]}
        native = {"steps": [{"bend_id": "B03", "status": "COLLISION"}]}
        conflicts = crosscheck_manual_planar_native_evidence(planar, native)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["kind"],
                         "NATIVE_COLLISION_NOT_DETECTED_BY_SAMPLED_2D")


class ManualPlanarOrientationTests(unittest.TestCase):
    def test_explicit_180_orientation_is_shared_by_stage_b_and_setup(self):
        graph = PanelGraph(("P01", "P02", "P03"), (
            GraphEdge("B01", "P01", "P02", 90.0,
                      ((0, 0, 0), (0, 100, 0))),
            GraphEdge("B02", "P02", "P03", 90.0,
                      ((10, 0, 0), (10, 100, 0))),
        ))
        state = FoldState(graph, "P01")
        session = SimpleNamespace(
            fold_state=state, canonical_order=("B01", "B02"),
            panel_vertices_xy={}, part_label="fixture",
        )
        setup = SimpleNamespace(to_dict=lambda: {})
        screener = PreliminaryScreeningReport(
            ScreeningDecision.CONTINUE_TO_3D, "STAGE_B", "sample_clear"
        )
        def fake_score(prefix, _setups, _weights):
            return SimpleNamespace(
                total=0.0,
                steps=[SimpleNamespace(to_dict=lambda: {}) for _ in prefix],
                to_dict=lambda: {"total": 0.0},
            )
        scope = "smbs_core.kinematics2d.manual_sequence_replay."
        with patch(scope + "_setup_with_scored_tool_lengths",
                   return_value=setup), patch(
            scope + "compute_workpiece_machine_setup", return_value=setup,
        ) as placements, patch(
            scope + "screen_candidate_stage_a", return_value=screener,
        ), patch(
            scope + "screen_candidate_stage_b", return_value=screener,
        ) as stage_b, patch(
            scope + "_classify_step",
            return_value="SAMPLED_2D_CLEAR_OR_TOUCH",
        ), patch(scope + "score_sequence", side_effect=fake_score):
            result = evaluate_manual_planar_order(
                session, SimpleNamespace(), ("B01", "B02"),
                reverse_axis_by_bend={"B01": True, "B02": False},
            )
        self.assertEqual(result["reverse_axis_by_bend"],
                         {"B01": True, "B02": False})
        self.assertEqual([s["reverse_axis"] for s in result["steps"]],
                         [True, False])
        self.assertTrue(placements.call_args_list[0].kwargs["reverse_axis"])
        self.assertFalse(placements.call_args_list[1].kwargs["reverse_axis"])
        self.assertTrue(stage_b.call_args_list[0].kwargs["reverse_axis"])
        self.assertFalse(stage_b.call_args_list[1].kwargs["reverse_axis"])
        self.assertEqual(state.angles, {"B01": 0.0, "B02": 0.0})


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
    def test_complete_native_attribution_focuses_real_panel_collision_bbox(self):
        full = {"xmin": -248.75, "xmax": 248.75,
                "ymin": -13.765, "ymax": 0.0, "zmin": -1.4, "zmax": 14.66}
        panel = {"xmin": -248.55, "xmax": 219.75,
                 "ymin": -13.765, "ymax": -13.363,
                 "zmin": 14.315, "zmax": 14.658}
        blocker = {
            "intersection_bbox_xyz_mm": {"punch": full},
            "component_attribution": {
                "status": "ATTRIBUTED",
                "complete": True,
                "hits": [{
                    "tool": "PUNCH", "component_id": "panel:P09",
                    "overlap_bbox_xyz_mm": panel,
                }],
            },
        }
        self.assertEqual(collision_preview_boxes(blocker), [{
            "tool": "PUNCH", "component_id": "panel:P09",
            "bbox": panel, "source": "NATIVE_COMPONENT_COMMON_AABB",
        }])
        blocker["component_attribution"]["complete"] = False
        self.assertEqual(collision_preview_boxes(blocker), [{
            "tool": "PUNCH", "component_id": "whole_sheet",
            "bbox": full, "source": "NATIVE_FULL_SHEET_COMMON_AABB",
        }])

    def test_missing_one_positive_component_bbox_falls_back_as_group(self):
        full = {"xmin": -1, "xmax": 1}
        attrs = {
            "status": "ATTRIBUTED", "complete": True,
            "hits": [
                {"tool": "PUNCH", "component_id": "panel:P09",
                 "overlap_bbox_xyz_mm": full},
                {"tool": "PUNCH", "component_id": "bend_zone:B07",
                 "overlap_bbox_xyz_mm": None},
            ],
        }
        self.assertEqual(collision_preview_boxes({
            "intersection_bbox_xyz_mm": {"punch": full},
            "component_attribution": attrs,
        })[0]["component_id"], "whole_sheet")

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

    def test_exact_component_rebuild_uses_isolated_bend_state_and_native_xyz(self):
        """End-to-end attribution fixture: P09 only, not a label inferred from bbox."""
        import sys
        from pathlib import Path
        from types import ModuleType
        from smbs_core.kinematics2d.native_collision_attribution import (
            attribute_native_collision,
        )
        from smbs_core.kinematics2d.panel_kinematics import RigidTransform

        class Shape:
            def __init__(self, name):
                self.name = name
                self.BoundBox = name
            def copy(self):
                return Shape(self.name)
            def transformShape(self, matrix, copy=False):
                return self

        class Zone:
            thickness_mm = 1.5
            def moving_transform(self, angle_deg, moving_left=True):
                return RigidTransform()

        graph = PanelGraph(("P05", "P08", "P09"), (
            GraphEdge("B03", "P05", "P08", 90,
                      ((0, 0, 0), (0, 100, 0)), Zone()),
            GraphEdge("B07", "P08", "P09", 90,
                      ((10, 0, 0), (10, 100, 0)), Zone()),
        ))
        state = FoldState(graph, "P05")
        baseline = dict(state.angles)
        session = SimpleNamespace(
            fold_state=state,
            panel_build=SimpleNamespace(
                panels=[SimpleNamespace(panel_id=p) for p in graph.panels],
                panel_faces=[Shape(p) for p in graph.panels],
            ),
        )
        placement = SimpleNamespace(
            rotation=((1.,0.,0.),(0.,1.,0.),(0.,0.,1.)),
            translation=(0.,0.,0.),
        )
        hit = SimpleNamespace(
            active_bend_id="B03", phase="BENDING",
            signed_angle_deg=40.0, ram_z_mm=-1.0,
            completed_angles={"B07": 90.0},
            workpiece_placement=placement,
            collision_report=SimpleNamespace(
                punch_result=SimpleNamespace(
                    status=CollisionClass.PENETRATION,
                ),
                die_result=SimpleNamespace(status=CollisionClass.CLEAR),
            ),
        )
        app = ModuleType("FreeCAD")
        app.Matrix = type("Matrix", (), {})
        loader = ModuleType("smbs_core.kinematics2d.native_tool_loader")
        loader.load_native_punch = lambda *args, **kwargs: SimpleNamespace(
            machine_shape=Shape("PUNCH")
        )
        loader.load_native_die = lambda *args, **kwargs: SimpleNamespace(
            machine_shape=Shape("DIE")
        )
        loader.set_punch_ram_z = lambda item,z: item
        build = ModuleType("smbs_core.kinematics2d.native_bend_solid_3d")
        build._source_panel_prism = lambda pid, face, thick, app: Shape(pid)
        build._apply_rigid_transform = lambda solid,tf,app: solid
        build.build_bend_zone_solid = lambda st, bend_id, segments: (
            SimpleNamespace(shape=Shape(bend_id))
        )
        def clearance(shape, tool_shape, tool_label):
            is_hit = shape.name == "P09"
            return SimpleNamespace(
                status=(CollisionClass.PENETRATION if is_hit
                        else CollisionClass.CLEAR),
                overlap_volume_mm3=350.0 if is_hit else 0.0,
                overlap_bbox_xyz_mm=None,
                reason="test native component common",
            )
        with patch.dict(sys.modules, {
            "FreeCAD": app,
            "smbs_core.kinematics2d.native_tool_loader": loader,
            "smbs_core.kinematics2d.native_bend_solid_3d": build,
        }), patch(
            "smbs_core.kinematics2d.native_collision_3d.check_pair_clearance",
            side_effect=clearance,
        ), patch(
            "smbs_core.kinematics2d.native_collision_3d.compute_aabb_gap",
            return_value=0.0,
        ):
            report = attribute_native_collision(
                session, hit, punch_source=Path("selected-punch.FCStd"),
                die_source=Path("selected-die.FCStd"),
                virtual_length=True, bend_length_mm=497.5,
                tooling=SimpleNamespace(
                    punch_section_offsets_xm=(),
                    die_section_offsets_xm=(),
                ),
            )
        self.assertTrue(report["complete"], report)
        self.assertEqual(report["status"], "ATTRIBUTED")
        self.assertEqual([x["component_id"] for x in report["hits"]],
                         ["panel:P09"])
        self.assertEqual(state.angles, baseline)

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
        self.assertIn('collision_preview_boxes(obstacle)', gui)
        self.assertIn('"MANUAL_FLAT_BEND_CLICK"', gui)


if __name__ == "__main__":
    unittest.main()
