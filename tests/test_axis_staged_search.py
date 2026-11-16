"""Pure-Python regressions for experimental axis-staged 2D planning."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.axis_staged_search import (
    AxisStageConfig, search_axis_staged, _bbox_intersects,
)
from smbs_core.kinematics2d.operation_cost import WorkpieceMachineSetup
from smbs_core.kinematics2d.sequence_search_2d import PlanarStep


class FakeState:
    def __init__(self):
        self.bends = {
            "A1": SimpleNamespace(axis_xyz=((0, 0, 0), (100, 0, 0))),
            "A2": SimpleNamespace(axis_xyz=((0, 10, 0), (100, 10, 0))),
            "B1": SimpleNamespace(axis_xyz=((0, 0, 0), (0, 100, 0))),
            "B2": SimpleNamespace(axis_xyz=((10, 0, 0), (10, 100, 0))),
        }
        self.angles = {}
        self.transforms = {}


def fixture():
    state = FakeState()
    session = SimpleNamespace(
        bend_order=tuple(state.bends),
        fold_state=state, panel_vertices_xy={"P01": [(0.0, 0.0)]},
        panel_build=object(), part_label="test",
    )
    tooling = SimpleNamespace(punch=object(), die=object())
    return session, tooling


def mock_step(prefix, bid, reverse, detailed):
    setup = WorkpieceMachineSetup(
        bend_id=bid, rotation_deg=180.0 if reverse else 0.0,
        flipped=bool(reverse), shift_xm_mm=0.0,
        bend_length_mm=100.0, is_complete=True,
    )
    blocked = (
        (bid == "A2" and "B1" in prefix and not reverse)
        or (bid == "B2" and "A1" not in prefix and not reverse)
    )
    return PlanarStep(
        bid,
        "2D_INTERFERENCE" if blocked else "SAMPLED_2D_CLEAR_OR_TOUCH",
        "sampled_polygon_overlap" if blocked else "sampled_no_overlap",
        ("APPROACH", "BENDING", "BOTTOM", "RETRACT"),
        ("CLEAR",), setup=setup,
        orientation_variant="REVERSE_MACHINE_Z_180" if reverse else "NORMAL",
    )


def config(**kwargs):
    defaults = dict(
        axis_beam=10, axis_plans_per_group=5, merge_beam=40,
        axis_combinations=20, full_candidates=40,
        finalists=5, planar_budget_s=20.0,
        max_step_evaluations=5000,
    )
    defaults.update(kwargs)
    return AxisStageConfig(**defaults)


class AxisStagedTests(unittest.TestCase):
    def test_separate_axes_and_global_rescreen_with_reverse(self):
        session, tooling = fixture()
        calls = []
        def checker(prefix, bid, reverse, detailed):
            calls.append((prefix, bid, reverse, detailed))
            return mock_step(prefix, bid, reverse, detailed)
        result = search_axis_staged(
            session, tooling, config=config(),
            step_checker=checker,
            bbox_checker=lambda c: {
                "status": "BBOX_PROXY_RANKING_ONLY",
                "overlapping_pairs": 1,
            },
        )
        self.assertEqual(len(result.groups), 2)
        self.assertTrue(all(result.axis_plans))
        self.assertTrue(result.candidates)
        self.assertTrue(all(len(c.sequence) == 4 for c in result.candidates))
        self.assertTrue(all(set(c.sequence) == set(session.bend_order)
                            for c in result.candidates))
        # The merge must not trust local axis plans as full-part clearance:
        # it evaluates later steps with predecessors from the OTHER axis.
        self.assertTrue(any(
            "B1" in prefix and bid.startswith("A")
            for prefix, bid, _, _ in calls
        ))
        self.assertTrue(any(detailed for _, _, _, detailed in calls))
        self.assertTrue(any(
            reverse for _, _, reverse, _ in calls
        ))
        # Both axis plans and complete merged orders are serializable.
        diagnostic = result.to_dict()
        self.assertEqual(diagnostic["schema_version"],
                         "smbs.axis_staged_experiment.v1")
        self.assertFalse(diagnostic["physically_certified"])
        self.assertLessEqual(len(result.finalists), 5)
        self.assertEqual(len(result.bbox_results), len(result.candidates))
        self.assertEqual(len(result.as_planar_report().candidates),
                         len(result.finalists))

    def test_individual_and_predecessor_cost_are_applied(self):
        session, tooling = fixture()
        result = search_axis_staged(
            session, tooling, config=config(),
            step_checker=mock_step,
            bbox_checker=lambda c: {
                "status": "BBOX_PROXY_RANKING_ONLY",
                "overlapping_pairs": 0,
            },
            per_bend_cost={"A1": 3.0},
            pair_cost={("A1", "A2"): 11.0},
            axis_change_cost=2.5,
        )
        self.assertTrue(result.candidates)
        with_pair = [
            c for c in result.candidates
            if any(c.sequence[i:i + 2] == ("A1", "A2")
                   for i in range(3))
        ]
        self.assertTrue(with_pair)
        self.assertTrue(all(c.cost >= 14.0 for c in with_pair))
        self.assertTrue(all(c.cost >= 3.0 for c in result.candidates))

    def test_bend_transition_multiplier_changes_full_order_cost(self):
        session, tooling = fixture()
        bbox = lambda c: {"overlapping_pairs": 0}
        original = search_axis_staged(
            session, tooling, config=config(),
            step_checker=mock_step, bbox_checker=bbox,
        )
        weighted = search_axis_staged(
            session, tooling, config=config(),
            step_checker=mock_step, bbox_checker=bbox,
            per_bend_transition_multiplier={"B2": 4.0},
            axis_change_cost=3.0,
        )
        self.assertTrue(weighted.candidates)
        self.assertEqual(
            weighted.to_dict()["cost_weights"]["transition_multiplier"],
            {"B2": 4.0},
        )
        self.assertEqual(
            weighted.to_dict()["cost_weights"]["axis_change_cost"], 3.0
        )
        self.assertTrue(any(
            step["bend_id"] == "B2"
            and step["transition_multiplier"] == 4.0
            for c in weighted.candidates
            for step in c.cost_breakdown
        ))
        for candidate in weighted.candidates:
            self.assertAlmostEqual(
                candidate.cost,
                sum(item["step_total"] for item in candidate.cost_breakdown),
            )
        self.assertTrue(original.candidates)

    def test_one_loading_collision_does_not_ban_other_orientations(self):
        session, tooling = fixture()
        result = search_axis_staged(
            session, tooling, config=config(),
            step_checker=mock_step,
            bbox_checker=lambda c: {"overlapping_pairs": 2},
        )
        self.assertTrue(any(
            s.bend_id == "A2"
            and s.orientation_variant == "REVERSE_MACHINE_Z_180"
            for c in result.candidates for s in c.steps
        ))

    def test_native_collision_never_becomes_certified_or_selectable(self):
        session, tooling = fixture()
        observed = []
        def native(_session, _tooling, sequence, **kwargs):
            observed.append((
                tuple(sequence), kwargs["reverse_axis_by_bend"],
                kwargs["config"].coarse_occ_screen,
            ))
            return {
                "status": "COLLISION", "reason": "proven_tool_overlap",
                "checked_bends": 2,
            }
        result = search_axis_staged(
            session, tooling, config=config(finalists=3),
            step_checker=mock_step,
            bbox_checker=lambda c: {"overlapping_pairs": 0},
            run_occ=True, native_runner=native,
        )
        self.assertEqual(len(observed), min(3, len(result.candidates)))
        self.assertTrue(all(not item[2] for item in observed))
        self.assertTrue(all(r["status"] == "COLLISION"
                            for r in result.occ_results))
        self.assertFalse(result.as_planar_report().candidates)
        self.assertFalse(result.to_dict()["physically_certified"])

    def test_bbox_overlap_cannot_prove_penetration(self):
        a = ((0, 1), (0, 1), (0, 1))
        b = ((0.5, 2), (0.5, 2), (0.5, 2))
        c = ((2, 3), (2, 3), (2, 3))
        self.assertTrue(_bbox_intersects(a, b))
        self.assertFalse(_bbox_intersects(a, c))
        session, tooling = fixture()
        result = search_axis_staged(
            session, tooling, config=config(),
            step_checker=mock_step,
            bbox_checker=lambda candidate: {
                "status": "BBOX_PROXY_RANKING_ONLY",
                "overlapping_pairs": 999,
                "proven_collision": False,
            },
        )
        self.assertTrue(result.finalists)
        self.assertEqual(len(result.occ_results), 0)

    def test_bad_costs_rejected(self):
        session, tooling = fixture()
        with self.assertRaises(ValueError):
            search_axis_staged(
                session, tooling, per_bend_cost={"A1": -1},
                step_checker=mock_step,
            )
        with self.assertRaises(ValueError):
            search_axis_staged(
                session, tooling, pair_cost={("UNKNOWN", "A1"): 1.0},
                step_checker=mock_step,
            )


if __name__ == "__main__":
    unittest.main()
