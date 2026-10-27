"""
machine_kinematics.py - Machine Kinematics and Real/Synthetic Tooling Setup Bridge
"""

from typing import Dict, Any, Optional
import os
import logging

from smbs_core.geometry_backend.base import GeometryBackend
from smbs_core.models.tooling import Tooling, ToolingKind, DatumRole, Datum, MachineConfiguration, GeometryStatus
from smbs_core.Physics.tooling_assembly import ToolingAssembly
from smbs_core.geometry_backend.backends.fcstd_loader import FcstdToolingLoader

logger = logging.getLogger(__name__)

try:
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    HAS_OCC = True
except ImportError:
    HAS_OCC = False


class PressBrakeMachine:
    def __init__(
        self,
        name: str = "press_100t",
        max_tonnage: float = 100.0,
        bed_length_mm: float = 2000.0,
        backgauge_x_max_mm: float = 800.0,
        backgauge_r_max_mm: float = 250.0,
        punch_path: Optional[str] = None,
        die_path: Optional[str] = None,
        punch_target_object_name: Optional[str] = None,
        die_target_object_name: Optional[str] = None,
        filepath: Optional[str] = None,
        machine_frame_path: Optional[str] = None,
        machine_frame_target_object_name: Optional[str] = None,
    ):
        self.name = name
        self.max_tonnage = max_tonnage
        self.bed_length_mm = bed_length_mm
        self.backgauge_x_max_mm = backgauge_x_max_mm
        self.backgauge_r_max_mm = backgauge_r_max_mm
        self.punch_path = punch_path
        self.die_path = die_path
        self.punch_target_object_name = punch_target_object_name
        self.die_target_object_name = die_target_object_name
        self.machine_frame_path = machine_frame_path
        self.machine_frame_target_object_name = machine_frame_target_object_name

        self.config = MachineConfiguration(
            id=name,
            bed_top_plane=Datum(DatumRole.MOUNTING_PLANE, "plane", ((0, 0, 0), (0, 0, 1)), source="sidecar", confirmed=True),
            ram_mount_plane=Datum(DatumRole.MOUNTING_PLANE, "plane", ((0, 0, 200), (0, 0, -1)), source="sidecar", confirmed=True),
            ram_stroke_mm=200.0,
            ram_direction=(0.0, 0.0, -1.0),
            backgauge_axes={"X": {"max": backgauge_x_max_mm}, "R": {"max": backgauge_r_max_mm}},
            max_tonnage_kn=max_tonnage * 10.0,
            max_bend_length_mm=bed_length_mm,
            source_file=filepath
        )

        self.assembly: Optional[ToolingAssembly] = None
        self.punch: Optional[Tooling] = None
        self.die: Optional[Tooling] = None
        self.machine_frame: Optional[Tooling] = None

    @classmethod
    def default_setup(cls) -> "PressBrakeMachine":
        return cls()

    @classmethod
    def with_default_tooling(cls) -> "PressBrakeMachine":
        default_p = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "toollibrary", "punch", "puancon.FCStd"))
        default_d = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "toollibrary", "die", "matrica.FCStd"))
        return cls(punch_path=default_p if os.path.exists(default_p) else None,
                   die_path=default_d if os.path.exists(default_d) else None,
                   punch_target_object_name="Body" if os.path.exists(default_p) else None,
                   die_target_object_name="Body" if os.path.exists(default_d) else None)

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "PressBrakeMachine":
        return cls(
            name=config.get("name", "press_100t"),
            bed_length_mm=config.get("bed_length_mm", 2000.0),
            punch_path=config.get("punch_path"),
            die_path=config.get("die_path"),
            punch_target_object_name=config.get("punch_target_object_name"),
            die_target_object_name=config.get("die_target_object_name"),
            filepath=config.get("source_file"),
            machine_frame_path=config.get("machine_frame_path"),
            machine_frame_target_object_name=config.get("machine_frame_target_object_name"),
        )

    def setup_assembly(self, backend: GeometryBackend) -> ToolingAssembly:
        self.assembly = ToolingAssembly(backend, self.config)
        loader = FcstdToolingLoader()

        if self.punch_path and os.path.exists(self.punch_path):
            self.punch = loader.load(self.punch_path, ToolingKind.PUNCH, target_object_name=self.punch_target_object_name)
        else:
            p_shape = backend.make_box(self.bed_length_mm, 50.0, 100.0)
            p_datums = {
                DatumRole.MOUNTING_PLANE: Datum(DatumRole.MOUNTING_PLANE, "plane", ((0, 0, 100), (0, 0, -1)), source="inferred", confirmed=False),
                DatumRole.WORKING_AXIS: Datum(DatumRole.WORKING_AXIS, "axis", ((0, 0, 0), (1, 0, 0)), source="inferred", confirmed=False)
            }
            self.punch = Tooling(
                id="punch_synth",
                kind=ToolingKind.PUNCH,
                source_file="synthetic",
                shape=p_shape,
                local_frame=None,
                geometry_status=GeometryStatus.SIMPLIFIED,
                datums=p_datums
            )
            logger.info("Created synthetic SIMPLIFIED punch tooling.")

        if self.die_path and os.path.exists(self.die_path):
            self.die = loader.load(self.die_path, ToolingKind.DIE, target_object_name=self.die_target_object_name)
        else:
            d_shape = backend.make_box(self.bed_length_mm, 60.0, 80.0)
            d_datums = {
                DatumRole.MOUNTING_PLANE: Datum(DatumRole.MOUNTING_PLANE, "plane", ((0, 0, 0), (0, 0, -1)), source="inferred", confirmed=False),
                DatumRole.V_CENTER: Datum(DatumRole.V_CENTER, "axis", ((0, 0, 80), (1, 0, 0)), source="inferred", confirmed=False)
            }
            self.die = Tooling(
                id="die_synth",
                kind=ToolingKind.DIE,
                source_file="synthetic",
                shape=d_shape,
                local_frame=None,
                geometry_status=GeometryStatus.SIMPLIFIED,
                datums=d_datums
            )
            logger.info("Created synthetic SIMPLIFIED die tooling.")
        
        if self.machine_frame_path and os.path.exists(self.machine_frame_path):
            self.machine_frame = loader.load(
                self.machine_frame_path,
                ToolingKind.MACHINE_FRAME,
                target_object_name=self.machine_frame_target_object_name,
            )
            self.config.frame = self.machine_frame
        else:
            self.machine_frame = None
            self.config.frame = None

        self.assembly.place(self.punch, self.die, machine_frame=self.machine_frame)
        return self.assembly
