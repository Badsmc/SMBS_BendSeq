"""
test_sheetmetal_canonicalization.py - Tests for 3-phase canonicalization:
1. test_l_bracket_has_two_panels
2. test_u_bracket_has_three_panels
3. test_u_bracket_bends_are_chained
4. test_canonical_orientation_seed_independent
5. test_w_flat_unchanged_after_canonicalization
"""

import os
import sys
import math
import subprocess
import unittest

sys.path.insert(0, '/Users/badmc/Desktop/SMBS_BendSeq')
sys.path.append('/Applications/FreeCAD.app/Contents/Resources/lib')

from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
from smbs_core.Geometry.feature_extractor import FeatureExtractor
from tests.test_technological_unfold_single_bend import create_l_bracket_shape
from tests.test_sheetmetal_ubracket import create_u_bracket_shape_perfect_concentric


class TestSheetMetalCanonicalization(unittest.TestCase):

    def setUp(self):
        self.backend = PythonOCCBackend()
        self.extractor = FeatureExtractor(self.backend)

    def test_l_bracket_has_two_panels(self):
        """L-bracket has exactly 2 physical panels (areas 9550 and 4750)."""
        shape = create_l_bracket_shape(90.0, radius=1.0, thickness=1.5, length=50.0, l1=95.5, l2=47.5)
        feats = self.extractor.extract_features(shape)

        panels = feats.get("panels", {})
        self.assertEqual(len(panels), 2, f"Expected 2 panels, got {len(panels)}")

        areas = sorted([p.total_area for p in panels.values()], reverse=True)
        self.assertAlmostEqual(areas[0], 9550.0, delta=1.0)
        self.assertAlmostEqual(areas[1], 4750.0, delta=1.0)

    def test_u_bracket_has_three_panels(self):
        """U-bracket has exactly 3 physical panels (areas 9550, 5000, and 4750)."""
        shape = create_u_bracket_shape_perfect_concentric(l1=95.5, l_mid=50.0, l2=47.5, radius=1.0, thickness=1.5, length=50.0)
        feats = self.extractor.extract_features(shape)

        panels = feats.get("panels", {})
        self.assertEqual(len(panels), 3, f"Expected 3 panels, got {len(panels)}")

        areas = sorted([p.total_area for p in panels.values()], reverse=True)
        self.assertAlmostEqual(areas[0], 9550.0, delta=1.0)
        self.assertAlmostEqual(areas[1], 5000.0, delta=1.0)
        self.assertAlmostEqual(areas[2], 4750.0, delta=1.0)

    def test_u_bracket_bends_are_chained(self):
        """Bends in U-bracket form a continuous chain from root: parents ∩ children is exactly 1 panel."""
        shape = create_u_bracket_shape_perfect_concentric(l1=95.5, l_mid=50.0, l2=47.5, radius=1.0, thickness=1.5, length=50.0)
        feats = self.extractor.extract_features(shape)

        bends = feats.get("bends", {})
        self.assertEqual(len(bends), 2, f"Expected 2 bends, got {len(bends)}")

        parents = {b["parent_panel_id"] for b in bends.values()}
        children = {b["child_panel_id"] for b in bends.values()}
        common = parents & children

        self.assertEqual(len(common), 1, f"Expected exactly 1 chained intermediate panel, got {common}")

        # Intermediate panel must be the middle flange (area 5000)
        intermediate_pid = list(common)[0]
        intermediate_panel = feats["panels"][intermediate_pid]
        self.assertAlmostEqual(intermediate_panel.total_area, 5000.0, delta=1.0)

    def test_canonical_orientation_seed_independent(self):
        """Extraction yields identical (P1, P2, signed_angle) across PYTHONHASHSEED in {0, 1, 42, 999}."""
        seeds = ["0", "1", "42", "999"]
        results = {}

        code = """
import sys
sys.path.insert(0, '/Users/badmc/Desktop/SMBS_BendSeq')
sys.path.append('/Applications/FreeCAD.app/Contents/Resources/lib')

from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
from smbs_core.Geometry.feature_extractor import FeatureExtractor
from tests.test_technological_unfold_single_bend import create_l_bracket_shape
from tests.test_sheetmetal_ubracket import create_u_bracket_shape_perfect_concentric

backend = PythonOCCBackend()
extractor = FeatureExtractor(backend)

l_shape = create_l_bracket_shape(90.0, radius=1.0, thickness=1.5, length=50.0, l1=95.5, l2=47.5)
l_feats = extractor.extract_features(l_shape)
l_bends = [(b['parent_panel_id'], b['child_panel_id'], b['signed_angle']) for b in l_feats['bends'].values()]

u_shape = create_u_bracket_shape_perfect_concentric(l1=95.5, l_mid=50.0, l2=47.5, radius=1.0, thickness=1.5, length=50.0)
u_feats = extractor.extract_features(u_shape)
u_bends = [(b['parent_panel_id'], b['child_panel_id'], b['signed_angle']) for b in u_feats['bends'].values()]

print(repr((l_bends, u_bends)))
"""

        python_bin = "/Applications/FreeCAD.app/Contents/Resources/bin/python"
        for s in seeds:
            env = os.environ.copy()
            env["PYTHONHASHSEED"] = s
            proc = subprocess.run([python_bin, "-c", code], env=env, capture_output=True, text=True, check=True)
            results[s] = proc.stdout.strip()

        ref_seed = seeds[0]
        for s in seeds[1:]:
            self.assertEqual(results[s], results[ref_seed], f"Inconsistent extraction between seed {ref_seed} and {s}:\n{results[ref_seed]} vs\n{results[s]}")

    def test_w_flat_unchanged_after_canonicalization(self):
        """W_flat(90°) = 145.6075 mm and W_flat(U) = 198.215 mm regression check."""
        # 1. L-bracket 90 deg
        l_shape = create_l_bracket_shape(90.0, radius=1.0, thickness=1.5, length=50.0, l1=95.5, l2=47.5)
        l_feats = self.extractor.extract_features(l_shape)
        l_bend = list(l_feats["bends"].values())[0]
        t = l_feats["thickness"]
        k = 0.44
        ba_l = math.pi * (l_bend["radius"] + k * t) * abs(l_bend["signed_angle"]) / 180.0
        # Flange flat lengths for 90 deg L-bracket
        w_flat_l = 95.5 + ba_l + 47.5
        self.assertAlmostEqual(w_flat_l, 145.6075, delta=1e-3)

        # 2. U-bracket
        u_shape = create_u_bracket_shape_perfect_concentric(l1=95.5, l_mid=50.0, l2=47.5, radius=1.0, thickness=1.5, length=50.0)
        u_feats = self.extractor.extract_features(u_shape)
        t_u = u_feats["thickness"]
        ba_sum = sum(
            math.pi * (b["radius"] + k * t_u) * abs(b["signed_angle"]) / 180.0
            for b in u_feats["bends"].values()
        )
        w_flat_u = 95.5 + 50.0 + 47.5 + ba_sum
        self.assertAlmostEqual(w_flat_u, 198.215, delta=1e-3)

    def test_root_selection_is_deterministic_for_symmetric_parts(self):
        """Root selection is 100% deterministic even for fully symmetric U-bracket with l1 == l2."""
        code = """
import sys
sys.path.insert(0, '/Users/badmc/Desktop/SMBS_BendSeq')
sys.path.append('/Applications/FreeCAD.app/Contents/Resources/lib')

from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
from smbs_core.Geometry.feature_extractor import FeatureExtractor
from tests.test_sheetmetal_ubracket import create_u_bracket_shape_perfect_concentric

backend = PythonOCCBackend()
extractor = FeatureExtractor(backend)
u_sym = create_u_bracket_shape_perfect_concentric(l1=50.0, l_mid=50.0, l2=50.0, radius=1.0, thickness=1.5, length=50.0)
feats = extractor.extract_features(u_sym)
# First bend parent is root
first_bend = list(feats['bends'].values())[0]
print(first_bend['parent_panel_id'])
"""
        python_bin = "/Applications/FreeCAD.app/Contents/Resources/bin/python"
        roots = set()
        for seed in ["0", "1", "42", "999"]:
            env = os.environ.copy()
            env["PYTHONHASHSEED"] = seed
            proc = subprocess.run([python_bin, "-c", code], env=env, capture_output=True, text=True, check=True)
            roots.add(proc.stdout.strip())

        self.assertEqual(len(roots), 1, f"Expected identical root across seeds for symmetric part, got: {roots}")


if __name__ == '__main__':
    unittest.main()
