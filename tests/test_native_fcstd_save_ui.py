"""Native FCStd save remains the only normal session-save UI."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import unittest
import weakref

SOURCE = Path(__file__).resolve().parents[1] / "freecad/bendseq/commands/pressbrake_2d.py"
CODE = SOURCE.read_text(encoding="utf-8")
TREE = ast.parse(CODE)


class Panel:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class NativeSaveTest(unittest.TestCase):
    def observer(self, allowed=True, reason=""):
        panel = Panel(session=object(), _smbs_allow_auto_embed=allowed,
                   _smbs_auto_embed_error=reason, _smbs_document_name="Part",
                   _write_embedded_smbs_json=Mock(), status=Mock())
        warn, error = Mock(), Mock()
        node = next(n for n in TREE.body if isinstance(n, ast.ClassDef)
                    and n.name == "_SMBSFCStdSaveObserver")
        env = dict(weakref=weakref, tr=lambda key, **kwargs: key + str(kwargs),
                   _warn=warn, _error=error)
        import traceback
        env["traceback"] = traceback
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), env)
        return panel, env["_SMBSFCStdSaveObserver"](panel), warn, error

    def test_native_save_updates_only_bound_document(self):
        panel, observer, warn, error = self.observer()
        other, bound = NS(Name="Other"), NS(Name="Part")
        observer.slotStartSaveDocument(other, "Other.FCStd")
        panel._write_embedded_smbs_json.assert_not_called()
        observer.slotStartSaveDocument(bound, "Part.FCStd")
        panel._write_embedded_smbs_json.assert_called_once_with(bound)
        warn.assert_not_called()

    def test_failed_restore_preserves_attachment_and_warns_on_save(self):
        panel, observer, warn, error = self.observer(False, "geometry mismatch")
        observer.slotStartSaveDocument(NS(Name="Other"), "Other.FCStd")
        warn.assert_not_called()
        observer.slotStartSaveDocument(NS(Name="Part"), "Part.FCStd")
        panel._write_embedded_smbs_json.assert_not_called()
        warn.assert_called_once()
        panel.status.setText.assert_called_once()

    def test_write_failure_does_not_abort_native_save(self):
        panel, observer, warn, error = self.observer()
        panel._write_embedded_smbs_json.side_effect = RuntimeError("disk error")
        observer.slotStartSaveDocument(NS(Name="Part"), "Part.FCStd")
        error.assert_called_once()
        panel.status.setText.assert_called_once()

    def test_removed_controls_and_preserved_restore(self):
        for token in ("fcstd_save_btn", "fcstd_load_btn", "fcstd_note",
                      "_save_embedded_smbs_json_clicked", "_restore_embedded_smbs_json_clicked"):
            self.assertNotIn(token, CODE)
        self.assertIn("self._restore_embedded_smbs_json(doc)", CODE)
        self.assertIn("self._register_embedded_smbs_observer()", CODE)
        self.assertIn("if self.session is not None and self._smbs_allow_auto_embed:", CODE)

    def test_manual_diagnostics_cannot_bypass_restore_guard(self):
        node = next(n for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef)
                    and n.name == "_persist_manual_check_to_fcstd")
        env = {"tr": lambda key, **kwargs: key}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), env)
        panel = NS(_bound_smbs_document=Mock(), _smbs_allow_auto_embed=False,
                   _smbs_auto_embed_error="geometry mismatch", _write_embedded_smbs_json=Mock())
        with self.assertRaises(RuntimeError):
            env[node.name](panel)
        panel._write_embedded_smbs_json.assert_not_called()
