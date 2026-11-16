"""D6 unresolved bending requests a targeted pose retry without certifying clearance."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from smbs_core.kinematics2d.fast_feasible_search import (
    FastSearchStatus, search_fast_feasible_sequence,
)
from smbs_core.kinematics2d.reverse_aabb_search import (
    ReverseCandidate, ReverseSearchReport,
)


class ReverseOrientationTests(unittest.TestCase):
    def test_automatic_pipeline_ignores_tool_length_without_mutating_setup(self):
        order = ("B09", "B10", "B08")
        state = SimpleNamespace(bends={bid: object() for bid in order})
        session = SimpleNamespace(
            bend_order=order, fold_state=state,
            panel_build=object(), part_label="synthetic",
        )
        tooling = SimpleNamespace(
            punch=SimpleNamespace(tool_id="p", length_mm=100),
            die=SimpleNamespace(tool_id="d", length_mm=100),
            match_active_bend_length=False,
            punch_section_offsets_xm=(0.0, 100.0),
            die_section_offsets_xm=(0.0, 100.0),
        )
        reverse = ReverseSearchReport(
            candidates=(ReverseCandidate(
                sequence=order,
                orientation_by_bend=tuple((bid, False) for bid in order),
                bbox_overlap_score=0.0,
            ),),
            deepest_unbent=tuple(reversed(order)),
            visited_states=8, evaluated_transitions=12, elapsed_s=0.01,
            timed_out=False, geometry_available=True, reason="test",
        )
        received = []
        def native_runner(_session, received_tooling, _seq, **kwargs):
            received.append((received_tooling, kwargs))
            return {
                "status": "UNVERIFIED", "reason": "test",
                "checked_bends": 0, "steps": (),
            }
        def reverse_runner(_session, **kwargs):
            self.assertTrue(kwargs["virtual_tool_length"])
            self.assertTrue(kwargs["tooling"].match_active_bend_length)
            self.assertEqual(kwargs["tooling"].punch_section_offsets_xm, ())
            return reverse

        with patch(
            "smbs_core.kinematics2d.reverse_aabb_search.search_reverse_aabb",
            side_effect=reverse_runner,
        ):
            result = search_fast_feasible_sequence(
                session, tooling, budget_s=120, max_candidates=1,
                native_runner=native_runner,
                sampling_config_factory=lambda *_: SimpleNamespace(
                    max_phase_time_s=18.0,
                ),
            )
        self.assertTrue(received)
        used_tooling, kwargs = received[0]
        self.assertIsNot(used_tooling, tooling)
        self.assertTrue(used_tooling.match_active_bend_length)
        self.assertEqual(used_tooling.die_section_offsets_xm, ())
        self.assertTrue(kwargs["match_active_bend_length"])
        self.assertFalse(tooling.match_active_bend_length)
        self.assertEqual(tooling.punch_section_offsets_xm, (0.0, 100.0))
        self.assertFalse(result.machine_feasible_certified)

    def test_preview_prefers_deepest_native_order_not_original_a_star(self):
        from smbs_core.kinematics2d.fast_feasible_search import (
            FastNativeAttempt, _best_unverified_preview,
        )
        original = ("B09", "B10", "B08", "B07", "B06")
        delayed = ("B09", "B10", "B08", "B06", "B07")
        positions = tuple((bid, False) for bid in original)
        attempts = (
            FastNativeAttempt(original, positions, "UNVERIFIED",
                              "B07 timeout", 3, 40.0),
            FastNativeAttempt(delayed, positions, "UNVERIFIED",
                              "B06 timeout", 4, 30.0),
        )
        selected = _best_unverified_preview(
            original, original, positions, attempts,
        )
        self.assertEqual(selected[0], delayed)
        self.assertEqual(selected[2], 4)
        self.assertEqual(selected[3], "NATIVE_ATTEMPT_UNVERIFIED")

    def test_rejected_order_must_not_be_shown_as_safe_preview(self):
        from smbs_core.kinematics2d.fast_feasible_search import (
            FastNativeAttempt, _best_unverified_preview,
        )
        order = ("B01", "B02")
        positions = (("B01", False), ("B02", False))
        rejected = FastNativeAttempt(
            order, positions, "COLLISION", "punch penetration", 2, 5.0
        )
        self.assertIsNone(_best_unverified_preview(
            order, order, positions, (rejected,),
        ))

    def test_preview_preserves_full_a_star_order_when_no_native_attempt(self):
        from smbs_core.kinematics2d.fast_feasible_search import (
            _best_unverified_preview,
        )
        order = ("B01", "B02")
        poses = (("B01", False), ("B02", True))
        self.assertEqual(
            _best_unverified_preview(order, order, poses, ()),
            (order, poses, 0, "REVERSE_RANKED_UNVERIFIED"),
        )
        self.assertIsNone(
            _best_unverified_preview(order, ("B01",), (), ()),
        )

    def test_existing_operator_order_is_separately_replayed_after_a_star(self):
        order = ("B09", "B10", "B08")
        manual = ("B08", "B10", "B09")
        session = SimpleNamespace(
            bend_order=order,
            fold_state=SimpleNamespace(
                bends={bid: object() for bid in order},
            ),
            panel_build=object(), part_label="test",
        )
        tooling = SimpleNamespace(
            punch=SimpleNamespace(tool_id="p", length_mm=100),
            die=SimpleNamespace(tool_id="d", length_mm=100),
            match_active_bend_length=False,
            punch_section_offsets_xm=(),
            die_section_offsets_xm=(),
        )
        reverse = ReverseSearchReport(
            candidates=(ReverseCandidate(
                sequence=order,
                orientation_by_bend=tuple((bid, False) for bid in order),
                bbox_overlap_score=0.0,
            ),),
            deepest_unbent=tuple(reversed(order)),
            visited_states=1, evaluated_transitions=1,
            elapsed_s=0.01, timed_out=False,
            geometry_available=True, reason="test",
        )
        replayed = []
        def fake_native(_session, _tooling, sequence, **kwargs):
            replayed.append(tuple(sequence))
            return {
                "status": "UNVERIFIED",
                "reason": "noncertifying_native_timeout",
                "checked_bends": 0,
                "steps": (),
            }
        with patch(
            "smbs_core.kinematics2d.reverse_aabb_search.search_reverse_aabb",
            return_value=reverse,
        ):
            result = search_fast_feasible_sequence(
                session, tooling, budget_s=600.0,
                max_candidates=2,
                seed_order=manual,
                seed_orientations={bid: False for bid in order},
                native_runner=fake_native,
                sampling_config_factory=lambda *_: SimpleNamespace(
                    max_phase_time_s=40.0,
                ),
            )
        self.assertEqual(replayed, [order, manual])
        self.assertIsNone(result.sequence)
        self.assertFalse(result.machine_feasible_certified)

    def test_sampled_2d_candidate_is_primary_and_skips_reverse_a_star(self):
        order = ("B01", "B02", "B03")
        desired = ("B03", "B02", "B01")
        session = SimpleNamespace(
            bend_order=order,
            fold_state=SimpleNamespace(bends={bid: object() for bid in order}),
            panel_build=object(), part_label="synthetic",
        )
        tooling = SimpleNamespace(
            punch=SimpleNamespace(tool_id="p", length_mm=100),
            die=SimpleNamespace(tool_id="d", length_mm=100),
            match_active_bend_length=False,
            punch_section_offsets_xm=(),
            die_section_offsets_xm=(),
        )
        sampled_candidate = SimpleNamespace(
            sequence=desired,
            steps=tuple(SimpleNamespace(
                bend_id=bid, orientation_variant="NORMAL",
            ) for bid in desired),
            evidence="SAMPLED_2D_CLEAR_OR_TOUCH", cost=5.0,
            cost_complete=True,
        )
        planar = SimpleNamespace(
            status=SimpleNamespace(value="SAMPLED_2D_CANDIDATES"),
            limit_reason="", deepest_prefix=desired,
            candidates=(sampled_candidate,),
        )
        validated = []
        def native_runner(_session, _tooling, seq, **kwargs):
            validated.append(tuple(seq))
            return {
                "status": "UNVERIFIED",
                "checked_bends": 0, "steps": (),
                "reason": "simulated_native_budget",
            }

        with patch(
            "smbs_core.kinematics2d.reverse_aabb_search.search_reverse_aabb",
            side_effect=AssertionError("A* must not bypass 2D primary"),
        ):
            result = search_fast_feasible_sequence(
                session, tooling, budget_s=120.0,
                planar_runner=lambda *a, **kw: planar,
                native_runner=native_runner,
                max_candidates=1,
                sampling_config_factory=lambda *_: SimpleNamespace(
                    max_phase_time_s=40.0,
                ),
            )
        self.assertEqual(validated, [desired])
        self.assertEqual(result.planar_status, "SAMPLED_2D_CANDIDATES")
        self.assertEqual(result.preview_sequence, desired)
        self.assertIsNone(result.sequence)
        self.assertFalse(result.machine_feasible_certified)

    def test_complete_five_angle_occ_screen_is_reported_as_preliminary(self):
        from smbs_core.kinematics2d.fast_feasible_search import (
            _coarse_five_point_candidate,
        )
        order = ("B01", "B02")
        poses = {"B01": False, "B02": True}
        physical = {
            "validation_mode": "COARSE_OCC_5_BEND_ANGLES",
            "status": "UNVERIFIED",
            "complete_part_order_requested": True,
            "stopped_early": False,
            "checked_bends": 2,
            "requested_bends": list(order),
            "tool_length_mode": "VIRTUAL_MATCH_BEND",
            "punch_fcstd_source": "/fake/punch.FCStd",
            "die_fcstd_source": "/fake/die.FCStd",
            "steps": [
                {"bend_id": "B01", "reverse_axis": False,
                 "status": "COARSE_3D_NO_PROVEN_COLLISION",
                 "is_conditional": False},
                {"bend_id": "B02", "reverse_axis": True,
                 "status": "COARSE_3D_NO_PROVEN_COLLISION",
                 "is_conditional": False},
            ],
        }
        self.assertTrue(_coarse_five_point_candidate(
            physical, order, poses,
        ))
        # Never accept a skipped bend, an alternate pose, or a collision.
        bad = dict(physical, checked_bends=1)
        self.assertFalse(_coarse_five_point_candidate(bad, order, poses))
        bad = dict(physical, steps=[
            physical["steps"][0],
            dict(physical["steps"][1], status="COLLISION"),
        ])
        self.assertFalse(_coarse_five_point_candidate(bad, order, poses))
        bad = dict(physical, steps=[
            physical["steps"][0],
            dict(physical["steps"][1], reverse_axis=False),
        ])
        self.assertFalse(_coarse_five_point_candidate(bad, order, poses))
        bad = dict(physical, validation_mode="FULL_ADAPTIVE_D6")
        self.assertFalse(_coarse_five_point_candidate(bad, order, poses))

    def test_phase_budget_spreads_bounded_search_across_orders(self):
        from smbs_core.kinematics2d.fast_feasible_search import (
            _default_sampling_config,
        )
        cfg = _default_sampling_config(600.0, 10, 40.0)
        self.assertLessEqual(cfg.max_phase_time_s, 40.0)
        self.assertGreaterEqual(cfg.max_phase_time_s, 35.0)
        self.assertGreaterEqual(cfg.max_phase_time_s, 2.0)
        self.assertTrue(cfg.coarse_occ_screen)
        self.assertEqual(cfg.max_evaluated_states_per_phase, 32)

    def test_unverified_middle_bend_is_moved_later_before_pose_retry(self):
        order = ("B09", "B10", "B08", "B07", "B06", "B05")
        state = SimpleNamespace(bends={bid: object() for bid in order})
        session = SimpleNamespace(
            bend_order=order, fold_state=state,
            panel_build=object(), part_label="synthetic",
        )
        tooling = SimpleNamespace(
            punch=SimpleNamespace(tool_id="p", length_mm=100),
            die=SimpleNamespace(tool_id="d", length_mm=100),
            match_active_bend_length=True,
        )
        candidate = ReverseCandidate(
            sequence=order,
            orientation_by_bend=tuple((bid, False) for bid in order),
            bbox_overlap_score=0.0,
        )
        reverse = ReverseSearchReport(
            candidates=(candidate,), deepest_unbent=tuple(reversed(order)),
            visited_states=1, evaluated_transitions=1, elapsed_s=0.01,
            timed_out=False, geometry_available=True, reason="test",
        )
        attempts = []
        def fake_native(*args, **kwargs):
            attempts.append(tuple(args[2]))
            return {
                "status": "UNVERIFIED",
                "reason": "native_3d_not_verified_at:B07",
                "checked_bends": 4,
                "steps": (
                    {"bend_id": "B09", "status": "SAMPLED_3D_CLEAR_NOT_CONTINUOUS"},
                    {"bend_id": "B10", "status": "SAMPLED_3D_CLEAR_NOT_CONTINUOUS"},
                    {"bend_id": "B08", "status": "SAMPLED_3D_CLEAR_NOT_CONTINUOUS"},
                    {"bend_id": "B07", "status": "UNVERIFIED"},
                ),
            }
        with patch(
            "smbs_core.kinematics2d.reverse_aabb_search.search_reverse_aabb",
            return_value=reverse,
        ):
            result = search_fast_feasible_sequence(
                session, tooling, budget_s=600.0, max_candidates=5,
                native_runner=fake_native,
                sampling_config_factory=lambda *_: SimpleNamespace(
                    max_phase_time_s=40.0,
                ),
            )
        self.assertGreaterEqual(len(attempts), 2)
        self.assertEqual(attempts[0], order)
        self.assertEqual(
            attempts[1],
            ("B09", "B10", "B08", "B06", "B05", "B07"),
        )
        self.assertEqual(result.status, FastSearchStatus.NATIVE_REJECTED)
        self.assertIsNone(result.sequence)

    def test_forward_validator_receives_180_degree_variants(self):
        order = ("B09", "B10", "B08")
        state = SimpleNamespace(bends={bid: object() for bid in order})
        session = SimpleNamespace(
            bend_order=order, fold_state=state,
            panel_build=object(), part_label="synthetic",
        )
        tooling = SimpleNamespace(
            punch=SimpleNamespace(tool_id="p", length_mm=100),
            die=SimpleNamespace(tool_id="d", length_mm=100),
            match_active_bend_length=True,
        )
        reverse = ReverseSearchReport(
            candidates=(ReverseCandidate(
                sequence=order,
                orientation_by_bend=tuple((bid, False) for bid in order),
                bbox_overlap_score=0.0,
            ),),
            deepest_unbent=tuple(reversed(order)),
            visited_states=8,
            evaluated_transitions=12,
            elapsed_s=0.01,
            timed_out=False,
            geometry_available=True,
            reason="test",
        )
        checks = []

        def native_runner(*args, **kwargs):
            checks.append((tuple(args[2]), dict(kwargs["reverse_axis_by_bend"])))
            return {
                "status": "UNVERIFIED",
                "reason": "native_3d_not_verified_at:B08",
                "checked_bends": 3,
                "steps": (
                    {"bend_id": "B09", "status": "SAMPLED_3D_CLEAR_NOT_CONTINUOUS"},
                    {"bend_id": "B10", "status": "SAMPLED_3D_CLEAR_NOT_CONTINUOUS"},
                    {"bend_id": "B08", "status": "UNVERIFIED"},
                ),
            }

        with patch(
            "smbs_core.kinematics2d.reverse_aabb_search.search_reverse_aabb",
            return_value=reverse,
        ):
            result = search_fast_feasible_sequence(
                session, tooling,
                budget_s=600.0, max_candidates=5,
                native_runner=native_runner,
                sampling_config_factory=lambda *_: SimpleNamespace(
                    max_phase_time_s=40.0,
                ),
            )
        self.assertTrue(checks)
        self.assertTrue(any(orientation.get("B08") is True
                            for _, orientation in checks))
        self.assertTrue(all(orientation.get("B09") is False
                            for _, orientation in checks))
        self.assertEqual(result.status, FastSearchStatus.NATIVE_REJECTED)
        self.assertIsNone(result.sequence)
        self.assertFalse(result.machine_feasible_certified)
        self.assertEqual(result.budget_s, 600.0)


if __name__ == "__main__":
    unittest.main()
