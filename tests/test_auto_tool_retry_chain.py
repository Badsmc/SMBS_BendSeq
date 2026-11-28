"""Exercise the retry event-loop orchestration without Qt or native CAD."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from freecad.bendseq.ui.auto_tool_selection_task import _start_attempts

MODULE = "freecad.bendseq.ui.auto_tool_selection_task"


class RetryChainTests(unittest.TestCase):
    def run_chain(self, reports, *, change_after_mesh=False):
        session = object()
        operation = NS(operation_id="B01", bend_id="B01")
        snapshot = (session, (), 40, None, None, ("B01",), (operation,),
                    (), (), (), (), "gauge")
        state = {"identity": "original", "snapshot": snapshot}
        jobs = []
        applied = []
        workspace = Mock()
        workspace.session = session
        workspace._task_progress_busy = False
        workspace.timer.isActive.return_value = False
        workspace.playing = False
        workspace._playback_alive.return_value = True
        workspace._mesh_preview_identity.side_effect = lambda: state["identity"]

        def apply(ws, kind, snap, tool, candidate, run_id, check_mesh):
            applied.append(tool.punch_id)
            state["identity"] = tool.punch_id
            return state["identity"], snapshot

        def mesh(**kwargs):
            report = reports.pop(0)
            if change_after_mesh:
                state["identity"] = "operator changed"
            return report

        workspace._run_mesh_sequence_check.side_effect = mesh
        qt = NS(QTimer=NS(singleShot=lambda delay, callback: jobs.append(callback)))
        attempts = tuple((NS(punch_id=name, die_id="D"), NS(operation_ids=("B01",)))
                         for name in ("P1", "P2", "P3"))
        with patch(MODULE + "._snapshot", side_effect=lambda w: state["snapshot"]), \
             patch(MODULE + "._apply_selection", side_effect=apply), \
             patch(MODULE + ".debug_event") as trace:
            _start_attempts(workspace, object(), snapshot, attempts, "run-1", True, True, qt)
            while jobs:
                jobs.pop(0)()
        finish = [c.kwargs for c in trace.call_args_list if c.args[0] == "AUTO_TOOL_RETRY_FINISHED"]
        self.assertEqual(len(finish), 1)
        self.assertEqual(finish[0]["run_id"], "run-1")
        return applied, finish[0]

    @staticmethod
    def report(collides=False, incomplete=False):
        return dict(completed=[] if incomplete else ["B01"],
                    pending=["B01"] if incomplete else [],
                    findings=[{"operation":"B01"}] if collides else [],
                    conditional_findings=[], unresolved=[], samples=15,
                    reason="TIME_BUDGET" if incomplete else "", elapsed_seconds=1.0)

    def test_confirmed_collision_tries_next_and_preserves_result(self):
        applied, final = self.run_chain([self.report(True), self.report()])
        self.assertEqual(applied, ["P1", "P2"])
        self.assertEqual(final["reason"], "SAMPLED_NO_COLLISION")
        self.assertEqual([a["outcome"] for a in final["attempts"]],
                         ["CONFIRMED_COLLISION", "SAMPLED_NO_COLLISION"])

    def test_incomplete_does_not_trigger_retry(self):
        applied, final = self.run_chain([self.report(incomplete=True)])
        self.assertEqual(applied, ["P1"])
        self.assertEqual(final["reason"], "INCOMPLETE")

    def test_changed_identity_blocks_alternative(self):
        applied, final = self.run_chain([self.report(True)], change_after_mesh=True)
        self.assertEqual(applied, ["P1"])
        self.assertEqual(final["reason"], "SETUP_CHANGED_OR_BUSY")

    def test_exhausted_alternatives_remain_collisions(self):
        applied, final = self.run_chain([self.report(True) for _ in range(3)])
        self.assertEqual(applied, ["P1", "P2", "P3"])
        self.assertEqual(final["reason"], "ALTERNATIVES_EXHAUSTED")
