"""Test the modern entry point without requiring native FreeCAD/Qt."""
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class NamespaceEntryPointTest(unittest.TestCase):
    def test_checkout_keeps_freecad_namespace_open(self):
        self.assertFalse((ROOT / "freecad" / "__init__.py").exists())
        self.assertTrue((ROOT / "freecad" / "bendseq" / "init_gui.py").is_file())
        self.assertTrue((ROOT / "freecad" / "bendseq" / "init.py").is_file())

    def test_real_checkout_metadata_and_duplicate_protection(self):
        entry = importlib.import_module("freecad.bendseq")
        metadata = SimpleNamespace(supportsCurrentFreeCAD=lambda: True)
        fake = SimpleNamespace(Metadata=lambda filename: metadata)
        with patch.dict(sys.modules, {"FreeCAD": fake}):
            self.assertTrue(entry.enabled())
            metadata.supportsCurrentFreeCAD = lambda: False
            self.assertFalse(entry.enabled())
            metadata.supportsCurrentFreeCAD = lambda: True
            with patch.object(entry, "find_spec", return_value=SimpleNamespace(origin="/other/freecad_smbs/__init__.py")):
                with self.assertRaises(ImportError):
                    entry.enabled()

    def test_disabled_zip_checkout_is_skipped(self):
        import tempfile
        entry = importlib.import_module("freecad.bendseq")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "ADDON_DISABLED").touch()
            with patch.object(entry, "_ROOT", root), patch.dict(sys.modules, {"FreeCAD": SimpleNamespace()}):
                self.assertFalse(entry.enabled())

    def test_both_startup_styles_share_canonical_module(self):
        entry = importlib.import_module("freecad.bendseq")
        canonical = importlib.import_module("freecad_smbs.Init")
        with patch.object(entry, "enabled", return_value=True):
            modern = importlib.import_module("freecad.bendseq.init")
            self.assertIs(modern._legacy_init, canonical)
