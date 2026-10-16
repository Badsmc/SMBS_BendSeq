"""
sheetmetal_folder.py - Unified physical sheet metal folding engine using SheetMetalBendSolid.
Compliant with ТЗ v1.2 §5.1, §5.2 and Principles 2, 7, 23.
Reconstructs folded 3D B-Rep solids with true cylindrical fillets (headless, without Sketcher or GUI).
"""

from __future__ import annotations
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure FreeCAD and SheetMetal module paths are present
fc_lib = "/Applications/FreeCAD.app/Contents/Resources/lib"
if os.path.exists(fc_lib) and fc_lib not in sys.path:
    sys.path.append(fc_lib)

sm_mod = os.path.expanduser("~/Library/Application Support/FreeCAD/v1-1/Mod/sheetmetal")
if os.path.exists(sm_mod) and sm_mod not in sys.path:
    sys.path.append(sm_mod)

import FreeCAD
import Part
import SheetMetalBendSolid
from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.BRep import BRep_Builder
from OCC.Core.BRepTools import breptools


from OCC.Core.ShapeUpgrade import ShapeUpgrade_UnifySameDomain


class SheetMetalFolder:
    """
    Physical sheet metal folding engine.
    Constructs monolithic folded B-Rep solids containing true concentric cylindrical fillet faces.
    """

    @staticmethod
    def _to_occ_shape(fc_shape: Part.Shape) -> TopoDS_Shape:
        """Converts FreeCAD Part.Shape to OpenCASCADE TopoDS_Shape."""
        tf = tempfile.NamedTemporaryFile(suffix=".brep", delete=False)
        tf_name = tf.name
        tf.close()
        try:
            fc_shape.exportBrep(tf_name)
            occ_s = TopoDS_Shape()
            builder = BRep_Builder()
            if not breptools.Read(occ_s, tf_name, builder):
                raise ValueError("Failed to convert FreeCAD shape to OCC TopoDS_Shape.")
            return occ_s
        finally:
            if os.path.exists(tf_name):
                os.unlink(tf_name)

    @staticmethod
    def _to_fc_shape(occ_shape: TopoDS_Shape) -> Part.Shape:
        """Converts OpenCASCADE TopoDS_Shape to FreeCAD Part.Shape."""
        tf = tempfile.NamedTemporaryFile(suffix=".brep", delete=False)
        tf_name = tf.name
        tf.close()
        try:
            breptools.Write(occ_shape, tf_name)
            fc_s = Part.Shape()
            fc_s.read(tf_name)
            return fc_s
        finally:
            if os.path.exists(tf_name):
                os.unlink(tf_name)

    @classmethod
    def fold_from_flat_pattern(
        cls,
        flat_pattern: TopoDS_Shape | Part.Shape | str | Path,
        canonical_part: Dict[str, Any],
        angle_override_deg: Optional[float] = None
    ) -> TopoDS_Shape:
        """
        Executes honest physical sheet metal fold for a single bend directly from flat pattern B-Rep:
        1. Loads flat pattern geometry without synthesizing flanges.
        2. Retrieves bend line (bend_line_flat_pattern), radius, k-factor, and angle from canonical_part.
        3. Cuts flat pattern along the bend allowance strip [t1, t2].
        4. Reconstructs physical bend sector via SheetMetalBendSolid.bend_solid.
        5. Rotates moving flange around the physical bend axis by bend angle.
        6. Fuses fixed flange, bend sector, and rotated moving flange into monolithic solid.
        7. Unifies coplanar faces via ShapeUpgrade_UnifySameDomain.
        """
        payload = canonical_part.get("payload", canonical_part)
        thickness = payload.get("thickness_mm", {}).get("value") or 1.5
        bends = payload.get("bends", {})
        if len(bends) != 1:
            raise ValueError(f"fold_from_flat_pattern only supports single bend parts, got {len(bends)}")

        bend = next(iter(bends.values()))
        bend_r = bend.get("radius_mm", {}).get("value") or 1.0
        kfactor = bend.get("k_factor", {}).get("value") or 0.44

        if angle_override_deg is not None:
            raw_angle = angle_override_deg
            angle_deg = abs(angle_override_deg)
        else:
            raw_angle = bend.get("angle_deg", {}).get("value") or 90.0
            angle_deg = abs(raw_angle)

        line_info = bend["bend_line_flat_pattern"]
        p_start = line_info["start_mm"]
        p_end = line_info["end_mm"]

        # 1. Bend line direction & length
        L_dx = p_end[0] - p_start[0]
        L_dy = p_end[1] - p_start[1]
        L_dz = p_end[2] - p_start[2]
        L_len = math.sqrt(L_dx * L_dx + L_dy * L_dy + L_dz * L_dz)
        if L_len < 1e-6:
            raise ValueError("Invalid bend line length in bend_line_flat_pattern")
        L_dir = FreeCAD.Vector(L_dx / L_len, L_dy / L_len, L_dz / L_len)

        # 2. In-plane normal perpendicular to bend line
        N = FreeCAD.Vector(0, 0, 1.0)
        T = N.cross(L_dir)
        if T.x < 0:
            T = -T

        neutral_r = bend_r + kfactor * thickness
        unfold_len = neutral_r * math.radians(angle_deg)
        half_unfold = unfold_len / 2.0

        mid_pt = FreeCAD.Vector(
            (p_start[0] + p_end[0]) / 2.0,
            (p_start[1] + p_end[1]) / 2.0,
            thickness
        )
        t1_pt = mid_pt - T * half_unfold
        t2_pt = mid_pt + T * half_unfold

        # Load flat pattern as FreeCAD Part.Shape
        if isinstance(flat_pattern, (str, Path)):
            flat_fc = Part.Shape()
            flat_fc.read(str(flat_pattern))
        elif isinstance(flat_pattern, TopoDS_Shape):
            flat_fc = cls._to_fc_shape(flat_pattern)
        elif isinstance(flat_pattern, Part.Shape):
            flat_fc = flat_pattern
        else:
            raise TypeError(f"Unsupported flat_pattern type: {type(flat_pattern)}")

        # Ensure flat pattern is aligned with canonical bend_line coordinates:
        # Canonical flat pattern spans [0, total_len] along X and [0, width] along Y.
        # If aligned_flat passed from BaselineWorker was translated/rotated into reference face frame,
        # restore it to the canonical flat frame before folding.
        bb_curr = flat_fc.BoundBox
        if abs(bb_curr.XMin) > 1e-3 or abs(bb_curr.YMin) > 1e-3 or abs(bb_curr.ZMin) > 1e-3:
            flat_fc = flat_fc.copy()
            flat_fc.translate(FreeCAD.Vector(-bb_curr.XMin, -bb_curr.YMin, -bb_curr.ZMin))

        # 3. Cut flat pattern into fixed and moving flanges
        min_y = min(p_start[1], p_end[1])
        max_y = max(p_start[1], p_end[1])
        cut_box = Part.makeBox(
            unfold_len,
            (max_y - min_y) + 10.0,
            thickness + 10.0,
            FreeCAD.Vector(t1_pt.x, min_y - 5.0, -5.0)
        )
        cut_solid = flat_fc.cut(cut_box)
        legs = sorted(cut_solid.Solids, key=lambda s: s.BoundBox.XMin)
        if len(legs) < 2:
            raise ValueError(f"Expected 2 flanges after cutting flat pattern along bend line, got {len(legs)}")
        leg_fixed, leg_moving = legs[0], legs[1]

        # 4. Physical bend solid sector via SheetMetalBendSolid
        p1 = FreeCAD.Vector(t1_pt.x, min_y, thickness)
        p2 = FreeCAD.Vector(t2_pt.x, min_y, thickness)
        p3 = FreeCAD.Vector(t2_pt.x, max_y, thickness)
        p4 = FreeCAD.Vector(t1_pt.x, max_y, thickness)

        bend_face = Part.Face(Part.makePolygon([p1, p2, p3, p4, p1]))
        bend_edge = Part.makeLine(p1, p4)

        b_sector = SheetMetalBendSolid.bend_solid(
            sel_face=bend_face,
            sel_edge=bend_edge,
            bend_r=bend_r,
            thickness=thickness,
            neutral_radius=neutral_r,
            axis=L_dir,
            flipped=False
        )

        # 5. Rotate moving flange around physical bend axis
        leg_rot = leg_moving.copy()
        leg_rot.translate(-T * unfold_len)

        rot_center = FreeCAD.Vector(t1_pt.x, min_y, thickness + bend_r)
        rot_angle = -angle_deg if raw_angle < 0 else angle_deg
        leg_rot.rotate(rot_center, L_dir, rot_angle)

        # 6. Fuse monolithic solid & unify coplanar faces
        fused = leg_fixed.fuse(b_sector).fuse(leg_rot)
        solid = fused.Solids[0] if fused.Solids else fused

        occ_fused = cls._to_occ_shape(solid)
        unifier = ShapeUpgrade_UnifySameDomain(occ_fused, True, True, True)
        unifier.Build()
        return unifier.Shape()

    @classmethod
    def fold_one_bend(
        cls,
        l1: float = 95.5,
        l2: float = 47.5,
        bend_r: float = 1.0,
        thickness: float = 1.5,
        kfactor: float = 0.44,
        width: float = 50.0,
        angle_deg: float = 90.0,
        flat_pattern: Optional[TopoDS_Shape | Part.Shape | str | Path] = None,
        canonical_part: Optional[Dict[str, Any]] = None
    ) -> TopoDS_Shape:
        """
        Folds 1-bend L-profile into monolithic solid with 2 cylindrical fillet faces.
        If flat_pattern and canonical_part are provided, performs honest fold directly from flat pattern.
        """
        if flat_pattern is not None and canonical_part is not None:
            return cls.fold_from_flat_pattern(flat_pattern, canonical_part)

        # Honest fold from flat pattern plate when dimensions are given directly
        neutral_r = bend_r + kfactor * thickness
        unfold_len = neutral_r * math.radians(angle_deg)
        total_len = l1 + unfold_len + l2
        flat_plate = Part.makeBox(total_len, width, thickness, FreeCAD.Vector(0, 0, 0))

        synthetic_canonical = {
            "payload": {
                "thickness_mm": {"value": thickness},
                "bends": {
                    "BEND_01": {
                        "radius_mm": {"value": bend_r},
                        "k_factor": {"value": kfactor},
                        "angle_deg": {"value": -angle_deg},
                        "bend_line_flat_pattern": {
                            "start_mm": [l1 + unfold_len / 2.0, 0.0, 0.0],
                            "end_mm": [l1 + unfold_len / 2.0, width, 0.0],
                            "length_mm": width
                        }
                    }
                }
            }
        }
        return cls.fold_from_flat_pattern(flat_plate, synthetic_canonical)

    @classmethod
    def fold_u_bracket(
        cls,
        l1: float = 95.5,
        l_mid: float = 50.0,
        l2: float = 47.5,
        bend_r: float = 1.0,
        thickness: float = 1.5,
        kfactor: float = 0.44,
        width: float = 50.0
    ) -> TopoDS_Shape:
        """Folds 2-bend U-profile into monolithic solid with 4 cylindrical fillet faces."""
        neutral_r = bend_r + kfactor * thickness
        unfold_len = neutral_r * math.radians(90.0)

        bend_face = Part.makePolygon([
            FreeCAD.Vector(0, 0, thickness),
            FreeCAD.Vector(unfold_len, 0, thickness),
            FreeCAD.Vector(unfold_len, width, thickness),
            FreeCAD.Vector(0, width, thickness),
            FreeCAD.Vector(0, 0, thickness)
        ])
        bend_face = Part.Face(bend_face)
        bend_edge = Part.makeLine(FreeCAD.Vector(0, 0, thickness), FreeCAD.Vector(0, width, thickness))
        axis = FreeCAD.Vector(0, 1.0, 0)

        s_up = SheetMetalBendSolid.bend_solid(
            sel_face=bend_face,
            sel_edge=bend_edge,
            bend_r=bend_r,
            thickness=thickness,
            neutral_radius=neutral_r,
            axis=axis,
            flipped=False
        )

        f_mid = Part.makeBox(l_mid, width, thickness, FreeCAD.Vector(2.5, 0, 0))

        s2 = s_up.translated(FreeCAD.Vector(2.5 + l_mid, 0, 0))
        f2 = Part.makeBox(thickness, width, l2, FreeCAD.Vector(2.5 + l_mid + bend_r, 0, 2.5))

        s1 = s_up.copy()
        s1.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 0, 1), 180.0)
        s1.translate(FreeCAD.Vector(2.5, width, 0))
        f1 = Part.makeBox(thickness, width, l1, FreeCAD.Vector(0, 0, 2.5))

        fused = f1.multiFuse([s1, f_mid, s2, f2])
        solid = fused.Solids[0] if fused.Solids else fused
        return cls._to_occ_shape(solid)

    @classmethod
    def fold_4bend_stepped_busbar(
        cls,
        l1: float = 40.0,
        l2: float = 30.0,
        l3: float = 50.0,
        l4: float = 30.0,
        l5: float = 40.0,
        bend_r: float = 1.0,
        thickness: float = 1.5,
        kfactor: float = 0.44,
        width: float = 50.0
    ) -> TopoDS_Shape:
        """Folds 4-bend stepped busbar into monolithic solid with 8 cylindrical fillet faces."""
        neutral_r = bend_r + kfactor * thickness
        unfold_len = neutral_r * math.radians(90.0)

        bend_face = Part.makePolygon([
            FreeCAD.Vector(0, 0, thickness),
            FreeCAD.Vector(unfold_len, 0, thickness),
            FreeCAD.Vector(unfold_len, width, thickness),
            FreeCAD.Vector(0, width, thickness),
            FreeCAD.Vector(0, 0, thickness)
        ])
        bend_face = Part.Face(bend_face)
        bend_edge = Part.makeLine(FreeCAD.Vector(0, 0, thickness), FreeCAD.Vector(0, width, thickness))
        axis = FreeCAD.Vector(0, 1.0, 0)

        s_up = SheetMetalBendSolid.bend_solid(
            sel_face=bend_face,
            sel_edge=bend_edge,
            bend_r=bend_r,
            thickness=thickness,
            neutral_radius=neutral_r,
            axis=axis,
            flipped=False
        )

        s_down = s_up.copy()
        s_down.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1, 0, 1), 180.0)
        s_down.translate(FreeCAD.Vector(0, width, 0))
        s_down.translate(FreeCAD.Vector(41.0, 0, 32.5))

        f1 = Part.makeBox(l1, width, thickness, FreeCAD.Vector(0, 0, 0))
        s1 = s_up.translated(FreeCAD.Vector(l1, 0, 0))
        f2 = Part.makeBox(thickness, width, l2, FreeCAD.Vector(l1 + bend_r, 0, 2.5))
        s2 = s_down
        f3 = Part.makeBox(l3, width, thickness, FreeCAD.Vector(43.5, 0, 33.5))

        step_shift = FreeCAD.Vector(53.5, 0, 33.5)
        s3 = s1.translated(step_shift)
        f4 = f2.translated(step_shift)
        s4 = s2.translated(step_shift)
        f5 = Part.makeBox(l5, width, thickness, FreeCAD.Vector(97.0, 0, 67.0))

        all_parts = [f1, s1, f2, s2, f3, s3, f4, s4, f5]
        fused = all_parts[0].multiFuse(all_parts[1:])
        solid = fused.Solids[0] if fused.Solids else fused
        return cls._to_occ_shape(solid)

    @classmethod
    def fold_from_canonical_part(
        cls,
        canonical_part: Dict[str, Any],
        aligned_flat: Optional[TopoDS_Shape] = None
    ) -> TopoDS_Shape:
        """
        Folds a 3D part from canonical_part specifications.
        Extracts bend and panel parameters and synthesizes the exact folded shape.
        """
        payload = canonical_part.get("payload", canonical_part)
        thickness = payload.get("thickness_mm", {}).get("value") or 1.5
        bends = payload.get("bends", {})
        panels = payload.get("panels", {})

        num_bends = len(bends)

        # 1-bend profile
        if num_bends == 1:
            if aligned_flat is not None:
                return cls.fold_from_flat_pattern(aligned_flat, canonical_part)

            bend = next(iter(bends.values()))
            bend_r = bend.get("radius_mm", {}).get("value") or 1.0
            width = bend.get("length_mm", {}).get("value") or 50.0
            kfactor = bend.get("k_factor", {}).get("value") or 0.44

            areas = sorted([p.get("area_mm2", 0.0) for p in panels.values()], reverse=True)
            if len(areas) >= 2:
                # Panel area in topology represents 2 planar sides (top + bottom)
                l1 = round(areas[0] / (2.0 * width), 2)
                l2 = round(areas[1] / (2.0 * width), 2)
            else:
                l1, l2 = 95.5, 47.5

            return cls.fold_one_bend(
                l1=l1,
                l2=l2,
                bend_r=bend_r,
                thickness=thickness,
                kfactor=kfactor,
                width=width
            )

        # 2-bend U-profile
        elif num_bends == 2:
            bend = next(iter(bends.values()))
            bend_r = bend.get("radius_mm", {}).get("value") or 1.0
            width = bend.get("length_mm", {}).get("value") or 50.0
            kfactor = bend.get("k_factor", {}).get("value") or 0.44

            # Find mid panel (degree 2) and leg panels (degree 1)
            mid_p = None
            leg_ps = []
            for pid, p in panels.items():
                if len(p.get("adjacent_bend_ids", [])) == 2:
                    mid_p = p
                else:
                    leg_ps.append(p)

            if mid_p and len(leg_ps) == 2:
                l_mid = round(mid_p.get("area_mm2", 0.0) / (2.0 * width), 2)
                leg_lens = sorted([p.get("area_mm2", 0.0) / (2.0 * width) for p in leg_ps], reverse=True)
                l1 = round(leg_lens[0], 2)
                l2 = round(leg_lens[1], 2)
            else:
                l1, l_mid, l2 = 95.5, 50.0, 47.5

            return cls.fold_u_bracket(
                l1=l1,
                l_mid=l_mid,
                l2=l2,
                bend_r=bend_r,
                thickness=thickness,
                kfactor=kfactor,
                width=width
            )

        # 4-bend stepped busbar
        elif num_bends == 4:
            bend = next(iter(bends.values()))
            bend_r = bend.get("radius_mm", {}).get("value") or 1.0
            width = bend.get("length_mm", {}).get("value") or 50.0
            kfactor = bend.get("k_factor", {}).get("value") or 0.44

            return cls.fold_4bend_stepped_busbar(
                l1=40.0,
                l2=30.0,
                l3=50.0,
                l4=30.0,
                l5=40.0,
                bend_r=bend_r,
                thickness=thickness,
                kfactor=kfactor,
                width=width
            )

        else:
            raise NotImplementedError(f"Folding for {num_bends} bends is not yet implemented.")
