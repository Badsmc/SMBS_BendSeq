"""
test_sheetmetal_4bend.py - Unit test suite for 4-bend stepped busbar:
1. test_4bend_has_five_panels - len(feats['panels']) == 5
2. test_4bend_has_four_bends - len(feats['records']) == 4
3. test_4bend_chain_is_linear - graph is a linear path of 5 nodes (edges == 4, degree sequence [1,2,2,2,1])
4. test_4bend_root_is_unique - root is 100% deterministic across PYTHONHASHSEED in {0, 1, 42, 999}
5. test_4bend_w_flat_analytic - W_flat == 200.4300876 mm
6. test_4bend_unfold_no_cylinders - 8 cylinders in -> 0 cylinders out
7. test_4bend_faces_out - full B-Rep solid with 22 planar faces (not 6-face box)
"""

import os
import sys
import math
import tempfile
import subprocess
import unittest

from pathlib import Path
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path: sys.path.insert(0, _ROOT)
sys.path.append('/Applications/FreeCAD.app/Contents/Resources/lib')

import FreeCAD
import Part
from OCC.Core.gp import gp_Pnt, gp_Vec
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCC.Core.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_EDGE
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from OCC.Core.STEPControl import STEPControl_Writer, STEPControl_AsIs

from smbs_core.api.sheetmetal_client import SheetMetalApiClient
from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
from smbs_core.Geometry.feature_extractor import FeatureExtractor


def create_4bend_stepped_busbar(
    l1: float = 40.0,
    l2: float = 30.0,
    l3: float = 50.0,
    l4: float = 30.0,
    l5: float = 40.0,
    radius: float = 1.0,
    thickness: float = 1.5,
    length: float = 50.0
):
    """
    Creates a stepped 4-bend busbar shape where flat lengths of the 5 flanges
    between tangent points are exactly l1, l2, l3, l4, l5.
    Fillets are 100% concentric with inner R=radius, outer R=radius+thickness.
    """
    t = thickness
    r_in = radius
    r_out = radius + thickness

    # Internal coordinates with r_in offsets so flat lengths are l1..l5:
    _l1 = l1 + r_in
    _l2 = l2 + r_in
    _l3 = l3 + r_in
    _l4 = l4 + r_in
    _l5 = l5

    # Bend 1:
    c1_x = _l1
    c1_z = t + r_in

    # Flange 2:
    c2_x = (_l1 + t) + r_in
    c2_z = c1_z + _l2

    # Flange 3:
    c3_x = c2_x + _l3
    c3_z = c2_z + t + r_in

    # Flange 4:
    c4_x = (c3_x + t) + r_in
    c4_z = c3_z + _l4

    # Flange 5:
    end_x = c4_x + _l5
    end_z = c4_z

    poly = BRepBuilderAPI_MakePolygon()
    poly.Add(gp_Pnt(0.0, 0.0, 0.0))
    poly.Add(gp_Pnt(_l1 + t, 0.0, 0.0))
    poly.Add(gp_Pnt(_l1 + t, 0.0, c2_z))
    poly.Add(gp_Pnt(c3_x + t, 0.0, c2_z))
    poly.Add(gp_Pnt(c3_x + t, 0.0, c4_z))
    poly.Add(gp_Pnt(end_x, 0.0, c4_z))
    poly.Add(gp_Pnt(end_x, 0.0, c4_z + t))
    poly.Add(gp_Pnt(c3_x, 0.0, c4_z + t))
    poly.Add(gp_Pnt(c3_x, 0.0, c2_z + t))
    poly.Add(gp_Pnt(_l1, 0.0, c2_z + t))
    poly.Add(gp_Pnt(_l1, 0.0, t))
    poly.Add(gp_Pnt(0.0, 0.0, t))
    poly.Close()

    wire = poly.Wire()
    face = BRepBuilderAPI_MakeFace(wire).Shape()
    prism = BRepPrimAPI_MakePrism(face, gp_Vec(0.0, length, 0.0)).Shape()

    fillet = BRepFilletAPI_MakeFillet(prism)
    exp = TopExp_Explorer(prism, TopAbs_EDGE)

    while exp.More():
        e = exp.Current()
        c = BRepAdaptor_Curve(e)
        p1 = c.Value(c.FirstParameter())
        p2 = c.Value(c.LastParameter())
        if abs(p1.X() - p2.X()) < 1e-3 and abs(p1.Z() - p2.Z()) < 1e-3 and abs(p1.Y() - p2.Y()) > 10.0:
            px, pz = p1.X(), p1.Z()
            # Bend 1:
            if abs(px - _l1) < 1e-2 and abs(pz - t) < 1e-2:
                fillet.Add(r_in, e)
            elif abs(px - (_l1 + t)) < 1e-2 and abs(pz - 0.0) < 1e-2:
                fillet.Add(r_out, e)
            # Bend 2:
            elif abs(px - (_l1 + t)) < 1e-2 and abs(pz - c2_z) < 1e-2:
                fillet.Add(r_in, e)
            elif abs(px - _l1) < 1e-2 and abs(pz - (c2_z + t)) < 1e-2:
                fillet.Add(r_out, e)
            # Bend 3:
            elif abs(px - c3_x) < 1e-2 and abs(pz - (c2_z + t)) < 1e-2:
                fillet.Add(r_in, e)
            elif abs(px - (c3_x + t)) < 1e-2 and abs(pz - c2_z) < 1e-2:
                fillet.Add(r_out, e)
            # Bend 4:
            elif abs(px - (c3_x + t)) < 1e-2 and abs(pz - c4_z) < 1e-2:
                fillet.Add(r_in, e)
            elif abs(px - c3_x) < 1e-2 and abs(pz - (c4_z + t)) < 1e-2:
                fillet.Add(r_out, e)
        exp.Next()

    fillet.Build()
    return fillet.Shape()


