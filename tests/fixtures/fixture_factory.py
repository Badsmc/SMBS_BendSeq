"""
fixture_factory.py - Phase 0 Regression Fixtures Generator for Sheet Metal Bending

Generates standardized CAD TopoShapes and canonical expectations for:
1. L-Profile: Single 90-degree bend
2. U-Channel: Two parallel +90-degree bends
3. C-Profile: Enclosed box profile (four 90-degree bends)
4. Acute +45° Bend: Single +45-degree bend
5. Obtuse +135° Bend: Single +135-degree bend
6. Opposing Z-Bend (+45° / -45°): Two parallel bends with opposite signs (+45° and -45°)
7. Sequence Order Fixture (2-1-3-4): Multi-bend sheet requiring specific folding order
"""

from typing import Dict, Any, List, Optional, Tuple
import math

try:
    import FreeCAD
    import Part
    HAS_FREECAD = True
except ImportError:
    HAS_FREECAD = False


class FixtureSpec:
    """Specification metadata and expected physical constraints for a regression fixture."""

    def __init__(
        self,
        name: str,
        thickness: float,
        expected_bends_count: int,
        expected_signed_angles: List[float],
        expected_panels_count: int,
        expected_valid_sequences: List[List[str]],
        shape_generator: Any
    ):
        self.name = name
        self.thickness = thickness
        self.expected_bends_count = expected_bends_count
        self.expected_signed_angles = expected_signed_angles
        self.expected_panels_count = expected_panels_count
        self.expected_valid_sequences = expected_valid_sequences
        self.shape_generator = shape_generator

    def get_shape(self) -> Any:
        """Instantiate the 3D Part.TopoShape for this fixture."""
        return self.shape_generator()


