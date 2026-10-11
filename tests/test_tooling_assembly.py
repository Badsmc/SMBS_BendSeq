import unittest
import os
import sys

# Ensure smbs_core is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from smbs_core.models.tooling import Tooling, ToolingKind, DatumRole, Datum, MachineConfiguration
from smbs_core.Physics.tooling_assembly import ToolingAssembly, AssemblyResult
from smbs_core.geometry_backend.backends.mock_backend import MockBackend

try:
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCC.Core.TopoDS import TopoDS_Shape
    HAS_OCC = True
except ImportError:
    HAS_OCC = False

class TestToolingAssembly(unittest.TestCase):
    def setUp(self):
        if not HAS_OCC:
            self.skipTest("pythonocc-core not installed")
            
        self.backend = MockBackend()
        
        # Machine Config
        self.machine = MachineConfiguration(
            id="TestPress",
            bed_top_plane=Datum(DatumRole.MOUNTING_PLANE, "plane", ((0,0,0), (0,0,1))),
            ram_mount_plane=Datum(DatumRole.MOUNTING_PLANE, "plane", ((0,0,200), (0,0,-1))),
            ram_stroke_mm=100.0,
            ram_direction=(0,0,-1),
            backgauge_axes={},
            max_tonnage_kn=100.0,
            max_bend_length_mm=1000.0
        )
        
        self.assembly = ToolingAssembly(self.backend, self.machine)
        
        # Synthetic Punch
        self.punch = Tooling(
            id="P1", kind=ToolingKind.PUNCH, source_file="", 
            shape=BRepPrimAPI_MakeBox(10, 10, 100).Shape(), local_frame=None
        )
        self.punch.datums[DatumRole.MOUNTING_PLANE] = Datum(DatumRole.MOUNTING_PLANE, "plane", ((0,0,100), (0,0,-1)))
        self.punch.datums[DatumRole.TIP_POINT] = Datum(DatumRole.TIP_POINT, "point", ((0,0,0),))
        self.punch.datums[DatumRole.WORKING_AXIS] = Datum(DatumRole.WORKING_AXIS, "axis", ((0,0,0), (1,0,0)))
        
        # Synthetic Die
        self.die = Tooling(
            id="D1", kind=ToolingKind.DIE, source_file="", 
            shape=BRepPrimAPI_MakeBox(10, 10, 50).Shape(), local_frame=None
        )
        self.die.datums[DatumRole.MOUNTING_PLANE] = Datum(DatumRole.MOUNTING_PLANE, "plane", ((0,0,0), (0,0,1)))
        self.die.datums[DatumRole.V_CENTER] = Datum(DatumRole.V_CENTER, "point", ((0,0,50),))
        self.die.datums[DatumRole.WORKING_AXIS] = Datum(DatumRole.WORKING_AXIS, "axis", ((0,0,50), (1,0,0)))

    def test_assembly_aligned(self):
        result = self.assembly.place(self.punch, self.die)
        self.assertEqual(result.status, "ALIGNED")
        
        # Punch should be moved up by 100 to mount at Z=200
        p_trsf = self.assembly.assembly_trsf[ToolingKind.PUNCH]
        self.assertAlmostEqual(p_trsf.TranslationPart().Z(), 100.0)
        
        # Die should be moved to Z=0
        d_trsf = self.assembly.assembly_trsf[ToolingKind.DIE]
        self.assertAlmostEqual(d_trsf.TranslationPart().Z(), 0.0)
        
    def test_assembly_misaligned(self):
        # Shift die V_CENTER by 2.0 mm in Y
        self.die.datums[DatumRole.V_CENTER] = Datum(DatumRole.V_CENTER, "point", ((0,2.0,50),))
        result = self.assembly.place(self.punch, self.die)
        self.assertEqual(result.status, "MISALIGNED")

    def test_missing_datums(self):
        del self.punch.datums[DatumRole.TIP_POINT]
        result = self.assembly.place(self.punch, self.die)
        self.assertEqual(result.status, "CANNOT_VERIFY")

if __name__ == '__main__':
    unittest.main()
