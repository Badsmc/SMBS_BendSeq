"""Reject-only contracts with fake BReps; NOT native FreeCAD acceptance."""
import os
import sys
import unittest
from contextlib import ExitStack
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch

from smbs_core.kinematics2d import component_collision_oracle as oracle
from smbs_core.kinematics2d.native_collision_3d import (
    CollisionClass, EndZoneCollisionPolicy, PunchCollisionZoneClassification as Zone,
)
from smbs_core.kinematics2d.native_bend_solid_3d import NativeSheetComponents
from smbs_core.kinematics2d.motion_trajectory_3d import is_blocking_collision
from tests.test_component_machine_shadow import box, placement
from tests.test_component_shadow_trajectory_integration import (
    fake_validator, fake_report, actual_pose, FakeShape as PoseShape,
)


class Shape:
    Volume = 1.0
    def __init__(self, bounds=None, valid=True):
        self.BoundBox = bounds or box(0, 1)
        self.valid = valid
    def isNull(self): return False
    def isValid(self): return self.valid
    def isClosed(self): return True
    def copy(self): return Shape(self.BoundBox, self.valid)
    def transformShape(self, matrix): pass


class RejectProofTests(unittest.TestCase):
    def screen(self, *, components=None, volume=2.0, classification=Zone.CENTRAL_ZONE,
               central=2.0, allow=False, die=None, punch=None, **limits):
        common_shape = Shape()
        analysis = NS(classification=classification, central_zone_overlap_volume_mm3=central)
        with (
            patch.object(oracle, '_evaluate_common_volume',
                         side_effect=volume if isinstance(volume, Exception) else None,
                         return_value=(volume, {'xmin': 0, 'xmax': 1}, common_shape)) as common,
            patch('smbs_core.kinematics2d.native_collision_3d.classify_punch_overlap_zones',
                  return_value=analysis) as classify,
        ):
            result = oracle.prove_machine_component_blocker(
                components if components is not None else (('P01', Shape()),),
                placement(), object(), punch or Shape(), die or Shape(box(10, 11)),
                volume_tolerance_mm3=1e-4,
                end_zone_policy=EndZoneCollisionPolicy(allow, 0.1), **limits)
        return result, common, classify, common_shape

    def test_proven_punch_rejection_keeps_other_tool_unknown(self):
        got, common, _, _ = self.screen()
        self.assertEqual(got.report.status, CollisionClass.PENETRATION)
        self.assertEqual(got.report.die_result.status, CollisionClass.MISSING_EVIDENCE)
        self.assertFalse(got.report.is_safe)
        self.assertEqual(got.evidence['hit']['component_id'], 'P01')
        self.assertEqual(common.call_count, 1)

    def test_die_rejection_is_not_waived_by_punch_policy(self):
        got, _, classify, _ = self.screen(allow=True, die=Shape(), punch=Shape(box(10, 11)))
        self.assertEqual(got.report.die_result.status, CollisionClass.PENETRATION)
        classify.assert_not_called()

    def test_central_overlap_uses_same_common_for_end_zone_classifier(self):
        got, common, classify, shape = self.screen(allow=True)
        self.assertIsNotNone(got.report)
        self.assertEqual(common.call_count, 1)
        self.assertIs(classify.call_args.kwargs['_precomputed_common'], shape)

    def test_end_only_unclassified_and_tiny_central_always_fall_back(self):
        for zone, central in ((Zone.END_ZONE_ONLY, 0), (Zone.UNCLASSIFIED, 2),
                              (Zone.MIXED_CENTRAL_AND_END_ZONE, 1e-5)):
            with self.subTest(zone=zone):
                got, _, _, _ = self.screen(allow=True, classification=zone, central=central)
                self.assertIsNone(got.report)

    def test_end_only_first_hit_cannot_hide_later_central_hit(self):
        with patch.object(oracle, '_evaluate_common_volume', return_value=(2., None, Shape())), patch(
            'smbs_core.kinematics2d.native_collision_3d.classify_punch_overlap_zones',
            side_effect=[NS(classification=Zone.END_ZONE_ONLY, central_zone_overlap_volume_mm3=0),
                         NS(classification=Zone.CENTRAL_ZONE, central_zone_overlap_volume_mm3=2)]):
            got = oracle.prove_machine_component_blocker(
                (('P01', Shape()), ('B09', Shape())), placement(), object(),
                Shape(), Shape(box(10, 11)), volume_tolerance_mm3=1e-4,
                end_zone_policy=EndZoneCollisionPolicy(True, 0.1))
        self.assertIsNotNone(got.report)
        self.assertEqual(got.evidence['hit']['component_id'], 'B09')

    def test_nonfinite_common_cannot_prove_a_blocker(self):
        for volume in (float('nan'), float('inf'), -1.):
            self.assertIsNone(self.screen(volume=volume)[0].report)

    def test_disjoint_boxes_never_run_common_or_claim_clear(self):
        got, common, _, _ = self.screen(components=(('P01', Shape(box(20, 21))),))
        self.assertIsNone(got.report)
        common.assert_not_called()
        self.assertEqual(got.evidence['aabb_rejected'], 2)

    def test_contact_and_small_overlap_fall_back(self):
        for volume in (0, 1e-5, 1e-4):
            self.assertIsNone(self.screen(volume=volume)[0].report)

    def test_invalid_component_after_potential_hit_prevents_rejection(self):
        got, common, _, _ = self.screen(components=(('P01', Shape()), ('P02', Shape(valid=False))))
        self.assertIsNone(got.report)
        common.assert_not_called()

    def test_invalid_tool_and_duplicate_component_fall_back(self):
        self.assertIsNone(self.screen(punch=Shape(valid=False))[0].report)
        self.assertIsNone(self.screen(components=(('P01', Shape()), ('P01', Shape())))[0].report)

    def test_occ_failure_falls_back(self):
        self.assertIsNone(self.screen(volume=RuntimeError('OCC failed'))[0].report)

    def test_candidate_and_time_limits_fall_back(self):
        got, common, _, _ = self.screen(die=Shape(), max_exact_candidates=1)
        self.assertIsNone(got.report)
        common.assert_not_called()
        self.assertIsNone(self.screen(max_screen_s=0)[0].report)

    def test_expired_during_occ_discards_shortcut(self):
        clock = [0.0]
        def common(*args, **kwargs):
            clock[0] = 3.0
            return 2.0, None, Shape()
        with patch.object(oracle.time, 'perf_counter', side_effect=lambda: clock[0]), patch.object(
                oracle, '_evaluate_common_volume', side_effect=common):
            got = oracle.prove_machine_component_blocker(
                (('P01', Shape()),), placement(), object(), Shape(), Shape(),
                volume_tolerance_mm3=1e-4, max_screen_s=2)
        self.assertIsNone(got.report)
        self.assertEqual(got.evidence['reason'], 'time_budget_fallback')