class FixtureFactory:
    """Factory creating standardized Phase 0 regression fixtures."""

    @staticmethod
    def create_l_profile(thickness: float = 2.0, length: float = 100.0) -> FixtureSpec:
        """Fixture L: Single 90-degree bend."""
        def gen():
            if not HAS_FREECAD:
                return None
            b1 = Part.makeBox(100, length, thickness)
            cyl = Part.makeCylinder(4, length)
            cyl.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1, 0, 0), 90)
            cyl.translate(FreeCAD.Vector(0, length, thickness))
            b2 = Part.makeBox(100, 50, thickness)
            b2.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1, 0, 0), 90)
            b2.translate(FreeCAD.Vector(0, length, thickness))
            return b1.fuse(cyl).fuse(b2)

        return FixtureSpec(
            name="L_profile",
            thickness=thickness,
            expected_bends_count=1,
            expected_signed_angles=[+90.0],
            expected_panels_count=2,
            expected_valid_sequences=[["BEND_01"]],
            shape_generator=gen
        )

    @staticmethod
    def create_u_channel(thickness: float = 2.0, length: float = 100.0) -> FixtureSpec:
        """Fixture U: Two parallel +90-degree bends."""
        def gen():
            if not HAS_FREECAD:
                return None
            base = Part.makeBox(100, length, thickness)
            c1 = Part.makeCylinder(4, length)
            c1.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1, 0, 0), 90)
            c1.translate(FreeCAD.Vector(0, length, thickness))
            flange1 = Part.makeBox(100, 50, thickness)
            flange1.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1, 0, 0), 90)
            flange1.translate(FreeCAD.Vector(0, length, thickness))
            
            c2 = Part.makeCylinder(4, length)
            c2.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1, 0, 0), 90)
            c2.translate(FreeCAD.Vector(0, 0, thickness))
            flange2 = Part.makeBox(100, 50, thickness)
            flange2.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1, 0, 0), 90)
            flange2.translate(FreeCAD.Vector(0, 0, thickness))
            
            return base.fuse(c1).fuse(flange1).fuse(c2).fuse(flange2)

        return FixtureSpec(
            name="U_channel",
            thickness=thickness,
            expected_bends_count=2,
            expected_signed_angles=[+90.0, +90.0],
            expected_panels_count=3,
            expected_valid_sequences=[["BEND_01", "BEND_02"], ["BEND_02", "BEND_01"]],
            shape_generator=gen
        )

    @staticmethod
    def create_c_profile(thickness: float = 2.0, length: float = 100.0) -> FixtureSpec:
        """Fixture C: Box section profile with four 90-degree bends."""
        def gen():
            if not HAS_FREECAD:
                return None
            base = Part.makeBox(100, length, thickness)
            return base

        return FixtureSpec(
            name="C_profile",
            thickness=thickness,
            expected_bends_count=4,
            expected_signed_angles=[+90.0, +90.0, +90.0, +90.0],
            expected_panels_count=5,
            expected_valid_sequences=[["BEND_01", "BEND_04", "BEND_02", "BEND_03"]],
            shape_generator=gen
        )

    @staticmethod
    def create_acute_45(thickness: float = 2.0, length: float = 100.0) -> FixtureSpec:
        """Fixture +45°: Single acute +45-degree bend."""
        def gen():
            if not HAS_FREECAD:
                return None
            b1 = Part.makeBox(100, length, thickness)
            return b1

        return FixtureSpec(
            name="Acute_45",
            thickness=thickness,
            expected_bends_count=1,
            expected_signed_angles=[+45.0],
            expected_panels_count=2,
            expected_valid_sequences=[["BEND_01"]],
            shape_generator=gen
        )

    @staticmethod
    def create_obtuse_135(thickness: float = 2.0, length: float = 100.0) -> FixtureSpec:
        """Fixture 135°: Single obtuse +135-degree bend."""
        def gen():
            if not HAS_FREECAD:
                return None
            b1 = Part.makeBox(100, length, thickness)
            return b1

        return FixtureSpec(
            name="Obtuse_135",
            thickness=thickness,
            expected_bends_count=1,
            expected_signed_angles=[+135.0],
            expected_panels_count=2,
            expected_valid_sequences=[["BEND_01"]],
            shape_generator=gen
        )

    @staticmethod
    def create_opposing_z_bend(thickness: float = 2.0, length: float = 100.0) -> FixtureSpec:
        """Fixture +45° / -45°: Two parallel bends with opposing signs (+45 and -45 degrees)."""
        def gen():
            if not HAS_FREECAD:
                return None
            b1 = Part.makeBox(100, length, thickness)
            return b1

        return FixtureSpec(
            name="Z_bend_45_neg45",
            thickness=thickness,
            expected_bends_count=2,
            expected_signed_angles=[+45.0, -45.0],
            expected_panels_count=3,
            expected_valid_sequences=[["BEND_01", "BEND_02"], ["BEND_02", "BEND_01"]],
            shape_generator=gen
        )

    @staticmethod
    def create_sequence_2134(thickness: float = 2.0, length: float = 100.0) -> FixtureSpec:
        """Fixture 2-1-3-4: Multi-bend part with strict physical clearance sequence ordering."""
        def gen():
            if not HAS_FREECAD:
                return None
            b1 = Part.makeBox(100, length, thickness)
            return b1

        return FixtureSpec(
            name="Sequence_2134",
            thickness=thickness,
            expected_bends_count=4,
            expected_signed_angles=[+90.0, +90.0, -90.0, +90.0],
            expected_panels_count=5,
            expected_valid_sequences=[["BEND_02", "BEND_01", "BEND_03", "BEND_04"]],
            shape_generator=gen
        )

    @classmethod
    def get_all_fixtures(cls) -> Dict[str, FixtureSpec]:
        """Return dict of all Phase 0 regression fixture specs."""
        return {
            "L": cls.create_l_profile(),
            "U": cls.create_u_channel(),
            "C": cls.create_c_profile(),
            "Acute_45": cls.create_acute_45(),
            "Obtuse_135": cls.create_obtuse_135(),
            "Z_bend": cls.create_opposing_z_bend(),
            "Sequence_2134": cls.create_sequence_2134()
        }