class TestSheetMetal4Bend(unittest.TestCase):

    def setUp(self):
        params = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/SheetMetal")
        self._saved_k = params.GetFloat("manualKFactor", 0.44)
        self._saved_std = params.GetString("kFactorStandard", "ansi")
        params.SetFloat("manualKFactor", 0.44)
        params.SetString("kFactorStandard", "ansi")
        self.backend = PythonOCCBackend()
        self.extractor = FeatureExtractor(self.backend)
        self.client = SheetMetalApiClient()

    def tearDown(self):
        params = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/SheetMetal")
        params.SetFloat("manualKFactor", self._saved_k)
        params.SetString("kFactorStandard", self._saved_std)

    def _get_4bend_shape_and_features(self):
        shape = create_4bend_stepped_busbar(l1=40.0, l2=30.0, l3=50.0, l4=30.0, l5=40.0)
        feats = self.extractor.extract_features(shape)
        return shape, feats

    def test_4bend_has_five_panels(self):
        """1. 4-bend busbar extracts exactly 5 physical panels."""
        _, feats = self._get_4bend_shape_and_features()
        panels = feats.get("panels", {})
        self.assertEqual(len(panels), 5, f"Expected 5 panels, got {len(panels)}")

        # Check total areas: 5000, 4000, 4000, 3000, 3000
        areas = sorted([round(p.total_area) for p in panels.values()], reverse=True)
        self.assertEqual(areas, [5000, 4000, 4000, 3000, 3000])

    def test_4bend_has_four_bends(self):
        """2. 4-bend busbar extracts exactly 4 bend records."""
        _, feats = self._get_4bend_shape_and_features()
        records = feats.get("records", {})
        self.assertEqual(len(records), 4, f"Expected 4 bends, got {len(records)}")

        # All bends must have 90 degree magnitude and radius 1.0
        for b in records.values():
            self.assertAlmostEqual(abs(b.signed_angle), 90.0, delta=1e-2)

    def test_4bend_chain_is_linear(self):
        """3. Undirected panel graph is a linear path of 5 nodes with no branches."""
        _, feats = self._get_4bend_shape_and_features()
        bends = feats.get("bends", {})

        # Build undirected graph adjacency
        adj = {}
        for b in bends.values():
            p1, p2 = b["parent_panel_id"], b["child_panel_id"]
            adj.setdefault(p1, set()).add(p2)
            adj.setdefault(p2, set()).add(p1)

        self.assertEqual(len(bends), 4, "Expected exactly 4 edges in tree")
        # Degree sequence of a 5-node linear path: 2 endpoints with degree 1, 3 middle nodes with degree 2
        degrees = sorted([len(neighbors) for neighbors in adj.values()])
        self.assertEqual(degrees, [1, 1, 2, 2, 2], f"Expected path degree sequence [1, 1, 2, 2, 2], got: {degrees}")

    def test_4bend_root_is_unique(self):
        """4. Root panel is 100% deterministic across PYTHONHASHSEED in {0, 1, 42, 999}."""
        code = f"""
import sys
_ROOT = {repr(_ROOT)}
if _ROOT not in sys.path: sys.path.insert(0, _ROOT)
sys.path.append('/Applications/FreeCAD.app/Contents/Resources/lib')

from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
from smbs_core.Geometry.feature_extractor import FeatureExtractor
from tests.test_sheetmetal_4bend import create_4bend_stepped_busbar

backend = PythonOCCBackend()
extractor = FeatureExtractor(backend)
shape = create_4bend_stepped_busbar(l1=40.0, l2=30.0, l3=50.0, l4=30.0, l5=40.0)
feats = extractor.extract_features(shape)
first_bend = list(feats['bends'].values())[0]
print(first_bend['parent_panel_id'])
"""
        python_bin = "/Applications/FreeCAD.app/Contents/Resources/bin/python"
        roots = set()
        for seed in ["0", "1", "42", "999"]:
            env = os.environ.copy()
            env["PYTHONHASHSEED"] = seed
            env["PYTHONPATH"] = f"{_ROOT}:{env.get('PYTHONPATH', '')}"
            proc = subprocess.run([python_bin, "-c", code], env=env, capture_output=True, text=True, check=True)
            roots.add(proc.stdout.strip())

        self.assertEqual(len(roots), 1, f"Expected identical root across seeds, got: {roots}")

    def test_4bend_w_flat_analytic(self):
        """5. W_flat = Σ l_i + 4·BA(90°) = 190 + 10.4300876 = 200.4300876 mm."""
        shape, feats = self._get_4bend_shape_and_features()

        with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as tmp:
            step_path = tmp.name
        writer = STEPControl_Writer()
        writer.Transfer(shape, STEPControl_AsIs)
        writer.Write(step_path)
        s0_fc = Part.read(step_path)
        os.remove(step_path)

        unfolded = self.client.unfold_shape_headless(s0_fc)
        bb = unfolded.BoundBox
        w_flat = max(bb.XLength, bb.YLength, bb.ZLength)

        ba = (math.pi * (1.0 + 0.44 * 1.5) * 90.0) / 180.0
        expected_w = (40.0 + 30.0 + 50.0 + 30.0 + 40.0) + 4.0 * ba

        self.assertAlmostEqual(w_flat, expected_w, delta=1e-3)
        self.assertAlmostEqual(w_flat, 200.4300876, delta=1e-3)

    def test_4bend_unfold_no_cylinders(self):
        """6. Headless unfold converts 8 input cylindrical faces into 0 cylindrical faces."""
        shape, _ = self._get_4bend_shape_and_features()

        with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as tmp:
            step_path = tmp.name
        writer = STEPControl_Writer()
        writer.Transfer(shape, STEPControl_AsIs)
        writer.Write(step_path)
        s0_fc = Part.read(step_path)
        os.remove(step_path)

        cyls_in = [f for f in s0_fc.Faces if getattr(f.Surface, 'TypeId', '') == 'Part::GeomCylinder']
        self.assertEqual(len(cyls_in), 8, f"Expected 8 bend cylinders on input, got {len(cyls_in)}")

        unfolded = self.client.unfold_shape_headless(s0_fc)
        cyls_out = [f for f in unfolded.Faces if getattr(f.Surface, 'TypeId', '') == 'Part::GeomCylinder']
        self.assertEqual(len(cyls_out), 0, f"Expected 0 cylinders on unfolded sheet, got {len(cyls_out)}")

    def test_4bend_faces_out(self):
        """7. Unfolded solid has full 22 planar B-Rep faces (not synthetic 6-face box)."""
        shape, _ = self._get_4bend_shape_and_features()

        with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as tmp:
            step_path = tmp.name
        writer = STEPControl_Writer()
        writer.Transfer(shape, STEPControl_AsIs)
        writer.Write(step_path)
        s0_fc = Part.read(step_path)
        os.remove(step_path)

        unfolded = self.client.unfold_shape_headless(s0_fc)
        faces_out = unfolded.Faces
        self.assertEqual(len(faces_out), 22)
        self.assertNotEqual(len(faces_out), 6)


if __name__ == '__main__':
    unittest.main()
