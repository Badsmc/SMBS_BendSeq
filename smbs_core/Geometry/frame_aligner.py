"""
Frame Aligner for SMBS / BendSeq.
Compliant with ТЗ v1.2 §5.2 and Principles 15, 18.
Calculates full 3D orthonormal frame alignment T = (R, t) between flat pattern and reference 3D model.
Enforces det(R) = +1.0 and rejects ambiguous frames with FRAME_AMBIGUOUS.
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.GProp import GProp_GProps
from OCC.Core.GeomAbs import GeomAbs_Plane
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_EDGE
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopoDS import TopoDS_Face, TopoDS_Shape, topods
from OCC.Core.gp import gp_Trsf, gp_Mat, gp_Vec, gp_Pnt, gp_Ax3, gp_Dir

from smbs_core.protocols.file_store import AlignmentStatus


@dataclass
class FrameAlignmentResult:
    status: AlignmentStatus
    det_R: float = 1.0
    normal_dot: float = 1.0
    tangent_dot: float = 1.0
    transform: Optional[gp_Trsf] = None
    matrix_R: Optional[List[List[float]]] = None
    translation_t: Optional[List[float]] = None
    error_message: Optional[str] = None


class FrameAligner:
    """
    Constructs full 3D rigid transform between reference faces.
    Resolves ambiguity by pairing normal + consistent tangent vector (e.g. adjacent bend axis or edge).
    """

    @staticmethod
    def _normalize(v: Tuple[float, float, float]) -> Tuple[float, float, float]:
        mag = math.sqrt(v[0]**2 + v[1]**2 + v[2]**2)
        if mag < 1e-9:
            raise ValueError(f"Zero vector cannot be normalized: {v}")
        return (v[0] / mag, v[1] / mag, v[2] / mag)

    @staticmethod
    def _cross(v1: Tuple[float, float, float], v2: Tuple[float, float, float]) -> Tuple[float, float, float]:
        return (
            v1[1]*v2[2] - v1[2]*v2[1],
            v1[2]*v2[0] - v1[0]*v2[2],
            v1[0]*v2[1] - v1[1]*v2[0]
        )

    @staticmethod
    def _dot(v1: Tuple[float, float, float], v2: Tuple[float, float, float]) -> float:
        return v1[0]*v2[0] + v1[1]*v2[1] + v1[2]*v2[2]

    @staticmethod
    def get_face_properties(face: TopoDS_Face) -> Tuple[Tuple[float, float, float], Tuple[float, float, float], float]:
        """Returns (normal, centroid, area) for a planar face."""
        surf = BRepAdaptor_Surface(face, True)
        if surf.GetType() != GeomAbs_Plane:
            raise ValueError("Face is not planar.")

        pln = surf.Plane()
        ax = pln.Axis()
        d = ax.Direction()
        normal = (d.X(), d.Y(), d.Z())

        props = GProp_GProps()
        brepgprop.SurfaceProperties(face, props)
        pnt = props.CentreOfMass()
        centroid = (pnt.X(), pnt.Y(), pnt.Z())
        area = props.Mass()

        return normal, centroid, area

    @staticmethod
    def extract_face_tangent(face: TopoDS_Face, normal: Tuple[float, float, float]) -> Optional[Tuple[float, float, float]]:
        """
        Finds the primary tangent direction on a planar face from its longest straight edge.
        Projects edge direction to be strictly perpendicular to normal.
        """
        from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
        from OCC.Core.GeomAbs import GeomAbs_Line

        longest_len = -1.0
        best_dir: Optional[Tuple[float, float, float]] = None

        exp = TopExp_Explorer(face, TopAbs_EDGE)
        while exp.More():
            edge = topods.Edge(exp.Current())
            curve = BRepAdaptor_Curve(edge)
            if curve.GetType() == GeomAbs_Line:
                p1 = curve.Value(curve.FirstParameter())
                p2 = curve.Value(curve.LastParameter())
                dx = p2.X() - p1.X()
                dy = p2.Y() - p1.Y()
                dz = p2.Z() - p1.Z()
                length = math.sqrt(dx*dx + dy*dy + dz*dz)
                if length > longest_len and length > 1e-4:
                    longest_len = length
                    raw_dir = (dx / length, dy / length, dz / length)
                    # Project onto plane perpendicular to normal: v_proj = v - (v . n) * n
                    dot_vn = FrameAligner._dot(raw_dir, normal)
                    proj = (
                        raw_dir[0] - dot_vn * normal[0],
                        raw_dir[1] - dot_vn * normal[1],
                        raw_dir[2] - dot_vn * normal[2]
                    )
                    mag_proj = math.sqrt(proj[0]**2 + proj[1]**2 + proj[2]**2)
                    if mag_proj > 1e-6:
                        best_dir = (proj[0] / mag_proj, proj[1] / mag_proj, proj[2] / mag_proj)
            exp.Next()

        return best_dir

    @classmethod
    def align_full_frame(
        cls,
        face_source: TopoDS_Face,
        face_target: TopoDS_Face,
        preferred_source_tangent: Optional[Tuple[float, float, float]] = None,
        preferred_target_tangent: Optional[Tuple[float, float, float]] = None,
    ) -> FrameAlignmentResult:
        """
        Calculates exact orthonormal transformation T = (R, t) mapping face_source to face_target.
        Enforces right-handed frame (det R = +1.0) and validates geometric invariants.
        """
        try:
            n_src, c_src, area_src = cls.get_face_properties(face_source)
            n_tgt, c_tgt, area_tgt = cls.get_face_properties(face_target)
        except Exception as e:
            return FrameAlignmentResult(
                status=AlignmentStatus.CANNOT_VERIFY,
                error_message=f"Failed to extract face properties: {e}"
            )

        # 1. Normal vectors
        n_src = cls._normalize(n_src)
        n_tgt = cls._normalize(n_tgt)

        # 2. Tangent vectors
        t_src = preferred_source_tangent or cls.extract_face_tangent(face_source, n_src)
        t_tgt = preferred_target_tangent or cls.extract_face_tangent(face_target, n_tgt)

        if not t_src or not t_tgt:
            return FrameAlignmentResult(
                status=AlignmentStatus.FRAME_AMBIGUOUS,
                error_message="Cannot extract unambiguous tangent directions for full frame alignment."
            )

        t_src = cls._normalize(t_src)
        t_tgt = cls._normalize(t_tgt)

        # 3. Bitangent vectors: b = n x t
        b_src = cls._normalize(cls._cross(n_src, t_src))
        b_tgt = cls._normalize(cls._cross(n_tgt, t_tgt))

        # Re-orthogonalize t to ensure perfect orthonormal right-handed frame: t = b x n
        t_src = cls._cross(b_src, n_src)
        t_tgt = cls._cross(b_tgt, n_tgt)

        # Frames: F = [t, b, n]
        # Rotation matrix R = F_tgt * F_src^T
        # F_src column vectors: (t_src, b_src, n_src)
        # In component form:
        # R = [t_tgt, b_tgt, n_tgt] * [t_src, b_src, n_src]^T
        R = [[0.0]*3 for _ in range(3)]
        for i in range(3):
            for j in range(3):
                R[i][j] = (
                    t_tgt[i] * t_src[j] +
                    b_tgt[i] * b_src[j] +
                    n_tgt[i] * n_src[j]
                )

        # Check det(R)
        det_R = (
            R[0][0] * (R[1][1]*R[2][2] - R[1][2]*R[2][1]) -
            R[0][1] * (R[1][0]*R[2][2] - R[1][2]*R[2][0]) +
            R[0][2] * (R[1][0]*R[2][1] - R[1][1]*R[2][0])
        )

        if abs(det_R - 1.0) > 1e-4:
            return FrameAlignmentResult(
                status=AlignmentStatus.FRAME_AMBIGUOUS,
                det_R=det_R,
                error_message=f"Rotation matrix is not a pure rotation: det(R) = {det_R:.6f} != +1.0"
            )

        # Translation: t = C_tgt - R * C_src
        RC_src = (
            R[0][0]*c_src[0] + R[0][1]*c_src[1] + R[0][2]*c_src[2],
            R[1][0]*c_src[0] + R[1][1]*c_src[1] + R[1][2]*c_src[2],
            R[2][0]*c_src[0] + R[2][1]*c_src[1] + R[2][2]*c_src[2]
        )
        trans_t = [c_tgt[0] - RC_src[0], c_tgt[1] - RC_src[1], c_tgt[2] - RC_src[2]]

        # Construct OCC gp_Trsf
        occ_mat = gp_Mat(
            R[0][0], R[0][1], R[0][2],
            R[1][0], R[1][1], R[1][2],
            R[2][0], R[2][1], R[2][2]
        )
        occ_vec = gp_Vec(trans_t[0], trans_t[1], trans_t[2])
        trsf = gp_Trsf()
        trsf.SetTransformation(gp_Ax3(), gp_Ax3())  # Reset identity
        trsf.SetValues(
            R[0][0], R[0][1], R[0][2], trans_t[0],
            R[1][0], R[1][1], R[1][2], trans_t[1],
            R[2][0], R[2][1], R[2][2], trans_t[2]
        )

        # Check normal and tangent alignment dots
        R_n_src = (
            R[0][0]*n_src[0] + R[0][1]*n_src[1] + R[0][2]*n_src[2],
            R[1][0]*n_src[0] + R[1][1]*n_src[1] + R[1][2]*n_src[2],
            R[2][0]*n_src[0] + R[2][1]*n_src[1] + R[2][2]*n_src[2]
        )
        normal_dot = cls._dot(R_n_src, n_tgt)

        R_t_src = (
            R[0][0]*t_src[0] + R[0][1]*t_src[1] + R[0][2]*t_src[2],
            R[1][0]*t_src[0] + R[1][1]*t_src[1] + R[1][2]*t_src[2],
            R[2][0]*t_src[0] + R[2][1]*t_src[1] + R[2][2]*t_src[2]
        )
        tangent_dot = cls._dot(R_t_src, t_tgt)

        return FrameAlignmentResult(
            status=AlignmentStatus.OK,
            det_R=det_R,
            normal_dot=normal_dot,
            tangent_dot=tangent_dot,
            transform=trsf,
            matrix_R=R,
            translation_t=trans_t,
        )
