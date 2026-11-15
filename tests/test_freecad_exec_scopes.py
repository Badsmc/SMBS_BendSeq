"""Regression tests for FreeCAD's exec(code, globals, locals) entrypoints."""
import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TestFreeCADExecScopes(unittest.TestCase):
    def test_checkout_root_can_resolve_without_global_path(self):
        for name in ("Init.py", "InitGui.py"):
            with self.subTest(entrypoint=name):
                file = ROOT / name
                source = file.read_text(encoding="utf-8")
                parsed = ast.parse(source)
                fn = next(
                    node for node in parsed.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "_checkout_root"
                )
                module = ast.Module(
                    body=[
                        ast.ImportFrom(
                            module="__future__",
                            names=[ast.alias(name="annotations")], level=0
                        ),
                        fn,
                    ],
                    type_ignores=[],
                )
                ast.fix_missing_locations(module)
                globals_dict = {"__builtins__": __builtins__}
                locals_dict = {}
                # FreeCAD may execute its Mod scripts with different
                # dictionaries for globals and locals. Imports at module
                # scope are not necessarily global to subsequently defined
                # functions in that mode.
                exec(compile(module, str(file), "exec"),
                     globals_dict, locals_dict)
                fn_obj = locals_dict["_checkout_root"]
                self.assertEqual(fn_obj(), ROOT)


if __name__ == "__main__":
    unittest.main()
