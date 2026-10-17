"""
collision_detector.py - Exact B-Rep Collision Engine (Headless Core).
Rule: CollisionStatus.UNKNOWN is NEVER treated as VALID.
"""

from enum import Enum
from dataclasses import dataclass
from typing import Dict, Any, Optional, Set, Tuple, Iterator
import logging

from smbs_core.geometry_backend.base import GeometryBackend
from smbs_core.Physics.tooling_assembly import ToolingAssembly, ToolingKind
from smbs_core.models.tooling import GeometryStatus

logger = logging.getLogger(__name__)


class CollisionStatus(Enum):
    VALID = "valid"
    INVALID = "invalid"
    UNKNOWN = "unknown"

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, str):
            return self.value == other.lower()
        return super().__eq__(other)


@dataclass
class CollisionResult:
    status: CollisionStatus
    message: str

    @property
    def is_valid(self) -> bool:
        # UNKNOWN is never treated as VALID
        return self.status == CollisionStatus.VALID

    def __iter__(self) -> Iterator[Any]:
        yield self.is_valid
        yield self.message


class CollisionDetector:
    def __init__(self, assembly: Any, backend: GeometryBackend):
        if assembly is not None and hasattr(assembly, 'setup_assembly') and not hasattr(assembly, 'get_world_shape'):
            self.machine = assembly
            self.assembly = assembly.setup_assembly(backend)
        else:
            self.assembly = assembly
            self.machine = getattr(assembly, 'machine', None) if assembly is not None else None

        self.backend = backend
        self._memo_panels: Dict[int, Tuple[Any, Dict[str, Any]]] = {}

    def begin_step(self) -> None:
        self._memo_panels.clear()

    def end_step(self) -> None:
        self._memo_panels.clear()

    def _extract_panel_shapes(self, shape: Any, panel_graph: Any) -> Dict[str, Any]:
        panel_shapes = {}
        if shape is None or panel_graph is None:
            return panel_shapes

        memo = self._memo_panels.get(id(shape))
        if memo is not None and memo[0] is shape:
            return memo[1]

        if not hasattr(panel_graph, 'panels') or not any(p.face_indices for p in panel_graph.panels.values()):
            self._memo_panels[id(shape)] = (shape, panel_shapes)
            return panel_shapes

        for panel_id, panel in panel_graph.panels.items():
            if panel.face_indices:
                faces = set(str(idx) for idx in panel.face_indices)
                panel_shapes[panel_id] = self.backend.extract_faces(shape, faces)

        self._memo_panels[id(shape)] = (shape, panel_shapes)
        return panel_shapes

    def _check_intersection(
        self,
        shape1: Any,
        shape2: Any,
        shape1_name: str = "Part",
        shape2_name: str = "Tool",
        tolerance_volume: float = 0.01
    ) -> CollisionResult:
        if shape1 is None or shape2 is None:
            return CollisionResult(CollisionStatus.UNKNOWN, "Missing geometry for collision check")

        try:
            if hasattr(self.backend, 'intersect_volume'):
                res = self.backend.intersect_volume(shape1, shape2)
                if hasattr(res, 'status'):
                    if res.status == "unknown":
                        return CollisionResult(CollisionStatus.UNKNOWN, f"Intersection check UNKNOWN between {shape1_name} and {shape2_name}: {res.message}")
                    if res.collides or res.status == "invalid":
                        return CollisionResult(CollisionStatus.INVALID, f"Intersection detected between {shape1_name} and {shape2_name}: {res.message}")
                    return CollisionResult(CollisionStatus.VALID, "Clear")
                else:
                    if res:
                        return CollisionResult(CollisionStatus.INVALID, f"Intersection detected between {shape1_name} and {shape2_name}")
                    return CollisionResult(CollisionStatus.VALID, "Clear")
            else:
                return CollisionResult(CollisionStatus.UNKNOWN, "Backend lacks exact intersection implementation")
        except Exception as e:
            logger.error(f"OCC Boolean collision check exception: {e}")
            return CollisionResult(CollisionStatus.UNKNOWN, f"OCC Exception: {e}")

    def self_collision(self, shape: Any, bend_id: Optional[str] = None, panel_graph: Optional[Any] = None) -> CollisionResult:
        if shape is None:
            return CollisionResult(CollisionStatus.UNKNOWN, "No part shape for self_collision check")

        if bend_id is None or panel_graph is None:
            return CollisionResult(CollisionStatus.UNKNOWN, "Missing bend_id or panel_graph for self_collision")

        try:
            fixed_panels = panel_graph.fixed_set(bend_id)
            moving_panels = panel_graph.moving_subtree(bend_id)

            if not fixed_panels or not moving_panels:
                return CollisionResult(CollisionStatus.UNKNOWN, "Empty fixed or moving panel set for self_collision")

            panel_shapes = self._extract_panel_shapes(shape, panel_graph)

            checked_pairs = 0
            for fp_id in fixed_panels:
                if fp_id not in panel_shapes or panel_shapes[fp_id] is None:
                    continue
                fp_shape = panel_shapes[fp_id]

                for mp_id in moving_panels:
                    if panel_graph.are_adjacent(fp_id, mp_id):
                        continue
                    if mp_id not in panel_shapes or panel_shapes[mp_id] is None:
                        continue
                    mp_shape = panel_shapes[mp_id]

                    checked_pairs += 1
                    res = self._check_intersection(fp_shape, mp_shape, f"Panel {mp_id}", f"Panel {fp_id}")
                    if not res.is_valid:
                        return res

            if checked_pairs == 0:
                return CollisionResult(CollisionStatus.UNKNOWN, "No non-adjacent panel pairs available for self_collision check")

            return CollisionResult(CollisionStatus.VALID, "Clear")
        except Exception as e:
            logger.error(f"self_collision exception: {e}")
            return CollisionResult(CollisionStatus.UNKNOWN, f"self_collision Exception: {e}")

    def place_part_on_tool_station(self, shape: Any, bend_info: Optional[Dict[str, Any]]) -> Any:
        if shape is None or not self.assembly or not bend_info:
            return shape

        if not hasattr(self.assembly, 'die') or self.assembly.die is None:
            return shape

        from smbs_core.models.tooling import DatumRole, ToolingKind
        die_vcenter = self.assembly.die.datums.get(DatumRole.V_CENTER)
        if not die_vcenter:
            return shape

        try:
            from OCC.Core.gp import gp_Pnt, gp_Dir, gp_Ax3, gp_Trsf
            from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform

            b_orig = bend_info.get("origin")
            b_axis = bend_info.get("axis")
            if not b_orig or not b_axis:
                return shape

            die_trsf = self.assembly.assembly_trsf.get(ToolingKind.DIE)
            if not die_trsf:
                return shape

            v_world = gp_Pnt(*die_vcenter.origin).Transformed(die_trsf)

            die_world = self.assembly.get_world_shape(ToolingKind.DIE)
            die_bb = self.backend.get_bounding_box(die_world) if die_world else None
            die_top_z = die_bb[5] if die_bb else v_world.Z()
            v_station = gp_Pnt(v_world.X(), v_world.Y(), die_top_z)

            axis_dir = gp_Dir(*b_axis)

            # Evaluate orientations to find clear placement if possible
            best_shape = None
            for n_vec in [(0, 0, 1), (0, 0, -1), (0, 1, 0), (0, -1, 0)]:
                dot = abs(sum(axis_dir.Coord()[i] * n_vec[i] for i in range(3)))
                if dot > 0.9:
                    continue

                trsf = gp_Trsf()
                trsf.SetDisplacement(
                    gp_Ax3(gp_Pnt(*b_orig), axis_dir, gp_Dir(*n_vec)),
                    gp_Ax3(v_station, gp_Dir(1, 0, 0), gp_Dir(0, 0, 1))
                )
                cand = BRepBuilderAPI_Transform(shape, trsf, False).Shape()
                if die_world:
                    res_d = self._check_intersection(cand, die_world, "Part", "Die")
                    if res_d.is_valid:
                        return cand
                if best_shape is None:
                    best_shape = cand

            return best_shape or shape
        except Exception as e:
            logger.debug(f"Could not transform part to tool station: {e}")
            return shape

    def approach_collision(self, shape: Any, bend_info: Dict[str, Any]) -> CollisionResult:
        if not self.assembly:
            return CollisionResult(CollisionStatus.UNKNOWN, "No tooling assembly available for approach_collision")

        placed_shape = self.place_part_on_tool_station(shape, bend_info)

        is_die_simplified = self.assembly.die and getattr(self.assembly.die, 'geometry_status', None) == GeometryStatus.SIMPLIFIED
        if is_die_simplified:
            return CollisionResult(CollisionStatus.UNKNOWN, "Simplified tooling — cannot verify approach collision with die")
        die = self.assembly.get_world_shape(ToolingKind.DIE)
        if die is None:
            return CollisionResult(CollisionStatus.UNKNOWN, "No die shape available for approach_collision")
        res = self._check_intersection(placed_shape, die, "Part", "Die")
        if not res.is_valid:
            return res

        machine = self.assembly.get_world_shape(ToolingKind.MACHINE_FRAME)
        if machine is None:
            return CollisionResult(CollisionStatus.UNKNOWN, "No machine_frame shape available for approach_collision")
        res = self._check_intersection(placed_shape, machine, "Part", "Machine Frame")
        if not res.is_valid:
            return res

        return CollisionResult(CollisionStatus.VALID, "Clear")

    def tool_collision(self, shape: Any, bend_info: Dict[str, Any], bend_id: str = "", panel_graph: Any = None) -> CollisionResult:
        if not self.assembly:
            return CollisionResult(CollisionStatus.UNKNOWN, "No tooling assembly available for tool_collision")

        placed_shape = self.place_part_on_tool_station(shape, bend_info)

        is_punch_simplified = self.assembly.punch and getattr(self.assembly.punch, 'geometry_status', None) == GeometryStatus.SIMPLIFIED
        if is_punch_simplified:
            return CollisionResult(CollisionStatus.UNKNOWN, "Simplified tooling — cannot verify punch collision")
        punch = self.assembly.get_world_shape(ToolingKind.PUNCH)
        if punch is None:
            return CollisionResult(CollisionStatus.UNKNOWN, "No punch shape available for tool_collision")
        res = self._check_intersection(placed_shape, punch, "Part", "Punch")
        if not res.is_valid:
            return res

        is_die_simplified = self.assembly.die and getattr(self.assembly.die, 'geometry_status', None) == GeometryStatus.SIMPLIFIED
        if is_die_simplified:
            return CollisionResult(CollisionStatus.UNKNOWN, "Simplified tooling — cannot verify die collision")
        die = self.assembly.get_world_shape(ToolingKind.DIE)
        if die is None:
            return CollisionResult(CollisionStatus.UNKNOWN, "No die shape available for tool_collision")
        res = self._check_intersection(placed_shape, die, "Part", "Die")
        if not res.is_valid:
            return res

        return CollisionResult(CollisionStatus.VALID, "Clear")

    def machine_collision(self, shape: Any, bend_info: Dict[str, Any], bend_id: str = "", panel_graph: Any = None) -> CollisionResult:
        if not self.assembly:
            return CollisionResult(CollisionStatus.UNKNOWN, "No tooling assembly available for machine_collision")

        placed_shape = self.place_part_on_tool_station(shape, bend_info)

        machine = self.assembly.get_world_shape(ToolingKind.MACHINE_FRAME)
        if machine is None:
            return CollisionResult(CollisionStatus.UNKNOWN, "No machine_frame shape available for machine_collision")
        res = self._check_intersection(placed_shape, machine, "Part", "Machine Frame")
        if not res.is_valid:
            return res

        bed = self.assembly.get_world_shape(ToolingKind.MACHINE_BED)
        if bed is None:
            return CollisionResult(CollisionStatus.UNKNOWN, "No machine_bed shape available for machine_collision")
        res = self._check_intersection(placed_shape, bed, "Part", "Machine Bed")
        if not res.is_valid:
            return res

        return CollisionResult(CollisionStatus.VALID, "Clear")

    def backgauge_collision(self, shape: Any, backgauge_shape: Any) -> CollisionResult:
        if backgauge_shape is None:
            return CollisionResult(CollisionStatus.UNKNOWN, "No backgauge shape available for backgauge_collision")
        return self._check_intersection(shape, backgauge_shape, "Part", "Backgauge Finger")

    def trajectory_collision(self, start_shape: Any, bend_info: Dict[str, Any], bend_id: str = "", panel_graph: Any = None, dtheta: float = 5.0) -> CollisionResult:
        if not self.assembly:
            return CollisionResult(CollisionStatus.UNKNOWN, "No tooling assembly available for trajectory_collision")
        
        target_angle = bend_info.get("angle", 90.0)
        # We need to simulate unbending, so the angle to apply is -target_angle
        # if this function is called during backward planning.
        # Wait, the trajectory_collision is called with bend_info from the physical validator.
        # PhysicalValidator calls it BEFORE unfolding in `validator.py`:
        # `traj_res = self.detector.trajectory_collision(current_state.shape, bend_info, bend_id=bend_id, panel_graph=panel_graph, dtheta=5.0)`
        # It means we start at current_state.shape and we need to unfold by -target_angle.
        
        import math
        unfold_angle = -target_angle
        steps = max(1, int(abs(unfold_angle) / dtheta))
        step_angle = unfold_angle / steps
        
        origin = bend_info.get("origin", (0.0, 0.0, 0.0))
        axis = bend_info.get("axis", (1.0, 0.0, 0.0))
        moving_panels = bend_info.get("moving_panels")
        if not moving_panels and panel_graph and bend_id:
            moving_panels = panel_graph.moving_subtree(bend_id)

        if not moving_panels:
            return CollisionResult(CollisionStatus.UNKNOWN, "trajectory_collision: moving_panels not provided; cannot verify trajectory")

        for i in range(1, steps + 1):
            current_angle = step_angle * i
            
            # Use BendTransform.rotate_subtree if it was available, but since we are inside CollisionDetector,
            # we should just use backend.rotate_subtree directly.
            inter_shape = self.backend.rotate_subtree(
                start_shape,
                origin,
                axis,
                current_angle,
                moving_panels,
                panel_graph
            )
            
            # Check collisions at this intermediate step
            # 1. Self collision
            if panel_graph is not None and bend_id:
                self_res = self.self_collision(inter_shape, bend_id, panel_graph)
                if not self_res.is_valid:
                    return CollisionResult(self_res.status, f"Trajectory Self-Collision at {current_angle:.1f} deg: {self_res.message}")
            
            # 2. Tool collision
            tool_res = self.tool_collision(inter_shape, bend_info, bend_id, panel_graph)
            if not tool_res.is_valid:
                return CollisionResult(tool_res.status, f"Trajectory Tool-Collision at {current_angle:.1f} deg: {tool_res.message}")
            
            # 3. Machine collision
            if self.assembly.get_world_shape(ToolingKind.MACHINE_FRAME) is not None or self.assembly.get_world_shape(ToolingKind.MACHINE_BED) is not None:
                mach_res = self.machine_collision(inter_shape, bend_info, bend_id, panel_graph)
                if not mach_res.is_valid:
                    return CollisionResult(mach_res.status, f"Trajectory Machine-Collision at {current_angle:.1f} deg: {mach_res.message}")
                
        return CollisionResult(CollisionStatus.VALID, "Clear")

    def retract_collision(self, shape: Any, bend_info: Dict[str, Any]) -> CollisionResult:
        return self.approach_collision(shape, bend_info)
