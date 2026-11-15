"""Conservative contracts for the optional punch-assisted RETRACT experiment."""
import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.native_assisted_retract import (
    sample_synchronous_lift, inspect_punch_assisted_retract,
    eligible_assisted_retract,
    historic_lift_candidate,
    sample_lateral_release,
)


def result(punch="TOUCHING", die="CLEAR", conditional=False, zone="END_ZONE_ONLY"):
    def pair(status):
        return SimpleNamespace(status=status, overlap_volume_mm3=(2.0 if status == "PENETRATION" else 0.0))
    return SimpleNamespace(
        punch_result=pair(punch), die_result=pair(die),
        is_conditional_safe=conditional,
        conditional_status="CONDITIONAL" if conditional else None,
        punch_end_zone_analysis=SimpleNamespace(
            classification=SimpleNamespace(value=zone),
            central_zone_overlap_volume_mm3=0.0 if zone == "END_ZONE_ONLY" else 1.0,
        ),
    )


class AssistedRetractTests(unittest.TestCase):
    def test_release_samples_both_directions_and_never_certifies_clear(self):
        box = lambda lo, hi: SimpleNamespace(YMin=lo, YMax=hi)
        sheet = SimpleNamespace(BoundBox=box(0, 2), dy=0.0)
        punch = SimpleNamespace(BoundBox=box(-2, 2))
        die = SimpleNamespace(BoundBox=box(-2, 2))

        def check(moved, _punch, _die):
            if moved.dy < 0:
                return result(punch="PENETRATION", zone="CENTRAL_ZONE")
            if moved.dy == 0:
                return result(punch="PENETRATION", conditional=True)
            return result(punch="CLEAR" if moved.dy >= 2 else "TOUCHING")

        report = sample_lateral_release(
            sheet, punch, die,
            translate_y=lambda shape, dy: SimpleNamespace(dy=dy),
            check=check, samples=5,
        )
        self.assertEqual(report["status"], "SAMPLED_RELEASE_ROUTE_UNVERIFIED")
        self.assertEqual(report["candidate_directions_y"], [1])
        self.assertEqual(report["paths"][0]["status"], "SAMPLED_RELEASE_BLOCKED")
        self.assertTrue(report["paths"][1]["samples"][0]["conditional_end_zone"])
        self.assertFalse(report["continuous_certification"])
        self.assertFalse(report["coupling_proven"])

    def setUp(self):
        self.calls = []

    def evaluate(self, status=None):
        def check(sheet, punch, die):
            self.calls.append((sheet, punch, die))
            return status(sheet, punch) if status else result()
        return sample_synchronous_lift(
            10.0, 20.0, "die", hit_ram_z_mm=1.0,
            bottom_ram_z_mm=-2.0, upper_ram_z_mm=6.0,
            samples=5, translate=lambda shape, dz: shape + dz,
            check=check,
        )

    def test_synchronous_motion_never_certifies_clear(self):
        report = self.evaluate()
        self.assertEqual(report["status"], "SAMPLED_LIFT_WITHOUT_PENETRATION_UNVERIFIED")
        self.assertFalse(report["continuous_certification"])
        self.assertEqual([x["ram_z_mm"] for x in report["samples"]], [-2, 0, 2, 4, 6])
        self.assertEqual([c[0] for c in self.calls], [10, 12, 14, 16, 18])
        self.assertEqual([c[1] for c in self.calls], [17, 19, 21, 23, 25])

    def test_die_obstruction_stops_at_first_sample(self):
        report = self.evaluate(lambda sheet, punch: result(die="PENETRATION") if sheet >= 14 else result())
        self.assertEqual(report["status"], "SAMPLED_LIFT_BLOCKED")
        self.assertEqual(len(report["samples"]), 3)
        self.assertEqual(report["samples"][-1]["die_overlap_mm3"], 2.0)

    def test_unknown_never_becomes_clear(self):
        report = self.evaluate(lambda sheet, punch: result(punch="INVALID_GEOMETRY"))
        self.assertEqual(report["status"], "UNVERIFIED")
        self.assertEqual(len(report["samples"]), 1)

    def test_authoritative_end_zone_conditional_can_continue_lift(self):
        report = self.evaluate(lambda sheet, punch: result(
            punch="PENETRATION", die="TOUCHING", conditional=True))
        self.assertEqual(report["status"], "SAMPLED_LIFT_CONDITIONAL_UNVERIFIED")
        self.assertEqual(report["conditional_samples"], 5)
        self.assertTrue(all(s["conditional_end_zone"] for s in report["samples"]))
        self.assertFalse(report["continuous_certification"])

    def test_central_punch_overlap_still_blocks_with_conditional_flag(self):
        report = self.evaluate(lambda sheet, punch: result(
            punch="PENETRATION", conditional=True, zone="CENTRAL_ZONE"))
        self.assertEqual(report["status"], "SAMPLED_LIFT_BLOCKED")
        self.assertEqual(len(report["samples"]), 1)

    def test_rejects_invalid_kinematics(self):
        with self.assertRaises(ValueError):
            sample_synchronous_lift(1, 2, 3, hit_ram_z_mm=9,
                                    bottom_ram_z_mm=0, upper_ram_z_mm=5,
                                    translate=lambda a, b: a, check=lambda a, b, c: result())

    def test_only_proven_retract_punch_hit_can_start_replay(self):
        report = inspect_punch_assisted_retract(None, None, "B02",
                                                {"phase": "BENDING", "tool": "PUNCH"},
                                                {"start_z_mm": -2, "end_z_mm": 6})
        self.assertEqual(report["status"], "UNVERIFIED")
        self.assertIn("requires_proven_retract_punch_collision", report["reason"])

    def test_logged_b02_retract_hit_enables_probe(self):
        blocker = {"phase": "RETRACT", "tool": "PUNCH",
                   "sampled_machine_pose": {"ram_z_mm": 1.5036577289}}
        retract = {"start_z_mm": -3.492685, "end_z_mm": 6.5}
        self.assertTrue(eligible_assisted_retract(blocker, retract))
        self.assertFalse(eligible_assisted_retract(
            {**blocker, "phase": "BENDING"}, retract))
        self.assertFalse(eligible_assisted_retract(
            blocker, {"start_z_mm": -3.492685, "end_z_mm": float("nan")}))

    def test_historical_replay_requires_matching_tooling_and_order(self):
        step = {"bend_id": "B02", "status": "COLLISION", "reverse_axis": True,
                "first_blocker": {"phase": "RETRACT", "tool": "PUNCH",
                                  "sampled_machine_pose": {"ram_z_mm": 1.5036577289}},
                "retract": {"start_z_mm": -3.492685, "end_z_mm": 6.5}}
        report = {"status": "COLLISION", "requested_bends": ["B01", "B02"],
                  "punch": "AP1017", "die": "AT3015",
                  "tool_length_mode": "VIRTUAL_MATCH_BEND",
                  "allow_end_zone_collision": True, "end_zone_length_mm": 20.0,
                  "steps": [{"bend_id": "B01", "status": "SAMPLED_3D_CLEAR"}, step]}
        setup = {"order": ("B01", "B02"), "punch_id": "AP1017", "die_id": "AT3015",
                 "virtual_length": True, "end_zone_allow": True, "end_zone_mm": 20.0}
        self.assertIs(historic_lift_candidate(report, **setup), step)
        self.assertIs(historic_lift_candidate(
            report, **setup, orientation_by_bend={"B02": True}), step)
        self.assertIsNone(historic_lift_candidate(
            report, **setup, orientation_by_bend={"B02": False}))
        self.assertIsNone(historic_lift_candidate(report, **{**setup, "die_id": "WRONG"}))
        self.assertIsNone(historic_lift_candidate(report, **{**setup, "order": ("B02", "B01")}))
        self.assertIsNone(historic_lift_candidate(report, **{**setup, "end_zone_mm": 10.0}))
        self.assertIsNone(historic_lift_candidate({**report, "status": "UNVERIFIED"}, **setup))


if __name__ == "__main__":
    unittest.main()
