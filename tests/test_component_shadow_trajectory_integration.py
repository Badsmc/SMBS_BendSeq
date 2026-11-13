"""Integration contract for opt-in shadow inside actual create_state_snapshot.

All FreeCAD BRep methods are mocked: this does NOT qualify physical CAD.
"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from smbs_core.kinematics2d.component_collision_oracle import (
    ComponentVerdict, MachineComponentScreen, count_component_shadow_checks,
)
from smbs_core.kinematics2d.motion_trajectory_3d import (
    AdaptiveSamplingConfig, AdaptiveTrajectoryValidator3D,
)
from smbs_core.kinematics2d.native_bend_solid_3d import NativeSheetComponents
from smbs_core.kinematics2d.native_collision_3d import (
    CollisionClass, PairClearanceResult, ToolCollisionReport,
)


class FakeShape:
    def copy(self):
        return FakeShape()

    def transformShape(self, mat):
        return None


def fake_report():
    punch = PairClearanceResult(
        CollisionClass.CLEAR, "aabb_separated", 5.0, 0.0, 5.0, 0.0,
    )
    die = PairClearanceResult(
        CollisionClass.CLEAR, "aabb_separated", 5.0, 0.0, 5.0, 0.0,
    )
    return ToolCollisionReport(
        CollisionClass.CLEAR, punch, die, True, [], 0.0,
    )


def fake_validator(*, probes):
    angles = {"B01": 0.0}
    def set_angle(bend_id, angle):
        angles[bend_id] = angle
    fold = SimpleNamespace(
        angles=angles,
        set_angle=set_angle,
        ordered_bends=[SimpleNamespace(
            bend_id="B01",
            bend_zone=SimpleNamespace(thickness_mm=1.5),
        )],
    )
    panel = SimpleNamespace(
        panels=[object()], panel_faces=[object()],
    )
    die = SimpleNamespace(
        v_opening_mm=12.0,
        effective_v_opening_mm=12.0,
        machine_shape=FakeShape(),
    )
    with patch.dict(os.environ, {"SMBS_COMPONENT_SHADOW_SAMPLES": str(probes)}):
        return AdaptiveTrajectoryValidator3D(
            fold_state=fold, panel_build=panel,
            punch_base=object(), die=die,
            config=AdaptiveSamplingConfig(),
        )


def actual_pose():
    return SimpleNamespace(
        rotation=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
        translation=(0, 0, 0),
    )


class ComponentShadowIntegrationTests(unittest.TestCase):
    def evaluate(self, *, probes=1, screen_fails=False):
        validator = fake_validator(probes=probes)
        observed_builder_kwargs = []
        probe = MachineComponentScreen(
            ComponentVerdict.FALLBACK_REQUIRED,
            "all_aabbs_disjoint_but_topology_unverified",
            "PUNCH", 2, 2, 0, 0, 0, 0.1,
        )
        components = NativeSheetComponents(
            (("P01", FakeShape()),),
            (("bend_zone:B01", FakeShape()),), 1.0,
        )

        def builder(*args, **kwargs):
            observed_builder_kwargs.append(kwargs)
            observer = kwargs.get("component_observer")
            if observer is not None:
                observer(components)
            return SimpleNamespace(shape=FakeShape())

        mat = lambda *v: SimpleNamespace(dx=v[3], dy=v[7])
        mod = "smbs_core.kinematics2d.motion_trajectory_3d."
        reporter = fake_report()
        with (
            patch.dict(sys.modules, {"FreeCAD": SimpleNamespace(Matrix=mat)}),
            patch(mod + "_panel_build_geometry_signature", return_value=("panelA",)),
            patch(mod + "_punch_geometry_signature", return_value=("punchA",)),
            patch(mod + "_die_geometry_signature", return_value=("dieA",)),
            patch(mod + "WorkpiecePlacement3D.compute_placement", return_value=actual_pose()),
            patch(mod + "build_multi_bend_sheet_solid", side_effect=builder),
            patch(mod + "set_punch_ram_z", return_value=SimpleNamespace(machine_shape=FakeShape())),
            patch(mod + "validate_workpiece_tooling_pose", return_value=reporter),
            patch(mod + "screen_machine_component_overlap",
                  side_effect=(RuntimeError("diagnostic") if screen_fails else
                               lambda *a, **kw: probe)) as screen,
            count_component_shadow_checks() as stats,
        ):
            state = validator.create_state_snapshot(
                "B01", "BENDING", 0.5, 45.0, 1.0, 0.0, {},
            )
            same_cached = validator.create_state_snapshot(
                "B01", "BENDING", 0.5, 45.0, 1.0, 0.0, {},
            )
        return state, same_cached, reporter, observed_builder_kwargs, screen, stats

    def test_shadow_runs_before_full_builder_and_never_overrides_original_result(self):
        st, cached, oracle, kwargs, screen, stats = self.evaluate(probes=1)
        self.assertIs(st.collision_report, oracle)
        self.assertIs(cached.collision_report, oracle)
        self.assertTrue(st.is_safe)
        self.assertTrue(cached.is_safe)
        self.assertEqual(stats.probes, 1)
        self.assertEqual(stats.exact_tool_screens, 2)
        self.assertEqual(screen.call_count, 2)
        self.assertEqual(len(kwargs), 1)
        self.assertIn("component_observer", kwargs[0])

    def test_real_retract_blocker_probed_even_if_sheet_is_cached(self):
        validator = fake_validator(probes=2)
        clear = fake_report()
        penetration = PairClearanceResult(
            CollisionClass.PENETRATION, "exact_positive",
            0.0, 2.752, 0.0, 1.0,
        )
        blocked = ToolCollisionReport(
            CollisionClass.PENETRATION, penetration, clear.die_result,
            False, ["PUNCH_PENETRATION"], 1.0,
        )
        native_components = NativeSheetComponents(
            (("P01", FakeShape()),),
            (("bend_zone:B09", FakeShape()),), 1.0,
        )
        build_calls = []
        def build(*args, **kwargs):
            build_calls.append(True)
            observer = kwargs.get("component_observer")
            if observer:
                observer(native_components)
            return SimpleNamespace(shape=FakeShape())

        probe = MachineComponentScreen(
            ComponentVerdict.PROVEN_PENETRATION, "exact_positive",
            "PUNCH", 2, 0, 2, 1, 1, 0.1,
            hit=SimpleNamespace(component_id="bend_zone:B09"),
        )
        mat = lambda *v: SimpleNamespace(dx=v[3], dy=v[7])
        mod = "smbs_core.kinematics2d.motion_trajectory_3d."
        with (
            patch.dict(sys.modules, {"FreeCAD": SimpleNamespace(Matrix=mat)}),
            patch(mod + "_panel_build_geometry_signature", return_value=("panelA",)),
            patch(mod + "_punch_geometry_signature", return_value=("punchA",)),
            patch(mod + "_die_geometry_signature", return_value=("dieA",)),
            patch(mod + "WorkpiecePlacement3D.compute_placement", return_value=actual_pose()),
            patch(mod + "build_multi_bend_sheet_solid", side_effect=build),
            patch(mod + "build_multi_bend_sheet_components", return_value=native_components) as rebuild,
            patch(mod + "set_punch_ram_z", return_value=SimpleNamespace(machine_shape=FakeShape())),
            patch(mod + "validate_workpiece_tooling_pose", side_effect=[clear, blocked]),
            patch(
                mod + "screen_machine_component_overlap",
                side_effect=[
                    MachineComponentScreen(ComponentVerdict.FALLBACK_REQUIRED, "no_hit",
                                           "PUNCH", 2, 1, 1, 1, 1, 0.1),
                    MachineComponentScreen(ComponentVerdict.FALLBACK_REQUIRED, "no_hit",
                                           "DIE", 2, 1, 1, 1, 1, 0.1),
                    probe,
                    MachineComponentScreen(ComponentVerdict.FALLBACK_REQUIRED, "no_hit",
                                           "DIE", 2, 1, 1, 1, 1, 0.1),
                ],
            ) as screen,
            count_component_shadow_checks() as stats,
        ):
            initial = validator.create_state_snapshot(
                "B01", "BENDING", 1.0, 90.0, 1.0, 0.0, {},
            )
            retract = validator.create_state_snapshot(
                "B01", "RETRACT", 0.2, 90.0, 1.3, 0.0, {},
            )
        self.assertTrue(initial.is_safe)
        self.assertFalse(retract.is_safe)
        self.assertIs(retract.collision_report, blocked)
        self.assertEqual(len(build_calls), 1)  # no second multiFuse
        rebuild.assert_called_once()
        self.assertEqual(stats.component_rebuild_calls, 1)
        self.assertGreaterEqual(stats.component_rebuild_s, 0.0)
        self.assertEqual(screen.call_count, 4)
        self.assertEqual(stats.probes, 2)
        self.assertEqual(stats.proven_component_hits, 1)
        self.assertEqual(stats.disagreements, 0)

    def test_conditional_retract_does_not_trigger_blocker_screen(self):
        validator = fake_validator(probes=2)
        conditional = fake_report()
        conditional = ToolCollisionReport(
            CollisionClass.PENETRATION,
            PairClearanceResult(CollisionClass.PENETRATION, "end_zone", 0, 50, 0, 0),
            conditional.die_result, False, [], 0.0,
            is_conditional_safe=True, conditional_status="CONDITIONAL",
        )
        native_components = NativeSheetComponents(
            (("P01", FakeShape()),), (("bend_zone:B01", FakeShape()),), 1.0,
        )
        def build(*args, **kwargs):
            if kwargs.get("component_observer"):
                kwargs["component_observer"](native_components)
            return SimpleNamespace(shape=FakeShape())
        mod = "smbs_core.kinematics2d.motion_trajectory_3d."
        mat = lambda *v: SimpleNamespace(dx=v[3], dy=v[7])
        with (
            patch.dict(sys.modules, {"FreeCAD": SimpleNamespace(Matrix=mat)}),
            patch(mod + "_panel_build_geometry_signature", return_value=("panelA",)),
            patch(mod + "_punch_geometry_signature", return_value=("punchA",)),
            patch(mod + "_die_geometry_signature", return_value=("dieA",)),
            patch(mod + "WorkpiecePlacement3D.compute_placement", return_value=actual_pose()),
            patch(mod + "build_multi_bend_sheet_solid", side_effect=build),
            patch(mod + "build_multi_bend_sheet_components") as rebuild,
            patch(mod + "set_punch_ram_z", return_value=SimpleNamespace(machine_shape=FakeShape())),
            patch(mod + "validate_workpiece_tooling_pose", side_effect=[fake_report(), conditional]),
            patch(mod + "screen_machine_component_overlap",
                  return_value=MachineComponentScreen(
                      ComponentVerdict.FALLBACK_REQUIRED, "no_proof",
                      "PUNCH", 2, 1, 1, 1, 1, 0.1)),
            count_component_shadow_checks() as stats,
        ):
            validator.create_state_snapshot("B01", "BENDING", 1.0, 90.0, 1.0, 0.0, {})
            retract = validator.create_state_snapshot("B01", "RETRACT", 0.2, 90.0, 1.3, 0.0, {})
        self.assertFalse(retract.is_safe)
        self.assertTrue(retract.is_conditional_safe)
        rebuild.assert_not_called()
        self.assertEqual(stats.component_rebuild_calls, 0)
        self.assertEqual(stats.probes, 1)

    def test_native_oracle_accepts_validity_proof_only_for_exact_same_object(self):
        from smbs_core.kinematics2d import native_collision_3d as native
        part = FakeShape()
        punch = FakeShape()
        die = FakeShape()
        known = (part, (True, "valid"))
        with (
            patch.object(native, "check_pair_clearance", return_value=fake_report().punch_result) as pair,
            patch.object(native, "check_solid_validity", return_value=(True, "valid")) as check,
        ):
            report = native.validate_workpiece_tooling_pose(
                part, punch, die, _validated_workpiece_shape=known)
            self.assertTrue(report.is_safe)
            check.assert_not_called()
            self.assertEqual(pair.call_count, 2)
            self.assertIs(pair.call_args_list[0].kwargs["_shared_shape_a_check"], known)
            self.assertIs(pair.call_args_list[1].kwargs["_shared_shape_a_check"], known)
        other_part = FakeShape()
        with (
            patch.object(native, "check_pair_clearance", return_value=fake_report().punch_result) as pair,
            patch.object(native, "check_solid_validity", return_value=(True, "valid")) as check,
        ):
            report = native.validate_workpiece_tooling_pose(
                other_part, punch, die, _validated_workpiece_shape=known)
            self.assertTrue(report.is_safe)
            check.assert_called_once_with(other_part, "shape_a")
            self.assertIs(pair.call_args_list[0].kwargs["_shared_shape_a_check"][0], other_part)

    def test_opt_in_reuses_exact_same_machine_sheet_at_new_ram(self):
        validator = fake_validator(probes=0)
        base = FakeShape()
        mod = "smbs_core.kinematics2d.motion_trajectory_3d."
        with (
            patch.dict(os.environ, {
                "SMBS_MACHINE_POSE_REUSE": "1",
                "SMBS_COMPONENT_REJECT": "0",
            }),
            patch.dict(sys.modules, {"FreeCAD": SimpleNamespace(Matrix=lambda *v: object())}),
            patch(mod + "_panel_build_geometry_signature", return_value=("panelA",)),
            patch(mod + "_punch_geometry_signature", return_value=("punchA",)),
            patch(mod + "_die_geometry_signature", return_value=("dieA",)),
            patch(mod + "WorkpiecePlacement3D.compute_placement", return_value=actual_pose()),
            patch(mod + "build_multi_bend_sheet_solid", return_value=SimpleNamespace(shape=base)) as builder,
            patch.object(base, "copy", wraps=base.copy) as copies,
            patch(mod + "check_solid_validity", return_value=(True, "valid")) as checks,
            patch(mod + "set_punch_ram_z", return_value=SimpleNamespace(machine_shape=FakeShape())),
            patch(mod + "validate_workpiece_tooling_pose", return_value=fake_report()) as full,
        ):
            a = validator.create_state_snapshot("B01", "BOTTOM", 1., 90., 1., 0., {})
            b = validator.create_state_snapshot("B01", "RETRACT", 0.5, 90., 2., 0., {})
            self.assertTrue(a.is_safe)
            self.assertTrue(b.is_safe)
            self.assertEqual(builder.call_count, 1)
            self.assertEqual(copies.call_count, 1)
            self.assertEqual(checks.call_count, 1)
            self.assertEqual(full.call_count, 2)
            self.assertIs(full.call_args_list[0].args[0], full.call_args_list[1].args[0])
            self.assertIs(full.call_args_list[0].kwargs["_validated_workpiece_shape"][0],
                          full.call_args_list[1].args[0])
            self.assertEqual(validator.machine_pose_reuse_hits, 1)
            self.assertEqual(validator.machine_pose_reuse_misses, 1)
            validator.clear_caches()
            self.assertIsNone(validator._machine_pose_cache)

    def test_opt_in_does_not_reuse_after_placement_changes(self):
        validator = fake_validator(probes=0)
        base = FakeShape()
        shifted = SimpleNamespace(
            rotation=actual_pose().rotation, translation=(0., 2., 0.))
        mod = "smbs_core.kinematics2d.motion_trajectory_3d."
        with (
            patch.dict(os.environ, {"SMBS_MACHINE_POSE_REUSE": "1",
                                     "SMBS_COMPONENT_REJECT": "0"}),
            patch.dict(sys.modules, {"FreeCAD": SimpleNamespace(Matrix=lambda *v: object())}),
            patch(mod + "_panel_build_geometry_signature", return_value=("panelA",)),
            patch(mod + "_punch_geometry_signature", return_value=("punchA",)),
            patch(mod + "_die_geometry_signature", return_value=("dieA",)),
            patch(mod + "WorkpiecePlacement3D.compute_placement",
                  side_effect=[actual_pose(), shifted]),
            patch(mod + "build_multi_bend_sheet_solid", return_value=SimpleNamespace(shape=base)),
            patch.object(base, "copy", wraps=base.copy) as copies,
            patch(mod + "check_solid_validity", return_value=(True, "valid")),
            patch(mod + "set_punch_ram_z", return_value=SimpleNamespace(machine_shape=FakeShape())),
            patch(mod + "validate_workpiece_tooling_pose", return_value=fake_report()) as full,
        ):
            validator.create_state_snapshot("B01", "BOTTOM", 1., 90., 1., 0., {})
            validator.create_state_snapshot("B01", "RETRACT", 0.5, 90., 2., 0., {})
            self.assertEqual(copies.call_count, 2)
            self.assertIsNot(full.call_args_list[0].args[0],
                             full.call_args_list[1].args[0])
            self.assertEqual(validator.machine_pose_reuse_hits, 0)

    def test_default_off_never_shares_machine_shape(self):
        validator = fake_validator(probes=0)
        base = FakeShape()
        mod = "smbs_core.kinematics2d.motion_trajectory_3d."
        with (
            patch.dict(os.environ, {"SMBS_MACHINE_POSE_REUSE": "0",
                                     "SMBS_COMPONENT_REJECT": "0"}),
            patch.dict(sys.modules, {"FreeCAD": SimpleNamespace(Matrix=lambda *v: object())}),
            patch(mod + "_panel_build_geometry_signature", return_value=("panelA",)),
            patch(mod + "_punch_geometry_signature", return_value=("punchA",)),
            patch(mod + "_die_geometry_signature", return_value=("dieA",)),
            patch(mod + "WorkpiecePlacement3D.compute_placement", return_value=actual_pose()),
            patch(mod + "build_multi_bend_sheet_solid", return_value=SimpleNamespace(shape=base)),
            patch.object(base, "copy", wraps=base.copy) as copies,
            patch(mod + "check_solid_validity", return_value=(True, "valid")) as checks,
            patch(mod + "set_punch_ram_z", return_value=SimpleNamespace(machine_shape=FakeShape())),
            patch(mod + "validate_workpiece_tooling_pose", return_value=fake_report()) as full,
        ):
            validator.create_state_snapshot("B01", "BOTTOM", 1., 90., 1., 0., {})
            validator.create_state_snapshot("B01", "RETRACT", 0.5, 90., 2., 0., {})
            self.assertEqual(copies.call_count, 2)
            checks.assert_not_called()
            self.assertNotIn("_validated_workpiece_shape",
                             full.call_args_list[0].kwargs)
            self.assertEqual(validator.machine_pose_reuse_hits, 0)

    def test_default_off_uses_original_native_builder_signature(self):
        st, cached, oracle, kwargs, screen, stats = self.evaluate(probes=0)
        self.assertIs(st.collision_report, oracle)
        self.assertEqual(stats.probes, 0)
        self.assertEqual(screen.call_count, 0)
        self.assertEqual(len(kwargs), 1)
        self.assertNotIn("component_observer", kwargs[0])

    def test_shadow_exception_is_incomplete_not_a_physical_verdict(self):
        st, cached, oracle, kwargs, screen, stats = self.evaluate(
            probes=1, screen_fails=True,
        )
        self.assertTrue(st.is_safe)
        self.assertIs(st.collision_report, oracle)
        self.assertEqual(stats.probes, 1)
        self.assertEqual(stats.incomplete_probes, 1)
        self.assertEqual(stats.disagreements, 0)


if __name__ == "__main__":
    unittest.main()
