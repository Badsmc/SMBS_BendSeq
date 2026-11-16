"""Pure-Python unit regressions for the isolated axis-staged experiment.

No FreeCAD/OCC required: geometry calls are injected, while real grouping,
beam/interleaving, per-bend weights and ranking code execute unchanged.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.axis_staged_experiment import (
    search_axis_staged_experiment,
)
from smbs_core.kinematics2d.axis_staged_broadphase_3d import (
    boxes_intersect, mesh_face_aabb_risk,
)


class FakeState:
    def __init__(self):
        axes = {
            "B01": ((0, 0, 0), (100, 0, 0)),
            "B02": ((0, 10, 0), (100, 10, 0)),
            "B03": ((0, 0, 0), (0, 100, 0)),
            "B04": ((10, 0, 0), (10, 100, 0)),
        }
        self.bends = {
            bid: SimpleNamespace(axis_xyz=axis, angle_deg=90.0,
                                 bend_zone=None, fixed_panel="P1",
                                 moving_panel="P2")
            for bid, axis in axes.items()
        }
        self.angles = {bid: 0.0 for bid in axes}
        self.transforms = {}

    def reset_all(self):
        for bid in self.angles:
            self.angles[bid] = 0.0

    def set_angle(self, bend_id, angle):
        self.angles[bend_id] = angle


def fixture():
    order = ("B01", "B02", "B03", "B04")
    state = FakeState()
    session = SimpleNamespace(
        bend_order=order, fold_state=state,
        panel_vertices_xy={"P1": ((0, 0), (10, 0), (10, 10))},
    )
    tooling = SimpleNamespace(
        punch=SimpleNamespace(length_mm=100.0),
        die=SimpleNamespace(length_mm=100.0),
    )
    return session, tooling


class AxisStagedSearchTests(unittest.TestCase):
    def test_two_axes_interleave_and_keep_five_or_fewer(self):
        session, tooling = fixture()
        result = search_axis_staged_experiment(
            session, tooling,
            transition_screen=lambda prefix, bid, reverse: (
                "SAMPLED_2D_CLEAR_OR_TOUCH", "synthetic_clear"
            ),
            broadphase_runner=lambda *a, **kw: {
                "status": "SAMPLED_PREVIEW_BBOX_POSSIBLE_OVERLAP",
                "possible_overlap_samples": 12,
            },
            max_time_s=20, per_axis_limit=3, beam_width=12,
            finalists=5, combined_pool=20,
        )
        self.assertEqual(len(result.axis_orders), 2)
        self.assertGreater(len(result.planar_report.candidates), 0)
        self.assertLessEqual(len(result.planar_report.candidates), 5)
        for c in result.planar_report.candidates:
            self.assertEqual(set(c.sequence), set(session.bend_order))
            self.assertEqual(len(c.steps), 4)
            self.assertNotIn("CERTIFIED", c.evidence)
        self.assertTrue(all(v == 0.0 for v in session.fold_state.angles.values()))
        self.assertGreater(result.cache_hits, 0)

    def test_interference_blocks_one_loading_not_the_bend_globally(self):
        session, tooling = fixture()
        def screen(prefix, bend_id, reverse):
            if bend_id == "B02" and not reverse:
                return "2D_INTERFERENCE", "punch_profile_interference"
            return "SAMPLED_2D_CLEAR_OR_TOUCH", "synthetic_clear"
        result = search_axis_staged_experiment(
            session, tooling, transition_screen=screen,
            broadphase_runner=lambda *a, **kw: {
                "status": "SAMPLED_PREVIEW_BBOX_SEPARATED",
                "possible_overlap_samples": 0,
            },
            max_time_s=20, beam_width=12, finalists=5,
        )
        self.assertGreater(result.rejected, 0)
        self.assertTrue(result.planar_report.candidates)
        self.assertTrue(all(
            next(s for s in c.steps if s.bend_id == "B02")
            .orientation_variant == "REVERSE_MACHINE_Z_180"
            for c in result.planar_report.candidates
        ))

    def test_bbox_overlap_is_not_used_as_collision_proof(self):
        a = ((0.0, 1.0), (0.0, 1.0), (0.0, 1.0))
        b = ((0.5, 2.0), (0.5, 2.0), (0.5, 2.0))
        c = ((5.0, 6.0), (5.0, 6.0), (5.0, 6.0))
        self.assertTrue(boxes_intersect(a, b))
        self.assertFalse(boxes_intersect(a, c))
        sf = SimpleNamespace(
            vertices=((0, 0, 0), (1, 0, 0), (0, 1, 0)),
            identifier="P01", kind="panel_top",
        )
        tf = SimpleNamespace(
            vertices=((0.3, 0, 0), (1.2, 0, 0), (0, 1, 0)),
            identifier="punch", kind="punch_side",
        )
        risk = mesh_face_aabb_risk((sf,), (tf,))
        self.assertEqual(risk["possible_overlap_pairs"], 1)
        self.assertEqual(risk["possible_components"][0], ("P01", "punch_side"))

    def test_invalid_weights_or_deadline_rejected(self):
        session, tooling = fixture()
        with self.assertRaises(ValueError):
            search_axis_staged_experiment(
                session, tooling, bend_weights={"B01": -1.0},
            )
        with self.assertRaises(ValueError):
            search_axis_staged_experiment(
                session, tooling, max_time_s=0.0,
            )


if __name__ == "__main__":
    unittest.main()
