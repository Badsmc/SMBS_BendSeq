import unittest
import pathlib


class _SpyValidator:
    """Minimal validator spy recording unknown_policy kwarg."""
    def __init__(self):
        self.calls = []

    def validate_step(self, current_state, bend_record, panel_graph=None,
                      tooling_override=None, unknown_policy="PRODUCTION"):
        self.calls.append({"bend_id": bend_record.id, "unknown_policy": unknown_policy})
        class _Res:
            valid = True
            next_state = current_state
            reason = ""
        return _Res()


class TestKL25UnknownPolicy(unittest.TestCase):
    def test_kl10_validator_accepts_exploratory_kwarg(self):
        """KL-10: mock validator accepts non-PRODUCTION unknown_policy kwarg (was TypeError)."""
        v = _SpyValidator()
        class _Bend:
            id = "B1"
        v.validate_step(None, _Bend(), panel_graph=None, unknown_policy="EXPLORATORY")
        self.assertEqual(v.calls[-1]["unknown_policy"], "EXPLORATORY")

    def test_kl25_solvers_have_no_inspect_signature_reflection(self):
        """KL-25: solver files must not use dynamic inspect.signature reflection."""
        solvers_dir = pathlib.Path(__file__).parent.parent / "smbs_core" / "Solvers"
        for name in ("dfs_backward.py", "greedy_unfold.py", "astar_backward.py"):
            content = (solvers_dir / name).read_text()
            self.assertNotIn("inspect.signature", content,
                             f"{name} still contains inspect.signature reflection")


if __name__ == "__main__":
    unittest.main()
