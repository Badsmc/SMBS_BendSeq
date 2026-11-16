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
