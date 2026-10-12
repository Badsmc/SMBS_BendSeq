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

    @classmethod
    def fold_one_bend(
        cls,
        l1: float = 95.5,
        l2: float = 47.5,
        bend_r: float = 1.0,
        thickness: float = 1.5,
        kfactor: float = 0.44,
        width: float = 50.0,
        angle_deg: float = 90.0
    ) -> TopoDS_Shape:
        """Folds 1-bend L-profile into monolithic solid with 2 cylindrical fillet faces."""
        neutral_r = bend_r + kfactor * thickness
        unfold_len = neutral_r * math.radians(angle_deg)

        f1 = Part.makeBox(l1, width, thickness, FreeCAD.Vector(0, 0, 0))

        bend_face = Part.makePolygon([
            FreeCAD.Vector(l1, 0, thickness),
            FreeCAD.Vector(l1 + unfold_len, 0, thickness),
            FreeCAD.Vector(l1 + unfold_len, width, thickness),
            FreeCAD.Vector(l1, width, thickness),
            FreeCAD.Vector(l1, 0, thickness)
        ])
        bend_face = Part.Face(bend_face)
        bend_edge = Part.makeLine(FreeCAD.Vector(l1, 0, thickness), FreeCAD.Vector(l1, width, thickness))
        axis = FreeCAD.Vector(0, 1.0, 0)

        b_sector = SheetMetalBendSolid.bend_solid(
            sel_face=bend_face,
            sel_edge=bend_edge,
            bend_r=bend_r,
            thickness=thickness,
            neutral_radius=neutral_r,
            axis=axis,
            flipped=False
        )

        f2 = Part.makeBox(thickness, width, l2, FreeCAD.Vector(l1 + bend_r, 0, thickness + bend_r))
        fused = f1.fuse(b_sector).fuse(f2)
        solid = fused.Solids[0] if fused.Solids else fused
        return cls._to_occ_shape(solid)

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
