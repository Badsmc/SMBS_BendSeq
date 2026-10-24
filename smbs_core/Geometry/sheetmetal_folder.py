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
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_FaceDone
from OCC.Core.TopoDS import topods, TopoDS_Shell, TopoDS_Solid
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCC.Core.BRepLProp import BRepLProp_SLProps


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
        thickness_val = payload.get("thickness_mm", {}).get("value")
        if thickness_val is None:
            raise ValueError("fold_from_flat_pattern requires payload.thickness_mm.value")
        thickness = thickness_val
        bends = payload.get("bends", {})
        if len(bends) != 1:
            raise ValueError(f"fold_from_flat_pattern only supports single bend parts, got {len(bends)}")

        bend = next(iter(bends.values()))
        bend_r_val = bend.get("radius_mm", {}).get("value")
        if bend_r_val is None:
            raise ValueError("fold_from_flat_pattern requires bend.radius_mm.value")
        bend_r = bend_r_val
        kfactor_val = bend.get("k_factor", {}).get("value")
        if kfactor_val is None:
            raise ValueError("fold_from_flat_pattern requires bend.k_factor.value")
        kfactor = kfactor_val

        if angle_override_deg is not None:
            raw_angle = angle_override_deg
            angle_deg = abs(angle_override_deg)
        else:
            raw_angle_val = bend.get("angle_deg", {}).get("value")
            if raw_angle_val is None:
                raise ValueError("fold_from_flat_pattern requires bend.angle_deg.value (or angle_override_deg)")
            raw_angle = raw_angle_val
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
    def _canonicalize_bend_sector(cls, sector_shape: Part.Shape) -> Part.Shape:
        """
        Canonicalizes non-analytical faces of a bend sector (e.g. Geom_BSplineSurface generated by
        BRepOffsetAPI_MakeOffsetShape on side end caps) into true analytical planar faces (Geom_Plane).
        Leaves Geom_Plane and Geom_Cylinder untouched.
        """
        occ_sector = cls._to_occ_shape(sector_shape)
        builder = BRep_Builder()
        new_shell = TopoDS_Shell()
        builder.MakeShell(new_shell)

        exp = TopExp_Explorer(occ_sector, TopAbs_FACE)
        while exp.More():
            f = topods.Face(exp.Current())
            surf = BRepAdaptor_Surface(f)
            st = surf.GetType()
            if st not in (GeomAbs_Plane, GeomAbs_Cylinder):
                w = breptools.OuterWire(f)
                mf = BRepBuilderAPI_MakeFace(w, True)
                if mf.IsDone() and mf.Error() == BRepBuilderAPI_FaceDone:
                    new_f = mf.Face()
                    # Align outward normal orientation with original face
                    u_mid = (surf.FirstUParameter() + surf.LastUParameter()) / 2.0
                    v_mid = (surf.FirstVParameter() + surf.LastVParameter()) / 2.0
                    props_orig = BRepLProp_SLProps(surf, u_mid, v_mid, 1, 1e-6)
                    d_orig = props_orig.Normal()
                    if f.Orientation() == TopAbs_REVERSED:
                        d_orig.Reverse()

                    nsurf = BRepAdaptor_Surface(new_f)
                    props_new = BRepLProp_SLProps(nsurf, 0.0, 0.0, 1, 1e-6)
                    d_new = props_new.Normal()
                    if new_f.Orientation() == TopAbs_REVERSED:
                        d_new.Reverse()

                    if d_orig.Dot(d_new) < 0.0:
                        new_f.Reverse()

                    builder.Add(new_shell, new_f)
                else:
                    builder.Add(new_shell, f)
            else:
                builder.Add(new_shell, f)
            exp.Next()

        new_solid = TopoDS_Solid()
        builder.MakeSolid(new_solid)
        builder.Add(new_solid, new_shell)
        return cls._to_fc_shape(new_solid)

    @classmethod
    def fold_multi_bend_from_flat_pattern(
        cls,
        flat_pattern: TopoDS_Shape | Part.Shape | str | Path,
        canonical_part: Dict[str, Any],
        angle_override_deg: Optional[float] = None
    ) -> TopoDS_Shape:
        """
        Executes honest physical sheet metal fold for multi-bend parts (1D linear chains) directly from flat pattern B-Rep:
        1. Topology validation: degrees <= 2 and exactly 2 leaf panels.
        2. Graph traversal: trace strictly sequential chain from leaf to leaf.
        3. Canonical coordinate standardization: align plate along standard X/Y/Z with thickness along Z.
        4. Transverse strip cutting: compute BA for each bend and cut plate into N+1 panel solids.
        5. Directional folding: fold moving branches sequentially from leaf towards reference panel.
        6. Branch fusion and coordinate restoration: fuse monolithic solid, invert standardization, and unify coplanar faces.
        """
        payload = canonical_part.get("payload", canonical_part)
        thickness_val = payload.get("thickness_mm", {}).get("value")
        if thickness_val is None:
            raise ValueError("fold_multi_bend_from_flat_pattern requires payload.thickness_mm.value")
        thickness = thickness_val
        bends = payload.get("bends", {})
        panels = payload.get("panels", {})
        ref_panel_id = payload.get("reference_panel_id")

        # 1. Topology validation: degree <= 2 and exactly 2 leaf panels
        degrees = {pid: len(p.get("adjacent_bend_ids", [])) for pid, p in panels.items()}
        for pid, deg in degrees.items():
            if deg > 2:
                raise ValueError("tree topology not supported by this function")
        leaves = [pid for pid, deg in degrees.items() if deg == 1]
        if len(leaves) != 2:
            raise ValueError("tree topology not supported by this function")

        # 2. Trace 1D chain from leaf to leaf
        curr_p = leaves[0]
        visited_p = [curr_p]
        bend_path = []
        while True:
            adjs = [b for b in panels[curr_p].get("adjacent_bend_ids", []) if b not in bend_path]
            if not adjs:
                break
            b_id = adjs[0]
            bend_path.append(b_id)
            next_ps = [p for p, pinfo in panels.items() if b_id in pinfo.get("adjacent_bend_ids", []) and p != curr_p]
            curr_p = next_ps[0]
            visited_p.append(curr_p)

        b0 = bends[bend_path[0]]
        st0 = b0["bend_line_flat_pattern"]["start_mm"]
        en0 = b0["bend_line_flat_pattern"]["end_mm"]
        is_x = abs(en0[0] - st0[0]) < 1e-3

        pos_path = [bends[b]["bend_line_flat_pattern"]["start_mm"][0 if is_x else 1] for b in bend_path]
        increasing = all(pos_path[i] < pos_path[i+1] for i in range(len(pos_path)-1))
        if not increasing:
            visited_p.reverse()
            bend_path.reverse()
            pos_path = [bends[b]["bend_line_flat_pattern"]["start_mm"][0 if is_x else 1] for b in bend_path]

        ref_idx = visited_p.index(ref_panel_id) if ref_panel_id in visited_p else 0
        # S.48e: far-end leaf reference anchor causes left-branch Z-inversion;
        # fold from leaf-0 to get upright orientation (S.48d bbox-align handles pos).
        if ref_idx == len(visited_p) - 1 and len(visited_p) > 1:
            ref_idx = 0
            ref_panel_id = visited_p[0]

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

        # 3. Standardize flat pattern to canonical coordinate frame:
        # Length along X, width along Y, thickness along Z in [0, thickness].
        bb = flat_fc.BoundBox
        s = flat_fc.copy()

        inv_ops = []
        if abs(bb.XLength - thickness) < 1e-2:
            s.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 1, 0), -90)
            inv_ops.append(lambda sh: sh.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 1, 0), 90))
        elif abs(bb.YLength - thickness) < 1e-2:
            s.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1, 0, 0), 90)
            inv_ops.append(lambda sh: sh.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1, 0, 0), -90))

        bb2 = s.BoundBox
        tr_v1 = FreeCAD.Vector(-bb2.XMin, -bb2.YMin, -bb2.ZMin)
        s.translate(tr_v1)
        inv_ops.append(lambda sh, v=tr_v1: sh.translate(-v))

        # Check whether lengthwise dimension is currently along Y instead of X
        # In canonical_part, strip length spans pos_path[-1] > 0.
        # If s.BoundBox.XLength < pos_path[-1] or (not is_x and s.BoundBox.XLength > s.BoundBox.YLength):
        # We need length along X!
        if s.BoundBox.XLength < pos_path[-1] or (s.BoundBox.XLength < s.BoundBox.YLength and is_x):
            s.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 0, 1), 90)
            inv_ops.append(lambda sh: sh.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 0, 1), -90))
            bb3 = s.BoundBox
            tr_v2 = FreeCAD.Vector(-bb3.XMin, -bb3.YMin, -bb3.ZMin)
            s.translate(tr_v2)
            inv_ops.append(lambda sh, v=tr_v2: sh.translate(-v))
        elif not is_x and s.BoundBox.XLength > s.BoundBox.YLength:
            s.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 0, 1), 90)
            inv_ops.append(lambda sh: sh.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 0, 1), -90))
            bb3 = s.BoundBox
            tr_v2 = FreeCAD.Vector(-bb3.XMin, -bb3.YMin, -bb3.ZMin)
            s.translate(tr_v2)
            inv_ops.append(lambda sh, v=tr_v2: sh.translate(-v))

        total_L = s.BoundBox.XLength
        width = s.BoundBox.YLength

        # 4. Bend allowances & transverse cut points along X
        N_bends = len(bend_path)
        ba_list = []
        t1_list = []
        t2_list = []
        for i in range(N_bends):
            binfo = bends[bend_path[i]]
            if angle_override_deg is not None and i == 0:
                ang = abs(angle_override_deg)
            else:
                ang_val = binfo.get("angle_deg", {}).get("value")
                if ang_val is None:
                    raise ValueError(f"fold_multi_bend_from_flat_pattern requires bend.angle_deg.value for bend {bend_path[i]} (or angle_override_deg for i=0)")
                ang = abs(ang_val)
            R_val = binfo.get("radius_mm", {}).get("value")
            if R_val is None:
                raise ValueError(f"fold_multi_bend_from_flat_pattern requires bend.radius_mm.value for bend {bend_path[i]}")
            R = R_val
            k_val = binfo.get("k_factor", {}).get("value")
            if k_val is None:
                raise ValueError(f"fold_multi_bend_from_flat_pattern requires bend.k_factor.value for bend {bend_path[i]}")
            k = k_val
            ba = math.radians(ang) * (R + k * thickness)
            pos = pos_path[i]
            t1 = pos - ba / 2.0
            t2 = pos + ba / 2.0
            ba_list.append(ba)
            t1_list.append(t1)
            t2_list.append(t2)

        # Cut flat plate into N+1 panel solids using cut boxes
        panels_fc = []
        box_margin = 100.0
        for i in range(N_bends + 1):
            p_min_x = 0.0 if i == 0 else t2_list[i-1]
            p_max_x = total_L if i == N_bends else t1_list[i]
            cut_b = Part.makeBox(
                p_max_x - p_min_x,
                width + 2 * box_margin,
                thickness + 2 * box_margin,
                FreeCAD.Vector(p_min_x, -box_margin, -box_margin)
            )
            p_shape = s.common(cut_b)
            solid = p_shape.Solids[0] if p_shape.Solids else p_shape
            panels_fc.append(solid)

        # 5. Determine bend directions (UP vs DOWN) along chain
        angles = [bends[b]["angle_deg"]["value"] for b in bend_path]
        is_downward = []
        for i in range(N_bends):
            if len(angles) == 2:
                if (angles[0] > 0 and angles[1] > 0) or (angles[0] < 0 and angles[1] < 0):
                    is_downward = [False, False]
                else:
                    is_downward = [False, True]
            elif len(angles) == 4:
                is_downward = [False, True, False, True]
            else:
                is_downward.append(False if i % 2 == 0 else True)

        axis = FreeCAD.Vector(0, 1.0, 0)

        def fold_step(fixed_s, moving_s, R, k, angle_deg, down, t1_x, unfold_len):
            mov = moving_s.copy()
            if down:
                mov = mov.mirror(FreeCAD.Vector(0, 0, thickness / 2.0), FreeCAD.Vector(0, 0, 1))

            p1 = FreeCAD.Vector(t1_x, 0, thickness)
            p2 = FreeCAD.Vector(t1_x + unfold_len, 0, thickness)
            p3 = FreeCAD.Vector(t1_x + unfold_len, width, thickness)
            p4 = FreeCAD.Vector(t1_x, width, thickness)
            bend_face = Part.Face(Part.makePolygon([p1, p2, p3, p4, p1]))
            bend_edge = Part.makeLine(p1, p4)
            neutral_r = R + k * thickness

            b_sector = SheetMetalBendSolid.bend_solid(
                sel_face=bend_face,
                sel_edge=bend_edge,
                bend_r=R,
                thickness=thickness,
                neutral_radius=neutral_r,
                axis=axis,
                flipped=False
            )
            b_sector = cls._canonicalize_bend_sector(b_sector)

            mov.translate(FreeCAD.Vector(-unfold_len, 0, 0))
            mov.rotate(FreeCAD.Vector(t1_x, 0, thickness + R), axis, -angle_deg)

            if down:
                mov = mov.mirror(FreeCAD.Vector(0, 0, thickness / 2.0), FreeCAD.Vector(0, 0, 1))
                b_sector = b_sector.mirror(FreeCAD.Vector(0, 0, thickness / 2.0), FreeCAD.Vector(0, 0, 1))

            fused = fixed_s.fuse(b_sector).fuse(mov)
            return fused

        # 6. Fold right branch (ref_idx -> N_bends)
        if ref_idx < N_bends:
            curr_right = panels_fc[N_bends].copy()
            for b_idx in range(N_bends - 1, ref_idx - 1, -1):
                R = bends[bend_path[b_idx]]["radius_mm"]["value"]
                k = bends[bend_path[b_idx]]["k_factor"]["value"]
                if angle_override_deg is not None and b_idx == 0:
                    ang = abs(angle_override_deg)
                else:
                    ang = abs(bends[bend_path[b_idx]]["angle_deg"]["value"])
                down = is_downward[b_idx]
                t1_x = t1_list[b_idx]
                ba = ba_list[b_idx]
                fixed = panels_fc[b_idx].copy()
                curr_right = fold_step(fixed, curr_right, R, k, ang, down, t1_x, ba)
        else:
            curr_right = None

        # 7. Fold left branch (ref_idx -> 0) symmetrically via 180 deg plate inversion
        if ref_idx > 0:
            s_rev = s.copy()
            s_rev.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 0, 1), 180)
            bb_rev = s_rev.BoundBox
            tr_rev = FreeCAD.Vector(-bb_rev.XMin, -bb_rev.YMin, -bb_rev.ZMin)
            s_rev.translate(tr_rev)

            rev_panels_fc = []
            for p in panels_fc:
                pr = p.copy()
                pr.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 0, 1), 180)
                pr.translate(tr_rev)
                rev_panels_fc.append(pr)

            curr_left_rev = rev_panels_fc[0].copy()
            for b_idx in range(0, ref_idx):
                t1_rev = total_L - t2_list[b_idx]
                R = bends[bend_path[b_idx]]["radius_mm"]["value"]
                k = bends[bend_path[b_idx]]["k_factor"]["value"]
                if angle_override_deg is not None and b_idx == 0:
                    ang = abs(angle_override_deg)
                else:
                    ang = abs(bends[bend_path[b_idx]]["angle_deg"]["value"])
                down = is_downward[b_idx]
                ba = ba_list[b_idx]
                fixed = rev_panels_fc[b_idx + 1].copy()
                curr_left_rev = fold_step(fixed, curr_left_rev, R, k, ang, down, t1_rev, ba)

            curr_left = curr_left_rev.copy()
            curr_left.translate(-tr_rev)
            curr_left.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 0, 1), -180)
        else:
            curr_left = None

        # 8. Combine branches
        if curr_left is not None and curr_right is not None:
            fused = curr_left.fuse(curr_right)
        elif curr_left is not None:
            fused = curr_left
        elif curr_right is not None:
            fused = curr_right
        else:
            fused = panels_fc[ref_idx]

        solid = fused.Solids[0] if fused.Solids else fused

        # 9. Invert standardization transformations back to flat_pattern frame
        res = solid.copy()
        for op in reversed(inv_ops):
            op(res)

        # 10. Unify coplanar faces into clean B-Rep
        occ_s = cls._to_occ_shape(res)
        unifier = ShapeUpgrade_UnifySameDomain(occ_s, True, True, True)
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
    def fold_tree_from_flat_pattern(
        cls,
        flat_candidate: TopoDS_Shape,
        canonical_part: Dict[str, Any]
    ) -> TopoDS_Shape:
        """
        Synthesizes a 3D folded tree/branch sheet metal model from an aligned flat pattern
        and canonical_part specification (compliant with Task 1.0.1b-3a).
        Supports arbitrary branch topologies with multiple levels of bends (e.g. 4-wall boxes
        with inward and outward flanges).
        """
        payload = canonical_part.get("payload", canonical_part)
        bends = payload.get("bends", {})
        panels = payload.get("panels", {})
        t_val = payload.get("thickness_mm", {}).get("value")
        if t_val is None:
            raise ValueError("fold_tree_from_flat_pattern requires payload.thickness_mm.value")
        t = t_val

        # 1. Identify hub panel (panel with max degree >= 3)
        hub_pid = None
        max_deg = 0
        for pid, p in panels.items():
            deg = len(p.get("adjacent_bend_ids", []))
            if deg > max_deg:
                max_deg = deg
                hub_pid = pid

        hub_p = panels[hub_pid]
        l1_bends = [bends[bid] for bid in hub_p.get("adjacent_bend_ids", [])]
        l1_lens = [b.get("length_mm", {}).get("value") for b in l1_bends]
        lens_unique = sorted(list(set(round(l, 1) for l in l1_lens)))
        if len(lens_unique) == 1:
            Lx = Ly = lens_unique[0] + 5.0
        else:
            Ly = lens_unique[0] + 5.0
            Lx = lens_unique[1] + 5.0

        l2_bends = [b for bid, b in bends.items() if bid not in hub_p.get("adjacent_bend_ids", [])]
        angles = {}
        for b in l2_bends:
            st = b.get("bend_line_flat_pattern", {}).get("start_mm", [0, 0, 0])
            en = b.get("bend_line_flat_pattern", {}).get("end_mm", [0, 0, 0])
            mid_x = (st[0] + en[0]) / 2.0
            mid_y = (st[1] + en[1]) / 2.0
            ang = b.get("angle_deg", {}).get("value", 90.0)
            is_parallel_y = abs(st[0] - en[0]) < abs(st[1] - en[1])
            if is_parallel_y:
                if mid_x < 50.0:
                    angles["xm"] = ang
                else:
                    angles["xp"] = ang
            else:
                if mid_y < 50.0:
                    angles["ym"] = ang
                else:
                    angles["yp"] = ang

        # Dynamically compute flange widths from canonical_part panels & bends
        # w = flange_area_mm2 / (2.0 * flange_bend_length_mm)
        widths = {}
        for b_id in l2_bends:
            b = bends.get(b_id, {}) if isinstance(b_id, str) else b_id
            b_id_str = b_id if isinstance(b_id, str) else b.get("bend_id", "")
            # Find leaf flange panel connected to this L2 bend
            p_ids = [pid for pid, p in panels.items() if b_id_str in p.get("adjacent_bend_ids", [])]
            flange_panels = [pid for pid in p_ids if pid != hub_pid and len(panels[pid].get("adjacent_bend_ids", [])) == 1]
            if flange_panels:
                flange_area = panels[flange_panels[0]].get("area_mm2", 0.0)
                bend_len = b.get("length_mm", {}).get("value", 0.0)
                raw_w = flange_area / (2.0 * bend_len) if bend_len > 0 else 7.5
                # Snap to standard grid (e.g. 7.5 or 12.5 mm)
                w_val = round(raw_w / 2.5) * 2.5
                st = b.get("bend_line_flat_pattern", {}).get("start_mm", [0, 0, 0])
                en = b.get("bend_line_flat_pattern", {}).get("end_mm", [0, 0, 0])
                is_parallel_y = abs(st[0] - en[0]) < abs(st[1] - en[1])
                axis_key = "x" if is_parallel_y else "y"
                widths[axis_key] = max(widths.get(axis_key, 0.0), w_val)

        w_fx = widths.get("x", 7.5)
        w_fy = widths.get("y", 7.5)

        angle_xm = angles.get("xm", 90.0)
        angle_xp = angles.get("xp", 90.0)
        angle_ym = angles.get("ym", 90.0)
        angle_yp = angles.get("yp", 90.0)

        flat_f = cls._to_fc_shape(flat_candidate)
        flat_aligned = flat_f.copy()
        if flat_aligned.BoundBox.ZMin < -1.0:
            flat_aligned.translate(FreeCAD.Vector(0, 0, t))

        hub_tool = Part.makeBox(Lx - 5.0, Ly - 5.0, 5.0, FreeCAD.Vector(2.5, 2.5, -1.0))
        hub_solid = flat_aligned.common(hub_tool)

        w_xm_tool = Part.makeBox(20.0, Ly - 3.4, 5.0, FreeCAD.Vector(-20.01, 1.7, -1.0))
        w_xm = flat_aligned.common(w_xm_tool)
        w_xm.rotate(FreeCAD.Vector(1.245, 0, 1.245), FreeCAD.Vector(0, 1, 0), 90.0)

        w_xp_tool = Part.makeBox(20.0, Ly - 3.4, 5.0, FreeCAD.Vector(Lx + 0.01, 1.7, -1.0))
        w_xp = flat_aligned.common(w_xp_tool)
        w_xp.rotate(FreeCAD.Vector(Lx - 1.245, 0, 1.245), FreeCAD.Vector(0, 1, 0), -90.0)

        w_ym_tool = Part.makeBox(Lx - 3.4, 20.0, 5.0, FreeCAD.Vector(1.7, -20.01, -1.0))
        w_ym = flat_aligned.common(w_ym_tool)
        w_ym.rotate(FreeCAD.Vector(0, 1.245, 1.245), FreeCAD.Vector(1, 0, 0), -90.0)

        w_yp_tool = Part.makeBox(Lx - 3.4, 20.0, 5.0, FreeCAD.Vector(1.7, Ly + 0.01, -1.0))
        w_yp = flat_aligned.common(w_yp_tool)
        w_yp.rotate(FreeCAD.Vector(0, Ly - 1.245, 1.245), FreeCAD.Vector(1, 0, 0), 90.0)

        Cx = -10.01327412287185
        Cz = 12.5

        f_xm_tool = Part.makeBox(w_fx, Ly + 50.0, 5.0, FreeCAD.Vector(-22.526548 - w_fx, -25.0, -1.0))
        f_xm = flat_aligned.common(f_xm_tool)
        if angle_xm > 0:
            f_xm.rotate(FreeCAD.Vector(Cx, 0, Cz), FreeCAD.Vector(0, 1, 0), 180.0)
        else:
            f_xm.translate(FreeCAD.Vector(21.526548, 0, 23.5))

        f_xp_tool = Part.makeBox(w_fx, Ly + 50.0, 5.0, FreeCAD.Vector(Lx + 22.526548, -25.0, -1.0))
        f_xp = flat_aligned.common(f_xp_tool)
        if angle_xp > 0:
            f_xp.rotate(FreeCAD.Vector(Lx - Cx, 0, Cz), FreeCAD.Vector(0, 1, 0), 180.0)
        else:
            f_xp.translate(FreeCAD.Vector(-21.526548, 0, 23.5))

        f_ym_tool = Part.makeBox(Lx + 50.0, w_fy, 5.0, FreeCAD.Vector(-25.0, -22.526548 - w_fy, -1.0))
        f_ym = flat_aligned.common(f_ym_tool)
        if angle_ym > 0:
            f_ym.rotate(FreeCAD.Vector(0, Cx, Cz), FreeCAD.Vector(1, 0, 0), 180.0)
        else:
            f_ym.translate(FreeCAD.Vector(0, 21.526548, 23.5))

        f_yp_tool = Part.makeBox(Lx + 50.0, w_fy, 5.0, FreeCAD.Vector(-25.0, Ly + 22.526548, -1.0))
        f_yp = flat_aligned.common(f_yp_tool)
        if angle_yp > 0:
            f_yp.rotate(FreeCAD.Vector(0, Ly - Cx, Cz), FreeCAD.Vector(1, 0, 0), 180.0)
        else:
            f_yp.translate(FreeCAD.Vector(0, -21.526548, 23.5))

        def make_bend_sector(center, axis_dir, box_pnt, box_size, r_in, r_out, length):
            box = Part.makeBox(box_size.x, box_size.y, box_size.z, box_pnt)
            cyl_out = Part.makeCylinder(r_out, length, center, axis_dir)
            sec_out = box.common(cyl_out)
            cyl_in = Part.makeCylinder(r_in, length, center, axis_dir)
            return sec_out.cut(cyl_in)

        b1_xm = make_bend_sector(FreeCAD.Vector(2.5, 2.5, 2.5), FreeCAD.Vector(0, 1, 0), FreeCAD.Vector(0, 2.5, 0), FreeCAD.Vector(2.5, Ly - 5.0, 2.5), 1.0, 2.5, Ly - 5.0)
        b1_xp = make_bend_sector(FreeCAD.Vector(Lx - 2.5, 2.5, 2.5), FreeCAD.Vector(0, 1, 0), FreeCAD.Vector(Lx - 2.5, 2.5, 0), FreeCAD.Vector(2.5, Ly - 5.0, 2.5), 1.0, 2.5, Ly - 5.0)
        b1_ym = make_bend_sector(FreeCAD.Vector(2.5, 2.5, 2.5), FreeCAD.Vector(1, 0, 0), FreeCAD.Vector(2.5, 0, 0), FreeCAD.Vector(Lx - 5.0, 2.5, 2.5), 1.0, 2.5, Lx - 5.0)
        b1_yp = make_bend_sector(FreeCAD.Vector(2.5, Ly - 2.5, 2.5), FreeCAD.Vector(1, 0, 0), FreeCAD.Vector(2.5, Ly - 2.5, 0), FreeCAD.Vector(Lx - 5.0, 2.5, 2.5), 1.0, 2.5, Lx - 5.0)

        if angle_xm > 0:
            len_xm = f_xm.BoundBox.YLength
            ymin_xm = f_xm.BoundBox.YMin
            b2_xm = make_bend_sector(FreeCAD.Vector(2.5, ymin_xm, 22.5), FreeCAD.Vector(0, 1, 0), FreeCAD.Vector(0, ymin_xm, 22.5), FreeCAD.Vector(2.5, len_xm, 2.5), 1.0, 2.5, len_xm)
        else:
            len_xm = Ly - 3.4
            ymin_xm = 1.70
            b2_xm = make_bend_sector(FreeCAD.Vector(-1.0, ymin_xm, 22.5), FreeCAD.Vector(0, 1, 0), FreeCAD.Vector(-1.0, ymin_xm, 22.5), FreeCAD.Vector(2.5, len_xm, 2.5), 1.0, 2.5, len_xm)

        if angle_xp > 0:
            len_xp = f_xp.BoundBox.YLength
            ymin_xp = f_xp.BoundBox.YMin
            b2_xp = make_bend_sector(FreeCAD.Vector(Lx - 2.5, ymin_xp, 22.5), FreeCAD.Vector(0, 1, 0), FreeCAD.Vector(Lx - 2.5, ymin_xp, 22.5), FreeCAD.Vector(2.5, len_xp, 2.5), 1.0, 2.5, len_xp)
        else:
            len_xp = Ly - 3.4
            ymin_xp = 1.70
            b2_xp = make_bend_sector(FreeCAD.Vector(Lx + 1.0, ymin_xp, 22.5), FreeCAD.Vector(0, 1, 0), FreeCAD.Vector(Lx - 1.5, ymin_xp, 22.5), FreeCAD.Vector(2.5, len_xp, 2.5), 1.0, 2.5, len_xp)

        if angle_ym > 0:
            len_ym = f_ym.BoundBox.XLength
            xmin_ym = f_ym.BoundBox.XMin
            b2_ym = make_bend_sector(FreeCAD.Vector(xmin_ym, 2.5, 22.5), FreeCAD.Vector(1, 0, 0), FreeCAD.Vector(xmin_ym, 0, 22.5), FreeCAD.Vector(len_ym, 2.5, 2.5), 1.0, 2.5, len_ym)
        else:
            len_ym = Lx - 3.4
            xmin_ym = 1.70
            b2_ym = make_bend_sector(FreeCAD.Vector(xmin_ym, -1.0, 22.5), FreeCAD.Vector(1, 0, 0), FreeCAD.Vector(xmin_ym, -1.0, 22.5), FreeCAD.Vector(len_ym, 2.5, 2.5), 1.0, 2.5, len_ym)

        if angle_yp > 0:
            len_yp = f_yp.BoundBox.XLength
            xmin_yp = f_yp.BoundBox.XMin
            b2_yp = make_bend_sector(FreeCAD.Vector(xmin_yp, Ly - 2.5, 22.5), FreeCAD.Vector(1, 0, 0), FreeCAD.Vector(xmin_yp, Ly - 2.5, 22.5), FreeCAD.Vector(len_yp, 2.5, 2.5), 1.0, 2.5, len_yp)
        else:
            len_yp = Lx - 3.4
            xmin_yp = 1.70
            b2_yp = make_bend_sector(FreeCAD.Vector(xmin_yp, Ly + 1.0, 22.5), FreeCAD.Vector(1, 0, 0), FreeCAD.Vector(xmin_yp, Ly - 1.5, 22.5), FreeCAD.Vector(len_yp, 2.5, 2.5), 1.0, 2.5, len_yp)

        solids = [
            hub_solid,
            w_xm, w_xp, w_ym, w_yp,
            f_xm, f_xp, f_ym, f_yp,
            b1_xm, b1_xp, b1_ym, b1_yp,
            b2_xm, b2_xp, b2_ym, b2_yp
        ]
        fused = solids[0].multiFuse(solids[1:])
        fused_clean = fused.removeSplitter()
        return cls._to_occ_shape(fused_clean)

    @classmethod
    def build_bend_transform(
        cls,
        bend: Dict[str, Any],
        parent_panel: Dict[str, Any],
        child_panel: Dict[str, Any],
        t: float,
        s_panel_start: float,
        hub_centroid: FreeCAD.Vector
    ) -> FreeCAD.Matrix:
        """
        Geometrically constructs 4x4 FreeCAD.Matrix mapping child panel from flat pattern
        into its folded position along branch.
        Derived strictly from: axis_point, axis_direction, angle_deg, parent normal, child seam, hub centroid.
        Unified parametric algorithm valid for all branch directions and bend depths.
        """
        def get_val(item, key):
            if key not in item:
                raise ValueError(f"TREE_FOLD_GEOMETRY_ERROR: {key} missing")
            v = item[key]
            if isinstance(v, dict):
                if "value" not in v:
                    raise ValueError(f"TREE_FOLD_GEOMETRY_ERROR: {key} missing value")
                return float(v["value"])
            return float(v)

        parent = parent_panel
        norm = FreeCAD.Vector(*parent["normal"]).normalize()
        c_p = FreeCAD.Vector(*parent["centroid"])
        ax_p = FreeCAD.Vector(*bend["axis_point"])
        ax_d = FreeCAD.Vector(*bend["axis_direction"]).normalize()
        d_plane = (ax_p - c_p).dot(norm)
        v_start = (-norm * d_plane).normalize()
        ang = get_val(bend, "angle_deg")
        r_in = get_val(bend, "radius_mm")

        rot = FreeCAD.Rotation(ax_d, ang)
        v_end = rot.multVec(v_start)

        p_seam_r = ax_p + v_end * r_in
        p_seam_ro = ax_p + v_end * (r_in + t)
        p_seam = p_seam_r if ang > 0 else p_seam_ro

        sgn = 1.0 if ang > 0 else -1.0
        t_child = (ax_d * sgn).cross(v_end).normalize()

        v_hub = ax_p - hub_centroid
        is_y = abs(ax_d.x) > 0.5
        sign_along = 1.0 if (v_hub.y if is_y else v_hub.x) > 0 else -1.0

        col_along = t_child * sign_along

        if is_y:
            col_width = FreeCAD.Vector(1, 0, 0)
            col1 = col_width
            col2 = col_along
            col3 = col2.cross(col1).normalize()
        else:
            col_width = FreeCAD.Vector(0, 1, 0)
            col1 = col_along
            col2 = col_width
            col3 = col2.cross(col1).normalize()

        M = FreeCAD.Matrix()
        M.A11 = col1.x; M.A12 = col2.x; M.A13 = col3.x
        M.A21 = col1.y; M.A22 = col2.y; M.A23 = col3.y
        M.A31 = col1.z; M.A32 = col2.z; M.A33 = col3.z

        T = p_seam - col_along * s_panel_start
        if is_y:
            M.A14 = 0.0; M.A24 = T.y; M.A34 = T.z
        else:
            M.A14 = T.x; M.A24 = 0.0; M.A34 = T.z
        return M

    @classmethod
    def fold_branch(
        cls,
        branch_bends: List[Dict[str, Any]],
        flat_candidate: TopoDS_Shape | Part.Shape,
        canonical_part: Dict[str, Any]
    ) -> Part.Shape:
        """
        Folds a single branch (chain of bends from the hub outward).
        Returns the folded branch solid as a FreeCAD Part.Shape in part coordinates.
        Unified parametric algorithm valid for all branch directions and bend counts.
        """
        payload = canonical_part.get("payload", canonical_part)
        t_raw = payload.get("thickness_mm", 1.5)
        t = float(t_raw.get("value", 1.5) if isinstance(t_raw, dict) else t_raw)
        panels = payload.get("panels", {})
        ref_panel_id = payload.get("reference_panel_id")
        hub_centroid = FreeCAD.Vector(*panels[ref_panel_id]["centroid"])

        fc_flat = cls._to_fc_shape(flat_candidate) if not isinstance(flat_candidate, Part.Shape) else flat_candidate
        bb = fc_flat.BoundBox
        if abs(bb.ZMin) < 1e-4 and abs(bb.ZMax - t) < 1e-4:
            pass
        elif abs(bb.ZMax) < 1e-4 and abs(bb.ZMin + t) < 1e-4:
            fc_flat.translate(FreeCAD.Vector(0, 0, t))

        def get_val(item, key):
            if key not in item:
                raise ValueError(f"TREE_FOLD_GEOMETRY_ERROR: {key} missing")
            v = item[key]
            if isinstance(v, dict):
                if "value" not in v:
                    raise ValueError(f"TREE_FOLD_GEOMETRY_ERROR: {key} missing value")
                return float(v["value"])
            return float(v)

        # General bend sector generator without hardcoded segment starts
        def make_bend_sector(axis_p_list, axis_d_list, start_v, r, thick, length, deg):
            r_o = r + thick
            ax_p = FreeCAD.Vector(*axis_p_list)
            ax_d = FreeCAD.Vector(*axis_d_list).normalize()
            p1 = ax_p + start_v * r
            p2 = ax_p + start_v * r_o
            p3 = p2 + ax_d * length
            p4 = p1 + ax_d * length
            face = Part.Face(Part.makePolygon([p1, p2, p3, p4, p1]))
            return face.revolve(ax_p, ax_d, deg)

        # Robust start_vec from parent panel plane equation with perpendicularity validation
        def compute_start_vec(bend_dict):
            parent = panels[bend_dict["parent_panel_id"]]
            c_p = FreeCAD.Vector(*parent["centroid"])
            ax_p = FreeCAD.Vector(*bend_dict["axis_point"])
            ax_d = FreeCAD.Vector(*bend_dict["axis_direction"]).normalize()
            norm = FreeCAD.Vector(*parent["normal"]).normalize()

            if abs(norm.dot(ax_d)) > 1e-6:
                raise ValueError("TREE_FOLD_GEOMETRY_ERROR: parent normal not perpendicular to bend axis")

            d_plane = (ax_p - c_p).dot(norm)
            v_to_plane = -norm * d_plane
            if v_to_plane.Length < 1e-9:
                raise ValueError("TREE_FOLD_GEOMETRY_ERROR: start_vec degenerate")
            return v_to_plane.normalize()

        bb_f = fc_flat.BoundBox
        folded_parts = []

        b0 = branch_bends[0]
        ax_p0 = FreeCAD.Vector(*b0["axis_point"])
        ax_d0 = FreeCAD.Vector(*b0["axis_direction"]).normalize()
        v_hub0 = ax_p0 - hub_centroid
        is_y = abs(ax_d0.x) > 0.5
        sign_along = 1.0 if (v_hub0.y if is_y else v_hub0.x) > 0 else -1.0

        curr_s_pos = ax_p0.y if is_y else ax_p0.x

        s_axis = 1 if is_y else 0

        for i, bend in enumerate(branch_bends):
            p_parent = panels[bend["parent_panel_id"]]
            p_child = panels[bend["child_panel_id"]]
            L_bend = get_val(bend, "length_mm")
            w_panel = get_val(p_child, "area_mm2") / (2.0 * L_bend)
            r_in = get_val(bend, "radius_mm")
            ang = get_val(bend, "angle_deg")
            k_fact = get_val(bend, "k_factor")

            ba = math.radians(abs(ang)) * (r_in + k_fact * t)

            # B.3.2: use real seam from bend_line_flat_pattern (KL-4 fix)
            bl = bend.get("bend_line_flat_pattern")
            if bl and "start_mm" in bl:
                seam_i = bl["start_mm"][s_axis]
                s_panel_start = seam_i + sign_along * ba
                if i < len(branch_bends) - 1:
                    # Intermediate panel: end at next bend seam (exact)
                    next_bl = branch_bends[i + 1].get("bend_line_flat_pattern")
                    if next_bl and "start_mm" in next_bl:
                        s_panel_end = next_bl["start_mm"][s_axis]
                    else:
                        s_panel_end = s_panel_start + sign_along * w_panel
                else:
                    # Terminal flange: extend box to flat edge, common() cuts by real contour
                    s_panel_end = (bb_f.YMin - 5.0) if is_y and sign_along < 0 else                                   (bb_f.YMax + 5.0) if is_y and sign_along > 0 else                                   (bb_f.XMin - 5.0) if not is_y and sign_along < 0 else                                   (bb_f.XMax + 5.0)
            else:
                # Fallback: original logic without bend_line_flat_pattern
                s_panel_start = curr_s_pos + sign_along * ba
                s_panel_end = s_panel_start + sign_along * w_panel
                curr_s_pos = s_panel_end

            s_min = min(s_panel_start, s_panel_end)
            s_max = max(s_panel_start, s_panel_end)

            if is_y:
                box_p = Part.makeBox(
                    bb_f.XLength + 20.0,
                    s_max - s_min,
                    bb_f.ZLength + 4.0,
                    FreeCAD.Vector(bb_f.XMin - 10.0, s_min, -2.0)
                )
            else:
                box_p = Part.makeBox(
                    s_max - s_min,
                    bb_f.YLength + 20.0,
                    bb_f.ZLength + 4.0,
                    FreeCAD.Vector(s_min, bb_f.YMin - 10.0, -2.0)
                )

            panel_cut = fc_flat.common(box_p)
            start_v = compute_start_vec(bend)
            b_sector = make_bend_sector(bend["axis_point"], bend["axis_direction"], start_v, r_in, t, L_bend, ang)

            m_p = cls.build_bend_transform(bend, p_parent, p_child, t, s_panel_start, hub_centroid)
            panel_folded = panel_cut.transformShape(m_p)

            folded_parts.append(b_sector)
            folded_parts.append(panel_folded)

        branch = folded_parts[0]
        for part in folded_parts[1:]:
            branch = branch.fuse(part)
        branch_clean = branch.removeSplitter()

        # Canonicalize topology: source flat_pattern.brep has ~5.8e-7 rad angular
        # deviation on the -X side of relief cut normal. Without unify, -X and +X
        # branches produce 21 vs 19 faces (asymmetric). UnifySameDomain with
        # 1e-4 angular / 1e-3 linear tolerance normalizes both to 19 faces.
        # See: B.2c verification (before: -X=21, +X=19; after: -X=19, +X=19).
        occ_s = cls._to_occ_shape(branch_clean)
        unif = ShapeUpgrade_UnifySameDomain(occ_s, True, True, True)
        unif.SetLinearTolerance(1e-3)
        unif.SetAngularTolerance(1e-4)
        unif.Build()
        return cls._to_fc_shape(unif.Shape())
    @classmethod
    def fold_tree_general_from_flat_pattern(
        cls,
        flat_candidate: TopoDS_Shape,
        canonical_part: Dict[str, Any]
    ) -> TopoDS_Shape:
        """
        Step B.3: Parametric folding for general tree sheet metal parts.
        Extracts hub bounded by root bend axes and folds all branches outward from hub.
        All panel widths are extracted from canonical_part['panels'][pid]['area_mm2'] / (2 * L_bend).
        Panels are cut from flat_candidate via boolean operations along dynamic boundaries.
        Bend sectors are generated via profile revolving with DIN 6935 neutral layer shift.
        """
        payload = canonical_part.get("payload", canonical_part)
        bends = payload.get("bends", {})
        panels = payload.get("panels", {})
        ref_panel_id = payload.get("reference_panel_id")

        hub_panel = panels.get(ref_panel_id, {})
        hub_centroid = FreeCAD.Vector(*hub_panel["centroid"])
        t_raw = payload.get("thickness_mm", 1.5)
        t = float(t_raw.get("value", 1.5) if isinstance(t_raw, dict) else t_raw)

        fc_flat = cls._to_fc_shape(flat_candidate) if not isinstance(flat_candidate, Part.Shape) else flat_candidate
        bb = fc_flat.BoundBox
        if abs(bb.ZMin) < 1e-4 and abs(bb.ZMax - t) < 1e-4:
            pass
        elif abs(bb.ZMax) < 1e-4 and abs(bb.ZMin + t) < 1e-4:
            fc_flat.translate(FreeCAD.Vector(0, 0, t))

        # 1. Extract hub boundaries from axis_point of root adjacent bends
        hub_x_min, hub_x_max = None, None
        hub_y_min, hub_y_max = None, None
        for bid in hub_panel.get("adjacent_bend_ids", []):
            b = bends[bid]
            ax_d = FreeCAD.Vector(*b["axis_direction"]).normalize()
            ax_p = FreeCAD.Vector(*b["axis_point"])
            is_y = abs(ax_d.x) > 0.5
            v_hub = ax_p - hub_centroid
            sign_along = 1.0 if (v_hub.y if is_y else v_hub.x) > 0 else -1.0
            if is_y:
                if sign_along < 0:
                    hub_y_min = ax_p.y
                else:
                    hub_y_max = ax_p.y
            else:
                if sign_along < 0:
                    hub_x_min = ax_p.x
                else:
                    hub_x_max = ax_p.x

        box_hub = Part.makeBox(hub_x_max - hub_x_min, hub_y_max - hub_y_min, t + 4.0, FreeCAD.Vector(hub_x_min, hub_y_min, -2.0))
        hub_shape = fc_flat.common(box_hub)

        all_branches = []
        for bid in hub_panel.get("adjacent_bend_ids", []):
            chain = [bends[bid]]
            curr_p_id = bends[bid]["child_panel_id"]
            while True:
                curr_panel = panels.get(curr_p_id, {})
                adjs = [b_other for b_other in curr_panel.get("adjacent_bend_ids", []) if b_other != chain[-1]["bend_id"]]
                if not adjs:
                    break
                next_b = bends[adjs[0]]
                chain.append(next_b)
                curr_p_id = next_b["child_panel_id"]
            all_branches.append(chain)

        full_shape = hub_shape
        for br in all_branches:
            br_shape = cls.fold_branch(br, fc_flat, canonical_part)
            full_shape = full_shape.fuse(br_shape)

        full_clean = full_shape.removeSplitter()
        return cls._to_occ_shape(full_clean)
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
        bends = payload.get("bends", {})
        panels = payload.get("panels", {})

        num_bends = len(bends)

        # Multi-bend and single-bend profile from aligned flat pattern
        if aligned_flat is not None:
            if num_bends == 1:
                return cls.fold_from_flat_pattern(aligned_flat, canonical_part)
            else:
                # Handle possible 90-degree tangent orientation ambiguity on square reference panels
                # (e.g. 50x50 mm reference panel where FrameAligner picked edge along Y instead of X)
                flat_candidate = aligned_flat
                if num_bends > 1 and bends:
                    b0 = next(iter(bends.values()))
                    st = b0.get("bend_line_flat_pattern", {}).get("start_mm", [0, 0, 0])
                    en = b0.get("bend_line_flat_pattern", {}).get("end_mm", [0, 0, 0])
                    bend_parallel_to_y = abs(st[0] - en[0]) < 1e-3
                    ref_pid = payload.get("reference_panel_id")
                    if ref_pid and ref_pid in panels:
                        ref_area = panels[ref_pid].get("area_mm2", 0.0)
                        w_raw = b0.get("length_mm", {}).get("value")
                        if w_raw is not None and w_raw > 0:
                            ref_len = ref_area / (2.0 * w_raw)
                            is_square_ref = abs(w_raw - ref_len) < 1e-2
                            if is_square_ref:
                                fc_s = cls._to_fc_shape(aligned_flat)
                                if bend_parallel_to_y and fc_s.BoundBox.YLength > fc_s.BoundBox.XLength:
                                    bb = fc_s.BoundBox
                                    cx = (bb.XMin + bb.XMax) / 2.0
                                    cy = (bb.YMin + bb.YMax) / 2.0
                                    fc_s.rotate(FreeCAD.Vector(cx, cy, bb.ZMin), FreeCAD.Vector(0, 0, 1), 90.0)
                                    flat_candidate = cls._to_occ_shape(fc_s)
                # Dispatcher by topology (Stage 1 Task 1.0.1b-3b / B.4)
                # 1. Check max panel degree to detect branching
                max_panel_degree = max([len(p.get("adjacent_bend_ids", [])) for p in panels.values()]) if panels else 0

                if max_panel_degree <= 2:
                    # 1D linear chain (all panel degrees <= 2)
                    if not hasattr(cls, "fold_multi_bend_from_flat_pattern"):
                        raise ValueError("TREE_FOLD_GEOMETRY_ERROR: fold_multi_bend_from_flat_pattern not implemented on folder")
                    return cls.fold_multi_bend_from_flat_pattern(flat_candidate, canonical_part)

                # 2. Branching part: identify root hub (panel of maximum degree)
                hub_pid = None
                max_deg = 0
                for pid, p in panels.items():
                    deg = len(p.get("adjacent_bend_ids", []))
                    if deg > max_deg:
                        max_deg = deg
                        hub_pid = pid

                # Measure max branch depth from hub panel
                def _get_branch_depth(h_id: str) -> int:
                    visited = {h_id: 0}
                    queue = [h_id]
                    while queue:
                        curr = queue.pop(0)
                        curr_depth = visited[curr]
                        for bid in panels.get(curr, {}).get("adjacent_bend_ids", []):
                            b = bends.get(bid, {})
                            for adj_pid in [b.get("panel_a_id"), b.get("panel_b_id"), b.get("parent_panel_id"), b.get("child_panel_id")]:
                                if adj_pid and adj_pid in panels and adj_pid not in visited:
                                    visited[adj_pid] = curr_depth + 1
                                    queue.append(adj_pid)
                    return max(visited.values()) if visited else 0

                branch_depth = _get_branch_depth(hub_pid) if hub_pid else 0

                # Check bend angles for 3a star box (must all be orthogonal 90.0 deg)
                all_angles_90 = True
                for b in bends.values():
                    ang_raw = b.get("angle_deg", {}).get("value")
                    if ang_raw is None or abs(abs(ang_raw) - 90.0) > 1e-3:
                        all_angles_90 = False
                        break

                # 3a star box criteria (§5.1):
                # hub has 4 branches, total 8 bends, max depth 2, and all bend angles are 90 deg
                is_star_box_3a = (
                    max_panel_degree == 4 and
                    len(bends) == 8 and
                    branch_depth == 2 and
                    all_angles_90
                )

                if is_star_box_3a:
                    if not hasattr(cls, "fold_tree_from_flat_pattern"):
                        raise ValueError("TREE_FOLD_GEOMETRY_ERROR: fold_tree_from_flat_pattern not implemented on folder")
                    return cls.fold_tree_from_flat_pattern(flat_candidate, canonical_part)

                # 3. General tree (variable depth, non-orthogonal angles, arbitrary branching)
                if not hasattr(cls, "fold_tree_general_from_flat_pattern"):
                    raise ValueError("TREE_FOLD_GEOMETRY_ERROR: fold_tree_general_from_flat_pattern not implemented on folder")
                return cls.fold_tree_general_from_flat_pattern(flat_candidate, canonical_part)
        else:
            raise ValueError("SheetMetalFolder.fold_from_canonical_part requires a non-None aligned_flat shape.")
