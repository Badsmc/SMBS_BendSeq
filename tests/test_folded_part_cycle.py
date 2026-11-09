"""Tests for post-bend ram retract sampled geometry and state restoration."""
import unittest

from tools.run_folded_part_cycle import probe_punch_retract


def rect(x0, y0, x1, y1):
    return ((x0,y0),(x1,y0),(x1,y1),(x0,y1))


class FakeTooling:
    def __init__(self):
        self.ram_z_mm = 5.0
        self.ram_stroke_mm = 0.0
        self.punch_machine_transform = ("PUNCH", 5.0)

    def set_punch_ram_z(self, z, stroke_mm=0.0):
        self.ram_z_mm = z
        self.ram_stroke_mm = stroke_mm
        self.punch_machine_transform = ("PUNCH", z)

    def punch_polygons_machine(self, arc_segments=24):
        z = self.ram_z_mm
        return rect(-1,z,-0.5,z+1), ()

    def die_polygons_machine(self, arc_segments=24):
        return rect(-10,-10,-9,-9), ()


class PostBendCycleTests(unittest.TestCase):
    def test_retract_detects_bottom_interference_and_restores_ram(self):
        tools = FakeTooling()
        panel = (("real_section", rect(-0.75, 0, 0.75, 1)),)
        initial = (tools.ram_z_mm, tools.ram_stroke_mm, tools.punch_machine_transform)
        report = probe_punch_retract(
            panel, tools, bottom_ram_z_mm=0.0, parked_ram_z_mm=5.0,
            sample_count=11,
        )
        self.assertEqual(report["status"], "SAMPLED_2D_INTERFERENCE")
        self.assertEqual(report["samples"][0]["phase"], "BOTTOM")
        self.assertEqual(report["samples"][0]["status"], "INTERFERENCE")
        self.assertEqual(report["samples"][-1]["status"], "CLEAR")
        self.assertTrue(any(
            f["component"] == "real_section" and f["tool"] == "PUNCH"
            for f in report["samples"][0]["findings"]
        ))
        self.assertEqual(
            (tools.ram_z_mm, tools.ram_stroke_mm, tools.punch_machine_transform),
            initial,
        )

    def test_retract_clear_when_punch_outside_sheet(self):
        tools = FakeTooling()
        panel = (("real_section", rect(5,0,6,1)),)
        report = probe_punch_retract(
            panel, tools, bottom_ram_z_mm=0.0,
            parked_ram_z_mm=5.0, sample_count=6,
        )
        self.assertEqual(report["status"], "SAMPLED_2D_CLEAR_NOT_CERTIFIED")

    def test_lift_then_withdraw_avoids_low_die_blocker(self):
        from tools.run_folded_part_cycle import probe_lift_then_withdraw
        # The formed sheet at z=0 cannot move horizontally through the die,
        # but first lifting by 4 mm moves it above the die and below punch.
        polygons = (("formed_panel", rect(-0.5, 0, 0.5, 1)),)
        punch = (rect(-1, 20, 1, 22), ())
        die = (rect(3, 0, 4, 1), ())
        report = probe_lift_then_withdraw(
            polygons, punch, die, lift_mm=4, distance_mm=8,
            direction=1, lift_samples=9, withdrawal_samples=17,
        )
        self.assertEqual(report["status"], "SAMPLED_2D_CLEAR_NOT_CERTIFIED")
        self.assertEqual(len(report["samples"]), 26)
        self.assertTrue(all(s["status"] == "CLEAR" for s in report["samples"]))

    def test_lift_path_detects_punch_ceiling(self):
        from tools.run_folded_part_cycle import probe_lift_then_withdraw
        polygons = (("formed_panel", rect(-0.5, 0, 0.5, 1)),)
        punch = (rect(-1, 3, 1, 5), ())
        die = (rect(20, -5, 21, -4), ())
        report = probe_lift_then_withdraw(
            polygons, punch, die, lift_mm=6, distance_mm=8,
            direction=-1, lift_samples=13,
        )
        self.assertEqual(report["status"], "SAMPLED_2D_INTERFERENCE")
        self.assertTrue(any(
            f["tool"] == "PUNCH"
            for s in report["samples"] if s["phase"] == "LIFT"
            for f in s["findings"]
        ))

    def test_path_search_chooses_sampled_clear_candidate_and_restores_tooling(self):
        from tools.run_folded_part_cycle import search_sampled_withdrawal_paths
        tools = FakeTooling()
        initial = (tools.ram_z_mm, tools.ram_stroke_mm, tools.punch_machine_transform)
        # Die is in the direct +s path at z=0; a lift of 2 mm
        # lets this synthetic post-bend section pass over it.
        result = search_sampled_withdrawal_paths(
            (("formed_panel", rect(0, 0, 1, 1)),), tools,
            bottom_ram_z_mm=0, contact_z_mm=0,
            parked_clearances_mm=(5, 10),
            lift_heights_mm=(2, 4),
            distance_mm=8, retract_samples=5,
            lift_samples=5, withdrawal_samples=9,
        )
        self.assertEqual(result["status"],
                         "SAMPLED_2D_PATH_CANDIDATES_NOT_CERTIFIED")
        self.assertGreater(result["evaluated_paths"], 0)
        self.assertTrue(result["candidates"])
        self.assertEqual(result["candidates"][0]["lift_mm"], 2)
        self.assertTrue(all(
            c["status"] == "SAMPLED_2D_CLEAR_NOT_CERTIFIED"
            for c in result["candidates"]
        ))
        self.assertEqual(
            (tools.ram_z_mm, tools.ram_stroke_mm, tools.punch_machine_transform),
            initial,
        )

    def test_path_search_no_candidates_is_unverified_not_impossible(self):
        from tools.run_folded_part_cycle import search_sampled_withdrawal_paths
        class BlockingTools(FakeTooling):
            def punch_polygons_machine(self, arc_segments=24):
                return rect(-10, -10, 10, 30), ()
        result = search_sampled_withdrawal_paths(
            (("formed_panel", rect(0, 0, 1, 1)),),
            BlockingTools(), bottom_ram_z_mm=0, contact_z_mm=0,
            parked_clearances_mm=(5,),
            lift_heights_mm=(2,),
            retract_samples=5, lift_samples=5,
            withdrawal_samples=5, distance_mm=5,
        )
        self.assertEqual(result["status"], "UNVERIFIED_NO_SAMPLED_PATH")
        self.assertEqual(result["candidates"], [])
        self.assertTrue(any(a["status"] == "RETRACT_NOT_CLEAR"
                            for a in result["attempts"]))

    def test_path_search_invalid_parameters_fail_closed(self):
        from tools.run_folded_part_cycle import search_sampled_withdrawal_paths
        with self.assertRaisesRegex(ValueError, "invalid path search"):
            search_sampled_withdrawal_paths(
                (("formed_panel", rect(0, 0, 1, 1)),),
                FakeTooling(), bottom_ram_z_mm=0, contact_z_mm=0,
                lift_heights_mm=(-1,),
            )

    def test_short_circuit_on_first_lateral_collision(self):
        from tools.run_folded_part_cycle import probe_lift_then_withdraw
        section = (("panel", rect(0, 0, 1, 1)),)
        punch = (rect(2, 3, 4, 6), ())
        die = (rect(20, -5, 21, -4), ())
        path = probe_lift_then_withdraw(
            section, punch, die, lift_mm=2, distance_mm=10,
            direction=1, lift_samples=3, withdrawal_samples=21,
            stop_on_obstruction=True,
        )
        # Full 3 + 21 samples were not evaluated after the blocker.
        self.assertLess(len(path["samples"]), 24)
        self.assertEqual(path["status"], "SAMPLED_2D_INTERFERENCE")

    def test_path_search_deadline_returns_unverified(self):
        from tools.run_folded_part_cycle import search_sampled_withdrawal_paths
        from unittest.mock import patch
        tools = FakeTooling()
        # Deadline is reached before even evaluating the first candidate.
        with patch("tools.run_folded_part_cycle.perf_counter",
                   side_effect=(0.0, 2.0, 3.0)):
            report = search_sampled_withdrawal_paths(
                (("panel", rect(0, 0, 1, 1)),), tools,
                bottom_ram_z_mm=0, contact_z_mm=0,
                max_search_time_s=1.0,
            )
        self.assertEqual(report["status"], "UNVERIFIED_SEARCH_LIMIT_REACHED")
        self.assertTrue(report["search_limit_reached"])
        self.assertEqual(report["evaluated_paths"], 0)

    def test_invalid_retract_range_rejected(self):
        with self.assertRaisesRegex(ValueError, "parked ram"):
            probe_punch_retract(
                (("panel", rect(5,0,6,1)),),
                FakeTooling(), bottom_ram_z_mm=5.0, parked_ram_z_mm=0.0,
            )

    def test_empty_real_section_unverified(self):
        report = probe_punch_retract(
            (), FakeTooling(), bottom_ram_z_mm=0, parked_ram_z_mm=5,
        )
        self.assertEqual(report["status"], "UNVERIFIED_2D")


if __name__ == "__main__":
    unittest.main()
