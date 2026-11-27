from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from freecad.bendseq import startup_diagnostics as startup


class StartupDiagnosticsTest(unittest.TestCase):
    def test_wrapper_order_and_deduplication(self):
        with patch.object(startup, "_GUI_ENTRYPOINTS", []):
            startup.note_gui_entrypoint("legacy")
            startup.note_gui_entrypoint("namespace")
            startup.note_gui_entrypoint("legacy")
            info = startup.startup_snapshot(Path(__file__).resolve().parents[1])
            self.assertEqual(info["gui_entrypoints_seen"], ["legacy", "namespace"])
            self.assertEqual(info["first_gui_entrypoint"], "legacy")
            self.assertTrue(info["interface_files_complete"])
            self.assertIsNotNone(info["interface_composite_sha256"])

    def test_missing_and_modified_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(startup, "_INTERFACE_FILES", ("a.py", "b.py")):
                (root / "a.py").write_text("first", encoding="utf-8")
                self.assertIsNone(startup.startup_snapshot(root)["interface_composite_sha256"])
                (root / "b.py").write_text("second", encoding="utf-8")
                before = startup.startup_snapshot(root)["interface_composite_sha256"]
                (root / "b.py").write_text("changed", encoding="utf-8")
                self.assertNotEqual(before, startup.startup_snapshot(root)["interface_composite_sha256"])
