import os
import xml.etree.ElementTree as ET
import unittest
from smbs_freecad.simulation import BendSeqSimulation

class TestPhase13FreeCAD(unittest.TestCase):
    def test_freecad_adapter_structure(self):
        import Init
        import InitGui
        import smbs_freecad
        import smbs_freecad.commands
        self.assertIsNotNone(Init)
        self.assertIsNotNone(InitGui)

    def test_simulation_from_json(self):
        sim = BendSeqSimulation()
        res = sim.load_sequence("non_existent_file.json")
        self.assertIsNone(res)

    def test_package_xml_metadata_valid(self):
        pkg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "package.xml"))
        self.assertTrue(os.path.exists(pkg_path))
        
        tree = ET.parse(pkg_path)
        root = tree.getroot()
        ns = "https://wiki.freecad.org/Package_Metadata"
        name = root.find(f"{{{ns}}}name") if root.tag.startswith("{") else root.find("name")
        version = root.find(f"{{{ns}}}version") if root.tag.startswith("{") else root.find("version")
        self.assertIsNotNone(name)
        self.assertIn("smbs", name.text.lower())
        self.assertEqual(version.text, "0.0.1")

if __name__ == "__main__":
    unittest.main()
