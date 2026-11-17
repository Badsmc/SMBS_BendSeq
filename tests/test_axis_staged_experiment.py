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

    def test_conditional_punch_section_is_not_a_proven_native_collision(self):
        from smbs_core.kinematics2d.axis_staged_experiment import (
            _punch_only_section_interference,
        )
        def report(*tools):
            return SimpleNamespace(
                stage="STAGE_B_SECTION_REQUIRES_3D",
                diagnostic_findings=tuple(
                    ("BENDING", tool, "P01", "INTERFERENCE", "overlap")
                    for tool in tools
                ),
            )
        self.assertTrue(_punch_only_section_interference(report("PUNCH")))
        self.assertFalse(_punch_only_section_interference(report("DIE")))
        self.assertFalse(_punch_only_section_interference(
            report("PUNCH", "DIE")
        ))
        self.assertFalse(_punch_only_section_interference(report()))

    def test_individual_weight_affects_incoming_and_outgoing_cost(self):
        from smbs_core.kinematics2d.axis_staged_experiment import (
            _weighted_transition_cost,
        )
        from smbs_core.kinematics2d.operation_cost import (
            WorkpieceMachineSetup, OperationWeights,
        )
        setup = lambda bid, rot: WorkpieceMachineSetup(
            bend_id=bid, rotation_deg=rot, flipped=False,
            shift_xm_mm=0.0, bend_length_mm=100.0,
        )
        a = setup("B01", 0.0)
        b = setup("B02", 90.0)
        c = setup("B03", 180.0)
        nominal = {"B01": 0.0, "B02": 0.0, "B03": 0.0}
        b_heavy = {"B01": 0.0, "B02": 10.0, "B03": 0.0}
        weights = OperationWeights()
        self.assertGreater(
            _weighted_transition_cost("B01", "B02", a, b, weights, b_heavy),
            _weighted_transition_cost("B01", "B02", a, b, weights, nominal),
        )
        self.assertGreater(
            _weighted_transition_cost("B02", "B03", b, c, weights, b_heavy),
            _weighted_transition_cost("B02", "B03", b, c, weights, nominal),
        )

    def test_native_feedback_blocks_only_exact_pose_and_keeps_alternatives(self):
        from smbs_core.kinematics2d.native_collision_feedback import (
            NativeCollisionFeedback, add_native_blocker,
        )
        session, tooling = fixture()
        feedback = NativeCollisionFeedback(
            sequence=("B01", "B02", "B03", "B04"),
            failing_index=0,
            orientation_by_bend={
                "B01": False, "B02": False, "B03": False, "B04": False,
            },
            evidence="CAD_3D_INTERFERENCE",
            reason="PUNCH:P05:BENDING",
        )
        blocks = add_native_blocker({}, feedback)
        self.assertEqual(
            blocks[(frozenset(), "B01", False)], "PUNCH:P05:BENDING"
        )
        result = search_axis_staged_experiment(
            session, tooling,
            transition_screen=lambda prefix, bid, reverse: (
                "SAMPLED_2D_CLEAR_OR_TOUCH", "synthetic"
            ),
            broadphase_runner=lambda *a, **kw: {
                "status": "SAMPLED_PREVIEW_BBOX_SEPARATED",
                "possible_overlap_samples": 0,
            },
            blocked_transitions=blocks, max_time_s=20, beam_width=12,
            finalists=5,
        )
        self.assertGreater(result.rejected, 0)
        self.assertTrue(result.planar_report.candidates)
        self.assertFalse(any(
            c.sequence[0] == "B01"
            and c.steps[0].orientation_variant == "NORMAL"
            for c in result.planar_report.candidates
        ))
        self.assertTrue(any(
            c.sequence[0] == "B01"
            and c.steps[0].orientation_variant == "REVERSE_MACHINE_Z_180"
            for c in result.planar_report.candidates
        ) or any(c.sequence[0] != "B01" for c in result.planar_report.candidates))

    def test_bounded_native_replan_tries_new_loading_after_collision(self):
        from smbs_core.kinematics2d.native_collision_feedback import (
            NativeCollisionFeedback, search_with_native_feedback,
        )
        session, tooling = fixture()
        calls = []
        def planner(blockers):
            calls.append(len(blockers))
            return search_axis_staged_experiment(
                session, tooling,
                transition_screen=lambda prefix, bid, reverse: (
                    "SAMPLED_2D_CLEAR_OR_TOUCH", "synthetic"
                ),
                broadphase_runner=lambda *a, **kw: {
                    "status": "SAMPLED_PREVIEW_BBOX_SEPARATED",
                    "possible_overlap_samples": 0,
                },
                blocked_transitions=blockers,
                max_time_s=10, beam_width=10, finalists=3,
            ).planar_report.candidates
        native_calls = []
        def verifier(candidate):
            native_calls.append(candidate.sequence)
            if len(native_calls) == 1:
                return NativeCollisionFeedback(
                    candidate.sequence, 0,
                    {step.bend_id: step.orientation_variant.startswith(
                        "REVERSE_MACHINE_Z_180"
                    ) for step in candidate.steps},
                    "CAD_3D_INTERFERENCE", "PUNCH:P05",
                )
            return None
        result = search_with_native_feedback(
            planner, verifier, max_attempts=4,
        )
        self.assertEqual(result.status, "NATIVE_VERIFIED")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(calls, [0, 1])
        self.assertEqual(len(result.blockers), 1)

    def test_opposite_loading_only_flips_collided_bend(self):
        from smbs_core.kinematics2d.native_collision_feedback import (
            NativeCollisionFeedback, propose_opposite_loading,
        )
        orientations = {
            "B09": False, "B10": True, "B06": True,
            "B05": False, "B07": True, "B01": False,
        }
        sequence = tuple(orientations)
        feedback = NativeCollisionFeedback(
            sequence, 5, orientations, "CAD_3D_INTERFERENCE",
            "PUNCH:P11:BENDING",
        )
        candidate_order, candidate_orientations = (
            propose_opposite_loading(feedback)
        )
        self.assertEqual(candidate_order, sequence)
        self.assertIs(candidate_orientations["B01"], True)
        self.assertEqual(
            {bid: candidate_orientations[bid] for bid in sequence[:5]},
            {bid: orientations[bid] for bid in sequence[:5]},
        )
        self.assertIs(orientations["B01"], False)
        self.assertEqual(
            feedback.blocked_transition()[0],
            (frozenset(sequence[:5]), "B01", False),
        )
        self.assertNotEqual(
            feedback.blocked_transition()[0],
            (frozenset(sequence[:5]), "B01", True),
        )

    def test_opposite_loading_requires_native_collision_not_bbox_risk(self):
        from smbs_core.kinematics2d.native_collision_feedback import (
            NativeCollisionFeedback, propose_opposite_loading,
        )
        for label in (
            "UNVERIFIED_2D",
            "SAMPLED_PREVIEW_BBOX_POSSIBLE_OVERLAP",
            "SAMPLED_3D_CLEAR_NOT_CONTINUOUS",
        ):
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    propose_opposite_loading(NativeCollisionFeedback(
                        ("B01",), 0, {"B01": False}, label, "uncertain",
                    ))

    def test_freecad_flip_priority_wired_before_replanning(self):
        import ast
        from pathlib import Path
        text = Path("freecad_smbs/commands/pressbrake_2d.py").read_text(
            encoding="utf-8"
        )
        ast.parse(text)
        section = text.split(
            "def calculate_axis_staged_native_feedback(self):", 1
        )[1].split("def open_sequence_catalog_dialog(self):", 1)[0]
        self.assertIn("if pending_flip is not None:", section)
        self.assertIn("propose_opposite_loading(feedback)", section)
        self.assertIn('"AXIS_NATIVE_FEEDBACK_FLIP_QUEUED"', section)
        self.assertIn('"AXIS_NATIVE_FEEDBACK_FLIP_TRIAL_STARTED"', section)
        self.assertIn('"trial_kind": trial_kind', section)
        self.assertLess(
            section.index("if pending_flip is not None:"),
            section.index("experiment = search_axis_staged_experiment("),
        )

    def test_ambiguous_native_evidence_never_blocks(self):
        from smbs_core.kinematics2d.native_collision_feedback import (
            NativeCollisionFeedback,
        )
        with self.assertRaises(ValueError):
            NativeCollisionFeedback(
                sequence=("B01",), failing_index=0,
                orientation_by_bend={"B01": False},
                evidence="SAMPLED_PREVIEW_BBOX_POSSIBLE_OVERLAP",
                reason="bbox overlap",
            ).blocked_transition()
        with self.assertRaises(ValueError):
            NativeCollisionFeedback(
                sequence=("B01",), failing_index=0,
                orientation_by_bend={},
                evidence="CAD_3D_INTERFERENCE",
                reason="missing orientation",
            ).blocked_transition()

    def test_freecad_native_feedback_entrypoint_compiles_and_is_wired(self):
        import ast
        from pathlib import Path
        source = Path("freecad_smbs/commands/pressbrake_2d.py").read_text(
            encoding="utf-8"
        )
        ast.parse(source)
        self.assertIn("def calculate_axis_staged_native_feedback(self):", source)
        self.assertIn(
            "self.axis_native_feedback_btn.clicked.connect(", source
        )
        self.assertIn(
            'blocked_transitions=blocked,', source
        )
        self.assertIn(
            '"AXIS_NATIVE_FEEDBACK_COMPLETED"', source
        )
        self.assertIn(
            'reverse_axis_by_bend=orientations,', source
        )

    def test_provisional_complete_candidate_when_axis_merge_budget_expires(self):
        from smbs_core.kinematics2d.axis_staged_experiment import (
            _provisional_full_paths, _Path,
        )
        order = ("B01", "B02", "B03", "B04")
        axes = [
            ("AXIS_01", [_Path(order=("B01", "B02"))]),
            ("AXIS_02", [_Path(order=("B03", "B04"))]),
        ]
        candidates = _provisional_full_paths(
            axes, order, {}, {}, {bid: 1.0 for bid in order}, 6,
        )
        self.assertTrue(candidates)
        for candidate in candidates:
            self.assertEqual(set(candidate.order), set(order))
            self.assertEqual(len(candidate.steps), len(order))
            self.assertEqual(candidate.unknown, len(order))
            self.assertTrue(all(
                step.evidence == "UNVERIFIED_2D"
                for step in candidate.steps
            ))

    def test_provisional_avoids_native_blocker_and_2d_proven_obstacle(self):
        from smbs_core.kinematics2d.axis_staged_experiment import (
            _provisional_full_paths, _Path,
        )
        from smbs_core.kinematics2d.sequence_search_2d import PlanarStep
        axes = [
            ("AXIS_01", [_Path(order=("B01",))]),
            ("AXIS_02", [_Path(order=("B02",))]),
        ]
        blocked = {(frozenset(), "B01", False): "native punch hit"}
        cache = {
            (frozenset(), "B01", True):
                PlanarStep("B01", "2D_INTERFERENCE", "definite section",
                           (), ()),
        }
        candidates = _provisional_full_paths(
            axes, ("B01", "B02"), blocked, cache,
            {"B01": 1.0, "B02": 1.0}, 5,
        )
        self.assertTrue(candidates)
        self.assertTrue(all(p.order[0] == "B02" for p in candidates))

    def test_top_toolbar_search_is_navigation_only(self):
        import ast
        from pathlib import Path
        source = Path("freecad_smbs/commands/pressbrake_2d.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        for cls_name, forbidden in (
            ("CalculateCommand", "calculate_sequence"),
            ("PlanarCandidatesCommand", "calculate_planar_candidates"),
        ):
            cls = next(
                n for n in ast.walk(tree)
                if isinstance(n, ast.ClassDef) and n.name == cls_name
            )
            method = next(
                n for n in cls.body
                if isinstance(n, ast.FunctionDef) and n.name == "Activated"
            )
            calls = [
                n.func.attr for n in ast.walk(method)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
            ]
            self.assertNotIn(forbidden, calls)
            self.assertIn("show_workspace_controls", [
                n.func.id for n in ast.walk(method)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
            ])
        self.assertIn('self.planar_start_btn.clicked.connect(', source)
        self.assertIn('self.legacy_d6_search_btn.clicked.connect(', source)

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
