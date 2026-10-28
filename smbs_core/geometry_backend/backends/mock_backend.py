import hashlib
from typing import Any, Tuple, Dict, Set, List, Optional
from smbs_core.geometry_backend.base import (
    GeometryBackend,
    CylinderData,
    CollisionCheckResult,
    InvalidRotationError,
)

class MockBackend(GeometryBackend):
    """
    Mock geometry backend for testing core planning logic without pythonocc or FreeCAD dependencies.
    """

    def load_shape(self, filepath: str) -> Any:
        return {"type": "mock_shape", "file": filepath, "id": "mock_1"}

    def load_from_brep_string(self, brep_str: str) -> Any:
        return {"type": "mock_shape", "brep": brep_str, "id": "mock_brep"}

    def copy_shape(self, shape: Any) -> Any:
        return shape.copy() if isinstance(shape, dict) else shape

    def get_bounding_box(self, shape: Any) -> Tuple[float, float, float, float, float, float]:
        if isinstance(shape, dict) and "bbox" in shape:
            return shape["bbox"]
        return (0.0, 0.0, 0.0, 100.0, 100.0, 10.0)

    def rotate_subtree(
        self,
        shape: Any,
        axis_origin: Tuple[float, float, float],
        axis_direction: Tuple[float, float, float],
        signed_angle_deg: float,
        moving_panels: Optional[Set[str]] = None,
        panel_graph: Optional[Any] = None
    ) -> Any:
        if not moving_panels:
            raise InvalidRotationError("rotate_subtree requires non-empty moving_panels set")

        new_shape = self.copy_shape(shape)
        if isinstance(new_shape, dict):
            new_shape["last_rotation"] = signed_angle_deg
            new_shape["moving_panels"] = list(moving_panels)
        return new_shape

    def intersect_volume(self, shape1: Any, shape2: Any) -> CollisionCheckResult:
        if shape1 is None or shape2 is None:
            return CollisionCheckResult(collides=False, status="unknown", volume=0.0, message="Missing geometry for intersection check")
        
        # Check if shapes explicitly define collision in test mock
        if isinstance(shape1, dict) and shape1.get("simulate_collision"):
            return CollisionCheckResult(collides=True, status="invalid", volume=10.0, message="Simulated intersection")
        if isinstance(shape2, dict) and shape2.get("simulate_collision"):
            return CollisionCheckResult(collides=True, status="invalid", volume=10.0, message="Simulated intersection")

        return CollisionCheckResult(collides=False, status="valid", volume=0.0, message="Clear")

    def get_faces(self, shape: Any) -> List[Any]:
        return [{"id": "FACE_0", "type": "plane"}, {"id": "FACE_1", "type": "cylinder"}]

    def extract_faces(self, shape: Any, face_ids: Set[str]) -> Any:
        if not face_ids:
            return None
        new_shape = self.copy_shape(shape)
        if isinstance(new_shape, dict):
            new_shape["extracted_faces"] = list(face_ids)
        return new_shape

    def get_face_hash(self, face: Any) -> str:
        s = str(face)
        return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

    def get_surface_type(self, face: Any) -> str:
        if isinstance(face, dict):
            return face.get("type", "plane")
        return "plane"

    def get_cylinder_data(self, face: Any) -> Optional[CylinderData]:
        if isinstance(face, dict) and face.get("type") == "cylinder":
            return CylinderData(radius=2.0, origin=(0, 0, 0), axis=(1, 0, 0), height=50.0)
        return None

    def get_face_normal(self, face: Any) -> Tuple[float, float, float]:
        return (0.0, 0.0, 1.0)

    def get_face_adjacencies(self, shape: Any) -> Dict[str, Set[str]]:
        return {"FACE_0": {"FACE_1"}, "FACE_1": {"FACE_0"}}

    def is_valid(self, shape: Any) -> bool:
        if shape is None:
            return False
        if isinstance(shape, dict) and shape.get("is_broken"):
            return False
        return True

    def export_brep(self, shape: Any, filepath: str) -> bool:
        return shape is not None

    def make_box(self, x: float, y: float, z: float) -> Any:
        return {"type": "mock_box", "dim": (x, y, z), "bbox": (0.0, 0.0, 0.0, x, y, z)}

    def transform_shape(
        self,
        shape: Any,
        translation: Tuple[float, float, float],
        axis_origin: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        axis_dir: Tuple[float, float, float] = (0.0, 0.0, 1.0),
        angle_deg: float = 0.0
    ) -> Any:
        new_shape = self.copy_shape(shape)
        if isinstance(new_shape, dict):
            new_shape["translation"] = translation
            new_shape["rotation"] = angle_deg
        return new_shape

    def fuse(self, shape1: Any, shape2: Any) -> Any:
        return {"type": "fused_shape", "parts": [shape1, shape2]}

    def cut(self, shape1: Any, shape2: Any) -> Any:
        return {"type": "cut_shape", "base": shape1, "tool": shape2}

    def min_distance(self, shape_a: Any, shape_b: Any) -> Optional[float]:
        if shape_a is None or shape_b is None:
            return None
        if isinstance(shape_a, dict) and "simulated_distance" in shape_a:
            return float(shape_a["simulated_distance"])
        if isinstance(shape_b, dict) and "simulated_distance" in shape_b:
            return float(shape_b["simulated_distance"])
        return 10.0

    def section_overlap_length(self, shape_a: Any, shape_b: Any) -> Optional[float]:
        if shape_a is None or shape_b is None:
            return None
        if isinstance(shape_a, dict) and "simulated_section_len" in shape_a:
            return float(shape_a["simulated_section_len"])
        if isinstance(shape_b, dict) and "simulated_section_len" in shape_b:
            return float(shape_b["simulated_section_len"])
        return 0.0
