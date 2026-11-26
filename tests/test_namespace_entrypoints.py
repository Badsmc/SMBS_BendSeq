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
        canonical = importlib.import_module("freecad.bendseq.headless")
        with patch.object(entry, "enabled", return_value=True):
            modern = importlib.import_module("freecad.bendseq.init")
            self.assertIs(modern.headless, canonical)


    def test_namespace_respects_freecad_disable_policy_for_zip_directory(self):
        import tempfile
        entry = importlib.import_module("freecad.bendseq")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "BendSeq-test-zip"
            root.mkdir()
            (root / "package.xml").touch()
            fake = SimpleNamespace(ConfigGet=lambda key: root.name)
            with patch.object(entry, "_ROOT", root), patch.dict(sys.modules, {"FreeCAD": fake}):
                self.assertFalse(entry.enabled())
                fake.ConfigGet = lambda key: ""
                (root.parent / "ALL_ADDONS_DISABLED").touch()
                self.assertFalse(entry.enabled())
