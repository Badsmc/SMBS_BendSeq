"""Pure-Python regressions for SMBS multi-report sequence catalog."""
import unittest

from smbs_core.kinematics2d.sequence_catalog import parse_diagnostic


class SequenceCatalogTests(unittest.TestCase):
    def test_automatic_all_candidates_scores_and_warnings(self):
        data = {
            "operation_weights": {"flip": 8.0, "punch_length_change_fixed": 4.0},
            "candidates": [
                {"sequence": ["B01", "B02"], "cost": 30,
                 "cost_complete": True,
                 "evidence": "CONDITIONAL_RETRACT_WARNING_NOT_3D_VALIDATED",
                 "cost_breakdown": [{"bend_id": "B02", "punch_length_cost": 5}],
                 "steps": [{"bend_id": "B02",
                            "evidence": "2D_RETRACT_WARNING_NOT_3D_VALIDATED"}]},
                {"sequence": ["B02", "B01"], "cost": 20,
                 "cost_complete": True, "evidence": "SAMPLED_2D_CLEAR_OR_TOUCH",
                 "steps": []},
            ],
        }
        result = parse_diagnostic(data, "search.json")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].warning_bends, ("B02",))
        self.assertEqual(result[0].weights["punch_length_change_fixed"], 4)
        self.assertEqual(result[0].cost_breakdown[0]["punch_length_cost"], 5)
        self.assertEqual(result[1].warning_count, 0)

    def test_manual_stopped_is_not_a_full_cost(self):
        data = {
            "selected_sequence": ["B01", "B02"],
            "steps": [{"bend_id": "B01", "evidence": "SAMPLED_2D_CLEAR_OR_TOUCH"},
                      {"bend_id": "B02", "evidence": "2D_INTERFERENCE"}],
            "accepted_prefix": ["B01"],
            "first_blocker": {"bend_id": "B02", "reason": "punch collision"},
            "status": "MANUAL_REPLAY_STOPPED",
            "total_bends": 2,
            "plan_cost": {"total": 0, "is_complete": True,
                          "steps": [{"total": 0}]},
            "plan_cost_selected_order_complete": False,
            "operation_weights": {"flip": 20},
        }
        result = parse_diagnostic(data, "manual.json")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].origin, "MANUAL")
        self.assertEqual(result[0].sequence, ("B01", "B02"))
        self.assertFalse(result[0].cost_complete)
        self.assertIn("STOP", result[0].warnings[-1])

    def test_partial_candidate_kept(self):
        data = {
            "operation_weights": {"flip": 8},
            "candidates": [],
            "partial_candidate": {
                "sequence": ["B03", "B02"],
                "evidence": "PARTIAL_COARSE_2D_NOT_FULLY_REPLAYED",
            },
        }
        result = parse_diagnostic(data, "partial.json")
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].partial)

    def test_invalid_duplicate_bend_rejected(self):
        data = {"candidates": [
            {"sequence": ["B01", "B01"]},
            {"sequence": ["B01", "B02"], "evidence": "UNVERIFIED_2D"},
        ]}
        self.assertEqual(len(parse_diagnostic(data, "bad.json")), 1)

    def test_reject_unknown_report_format(self):
        with self.assertRaises(ValueError):
            parse_diagnostic({"random": "data"}, "unknown.json")


if __name__ == "__main__":
    unittest.main()
