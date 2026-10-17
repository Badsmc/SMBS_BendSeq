"""
test_tooling_fcstd.py - Mandatory Tests for Tooling System (Phase 7)
"""
import unittest
import os
import json
import sys

# Ensure smbs_core is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from smbs_core.models.tooling import ToolingKind, DatumRole, MachineConfiguration, Datum
from smbs_core.geometry_backend.backends.fcstd_loader import FcstdToolingLoader
from smbs_core.geometry_backend.backends.mock_backend import MockBackend
from smbs_core.Physics.tooling_assembly import ToolingAssembly
from smbs_core.Physics.collision_detector import CollisionDetector, CollisionStatus

try:
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    HAS_OCC = True
except ImportError:
    HAS_OCC = False

class TestToolingSystem(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fcstd_path = os.path.join(os.path.dirname(__file__), "..", "SMBendTests", "L-(ok).FCStd")
        cls.sidecar_path = cls.fcstd_path.replace(".FCStd", ".tooling.json")
        
        cls.machine = MachineConfiguration(
            id="TestPress",
            bed_top_plane=Datum(DatumRole.MOUNTING_PLANE, "plane", ((0,0,0), (0,0,1))),
            ram_mount_plane=Datum(DatumRole.MOUNTING_PLANE, "plane", ((0,0,200), (0,0,-1))),
            ram_stroke_mm=100.0,
            ram_direction=(0,0,-1),
            backgauge_axes={},
            max_tonnage_kn=100.0,
            max_bend_length_mm=1000.0
        )

    def tearDown(self):
        if os.path.exists(self.sidecar_path):
            os.remove(self.sidecar_path)

    def test_missing_sidecar(self):
        """Проверяет, что при отсутствии sidecar выдаётся geometry_status = SIMPLIFIED."""
        if not HAS_OCC:
            self.skipTest("pythonocc-core not installed")
        if not os.path.exists(self.fcstd_path):
            self.skipTest("Test file not found")
            
        if os.path.exists(self.sidecar_path):
            os.remove(self.sidecar_path)
            
        loader = FcstdToolingLoader()
        tool = loader.load(self.fcstd_path, ToolingKind.PUNCH, target_object_name="Body")
        
        self.assertEqual(tool.geometry_status, "SIMPLIFIED")
        self.assertIn(DatumRole.MOUNTING_PLANE, tool.datums)

    def test_sidecar_loading(self):
        """Проверяет загрузку с валидным sidecar (geometry_status = REAL)."""
        if not HAS_OCC:
            self.skipTest("pythonocc-core not installed")
        if not os.path.exists(self.fcstd_path):
            self.skipTest("Test file not found")
            
        sidecar_data = {
            "id": "TestPunch",
            "datums": {
                "mounting_plane": {"kind": "plane", "origin": [0,0,100], "normal": [0,0,1]},
                "tip_point": {"kind": "point", "position": [0,0,0]},
                "working_axis": {"kind": "axis", "origin": [0,0,0], "direction": [1,0,0]}
            }
        }
        with open(self.sidecar_path, 'w') as f:
            json.dump(sidecar_data, f)
            
        loader = FcstdToolingLoader()
        tool = loader.load(self.fcstd_path, ToolingKind.PUNCH, target_object_name="Body")
        
        self.assertEqual(tool.geometry_status, "REAL")
        self.assertEqual(tool.id, "TestPunch")
        self.assertEqual(tool.datums[DatumRole.MOUNTING_PLANE].geometry, ((0,0,100), (0,0,1)))

    def test_assembly_misalignment(self):
        """Проверяет, что если сместить датум монтажа (MOUNTING_PLANE), ToolingAssembly выдаст MISALIGNED."""
        if not HAS_OCC:
            self.skipTest("pythonocc-core not installed")
        if not os.path.exists(self.fcstd_path):
            self.skipTest("Test file not found")
            
        sidecar_data_punch = {
            "datums": {
                "mounting_plane": {"kind": "plane", "origin": [0,0,100], "normal": [0,0,1]},
                "tip_point": {"kind": "point", "position": [0,5,0]},  # СМЕЩЕНИЕ НА 5 ММ
                "working_axis": {"kind": "axis", "origin": [0,5,0], "direction": [1,0,0]}
            }
        }
        with open(self.sidecar_path, 'w') as f:
            json.dump(sidecar_data_punch, f)
            
        loader = FcstdToolingLoader()
        punch = loader.load(self.fcstd_path, ToolingKind.PUNCH, target_object_name="Body")
        
        # Создаем валидную матрицу
        sidecar_data_die = {
            "datums": {
                "mounting_plane": {"kind": "plane", "origin": [0,0,0], "normal": [0,0,-1]},
                "v_center": {"kind": "point", "position": [0,0,50]},  # X=0, Y=0
                "working_axis": {"kind": "axis", "origin": [0,0,50], "direction": [1,0,0]}
            }
        }
        with open(self.sidecar_path, 'w') as f:
            json.dump(sidecar_data_die, f)
            
        die = loader.load(self.fcstd_path, ToolingKind.DIE, target_object_name="Body")
        
        backend = MockBackend()
        assembly = ToolingAssembly(backend, self.machine)
        result = assembly.place(punch, die)
        
        self.assertEqual(result.status, "MISALIGNED")
        self.assertTrue(any("misaligned" in msg.lower() for msg in result.diagnostics))

    def test_penetration_collision(self):
        """Проверяет, что PartVsPunch при пересечении в 0.1 мм выдает INVALID."""
        if not HAS_OCC:
            self.skipTest("pythonocc-core not installed")
            
        # Instead of a full FCStd pipeline for the collision, we can use OCC directly.
        box1 = BRepPrimAPI_MakeBox(10, 10, 10).Shape()
        # Create box2 that penetrates box1 slightly (0.5mm)
        box2 = BRepPrimAPI_MakeBox(10, 10, 10).Shape()
        
        # Using a proper collision backend
        from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
        backend = PythonOCCBackend()
        
        # Translate box2 to intersect box1
        box2_trans = backend.transform_shape(box2, translation=(9.5, 0, 0)) # Intersects by 0.5mm
        
        # We manually construct Assembly and Detector
        assembly = ToolingAssembly(backend, self.machine)
        # Mocking the world shape directly in the assembly dict is tricky, let's use the Detector method directly
        detector = CollisionDetector(assembly, backend)
        
        res = detector._check_intersection(box1, box2_trans, tolerance_volume=0.1)
        self.assertEqual(res.status, CollisionStatus.INVALID)
        
        # Now translate so they just touch or don't intersect
        box2_clear = backend.transform_shape(box2, translation=(10.05, 0, 0))
        res2 = detector._check_intersection(box1, box2_clear, tolerance_volume=0.1)
        self.assertEqual(res2.status, CollisionStatus.VALID)

    def test_get_world_shape_with_mock_returns_dict_unchanged(self):
        """Проверяет, что get_world_shape с MockBackend возвращает dict без падений на OCC."""
        backend = MockBackend()
        assembly = ToolingAssembly(backend, self.machine)
        from smbs_core.models.tooling import Tooling, GeometryStatus
        mock_tool = Tooling(
            id="MockDie",
            kind=ToolingKind.DIE,
            source_file="mock.fcstd",
            shape={"mock": "shape_dict"},
            local_frame=None,
            geometry_status=GeometryStatus.REAL,
            datums={}
        )
        assembly.die = mock_tool
        shape = assembly.get_world_shape(ToolingKind.DIE)
        self.assertEqual(shape, {"mock": "shape_dict"})

if __name__ == '__main__':
    unittest.main()
