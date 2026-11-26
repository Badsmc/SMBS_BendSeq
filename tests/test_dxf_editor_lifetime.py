"""Headless regression coverage for the native nonblocking DXF editor."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

SOURCE = (Path(__file__).resolve().parents[1] /
          "freecad/bendseq/integration/dxf_manual.py").read_text(encoding="utf-8")


class DxfEditorLifetimeTest(unittest.TestCase):
    def setUp(self):
        self.tree = ast.parse(SOURCE)

    def _node(self, kind, name):
        return next(n for n in ast.walk(self.tree)
                    if isinstance(n, kind) and n.name == name)

    def test_busy_dialog_refuses_close_and_reject(self):
        class Dialog:
            def reject(self):
                self.rejected = True

            def closeEvent(self, event):
                self.closed = True

        node = self._node(ast.ClassDef, "DxfEditor")
        ns = {"qt": SimpleNamespace(QDialog=Dialog)}
        exec(compile(ast.Module(body=[node], type_ignores=[]),
                     "<editor>", "exec"), ns)
        editor = ns["DxfEditor"]()
        ignored = []
        event = SimpleNamespace(ignore=lambda: ignored.append(True))
        editor.calculating = True
        editor.reject()
        editor.closeEvent(event)
        self.assertFalse(getattr(editor, "rejected", False))
        self.assertFalse(getattr(editor, "closed", False))
        self.assertEqual(ignored, [True])
        editor.calculating = False
        editor.reject()
        editor.closeEvent(event)
        self.assertTrue(editor.rejected)
        self.assertTrue(editor.closed)

    def test_cancelled_calculation_does_not_touch_widgets(self):
        node = self._node(ast.FunctionDef, "calculate")
        wrapper = ast.parse(
            "def harness():\n"
            "    attempt = 0\n"
            "    pending = True\n"
            "    closed = True\n"
            "    return calculate\n"
        ).body[0]
        wrapper.body.insert(-1, node)
        ns = {}
        exec(compile(ast.Module(body=[wrapper], type_ignores=[]),
                     "<queued-callback>", "exec"), ns)
        # Deliberately no dialog or widgets in the harness.
        ns["harness"]()()

    def test_duplicate_queued_calculation_is_ignored(self):
        node = self._node(ast.FunctionDef, "start_calculation")
        wrapper = ast.parse(
            "def harness():\n"
            "    pending = True\n"
            "    closed = False\n"
            "    return start_calculation\n"
        ).body[0]
        wrapper.body.insert(-1, node)
        ns = {}
        exec(compile(ast.Module(body=[wrapper], type_ignores=[]),
                     "<queued-start>", "exec"), ns)
        ns["harness"]()()


    def test_legacy_and_modern_dxf_layer_names(self):
        node = self._node(ast.FunctionDef, "_decode_dxf_text")
        ns = {}
        exec(compile(ast.Module(body=[node], type_ignores=[]),
                     "<dxf-decoding>", "exec"), ns)
        decode = ns["_decode_dxf_text"]

        def file(version, codepage, layer, encoding):
            text = ("0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\n"
                    + version + "\n9\n$DWGCODEPAGE\n3\n" + codepage
                    + "\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n"
                    + "0\nLINE\n8\n" + layer + "\n0\nENDSEC\n0\nEOF\n")
            return text.encode(encoding)

        for version, codepage, encoding in (
            ("AC1015", "ANSI_1251", "cp1251"),
            ("AC1015", "DOS866", "cp866"),
            ("AC1021", "ANSI_1252", "utf-8"),
            ("AC1032", "ANSI_1251", "utf-8-sig"),
        ):
            with self.subTest(version=version, codepage=codepage):
                raw = file(version, codepage, "Линии гиба", encoding)
                self.assertIn("Линии гиба", decode(raw))
                self.assertNotIn("\ufffd", decode(raw))

        with self.assertRaises(UnicodeDecodeError):
            decode(file("AC1021", "ANSI_1251", "Гибы", "cp1251"))
        with self.assertRaises(LookupError):
            decode(file("AC1015", "INVALID_CODEC", "Layer", "ascii"))
