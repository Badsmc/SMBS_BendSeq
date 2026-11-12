"""Headless Manual 3D orchestration tests; FreeCAD native kernel is mocked.

These cover selected FCStd identities, completed-bend history, user-set
virtual length, conditional end zones, early collision stop, failures,
partial orders and nonmutation of the original fold state.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
from enum import Enum
from smbs_core.kinematics2d.manual_physical_validation import (
    _check_selected_tool_pair_at_sampled_rams,
)

from smbs_core.kinematics2d.manual_physical_validation import (
    check_manual_3d_sequence, concise_3d_blocker,
)
from smbs_core.kinematics2d.physical_validation_25d import PhysicalEvidence
from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.tooling_25d import ValidationStatus


def part():
    graph = PanelGraph(("P01", "P02", "P03"), (
        GraphEdge("B01", "P01", "P02", 90.0,
                  ((10, 0, 0), (10, 50, 0))),
        GraphEdge("B02", "P02", "P03", -45.0,
                  ((32, 0, 0), (32, 50, 0))),
    ))
    state = FoldState(graph, "P01")
    return SimpleNamespace(
        fold_state=state, panel_build=SimpleNamespace(),
        bend_order=("B01", "B02"),
    )


def evidence(status, *, conditional=False):
    return SimpleNamespace(
        status=status, native_status=status.value, reason="test_" + status.value,
        checked_scope=("3D_PUNCH_DIE_SAMPLED_BEND",),
        tool_length_info={"active_bend_length_mm": 50.0},
        raw_report=SimpleNamespace(
            ram_cycle_positions_mm={
                "contact_z_mm": 0.0, "bottom_z_mm": -4.0,
                "retract_start_z_mm": -4.0, "retract_end_z_mm": 5.0,
                "initial_clearance_mm": 5.0,
                "approach_start_z_mm": 5.0,
            },
            phase_results=(
                SimpleNamespace(
                    phase_name="RETRACT",
                    evidence_status=SimpleNamespace(
                        value="SAMPLED_INTERVAL_CONVERGED",
                    ),
                    states=(
                        SimpleNamespace(path_u=0.0, ram_z_mm=-4.0,
                                        collision_report=None),
                        SimpleNamespace(path_u=1.0, ram_z_mm=5.0,
                                        collision_report=None),
                    ),
                    diagnostics=(),
                    unresolved_intervals=(),
                    uninspected_intervals=(),
                    discontinuities=(),
                    max_punch_overlap_volume_mm3=0.0,
                    max_die_overlap_volume_mm3=0.0,
                ),
            ),
        ),
        is_conditional=conditional,
    )


class ManualToolingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.punch_file = root / "selected_punch.FCStd"
        self.die_file = root / "selected_die.FCStd"
        self.punch_file.write_bytes(b"test-tool")
        self.die_file.write_bytes(b"test-tool")
        self.tooling = SimpleNamespace(
            punch=SimpleNamespace(
                tool_id=self.punch_file.stem,
                name="Chosen Punch",
                source_file=str(self.punch_file),
                validation_status=ValidationStatus.GEOMETRY_VERIFIED,
            ),
            die=SimpleNamespace(
                tool_id=self.die_file.stem,
                name="Chosen Die",
                source_file=str(self.die_file),
                validation_status=ValidationStatus.GEOMETRY_VERIFIED,
            ),
            punch_section_offsets_xm=(),
            die_section_offsets_xm=(),
        )
        self.session = part()
        self.pair_patcher = patch(
            "smbs_core.kinematics2d.manual_physical_validation"
            "._check_selected_tool_pair_at_sampled_rams",
            return_value={
                "status": "SAMPLED_CLEAR",
                "reason": "test_pair_clear",
                "checked_ram_positions": 6,
            },
        )
        self.pair_mock = self.pair_patcher.start()
        self.addCleanup(self.pair_patcher.stop)

    def test_all_steps_use_selected_tools_and_signed_prior_geometry(self):
        original = dict(self.session.fold_state.angles)
        calls = []

        def fake(pseudo_session, selected_tools, **kwargs):
            bid = pseudo_session.active_bend_id
            calls.append((bid, dict(pseudo_session.fold_state.angles), kwargs))
            self.assertIs(selected_tools, self.tooling)
            self.assertIsNot(pseudo_session.fold_state, self.session.fold_state)
            self.assertEqual(pseudo_session.fold_state.angles[bid], 0.0)
            return evidence(PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS)

        with patch(
            "smbs_core.kinematics2d.manual_physical_validation"
            ".check_active_25d_bend_trajectory", side_effect=fake,
        ):
            report = check_manual_3d_sequence(
                self.session, self.tooling, ("B01", "B02"),
                match_active_bend_length=True,
            )
        self.assertEqual(report["status"], "SAMPLED_3D_CLEAR_NOT_CONTINUOUS")
        self.assertEqual(report["checked_bends"], 2)
        self.assertFalse(report["continuous_motion_certified"])
        self.assertFalse(report["backgauge_collisions_checked"])
        self.assertEqual(report["punch_die_pair_checked"], 2)
        self.assertEqual(report["steps"][0]["retract"]["status"],
                         "SAMPLED_CLEAR")
        self.assertEqual(report["steps"][0]["retract"]["start_z_mm"], -4.0)
        self.assertEqual(report["steps"][0]["retract"]["end_z_mm"], 5.0)
        self.assertEqual(report["punch"], self.punch_file.stem)
        self.assertEqual(report["die"], self.die_file.stem)
        self.assertEqual(calls[0][1], {"B01": 0.0, "B02": 0.0})
        self.assertEqual(calls[1][1], {"B01": 90.0, "B02": 0.0})
        self.assertTrue(all(call[2]["match_active_bend_length"] for call in calls))
        self.assertEqual(self.session.fold_state.angles, original)

    def test_collision_stops_at_first_proven_hit(self):
        def fake(*args, **kwargs):
            return evidence(PhysicalEvidence.COLLISION)
        with patch(
            "smbs_core.kinematics2d.manual_physical_validation"
            ".check_active_25d_bend_trajectory", side_effect=fake,
        ) as check:
            report = check_manual_3d_sequence(
                self.session, self.tooling, ("B01", "B02"),
            )
        self.assertEqual(report["status"], "COLLISION")
        self.assertEqual(report["checked_bends"], 1)
        check.assert_called_once()

    def test_end_zone_option_survives_and_never_becomes_clear(self):
        def fake(*args, **kwargs):
            self.assertTrue(kwargs["allow_end_zone_collision"])
            self.assertEqual(kwargs["end_zone_length_mm"], 10.0)
            return evidence(PhysicalEvidence.CONDITIONAL, conditional=True)
        with patch(
            "smbs_core.kinematics2d.manual_physical_validation"
            ".check_active_25d_bend_trajectory", side_effect=fake,
        ):
            report = check_manual_3d_sequence(
                self.session, self.tooling, ("B01", "B02"),
                allow_end_zone_collision=True, end_zone_length_mm=10.0,
            )
        self.assertEqual(report["status"], "CONDITIONAL")
        self.assertTrue(all(s["is_conditional"] for s in report["steps"]))

    def test_punch_die_tool_pair_intersection_is_blocking_even_if_sheet_clear(self):
        self.pair_mock.return_value = {
            "status": "COLLISION",
            "reason": "punch_die_positive_overlap",
            "checked_ram_positions": 3,
            "ram_z_mm": -4.0,
            "overlap_volume_mm3": 2.5,
        }
        with patch(
            "smbs_core.kinematics2d.manual_physical_validation"
            ".check_active_25d_bend_trajectory",
            return_value=evidence(
                PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS
            ),
        ) as check:
            report = check_manual_3d_sequence(
                self.session, self.tooling, ("B01", "B02"),
                allow_end_zone_collision=True,
            )
        self.assertEqual(report["status"], "COLLISION")
        self.assertEqual(report["checked_bends"], 1)
        self.assertIn("punch_die_tool_tool_collision", report["steps"][0]["reason"])
        check.assert_called_once()

    def test_missing_tool_pair_evidence_cannot_be_clear(self):
        self.pair_mock.return_value = {
            "status": "UNVERIFIED", "reason": "tool_shape_missing",
            "checked_ram_positions": 0,
        }
        with patch(
            "smbs_core.kinematics2d.manual_physical_validation"
            ".check_active_25d_bend_trajectory",
            return_value=evidence(
                PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS
            ),
        ):
            report = check_manual_3d_sequence(
                self.session, self.tooling, ("B01", "B02"),
            )
        self.assertEqual(report["status"], "UNVERIFIED")
        self.assertEqual(report["checked_bends"], 1)

    def test_reverse_loaded_bend_is_not_falsely_certified(self):
        with patch(
            "smbs_core.kinematics2d.manual_physical_validation"
            ".check_active_25d_bend_trajectory",
            return_value=evidence(
                PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS
            ),
        ) as check:
            report = check_manual_3d_sequence(
                self.session, self.tooling, ("B01", "B02"),
                reverse_axis_by_bend={"B01": True},
            )
        self.assertEqual(report["status"], "UNVERIFIED")
        self.assertIn("reverse_axis", report["reason"])
        check.assert_not_called()

    def test_tool_pair_checker_uses_every_distinct_sample_and_actual_source(self):
        class NativeStatus(Enum):
            CLEAR = "CLEAR"
            NEAR_CONTACT = "NEAR_CONTACT"
            TOUCHING = "TOUCHING"
            PENETRATION = "PENETRATION"

        called = []
        app = ModuleType("FreeCAD")
        app.ActiveDocument = None
        app.getDocument = lambda _name: None
        app.setActiveDocument = lambda _name: None
        loader = ModuleType("smbs_core.kinematics2d.native_tool_loader")
        def load_punch(file_path, **kw):
            self.assertEqual(Path(file_path), self.punch_file)
            self.assertEqual(kw["virtual_length_mm"], 50.0)
            return SimpleNamespace()
        def load_die(file_path, **kw):
            self.assertEqual(Path(file_path), self.die_file)
            self.assertEqual(kw["virtual_length_mm"], 50.0)
            return SimpleNamespace(machine_shape="die")
        def move_punch(_base, z):
            return SimpleNamespace(machine_shape=z)
        loader.load_native_punch = load_punch
        loader.load_native_die = load_die
        loader.set_punch_ram_z = move_punch
        collisions = ModuleType("smbs_core.kinematics2d.native_collision_3d")
        collisions.CollisionClass = NativeStatus
        def check_pair(a, b, **kwargs):
            self.assertEqual(b, "die")
            self.assertEqual(kwargs["tool_label"], "PUNCH_DIE")
            called.append(a)
            return SimpleNamespace(
                status=(NativeStatus.PENETRATION if a == -2.0
                        else NativeStatus.CLEAR),
                overlap_volume_mm3=2.5, reason="test",
            )
        collisions.check_pair_clearance = check_pair
        phases = SimpleNamespace(phase_results=(
            SimpleNamespace(states=(
                SimpleNamespace(ram_z_mm=12.0),
                SimpleNamespace(ram_z_mm=12.0),
                SimpleNamespace(ram_z_mm=3.0),
            )),
            SimpleNamespace(states=(
                SimpleNamespace(ram_z_mm=-2.0),
                SimpleNamespace(ram_z_mm=3.0),
            )),
        ))
        with patch.dict("sys.modules", {
            "FreeCAD": app,
            "smbs_core.kinematics2d.native_tool_loader": loader,
            "smbs_core.kinematics2d.native_collision_3d": collisions,
        }):
            result = _check_selected_tool_pair_at_sampled_rams(
                self.punch_file, self.die_file, 50.0,
                self.tooling, phases, virtual_length=True,
            )
        self.assertEqual(result["status"], "COLLISION")
        self.assertEqual(result["overlap_volume_mm3"], 2.5)
        self.assertEqual(result["checked_ram_positions"], 1)
        self.assertEqual(called, [-2.0])

    def test_incomplete_manual_prefix_is_unverified(self):
        with patch(
            "smbs_core.kinematics2d.manual_physical_validation"
            ".check_active_25d_bend_trajectory",
            return_value=evidence(
                PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS
            ),
        ):
            report = check_manual_3d_sequence(
                self.session, self.tooling, ("B01",),
            )
        self.assertEqual(report["status"], "UNVERIFIED")
        self.assertEqual(report["checked_bends"], 1)
        self.assertFalse(report["complete_part_order_requested"])

    def test_missing_real_fcstd_is_not_approved(self):
        self.tooling.die.source_file = "missing.FCStd"
        with patch(
            "smbs_core.kinematics2d.manual_physical_validation"
            ".check_active_25d_bend_trajectory",
        ) as check:
            report = check_manual_3d_sequence(
                self.session, self.tooling, ("B01", "B02"),
            )
        self.assertEqual(report["status"], "UNVERIFIED")
        self.assertEqual(report["checked_bends"], 0)
        check.assert_not_called()

    def test_multi_section_and_virtual_length_are_incompatible(self):
        self.tooling.punch_section_offsets_xm = (-50, 50)
        with patch(
            "smbs_core.kinematics2d.manual_physical_validation"
            ".check_active_25d_bend_trajectory",
        ) as check:
            report = check_manual_3d_sequence(
                self.session, self.tooling, ("B01", "B02"),
                match_active_bend_length=True,
            )
        self.assertEqual(report["status"], "UNVERIFIED")
        check.assert_not_called()

    def test_occt_exception_does_not_mutate_original_or_prove_clear(self):
        originals = (dict(self.session.fold_state.angles),
                     dict(self.session.fold_state.transforms))
        with patch(
            "smbs_core.kinematics2d.manual_physical_validation"
            ".check_active_25d_bend_trajectory",
            side_effect=RuntimeError("OCC boolean timeout"),
        ):
            report = check_manual_3d_sequence(
                self.session, self.tooling, ("B01", "B02"),
            )
        self.assertEqual(report["status"], "UNVERIFIED")
        self.assertEqual(report["checked_bends"], 1)
        self.assertIn("OCC boolean timeout", report["steps"][0]["reason"])
        self.assertEqual(self.session.fold_state.angles, originals[0])
        self.assertEqual(self.session.fold_state.transforms, originals[1])

    def test_unknown_or_misordered_manual_prefix_fails(self):
        for order in (("B02",), ("B01", "B01"), ("B99",)):
            with self.subTest(order=order):
                with self.assertRaises(ValueError):
                    check_manual_3d_sequence(self.session, self.tooling, order)



class NativePhaseBlockerTests(unittest.TestCase):
    """A long log must not hide the BENDING/BOTTOM phase that rejected B03."""

    @staticmethod
    def _phase(name, verdict, *, states=(), punch_overlap=0.0,
               die_overlap=0.0, discontinuities=()):
        return SimpleNamespace(
            phase_name=name,
            evidence_status=SimpleNamespace(value=verdict),
            states=states,
            max_punch_overlap_volume_mm3=punch_overlap,
            max_die_overlap_volume_mm3=die_overlap,
            diagnostics=[
                "TIMINGS: phase=2500ms, cache_lookups=0.2ms",
                "native collision evidence",
            ],
            discontinuities=discontinuities,
            unresolved_intervals=(),
            uninspected_intervals=(),
        )

    @staticmethod
    def _state(tool, z, angle, phase):
        punch_state = "PENETRATION" if tool == "PUNCH" else "CLEAR"
        die_state = "PENETRATION" if tool == "DIE" else "CLEAR"
        return SimpleNamespace(
            phase=phase, ram_z_mm=z, signed_angle_deg=angle,
            is_safe=False, is_conditional_safe=False,
            collision_report=SimpleNamespace(
                status=SimpleNamespace(value="PENETRATION"),
                punch_result=SimpleNamespace(
                    status=SimpleNamespace(value=punch_state),
                ),
                die_result=SimpleNamespace(
                    status=SimpleNamespace(value=die_state),
                ),
            ),
        )

    def test_clear_approach_and_contact_do_not_hide_bending_die_penetration(self):
        hit = self._state("DIE", -3.5, 62.0, "BENDING")
        trajectory = SimpleNamespace(
            phase_results=[
                self._phase("APPROACH", "SAMPLED_INTERVAL_CONVERGED"),
                self._phase("CONTACT", "SAMPLED_INTERVAL_CONVERGED"),
                self._phase("BENDING", "DISCRETE_SAMPLES_REJECTED",
                            states=(hit,), die_overlap=12.543210),
            ],
            first_collision_state=hit,
        )
        result = concise_3d_blocker(trajectory)
        self.assertEqual(result["phase"], "BENDING")
        self.assertEqual(result["tool"], "DIE")
        self.assertAlmostEqual(result["ram_z_mm"], -3.5)
        self.assertAlmostEqual(result["angle_deg"], 62.0)
        self.assertEqual(result["die_overlap_mm3"], 12.54321)
        self.assertEqual(result["reason"], "native collision evidence")

    def test_punch_penetration_reports_correct_instrument(self):
        hit = self._state("PUNCH", -2.0, 25.0, "BOTTOM")
        trajectory = SimpleNamespace(
            phase_results=[
                self._phase("BOTTOM", "DISCRETE_SAMPLES_REJECTED",
                            states=(hit,), punch_overlap=3.2),
            ],
            first_collision_state=hit,
        )
        result = concise_3d_blocker(trajectory)
        self.assertEqual(result["phase"], "BOTTOM")
        self.assertEqual(result["tool"], "PUNCH")
        self.assertAlmostEqual(result["punch_overlap_mm3"], 3.2)

    def test_native_discontinuity_not_falsely_called_tool_penetration(self):
        trajectory = SimpleNamespace(
            phase_results=[
                self._phase("BENDING", "DISCRETE_SAMPLES_REJECTED",
                            discontinuities=("machine_pose_jump",)),
            ],
            first_collision_state=None,
        )
        result = concise_3d_blocker(trajectory)
        self.assertEqual(result["tool"], "UNKNOWN")
        self.assertIn("machine_pose_jump", result["reason"])

    def test_clear_trajectory_has_no_blocker(self):
        trajectory = SimpleNamespace(
            phase_results=[
                self._phase("BENDING", "SAMPLED_INTERVAL_CONVERGED"),
                self._phase("RETRACT", "SAMPLED_INTERVAL_CONVERGED"),
            ]
        )
        self.assertEqual(concise_3d_blocker(trajectory)["phase"], "NONE")


if __name__ == "__main__":
    unittest.main()
