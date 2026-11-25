import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from dataclasses import replace

from smbs_core.kinematics2d.parametric_tool import (
    ToolRecipe, build_contour, dxf_text, starter_recipes, tooling_metadata,
)
from smbs_core.kinematics2d.tooling_25d import (
    ArcSegment2D, ValidationStatus, import_tool_from_dxf,
)


class ParametricToolTest(unittest.TestCase):
    def test_all_starter_profiles_roundtrip(self):
        with TemporaryDirectory() as tmp:
            for recipe in starter_recipes():
                with self.subTest(tool=recipe.tool_id):
                    contour = build_contour(recipe)
                    self.assertTrue(contour.is_closed(1e-7))
                    self.assertTrue(any(isinstance(e, ArcSegment2D) for e in contour.segments))
                    path = Path(tmp) / (recipe.tool_id + ".dxf")
                    path.write_text(dxf_text(contour), encoding="ascii")
                    tool = import_tool_from_dxf(path, recipe.kind, length_mm=recipe.length)
                    self.assertEqual(tool.validation_status, ValidationStatus.GEOMETRY_VERIFIED)
                    if recipe.kind == "punch":
                        self.assertAlmostEqual(float(tool.punch_datums.included_angle_deg), recipe.angle, places=3)
                        self.assertAlmostEqual(float(tool.tip_radius_mm), recipe.radius, places=5)
                    else:
                        self.assertAlmostEqual(float(tool.die_angle_deg), recipe.angle, places=3)
                        self.assertAlmostEqual(float(tool.v_opening_mm), recipe.opening, places=5)
                        self.assertAlmostEqual(float(tool.shoulder_radius_mm), recipe.radius, places=5)
                    meta = tooling_metadata(recipe)
                    self.assertEqual(meta["metadata"]["segments"], [])
                    self.assertFalse(meta["metadata"]["manufacturing_certified"])

    def test_invalid_geometry_and_names(self):
        for recipe in (ToolRecipe("../bad"), ToolRecipe("bad", height=3),
                       ToolRecipe("bad", kind="die", opening=50, width=30),
                       ToolRecipe("bad", relief_depth=100), ToolRecipe("bad", radius=float("nan"))):
            with self.subTest(recipe=recipe), self.assertRaises(ValueError):
                build_contour(recipe)
