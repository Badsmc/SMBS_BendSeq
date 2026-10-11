from abc import ABC, abstractmethod
from typing import Any, Tuple, Dict, Set, List, Optional
from dataclasses import dataclass

@dataclass
class CylinderData:
    radius: float
    origin: Tuple[float, float, float]
    axis: Tuple[float, float, float]
    height: float

@dataclass
class CollisionCheckResult:
    collides: bool
    status: str  # "valid", "invalid", "unknown"
    volume: float
    message: str

class InvalidRotationError(ValueError):
    """Raised when rotate_subtree is called with an empty or invalid moving_panels set."""
    pass

class GeometryBackend(ABC):
    """
    Abstract Base Class defining required geometry operations for SMBS.
    Decoupled from FreeCAD to allow running in headless environments via pythonocc-core.
    """

    @abstractmethod
    def load_shape(self, filepath: str) -> Any:
        """Load a neutral 3D model (e.g. STEP)."""
        pass

    @abstractmethod
    def load_from_brep_string(self, brep_str: str) -> Any:
        """Load shape from B-Rep string content."""
        pass

    @abstractmethod
    def copy_shape(self, shape: Any) -> Any:
        """Create an independent deep copy of the shape."""
        pass

    @abstractmethod
    def get_bounding_box(self, shape: Any) -> Tuple[float, float, float, float, float, float]:
        """Returns (xmin, ymin, zmin, xmax, ymax, zmax)"""
        pass

    @abstractmethod
    def rotate_subtree(
        self,
        shape: Any,
        axis_origin: Tuple[float, float, float],
        axis_direction: Tuple[float, float, float],
        signed_angle_deg: float,
        moving_panels: Optional[Set[str]] = None,
        panel_graph: Optional[Any] = None
    ) -> Any:
        """Rotates a specified subset of faces/panels around a given axis."""
        pass

    @abstractmethod
    def intersect_volume(self, shape1: Any, shape2: Any) -> CollisionCheckResult:
        """Returns structured result containing intersection status, volume, and message."""
        pass

    @abstractmethod
    def get_faces(self, shape: Any) -> List[Any]:
        """Returns a list of face objects in the shape."""
        pass

    @abstractmethod
    def extract_faces(self, shape: Any, face_ids: Set[str]) -> Any:
        """Extracts a subshape consisting of specified face_ids."""
        pass

    def get_face_center(self, face: Any) -> Tuple[float, float, float]:
        """Returns the center of mass (X, Y, Z) of a face."""
        return (0.0, 0.0, 0.0)

    @abstractmethod
    def get_face_hash(self, face: Any) -> str:
        """Returns deterministic hash string for face identification (area + type + center of mass)."""
        pass

    @abstractmethod
    def get_surface_type(self, face: Any) -> str:
        """Returns surface type: 'plane', 'cylinder', or 'other'."""
        pass

    @abstractmethod
    def get_cylinder_data(self, face: Any) -> Optional[CylinderData]:
        """Returns CylinderData if surface is cylindrical, else None."""
        pass

    @abstractmethod
    def get_face_normal(self, face: Any) -> Tuple[float, float, float]:
        """Returns normal vector of face at its center of mass."""
        pass

    @abstractmethod
    def get_face_adjacencies(self, shape: Any) -> Dict[str, Set[str]]:
        """Returns map of face_id to set of adjacent face_ids."""
        pass

    @abstractmethod
    def is_valid(self, shape: Any) -> bool:
        """Returns True if shape is valid and non-null."""
        pass

    @abstractmethod
    def export_brep(self, shape: Any, filepath: str) -> bool:
        """Export shape to BREP file."""
        pass

    @abstractmethod
    def make_box(self, x: float, y: float, z: float) -> Any:
        """Creates axis-aligned box primitive with dimensions (x, y, z)."""
        pass

    @abstractmethod
    def transform_shape(
        self,
        shape: Any,
        translation: Tuple[float, float, float],
        axis_origin: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        axis_dir: Tuple[float, float, float] = (0.0, 0.0, 1.0),
        angle_deg: float = 0.0
    ) -> Any:
        """Transforms shape with translation and rotation."""
        pass

    @abstractmethod
    def fuse(self, shape1: Any, shape2: Any) -> Any:
        """Boolean union of shape1 and shape2."""
        pass

    @abstractmethod
    def cut(self, shape1: Any, shape2: Any) -> Any:
        """Boolean subtraction of shape2 from shape1."""
        pass
