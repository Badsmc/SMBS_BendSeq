import unittest
import pathlib


class TestKL24PendingGScores(unittest.TestCase):
    def test_kl24_pending_pop_uses_g_scores(self):
        """KL-24: pending-pop must use authoritative g_scores[fp], not parent_g + 1.0."""
        src = (pathlib.Path(__file__).parent.parent
               / "smbs_core" / "Solvers" / "astar_backward.py").read_text()
        # Old bug pattern must not exist
        self.assertNotIn('current_g = p["parent_g"] + 1.0\n', src,
                         "KL-24 regression: raw parent_g+1.0 still present")
        # Fix pattern must exist
        self.assertIn('current_g = g_scores.get(current_fp, p["parent_g"] + 1.0)',
                      src, "KL-24: g_scores lookup not present in pending branch")


if __name__ == "__main__":
    unittest.main()
