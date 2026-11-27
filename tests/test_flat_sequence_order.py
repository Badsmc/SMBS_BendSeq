import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

from freecad.bendseq.ui.flat_sequence_order import displayed_operations, operation_numbers


def steps(ids):
    return tuple(NS(bend_id=i.split(".")[0], operation_id=i) for i in ids)


class FlatSequenceOrderTest(unittest.TestCase):
    def test_logged_fast_order_is_numbered_from_controller(self):
        order = ("B10","B09","B02","B08","B05","B07","B03","B01","B06","B04")
        controller = NS(has_stages=False, operation_steps=steps(order))
        visible = displayed_operations(controller)
        self.assertEqual(tuple(op.operation_id for op in visible), order)
        self.assertEqual(operation_numbers(visible)["B01"][0][0],8)
        self.assertEqual(operation_numbers(visible)["B10"][0][0],1)
        self.assertEqual(displayed_operations(controller,manual_prefix=("B01","B02")),visible)

    def test_partial_and_edit_scopes(self):
        controller=NS(has_stages=False,operation_steps=steps(("B03","B01","B02")))
        self.assertEqual(displayed_operations(controller,editing=True),())
        self.assertEqual(displayed_operations(controller,manual_prefix=("B03",)),controller.operation_steps[:1])
        self.assertEqual(displayed_operations(controller,catalog_prefix=("B03","B01")),controller.operation_steps[:2])

    def test_staged_order(self):
        controller=NS(has_stages=True,operation_steps=steps(("B01.1","B02","B01.2")))
        labels=operation_numbers(displayed_operations(controller))
        self.assertEqual([i for i,_ in labels["B01"]],[1,3])

    def test_fast_lightweight_refresh_redraws_flat_without_native_preview(self):
        root=Path(__file__).resolve().parents[1]
        tree=ast.parse((root/"freecad/bendseq/commands/pressbrake_2d.py").read_text(encoding="utf-8"))
        fn=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=="_on_staged_plan_changed")
        branch=next(n for n in ast.walk(fn) if isinstance(n,ast.If) and isinstance(n.test,ast.Name) and n.test.id=="fast_apply" and any(isinstance(x,ast.Return) for x in n.body))
        calls=[n.func.attr for n in ast.walk(branch) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)]
        self.assertIn("_populate_operations_fast",calls)
        self.assertIn("_draw_flat_pattern",calls)
        self.assertNotIn("_update_3d_preview",calls)
        self.assertNotIn("_ensure_press_cycle_controller",calls)
