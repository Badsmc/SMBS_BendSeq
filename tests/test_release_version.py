"""Check public version metadata without requiring FreeCAD."""
from pathlib import Path
from xml.etree import ElementTree as ET
import unittest

import freecad_smbs
import smbs_core

ROOT = Path(__file__).resolve().parents[1]
NS = {"p": "https://wiki.freecad.org/Package_Metadata"}


class ReleaseVersionTest(unittest.TestCase):
    def test_version_consistency(self):
        package = ET.parse(ROOT / "package.xml").getroot()
        version = package.findtext("p:version", namespaces=NS)
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        self.assertEqual(version, freecad_smbs.__version__)
        self.assertEqual(version, smbs_core.__version__)
        self.assertIn(f"## {version} ", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
        self.assertIn(version, (ROOT / "README.md").read_text(encoding="utf-8"))
        icon = package.findtext("p:icon", namespaces=NS)
        self.assertTrue((ROOT / icon).is_file())
        self.assertEqual(package.findtext("p:content/p:workbench/p:classname", namespaces=NS),
                         "SMBSWorkbench")
