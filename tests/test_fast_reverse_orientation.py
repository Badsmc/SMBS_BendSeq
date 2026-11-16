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

    def test_automatic_ten_bend_search_requests_diverse_first_two_bends(self):
        order = tuple("B%02d" % i for i in range(1, 11))
        session = SimpleNamespace(
            bend_order=order,
            fold_state=SimpleNamespace(
                bends={bend_id: object() for bend_id in order},
            ),
            panel_build=object(), part_label="test",
        )
        tooling = SimpleNamespace(
            punch=SimpleNamespace(tool_id="p", length_mm=100),
            die=SimpleNamespace(tool_id="d", length_mm=100),
            match_active_bend_length=False,
            punch_section_offsets_xm=(), die_section_offsets_xm=(),
        )
        candidate = SimpleNamespace(
            sequence=order,
            steps=tuple(SimpleNamespace(
                bend_id=bend_id, orientation_variant="NORMAL",
            ) for bend_id in order),
            evidence="SAMPLED_2D_CLEAR_OR_TOUCH",
            cost=0.0, cost_complete=True,
        )
        planar_report = SimpleNamespace(
            status=SimpleNamespace(value="SAMPLED_2D_CANDIDATES"),
            limit_reason="", deepest_prefix=order,
            candidates=(candidate,),
        )
        captured = {}
        def fake_planar(_session, _tooling, **kwargs):
            captured.update(kwargs)
            return planar_report
        with patch(
            "smbs_core.kinematics2d.reverse_aabb_search.search_reverse_aabb",
            side_effect=AssertionError("Do not replace the 2D primary search"),
        ):
            result = search_fast_feasible_sequence(
                session, tooling, budget_s=1000.0, max_candidates=20,
                planar_runner=fake_planar,
                native_runner=lambda *args, **kwargs: {
                    "status": "UNVERIFIED", "checked_bends": 0,
                    "steps": (), "reason": "synthetic",
                },
            )
        self.assertEqual(captured["max_candidates_per_prefix"], 2)
        self.assertEqual(captured["diversity_prefix_length"], 2)
        self.assertEqual(captured["max_candidates_per_first_bend"], 5)
        self.assertTrue(captured["explore_both_orientations_without_gauge"])
        self.assertIsNone(result.sequence)

    def test_two_endpoint_occ_success_is_separate_from_machine_certification(self):
        order = ("B01", "B02")
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
        cand = SimpleNamespace(
            sequence=order, evidence="SAMPLED_2D_CLEAR_OR_TOUCH",
            cost=1.0, cost_complete=True,
            steps=tuple(SimpleNamespace(
                bend_id=b, orientation_variant="NORMAL",
            ) for b in order),
        )
        planar = SimpleNamespace(
            status=SimpleNamespace(value="SAMPLED_2D_CANDIDATES"),
            limit_reason="", deepest_prefix=order, candidates=(cand,),
        )
        configurations = []
        def endpoint_runner(_session, _tooling, sequence, **kwargs):
            configurations.append(kwargs["config"])
            return {
                "status": "UNVERIFIED",
                "validation_mode": "COARSE_OCC_2_BEND_ENDPOINTS",
                "complete_part_order_requested": True,
                "stopped_early": False,
                "checked_bends": len(sequence),
                "requested_bends": list(sequence),
                "tool_length_mode": "VIRTUAL_MATCH_BEND",
                "punch_fcstd_source": "punch.FCStd",
                "die_fcstd_source": "die.FCStd",
                "steps": [
                    {
                        "bend_id": b, "reverse_axis": False,
                        "status": "COARSE_3D_NO_PROVEN_COLLISION",
                        "is_conditional": False,
                    } for b in sequence
                ],
                "reason": "sampled two endpoints only",
            }
        result = search_fast_feasible_sequence(
            session, tooling, budget_s=120, max_candidates=1,
            planar_runner=lambda *args, **kwargs: planar,
            native_runner=endpoint_runner,
        )
        self.assertTrue(configurations[0].coarse_occ_screen)
        self.assertEqual(
            result.status, FastSearchStatus.TWO_POINT_NO_DETECTED_COLLISION
        )
        self.assertEqual(result.sequence, order)
        self.assertTrue(result.two_point_no_detected_collision)
        self.assertFalse(result.virtual_tool_clear)
        self.assertFalse(result.machine_feasible_certified)
        self.assertFalse(result.to_dict()["continuous_motion_certified"])

    def test_complete_two_endpoint_occ_screen_is_reported_as_preliminary(self):
        from smbs_core.kinematics2d.fast_feasible_search import (
            _coarse_endpoint_candidate,
        )
        order = ("B01", "B02")
        poses = {"B01": False, "B02": True}
        physical = {
            "validation_mode": "COARSE_OCC_2_BEND_ENDPOINTS",
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
        self.assertEqual(_coarse_endpoint_candidate(
            physical, order, poses,
        ), "NO_DETECTED_COLLISION")
        # Never accept a skipped bend, an alternate pose, or a collision.
        bad = dict(physical, checked_bends=1)
        self.assertIsNone(_coarse_endpoint_candidate(bad, order, poses))
        bad = dict(physical, steps=[
            physical["steps"][0],
            dict(physical["steps"][1], status="COLLISION"),
        ])
        self.assertIsNone(_coarse_endpoint_candidate(bad, order, poses))
        bad = dict(physical, steps=[
            physical["steps"][0],
            dict(physical["steps"][1], reverse_axis=False),
        ])
        self.assertIsNone(_coarse_endpoint_candidate(bad, order, poses))
        bad = dict(physical, validation_mode="FULL_ADAPTIVE_D6")
        self.assertIsNone(_coarse_endpoint_candidate(bad, order, poses))

    def test_conditional_punch_end_zones_require_explicit_permission(self):
        from smbs_core.kinematics2d.fast_feasible_search import (
            _coarse_endpoint_candidate,
        )
        order = ("B01", "B02")
        poses = {"B01": False, "B02": True}
        report = {
            "status": "UNVERIFIED",
            "validation_mode": "COARSE_OCC_2_BEND_ENDPOINTS",
            "complete_part_order_requested": True,
            "stopped_early": False,
            "checked_bends": 2,
            "requested_bends": list(order),
            "tool_length_mode": "VIRTUAL_MATCH_BEND",
            "punch_fcstd_source": "selected.FCStd",
            "die_fcstd_source": "die.FCStd",
            "steps": [
                {"bend_id": "B01", "reverse_axis": False,
                 "status": "COARSE_3D_NO_PROVEN_COLLISION",
                 "is_conditional": False},
                {"bend_id": "B02", "reverse_axis": True,
                 "status": "COARSE_3D_CONDITIONAL_END_ZONE",
                 "is_conditional": True},
            ],
        }
        self.assertIsNone(_coarse_endpoint_candidate(report, order, poses))
        self.assertIsNone(_coarse_endpoint_candidate(
            report, order, poses, conditional_allowed=True,
            end_zone_length_mm=0.0,
        ))
        self.assertEqual(_coarse_endpoint_candidate(
            report, order, poses, conditional_allowed=True,
            end_zone_length_mm=10.0,
        ), "CONDITIONAL_END_ZONES")
        wrong_zone = dict(report, steps=[
            report["steps"][0], dict(
                report["steps"][1], status="COLLISION",
            ),
        ])
        self.assertIsNone(_coarse_endpoint_candidate(
            wrong_zone, order, poses, conditional_allowed=True,
            end_zone_length_mm=10.0,
        ))
        wrong_pose = dict(report, steps=[
            report["steps"][0], dict(report["steps"][1], reverse_axis=False),
        ])
        self.assertIsNone(_coarse_endpoint_candidate(
            wrong_pose, order, poses, conditional_allowed=True,
            end_zone_length_mm=10.0,
        ))

    def test_auto_search_forwards_conditional_both_end_parameters(self):
        order = ("B01", "B02")
        state = SimpleNamespace(bends={bid: object() for bid in order})
        session = SimpleNamespace(
            bend_order=order, fold_state=state, panel_build=object(),
            part_label="synthetic",
        )
        tooling = SimpleNamespace(
            punch=SimpleNamespace(tool_id="p", length_mm=100),
            die=SimpleNamespace(tool_id="d", length_mm=100),
            match_active_bend_length=False,
            punch_section_offsets_xm=(), die_section_offsets_xm=(),
            allow_end_zone_collision=True, end_zone_length_mm=12.0,
        )
        candidate = SimpleNamespace(
            sequence=order, evidence="SAMPLED_2D_CLEAR_OR_TOUCH",
            cost=0.0, cost_complete=True,
            steps=tuple(SimpleNamespace(
                bend_id=b, orientation_variant="NORMAL",
            ) for b in order),
        )
        planar_report = SimpleNamespace(
            status=SimpleNamespace(value="SAMPLED_2D_CANDIDATES"),
            limit_reason="", deepest_prefix=order,
            candidates=(candidate,),
        )
        received = []
        def planar_runner(_session, _tooling, **kwargs):
            received.append(("planar", kwargs))
            return planar_report
        def native_runner(_session, _tooling, sequence, **kwargs):
            received.append(("native", kwargs))
            return {
                "status": "UNVERIFIED",
                "validation_mode": "COARSE_OCC_2_BEND_ENDPOINTS",
                "complete_part_order_requested": True,
                "stopped_early": False,
                "checked_bends": len(sequence),
                "requested_bends": list(sequence),
                "tool_length_mode": "VIRTUAL_MATCH_BEND",
                "punch_fcstd_source": "p.FCStd",
                "die_fcstd_source": "d.FCStd",
                "steps": [
                    {"bend_id": b, "reverse_axis": False,
                     "status": "COARSE_3D_CONDITIONAL_END_ZONE",
                     "is_conditional": True}
                    for b in sequence
                ],
            }
        result = search_fast_feasible_sequence(
            session, tooling, budget_s=1000.0, max_candidates=1,
            planar_runner=planar_runner, native_runner=native_runner,
        )
        self.assertEqual(
            result.status, FastSearchStatus.TWO_POINT_CONDITIONAL_END_ZONES
        )
        self.assertTrue(result.two_point_candidate_accepted)
        self.assertFalse(result.machine_feasible_certified)
        self.assertEqual(result.sequence, order)
        for stage, settings in received:
            self.assertTrue(settings["allow_end_zone_collision"])
            self.assertEqual(settings["end_zone_length_mm"], 12.0)
        self.assertTrue(tooling.allow_end_zone_collision)
        self.assertFalse(tooling.match_active_bend_length)

    def test_left_and_right_punch_end_zones_are_separate_10mm_each(self):
        from smbs_core.kinematics2d.native_collision_3d import (
            _classify_validated_punch_overlap_zones,
            PunchCollisionZoneClassification,
        )
        def bb(x0, x1):
            return SimpleNamespace(
                XMin=x0, XMax=x1, YMin=-1.0, YMax=1.0,
                ZMin=0.0, ZMax=2.0,
            )
        punch = SimpleNamespace(BoundBox=bb(-250.0, 250.0))
        sheet = SimpleNamespace()
        def common(solids):
            return SimpleNamespace(
                Volume=sum(sol.Volume for sol in solids),
                Solids=solids,
            )
        left = SimpleNamespace(Volume=3.0, BoundBox=bb(-245.0, -243.0))
        right = SimpleNamespace(Volume=4.0, BoundBox=bb(243.0, 245.0))
        both = _classify_validated_punch_overlap_zones(
            sheet, punch, 10.0, _precomputed_common=common([left, right]),
        )
        self.assertEqual(
            both.classification,
            PunchCollisionZoneClassification.END_ZONE_ONLY,
        )
        self.assertEqual(both.left_end_zone_xm, (-250.0, -240.0))
        self.assertEqual(both.right_end_zone_xm, (240.0, 250.0))
        self.assertEqual(both.central_zone_xm, (-240.0, 240.0))
        self.assertEqual(set(both.affected_sides), {"LEFT", "RIGHT"})
        center = SimpleNamespace(Volume=5.0, BoundBox=bb(-2.0, 2.0))
        forbidden = _classify_validated_punch_overlap_zones(
            sheet, punch, 10.0,
            _precomputed_common=common([left, center, right]),
        )
        self.assertEqual(
            forbidden.classification,
            PunchCollisionZoneClassification.MIXED_CENTRAL_AND_END_ZONE,
        )
        too_short = _classify_validated_punch_overlap_zones(
            sheet, punch, 250.0,
            _precomputed_common=common([left, right]),
        )
        self.assertEqual(
            too_short.classification,
            PunchCollisionZoneClassification.UNCLASSIFIED,
        )

    def test_conditional_punch_only_2d_needs_native_not_pruned(self):
        from smbs_core.kinematics2d.sequence_search_2d import _classify_step
        from smbs_core.kinematics2d.preliminary_screening import (
            PreliminaryScreeningReport, ScreeningDecision,
        )
        def report(tool):
            return PreliminaryScreeningReport(
                decision=ScreeningDecision.CONTINUE_TO_3D,
                stage="STAGE_B_SECTION_REQUIRES_3D",
                reason="section_requires_3d",
                diagnostic_findings=(
                    ("BENDING", tool, "panel:P09", "INTERFERENCE", "overlap"),
                ),
            )
        self.assertEqual(_classify_step(report("PUNCH")), "2D_INTERFERENCE")
        self.assertEqual(
            _classify_step(
                report("PUNCH"), conditional_punch_ends_allowed=True,
            ), "UNVERIFIED_2D",
        )
        self.assertEqual(
            _classify_step(
                report("DIE"), conditional_punch_ends_allowed=True,
            ), "2D_INTERFERENCE",
        )
        self.assertEqual(
            _classify_step(
                report("BACKGAUGE"), conditional_punch_ends_allowed=True,
            ), "2D_INTERFERENCE",
        )
        ambiguous = PreliminaryScreeningReport(
            decision=ScreeningDecision.CONTINUE_TO_3D,
            stage="STAGE_B_SECTION_REQUIRES_3D",
            reason="no evidence",
        )
        self.assertEqual(
            _classify_step(
                ambiguous, conditional_punch_ends_allowed=True,
            ), "2D_INTERFERENCE",
        )

    def test_coarse_occ_cache_distinguishes_clear_conditional_and_collision(self):
        from smbs_core.kinematics2d.manual_physical_validation import (
            _cacheable_coarse_occ_step,
        )
        self.assertTrue(_cacheable_coarse_occ_step(
            {"status": "COARSE_3D_NO_PROVEN_COLLISION",
             "is_conditional": False},
            conditional_allowed=False,
        ))
        self.assertFalse(_cacheable_coarse_occ_step(
            {"status": "COARSE_3D_NO_PROVEN_COLLISION",
             "is_conditional": True},
            conditional_allowed=True,
        ))
        conditional = {
            "status": "COARSE_3D_CONDITIONAL_END_ZONE",
            "is_conditional": True,
        }
        self.assertTrue(_cacheable_coarse_occ_step(
            conditional, conditional_allowed=True,
        ))
        self.assertFalse(_cacheable_coarse_occ_step(
            conditional, conditional_allowed=False,
        ))
        for status in ("COLLISION", "UNVERIFIED",
                       "SAMPLED_3D_CLEAR_NOT_CONTINUOUS"):
            self.assertFalse(_cacheable_coarse_occ_step(
                {"status": status, "is_conditional": False},
                conditional_allowed=True,
            ))

    def test_full_initial_shortlist_reserves_and_prioritizes_native_pose_retry(self):
        order = tuple("B%02d" % i for i in range(1, 11))
        session = SimpleNamespace(
            bend_order=order,
            fold_state=SimpleNamespace(bends={x: object() for x in order}),
            panel_build=object(), part_label="test",
        )
        tooling = SimpleNamespace(
            punch=SimpleNamespace(tool_id="p", length_mm=100),
            die=SimpleNamespace(tool_id="d", length_mm=100),
            match_active_bend_length=False,
            punch_section_offsets_xm=(), die_section_offsets_xm=(),
        )
        rotations = [order[i:] + order[:i] for i in range(8)]
        candidates = tuple(SimpleNamespace(
            sequence=seq, evidence="SAMPLED_2D_CLEAR_OR_TOUCH",
            cost=float(i), cost_complete=True,
            steps=tuple(SimpleNamespace(
                bend_id=x, orientation_variant="NORMAL",
            ) for x in seq),
        ) for i, seq in enumerate(rotations))
        planar = SimpleNamespace(
            status=SimpleNamespace(value="SAMPLED_2D_CANDIDATES"),
            limit_reason="", deepest_prefix=order, candidates=candidates,
        )
        tried = []
        def native(_session, _tooling, sequence, **kwargs):
            pose = kwargs["reverse_axis_by_bend"]
            tried.append((tuple(sequence), dict(pose)))
            if pose[sequence[1]]:
                return {
                    "status": "UNVERIFIED", "reason": "unknown",
                    "checked_bends": 0, "steps": [],
                }
            return {
                "status": "COLLISION", "reason": "blocked",
                "checked_bends": 2,
                "steps": [
                    {"bend_id": sequence[0],
                     "status": "COARSE_3D_NO_PROVEN_COLLISION"},
                    {"bend_id": sequence[1], "status": "COLLISION"},
                ],
            }
        result = search_fast_feasible_sequence(
            session, tooling, budget_s=1000, max_candidates=8,
            planar_runner=lambda *args, **kwargs: planar,
            native_runner=native,
        )
        self.assertGreaterEqual(len(tried), 2)
        self.assertEqual(tried[0][0], order)
        self.assertEqual(tried[1][0], order)
        self.assertTrue(tried[1][1]["B02"])
        self.assertLessEqual(len(result.attempts), 8)
        self.assertIsNone(result.sequence)
        self.assertFalse(result.machine_feasible_certified)

    def test_proven_collision_triggers_opposite_loading_before_abandonment(self):
        order = ("B01", "B02", "B03")
        session = SimpleNamespace(
            bend_order=order,
            fold_state=SimpleNamespace(bends={x: object() for x in order}),
            panel_build=object(), part_label="test",
        )
        tooling = SimpleNamespace(
            punch=SimpleNamespace(tool_id="p", length_mm=100),
            die=SimpleNamespace(tool_id="d", length_mm=100),
            match_active_bend_length=False,
            punch_section_offsets_xm=(), die_section_offsets_xm=(),
        )
        candidate = SimpleNamespace(
            sequence=order, evidence="SAMPLED_2D_CLEAR_OR_TOUCH",
            cost=0.0, cost_complete=True,
            steps=tuple(SimpleNamespace(
                bend_id=x, orientation_variant="NORMAL",
            ) for x in order),
        )
        planar = SimpleNamespace(
            status=SimpleNamespace(value="SAMPLED_2D_CANDIDATES"),
            limit_reason="", deepest_prefix=order,
            candidates=(candidate,),
        )
        tried = []
        def native(_session, _tooling, sequence, **kwargs):
            poses = kwargs["reverse_axis_by_bend"]
            tried.append((tuple(sequence), dict(poses)))
            # A true native obstacle at the second bend is only valid
            # for the unflipped loading of B02.
            if not poses["B02"]:
                return {
                    "status": "COLLISION",
                    "reason": "punch_or_die_collision_at:B02",
                    "checked_bends": 2,
                    "steps": [
                        {"bend_id": sequence[0],
                         "status": "COARSE_3D_NO_PROVEN_COLLISION"},
                        {"bend_id": sequence[1], "status": "COLLISION"},
                    ],
                }
            return {
                "status": "UNVERIFIED", "checked_bends": 0,
                "steps": [], "reason": "synthetic_unknown",
            }
        result = search_fast_feasible_sequence(
            session, tooling, budget_s=1000, max_candidates=3,
            planar_runner=lambda *args,**kwargs: planar,
            native_runner=native,
        )
        self.assertGreaterEqual(len(tried), 2)
        self.assertTrue(any(
            sequence == order and poses["B02"]
            for sequence, poses in tried
        ))
        self.assertEqual(result.attempts[0].status, "COLLISION")
        self.assertIsNone(result.sequence)
        self.assertFalse(result.machine_feasible_certified)

    def test_native_collision_prefix_skips_equivalent_suffixes(self):
        order = ("B01", "B02", "B03", "B04")
        session = SimpleNamespace(
            bend_order=order,
            fold_state=SimpleNamespace(bends={x: object() for x in order}),
            panel_build=object(), part_label="test",
        )
        tooling = SimpleNamespace(
            punch=SimpleNamespace(tool_id="p", length_mm=100),
            die=SimpleNamespace(tool_id="d", length_mm=100),
            match_active_bend_length=False,
            punch_section_offsets_xm=(), die_section_offsets_xm=(),
        )
        one = ("B01", "B02", "B03", "B04")
        two = ("B01", "B02", "B04", "B03")
        three = ("B02", "B01", "B03", "B04")
        def cand(seq):
            return SimpleNamespace(
                sequence=seq,
                evidence="SAMPLED_2D_CLEAR_OR_TOUCH",
                cost=1.0, cost_complete=True,
                steps=tuple(SimpleNamespace(
                    bend_id=b, orientation_variant="NORMAL",
                ) for b in seq),
            )
        plan = SimpleNamespace(
            status=SimpleNamespace(value="SAMPLED_2D_CANDIDATES"),
            limit_reason="", deepest_prefix=one,
            candidates=(cand(one),cand(two),cand(three)),
        )
        native_calls = []
        def native(_session, _tooling, sequence, **kwargs):
            native_calls.append(tuple(sequence))
            return {
                "status": "COLLISION",
                "reason": "punch_or_die_collision_at:" + sequence[1],
                "checked_bends": 2,
                "requested_bends": list(sequence),
                "steps": [
                    {"bend_id": sequence[0], "status": "COARSE_3D_NO_PROVEN_COLLISION"},
                    {"bend_id": sequence[1], "status": "COLLISION"},
                ],
            }
        result = search_fast_feasible_sequence(
            session, tooling, budget_s=1000,
            max_candidates=3,
            planar_runner=lambda *args,**kwargs: plan,
            native_runner=native,
        )
        self.assertEqual(len(native_calls), 2)
        self.assertEqual(
            [a.status for a in result.attempts],
            ["COLLISION", "COLLISION", "COLLISION"],
        )
        self.assertTrue(any(
            a.reason.startswith("reused_proven_colliding_prefix:")
            for a in result.attempts
        ))
        self.assertIsNone(result.sequence)

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
        self.assertEqual(cfg.coarse_occ_bending_fractions, (0.0, 1.0))

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
