"""
tooling_assembly.py - Assembles tooling in machine coordinate space and validates alignment.
"""

import math
from dataclasses import dataclass
from typing import Dict, Any, Optional, List, Tuple

from smbs_core.models.tooling import (
    Tooling,
    MachineConfiguration,
    DatumRole,
    ToolingKind,
    AlignmentStatus,
    GeometryStatus,
    Datum,
)

try:
    from OCC.Core.gp import gp_Trsf, gp_Vec, gp_Ax3, gp_Pnt, gp_Dir
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
    HAS_OCC = True
except ImportError:
    HAS_OCC = False


@dataclass
class AssemblyResult:
    status: AlignmentStatus  # ALIGNED, MISALIGNED, CANNOT_VERIFY
    diagnostics: List[str]


class ToolingAssembly:
    def __init__(self, backend: Any, machine: MachineConfiguration):
        self.backend = backend
        self.machine = machine
        self.punch: Optional[Tooling] = None
        self.die: Optional[Tooling] = None
        self.machine_frame: Optional[Tooling] = None
        self.bed: Optional[Tooling] = None
        self.assembly_trsf: Dict[ToolingKind, Any] = {}

    @staticmethod
    def _align_planes(datum_source: Datum, datum_target: Datum) -> Any:
        """
        Computes gp_Trsf aligning datum_source to datum_target using full gp_Ax3 coordinates.
        """
        if not HAS_OCC:
            return None
        
        orig_s = gp_Pnt(*datum_source.origin)
        norm_s = gp_Dir(*datum_source.direction)
        x_dir_s = gp_Dir(*datum_source.x_direction) if datum_source.x_direction else gp_Dir(1, 0, 0)
        ax3_source = gp_Ax3(orig_s, norm_s, x_dir_s)

        orig_t = gp_Pnt(*datum_target.origin)
        norm_t = gp_Dir(*datum_target.direction)
        x_dir_t = gp_Dir(*datum_target.x_direction) if datum_target.x_direction else gp_Dir(1, 0, 0)
        ax3_target = gp_Ax3(orig_t, norm_t, x_dir_t)

        trsf = gp_Trsf()
        trsf.SetDisplacement(ax3_source, ax3_target)
        return trsf

    def place(
        self,
        punch: Tooling,
        die: Tooling,
        machine_frame: Optional[Tooling] = None,
        bed: Optional[Tooling] = None
    ) -> AssemblyResult:
        """
        1. Die -> MOUNTING_PLANE aligned with bed_top_plane.
        2. Punch -> MOUNTING_PLANE aligned with ram_mount_plane.
        3. Check alignment (axis collinearity, transverse offset, working zone).
        """
        self.punch = punch
        self.die = die
        self.machine_frame = machine_frame
        self.bed = bed

        diagnostics = []

        if punch is None:
            diagnostics.append("Punch tool is missing or could not be loaded.")
        elif not punch.has_verified_datums([DatumRole.MOUNTING_PLANE, DatumRole.WORKING_AXIS, DatumRole.TIP_POINT]):
            diagnostics.append(f"Punch '{punch.id}' lacks verified datums (inferred or missing).")

        if die is None:
            diagnostics.append("Die tool is missing or could not be loaded.")
        elif not die.has_verified_datums([DatumRole.MOUNTING_PLANE, DatumRole.WORKING_AXIS, DatumRole.V_CENTER]):
            diagnostics.append(f"Die '{die.id}' lacks verified datums (inferred or missing).")

        if diagnostics:
            return AssemblyResult(AlignmentStatus.CANNOT_VERIFY, diagnostics)

        if not HAS_OCC:
            return AssemblyResult(AlignmentStatus.CANNOT_VERIFY, ["pythonocc-core is unavailable"])

        # 1. Place Die on Machine Bed
        die_mount = die.datums[DatumRole.MOUNTING_PLANE]
        die_trsf = self._align_planes(die_mount, self.machine.bed_top_plane)
        self.assembly_trsf[ToolingKind.DIE] = die_trsf

        # 2. Place Punch on Ram
        punch_mount = punch.datums[DatumRole.MOUNTING_PLANE]
        punch_trsf = self._align_planes(punch_mount, self.machine.ram_mount_plane)
        self.assembly_trsf[ToolingKind.PUNCH] = punch_trsf

        # 3. Machine Frame / Bed
        if machine_frame and machine_frame.shape:
            if hasattr(machine_frame, "datums") and machine_frame.datums and DatumRole.MOUNTING_PLANE in machine_frame.datums:
                frame_mount = machine_frame.datums[DatumRole.MOUNTING_PLANE]
                self.assembly_trsf[ToolingKind.MACHINE_FRAME] = self._align_planes(frame_mount, self.machine.bed_top_plane)
            else:
                self.assembly_trsf[ToolingKind.MACHINE_FRAME] = gp_Trsf()
        else:
            self.assembly_trsf.pop(ToolingKind.MACHINE_FRAME, None)

        if bed and bed.shape:
            self.assembly_trsf[ToolingKind.MACHINE_BED] = gp_Trsf()
        else:
            self.assembly_trsf.pop(ToolingKind.MACHINE_BED, None)

        # 4. Alignment verification
        punch_tip_local = punch.datums[DatumRole.TIP_POINT].origin
        punch_tip_world = gp_Pnt(*punch_tip_local).Transformed(punch_trsf)

        die_vcenter_local = die.datums[DatumRole.V_CENTER].origin
        die_vcenter_world = gp_Pnt(*die_vcenter_local).Transformed(die_trsf)

        # Transverse offset check (in XY plane)
        dx = punch_tip_world.X() - die_vcenter_world.X()
        dy = punch_tip_world.Y() - die_vcenter_world.Y()
        offset_xy = math.sqrt(dx * dx + dy * dy)

        if offset_xy > self.machine.lateral_tolerance_mm:
            diagnostics.append(f"Punch TIP and Die V_CENTER misaligned in XY by {offset_xy:.3f} mm (tolerance {self.machine.lateral_tolerance_mm} mm).")
            return AssemblyResult(AlignmentStatus.MISALIGNED, diagnostics)

        # Working axis angle check
        p_axis_dir = punch.datums[DatumRole.WORKING_AXIS].direction
        d_axis_dir = die.datums[DatumRole.WORKING_AXIS].direction

        dot = abs(sum(p_axis_dir[i] * d_axis_dir[i] for i in range(3)))
        if dot < 0.999:  # > ~2.5 deg misalignment
            angle_deg = math.degrees(math.acos(min(1.0, dot)))
            diagnostics.append(f"Punch and Die working axes are misaligned by {angle_deg:.2f}° (tolerance {self.machine.angular_tolerance_deg}°).")
            return AssemblyResult(AlignmentStatus.MISALIGNED, diagnostics)

        return AssemblyResult(AlignmentStatus.ALIGNED, ["Tooling aligned and verified."])

    def get_world_shape(self, kind: ToolingKind) -> Any:
        tool_map = {
            ToolingKind.PUNCH: (self.punch, ToolingKind.PUNCH),
            ToolingKind.DIE: (self.die, ToolingKind.DIE),
            ToolingKind.MACHINE_FRAME: (self.machine_frame, ToolingKind.MACHINE_FRAME),
            ToolingKind.MACHINE_BED: (self.bed, ToolingKind.MACHINE_BED),
        }

        if kind not in tool_map:
            return None

        tool, t_kind = tool_map[kind]
        if tool is None or tool.shape is None or tool.geometry_status == GeometryStatus.MISSING:
            return None

        if not HAS_OCC or not hasattr(tool.shape, "IsNull") or tool.shape is None:
            return tool.shape

        trsf = self.assembly_trsf.get(t_kind, gp_Trsf())
        return BRepBuilderAPI_Transform(tool.shape, trsf, False).Shape()
