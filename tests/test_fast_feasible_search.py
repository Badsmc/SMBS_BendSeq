"""Headless contract tests for fast hierarchical bend-sequence search.

The real OCC integration is verified separately inside FreeCAD. These tests
guard the never-promote-unverified evidence and no-mutation invariants.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
import unittest

from smbs_core.kinematics2d.fast_feasible_search import (
    FastSearchStatus, _native_result_is_qualified,
    search_fast_feasible_sequence,
)


ORDER = ("B01", "B02", "B03")


@dataclass
class Candidate:
    sequence: tuple
    steps: tuple
    evidence: str = "SAMPLED_2D_CLEAR_OR_TOUCH"
    cost: float = 0.0
    cost_complete: bool = True


def candidate(order, reverse=()):
    return Candidate(tuple(order), tuple(
        SimpleNamespace(
            bend_id=bid,
            orientation_variant=(
                "REVERSE_MACHINE_Z_180" if bid in reverse else "NORMAL"
            ),
        ) for bid in order
    ))


def planar(candidates):
    return SimpleNamespace(
        candidates=tuple(candidates),
        status=SimpleNamespace(value="SAMPLED_2D_CANDIDATES_NOT_3D_VALIDATED"),
        limit_reason="", deepest_prefix=ORDER,
    )


def setup():
    fs = SimpleNamespace(bends=dict.fromkeys(ORDER), angles=dict.fromkeys(ORDER, 0.0))
    sess = SimpleNamespace(
        bend_order=list(ORDER), fold_state=fs, panel_build=object(),
        part_label="DEMO",
    )
    tools = SimpleNamespace(punch=object(), die=object())
    return sess, tools


def native_clear(seq):
    return {
        "status": "SAMPLED_3D_CLEAR_NOT_CONTINUOUS",
        "reason": "sampled only",
        "requested_bends": list(seq),
        "complete_part_order_requested": True,
        "stopped_early": False,
        "checked_bends": len(seq),
        "validation_mode": "FULL_ADAPTIVE_D6",
        "tool_length_mode": "SELECTED_FCSTD_REAL_LENGTH",
        "punch_fcstd_source": "/tools/punch.FCStd",
        "die_fcstd_source": "/tools/die.FCStd",
        "steps": [
            {
                "bend_id": b,
                "status": "SAMPLED_3D_CLEAR_NOT_CONTINUOUS",
                "punch_die_pair": {"status": "SAMPLED_CLEAR"},
                "retract": {"status": "SAMPLED_CLEAR"},
            } for b in seq
        ],
    }


class TestFastFeasibleSearch(unittest.TestCase):
    def test_native_qualification_guards_each_scope(self):
        good = native_clear(ORDER)
        self.assertTrue(_native_result_is_qualified(good, ORDER))
        for key, value in (
            ("validation_mode", "COARSE_OCC_5_BEND_ANGLES"),
            ("tool_length_mode", "VIRTUAL_MATCH_BEND"),
            ("stopped_early", True),
            ("checked_bends", 2),
            ("punch_fcstd_source", None),
        ):
            bad = {**good, key: value}
            with self.subTest(key=key):
                self.assertFalse(_native_result_is_qualified(bad, ORDER))
        bad = {**good, "steps": [dict(step) for step in good["steps"]]}
        bad["steps"][1]["retract"] = {"status": "UNVERIFIED"}
        self.assertFalse(_native_result_is_qualified(bad, ORDER))
        bad = {**good, "steps": [dict(step) for step in good["steps"]]}
        bad["steps"][1]["punch_die_pair"] = None
        self.assertFalse(_native_result_is_qualified(bad, ORDER))

    def test_rejected_first_candidate_backtracks_and_second_succeeds(self):
        sess, tools = setup()
        c1 = candidate(ORDER, reverse=("B02",))
        c2 = candidate(("B02", "B01", "B03"), reverse=("B01",))
        inspected = []

        def planar_runner(*args, **kwargs):
            self.assertFalse(kwargs["match_active_bend_length"])
            self.assertFalse(kwargs["allow_end_zone_collision"])
            return planar([c1, c2])

        def native_runner(native_session, _tools, order, **kwargs):
            inspected.append((tuple(order), kwargs["reverse_axis_by_bend"]))
            self.assertIs(native_session.fold_state, sess.fold_state)
            self.assertFalse(kwargs["config"].coarse_occ_screen)
            self.assertIsNotNone(kwargs["sequence_geometry_cache"])
            if len(inspected) == 1:
                return {
                    "status": "COLLISION", "reason": "DIE",
                    "checked_bends": 1,
                }
            return native_clear(order)

        result = search_fast_feasible_sequence(
            sess, tools, planar_runner=planar_runner,
            native_runner=native_runner,
        )
        self.assertEqual(result.status, FastSearchStatus.SAMPLED_TOOL_CLEAR)
        self.assertEqual(result.sequence, c2.sequence)
        self.assertTrue(dict(result.orientation_by_bend)["B01"])
        self.assertFalse(result.machine_feasible_certified)
        self.assertFalse(result.to_dict()["continuous_motion_certified"])
        self.assertEqual(len(result.attempts), 2)
        # The original operator session/order/playback state is untouched.
        self.assertEqual(tuple(sess.bend_order), ORDER)
        self.assertEqual(sess.fold_state.angles, dict.fromkeys(ORDER, 0.0))

    def test_partial_or_virtual_native_cannot_be_reported_as_feasible(self):
        sess, tools = setup()
        for invalid in (
            {"validation_mode": "COARSE_OCC_5_BEND_ANGLES"},
            {"checked_bends": 2, "stopped_early": True},
            {"tool_length_mode": "VIRTUAL_MATCH_BEND"},
        ):
            report = {**native_clear(ORDER), **invalid}
            result = search_fast_feasible_sequence(
                sess, tools, planar_runner=lambda *a, **k: planar([candidate(ORDER)]),
                native_runner=lambda *a, **k: report,
            )
            self.assertEqual(result.status, FastSearchStatus.NATIVE_REJECTED)
            self.assertIsNone(result.sequence)

    def test_no_2d_candidate_does_not_assert_impossibility(self):
        sess, tools = setup()
        result = search_fast_feasible_sequence(
            sess, tools,
            planar_runner=lambda *a, **k: planar(()),
            native_runner=lambda *a, **k: self.fail("native should not run"),
        )
        self.assertEqual(result.status, FastSearchStatus.NO_2D_CANDIDATES)
        self.assertIsNone(result.sequence)

    def test_soft_budget_does_not_start_native_when_planar_overruns(self):
        sess, tools = setup()

        class Clock:
            elapsed = 0.0
            def __call__(self):
                return self.elapsed

        clock = Clock()
        def overbudget(*a, **k):
            clock.elapsed = 61.0
            return planar([candidate(ORDER)])
        result = search_fast_feasible_sequence(
            sess, tools, budget_s=60.0, clock=clock,
            planar_runner=overbudget,
            native_runner=lambda *a, **k: self.fail("native must not run"),
        )
        self.assertEqual(result.status, FastSearchStatus.TIME_BUDGET_REACHED)
        self.assertTrue(result.deadline_exceeded)
        self.assertEqual(len(result.attempts), 0)

    def test_duplicate_and_invalid_orders_never_reach_native(self):
        sess, tools = setup()
        unique = candidate(ORDER)
        wrong = candidate(("B01", "B02", "unknown"))
        calls = []
        result = search_fast_feasible_sequence(
            sess, tools,
            planar_runner=lambda *a, **k: planar([unique, unique, wrong]),
            native_runner=lambda *a, **k: (
                calls.append(1) or {"status": "COLLISION", "checked_bends": 1}
            ),
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(result.planar_candidates), 1)


if __name__ == "__main__":
    unittest.main()
