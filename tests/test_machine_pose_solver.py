from types import SimpleNamespace
from unittest import TestCase

from smbs_core.kinematics2d.backgauge_2d import BackgaugeModel2D
from smbs_core.kinematics2d.fast_bfm_beam_search import search_fast_candidates
from smbs_core.kinematics2d.fast_orientation_25d import four_loading_poses
from smbs_core.kinematics2d.machine_pose_contract import compute_machine_pose
from smbs_core.kinematics2d.machine_pose_solver import (
    evaluate_loading_setup,
    loading_fold_state,
    solve_machine_setups,
    tool_station_midpoints,
)
from smbs_core.kinematics2d.press_cycle import CyclePhase, PressCycleController
from test_machine_pose_contract import sample_bend


class TestMachinePoseSolver(TestCase):
    def test_four_presentations_keep_only_the_correct_punch_face(self):
        fold, outlines = sample_bend()
        setups = [
            evaluate_loading_setup(fold, "B01", outlines, pose)
            for pose in four_loading_poses()
        ]
        self.assertEqual([s.punch_face_ok for s in setups], [True, True, False, False])
        self.assertEqual(fold.angles, {"B01": 0.0})
        for setup in setups[:2]:
            self.assertEqual(setup.frame.placement, compute_machine_pose(
                fold, "B01", phase="APPROACH",
                support_plane_zm=fold.bends["B01"].bend_zone.outer_radius_mm,
                reverse_axis=setup.orientation.reverse_axis,
                rear_panel_vertices_xy=outlines,
            ))

    def test_fast_records_and_preserves_the_pinned_machine_setup(self):
        fold, outlines = sample_bend()
        gauge = BackgaugeModel2D(1.0, 1.0, {}, automatic=True)
        common = dict(
            panel_vertices_xy=outlines, backgauge_model=gauge,
            screen_tool_center_axis=False, limit_seconds=5.0,
        )
        report = search_fast_candidates(fold, **common)
        candidate = report.candidates[0]
        self.assertEqual(candidate.orientations(), {"B01": False})
        setup = dict(candidate.machine_setups)["B01"]
        self.assertEqual(setup.gauge_status, "REAR_EDGE_CANDIDATE_2D")
        self.assertEqual(setup.gauge_edges[0].panel_id, "P0")
        self.assertAlmostEqual(setup.gauge_edges[0].distance_mm, 32.0)
        self.assertEqual(candidate.to_dict()["machine_setup_by_operation"]["B01"][
            "verification_3d"], "NOT_CHECKED")

        pinned = search_fast_candidates(fold, fixed_orientations={"B01": True}, **common)
        candidate = pinned.candidates[0]
        self.assertEqual(candidate.orientations(), {"B01": True})
        self.assertGreater(candidate.gauge_penalty, 0.0)

    def test_fast_prefers_less_sheet_depth_inside_press(self):
        fold, outlines = sample_bend()
        outlines["P0"] = ((0., -85.), (50., -85.), (50., -1.2), (0., -1.2))
        report = search_fast_candidates(
            fold, panel_vertices_xy=outlines, screen_tool_center_axis=False,
        )
        candidate = report.candidates[0]
        setup = dict(candidate.machine_setups)["B01"]
        self.assertTrue(candidate.orientations()["B01"])
        self.assertGreater(setup.operator_depth_mm, setup.rear_depth_mm)
        self.assertEqual(candidate.ergonomic_penalty, 0.0)

    def test_cycle_uses_fast_rear_frame_for_selected_step(self):
        fold, outlines = sample_bend()
        outlines = {
            panel: tuple((x, -y) for x, y in outline)
            for panel, outline in outlines.items()
        }
        cycle = PressCycleController.__new__(PressCycleController)
        cycle.sequence = SimpleNamespace(
            fold_state=fold, active_index=0, order=("B01",), active_bend_id="B01",
        )
        cycle.phase = CyclePhase.IDLE
        cycle.transfer_fault = None
        cycle.coupled_kinematics = None
        cycle.loading_orientation_by_bend = {"B01": True}
        raw = cycle.current_workpiece_placement_3d()
        cycle.loading_rear_frame_for_step = lambda index: outlines
        machine = cycle.current_workpiece_placement_3d()
        fast = evaluate_loading_setup(fold, "B01", outlines, four_loading_poses()[1])
        self.assertNotEqual(raw.rotation, machine.rotation)
        self.assertEqual(machine.rotation, fast.frame.placement.rotation)

    def test_nonzero_tool_station_moves_axis_without_using_gaps(self):
        fold, outlines = sample_bend()
        stations = tool_station_midpoints(50.0, ((5, 60), (70, 100)), ((0, 80),))
        self.assertEqual(stations, (30.0, 32.5, 35.0))
        self.assertEqual(tool_station_midpoints(50.0, ((0, 40),), ((0, 80),)), ())
        setups = solve_machine_setups(fold, "B01", outlines, midpoint_candidates=stations)
        self.assertEqual(len(setups), 6)
        self.assertAlmostEqual(setups[0].frame.placement.apply((25., 0., 0.))[0], 30.)

    def test_loading_history_preserves_final_angle_if_operation_input_is_unordered(self):
        fold, _ = sample_bend()
        operations = (
            SimpleNamespace(bend_id="B01", target_angle_deg=90.0),
            SimpleNamespace(bend_id="B01", target_angle_deg=35.0),
        )
        self.assertEqual(loading_fold_state(fold, operations, 0b10).angles["B01"], 35.0)
        self.assertEqual(loading_fold_state(fold, operations, 0b11).angles["B01"], 90.0)
        self.assertEqual(fold.angles["B01"], 0.0)
