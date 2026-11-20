"""Regression: Qt drag/drop must not rebuild operation widgets mid-event.

These tests simulate a Qt drop with a headless QTreeWidget stand-in. The real
Windows/FreeCAD/PySide native event loop remains an integration test.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
import textwrap
import unittest

SOURCE = (
    Path(__file__).resolve().parents[1]
    / "freecad_smbs" / "commands" / "pressbrake_2d.py"
).read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def source_of(name, kind=ast.FunctionDef):
    matches = [node for node in ast.walk(TREE)
               if isinstance(node, kind) and node.name == name]
    if len(matches) != 1:
        raise AssertionError(f"Expected one {name}, found {len(matches)}")
    return textwrap.dedent(ast.get_source_segment(SOURCE, matches[0]))


class FakeQtTimer:
    calls = []

    @classmethod
    def singleShot(cls, delay, callback):
        cls.calls.append((delay, callback))


class NativeTree:
    def dropEvent(self, event):
        event.trace.append(("native", self.stage_drop_owner._staged_dnd_in_progress))
        event.accepted = True


class StagedQtDropTests(unittest.TestCase):
    def setUp(self):
        FakeQtTimer.calls.clear()
        self.events = []
        self.env = {
            "QtCore": SimpleNamespace(QTimer=FakeQtTimer),
            "QtWidgets": SimpleNamespace(QTreeWidget=NativeTree),
            "_desktop_debug_event": lambda *args, **kwargs: self.events.append(
                (args, kwargs)
            ),
        }

    def test_one_delayed_commit_after_native_drop_finishes(self):
        exec(source_of("StagedOperationsTree", ast.ClassDef), self.env)
        tree = self.env["StagedOperationsTree"]()
        session = SimpleNamespace(
            can_reorder=lambda: True,
            controller=SimpleNamespace(operation_steps=[
                SimpleNamespace(operation_id="B01.1"),
                SimpleNamespace(operation_id="B02"),
                SimpleNamespace(operation_id="B01.2"),
            ]),
        )
        owner = SimpleNamespace(
            _closing=False,
            _staged_dnd_pending=False,
            _staged_dnd_in_progress=False,
            session=session,
            timer=SimpleNamespace(isActive=lambda: False),
            playing=False,
            _on_staged_operations_dropped=lambda: self.env[
                "_on_staged_operations_dropped"
            ](owner),
            _apply_staged_drop=lambda: None,
        )
        exec(source_of("_on_staged_operations_dropped"), self.env)
        tree.stage_drop_owner = owner
        event = SimpleNamespace(
            accepted=False, trace=[],
            isAccepted=lambda: event.accepted,
            ignore=lambda: event.trace.append(("ignored", False)),
        )
        tree.dropEvent(event)
        self.assertEqual(event.trace, [("native", True)])
        self.assertTrue(event.accepted)
        self.assertFalse(owner._staged_dnd_in_progress)
        self.assertTrue(owner._staged_dnd_pending)
        self.assertEqual(len(FakeQtTimer.calls), 1)
        self.assertGreaterEqual(FakeQtTimer.calls[0][0], 40)
        # A double event during a pending commit cannot enqueue another.
        owner._on_staged_operations_dropped()
        self.assertEqual(len(FakeQtTimer.calls), 1)

    def test_no_drop_while_playing_or_closing(self):
        exec(source_of("StagedOperationsTree", ast.ClassDef), self.env)
        tree = self.env["StagedOperationsTree"]()
        owner = SimpleNamespace(
            _closing=False,
            _staged_dnd_pending=False,
            _staged_dnd_in_progress=False,
            session=SimpleNamespace(can_reorder=lambda: True),
            timer=SimpleNamespace(isActive=lambda: True),
            playing=True,
        )
        tree.stage_drop_owner = owner
        event = SimpleNamespace(
            trace=[], ignored=False,
            ignore=lambda: setattr(event, "ignored", True),
        )
        tree.dropEvent(event)
        self.assertTrue(event.ignored)
        self.assertEqual(event.trace, [])
        self.assertEqual(len(FakeQtTimer.calls), 0)
        owner.playing = False
        owner.timer = SimpleNamespace(isActive=lambda: False)
        owner._closing = True
        tree.dropEvent(event)
        self.assertEqual(event.trace, [])

    def test_reorder_selection_does_not_update_3d_mid_drop(self):
        method = source_of("_on_operation_selected")
        self.assertIn("self._staged_dnd_in_progress", method)
        self.assertIn("self._staged_dnd_pending", method)
        code = source_of("_update_operations_visual_state")
        self.assertIn("self._staged_dnd_in_progress", code)
        source = source_of("_apply_staged_drop")
        self.assertIn("STAGED_DROP_COMMIT_STARTED", source)
        self.assertIn("STAGED_DROP_REJECTED", source)


if __name__ == "__main__":
    unittest.main()