class RejectIntegrationTests(unittest.TestCase):
    def run_states(self, outcomes, *, enabled=True, same_angle=False):
        validator = fake_validator(probes=0)
        components = NativeSheetComponents((('P01', PoseShape()),), (), 0.0)
        calls = {'union': 0}
        def build(*a, **kw):
            if kw.get('component_observer'):
                kw['component_observer'](components)
            calls['union'] += 1
            return NS(shape=PoseShape())
        mod = 'smbs_core.kinematics2d.motion_trajectory_3d.'
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'SMBS_COMPONENT_REJECT': '1' if enabled else '0'}))
            stack.enter_context(patch.dict(sys.modules, {'FreeCAD': NS(Matrix=lambda *v: object())}))
            for name in ('_panel_build_geometry_signature', '_punch_geometry_signature', '_die_geometry_signature'):
                stack.enter_context(patch(mod + name, return_value=('A',)))
            stack.enter_context(patch(mod + 'WorkpiecePlacement3D.compute_placement', return_value=actual_pose()))
            stack.enter_context(patch(mod + 'set_punch_ram_z', return_value=NS(machine_shape=PoseShape())))
            stack.enter_context(patch(mod + 'build_multi_bend_sheet_solid', side_effect=build))
            rebuild = stack.enter_context(patch(mod + 'build_multi_bend_sheet_components', return_value=components))
            screen = stack.enter_context(patch(mod + 'prove_machine_component_blocker', side_effect=outcomes))
            full = stack.enter_context(patch(mod + 'validate_workpiece_tooling_pose', return_value=fake_report()))
            stack.enter_context(patch('smbs_core.kinematics2d.developer_debug_log.debug_event'))
            states = [validator.create_state_snapshot('B01', 'RETRACT' if same_angle else 'BENDING',
                      i / 10, 45 if same_angle else 45 + i, 1 + i, 0, {}) for i in range(len(outcomes))]
        return validator, states, calls, full, screen, rebuild

    @staticmethod
    def outcome(block=False):
        report = None
        if block:
            clear = fake_report()
            report = replace(clear, status=CollisionClass.PENETRATION, is_safe=False,
                             punch_result=replace(clear.punch_result, status=CollisionClass.PENETRATION,
                                                  overlap_volume_mm3=2.0))
        return oracle.ComponentBlockerScreen(report, {'common_calls': 1})

    def test_proof_skips_union_and_full_oracle_and_never_populates_pair_cache(self):
        v, states, calls, full, _, _ = self.run_states([self.outcome(True)])
        self.assertTrue(is_blocking_collision(states[0]))
        self.assertEqual(calls['union'], 0)
        full.assert_not_called()
        self.assertFalse(v._solid_cache)
        self.assertFalse(v._punch_clearance_cache)
        self.assertFalse(v._pose_collision_cache)
        self.assertEqual(v.actual_common_queries, 1)

    def test_no_proof_and_exception_preserve_full_oracle(self):
        for result in (self.outcome(), RuntimeError('failed')):
            _, states, calls, full, _, _ = self.run_states([result])
            self.assertTrue(states[0].is_safe)
            self.assertEqual(calls['union'], 1)
            self.assertEqual(full.call_count, 1)

    def test_disabled_mode_keeps_full_validator(self):
        _, states, calls, full, screen, _ = self.run_states([self.outcome(True)], enabled=False)
        self.assertTrue(states[0].is_safe)
        screen.assert_not_called()
        self.assertEqual(full.call_count, 1)

    def test_cached_fold_components_reused_for_retract_at_new_ram(self):
        v, states, calls, full, screen, rebuild = self.run_states(
            [self.outcome(), self.outcome(True)], same_angle=True)
        self.assertTrue(states[0].is_safe)
        self.assertTrue(is_blocking_collision(states[1]))
        self.assertEqual(full.call_count, 1)
        self.assertEqual(calls['union'], 1)
        rebuild.assert_not_called()
        self.assertEqual(screen.call_count, 2)
        v.clear_caches()
        self.assertFalse(v._component_reject_cache)
