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

    def test_live_drawing_matches_export_and_updates_dimensions(self):
        from smbs_core.kinematics2d.parametric_tool_drawing import drawing_model
        for recipe in starter_recipes():
            with self.subTest(tool=recipe.tool_id):
                contour, dimensions = drawing_model(recipe)
                self.assertEqual(dxf_text(contour), dxf_text(build_contour(recipe)))
                values = {d.key:d.value for d in dimensions}
                self.assertEqual(values["height"],recipe.height)
                self.assertEqual(values["angle"],recipe.angle)
                self.assertEqual(values["radius"],recipe.radius)
                if recipe.kind == "punch":
                    self.assertEqual(values["working_height"],recipe.height-recipe.shank_height)
                else:
                    self.assertEqual(values["opening"],recipe.opening)
                    if recipe.base_width:
                        self.assertEqual(values["base_width"],recipe.base_width)
                        self.assertAlmostEqual(contour.bbox()[2]-contour.bbox()[0],recipe.base_width)
                updated = replace(recipe,height=recipe.height+10)
                changed, dims = drawing_model(updated)
                self.assertNotEqual(dxf_text(contour),dxf_text(changed))
                self.assertEqual(next(d.value for d in dims if d.key=="height"),updated.height)

    def test_stepped_die_rejects_partial_or_inverted_base(self):
        for width,height in ((60,0),(0,18),(10,18),(60,100)):
            with self.subTest(width=width,height=height), self.assertRaises(ValueError):
                build_contour(ToolRecipe("bad",kind="die",base_width=width,base_height=height))

    def test_independent_die_dimensions(self):
        from smbs_core.kinematics2d.parametric_tool_drawing import drawing_model
        recipe = ToolRecipe("asymmetric",kind="die",width=36,opening=25,height=80,
                            body_offset=1,base_width=64,base_offset=2,
                            base_height=17,base_right_height=20,radius=1)
        contour,dims = drawing_model(recipe)
        values={d.key:d.value for d in dims}
        self.assertEqual((values["left_width"],values["right_width"]),(17,19))
        self.assertEqual((values["base_left"],values["base_right"]),(30,34))
        self.assertEqual((values["upper_left"],values["upper_right"]),(63,60))
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/"die.dxf"
            path.write_text(dxf_text(contour),encoding="ascii")
            profile=import_tool_from_dxf(path,"die")
            self.assertEqual(profile.validation_status,ValidationStatus.GEOMETRY_VERIFIED)
            self.assertAlmostEqual(float(profile.v_opening_mm),25,places=5)

    def test_mirror_and_relief_controls(self):
        from smbs_core.kinematics2d.parametric_tool_drawing import drawing_model
        recipe = ToolRecipe("relief",height=150,width=45,relief_depth=24,
                            relief_height=35,relief_start=8,relief_transition=3)
        right,dims=drawing_model(recipe)
        left,_=drawing_model(replace(recipe,relief_side="left"))
        self.assertAlmostEqual(right.area,left.area,places=5)
        self.assertNotEqual(dxf_text(right),dxf_text(left))
        self.assertEqual(next(d.value for d in dims if d.key=="relief_start"),8)
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/"punch.dxf"
            path.write_text(dxf_text(left),encoding="ascii")
            profile=import_tool_from_dxf(path,"punch")
            self.assertEqual(profile.validation_status,ValidationStatus.GEOMETRY_VERIFIED)
        self.assertRaises(ValueError,build_contour,replace(recipe,relief_transition=20))
