import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from dataclasses import replace

from freecad.bendseq.core.kinematics2d.parametric_tool import (
    ToolRecipe, build_contour, dxf_text, starter_recipes, tooling_metadata,
)
from freecad.bendseq.core.kinematics2d.tooling_25d import (
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
        from freecad.bendseq.core.kinematics2d.parametric_tool_drawing import drawing_model
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
        from freecad.bendseq.core.kinematics2d.parametric_tool_drawing import drawing_model
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
        from freecad.bendseq.core.kinematics2d.parametric_tool_drawing import drawing_model
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

    def test_punch_offset_keeps_tip_shank_and_angle(self):
        from freecad.bendseq.core.kinematics2d.parametric_tool_drawing import drawing_model
        for offset in (-12,-5,0,5,12):
            for relief in ("left","right"):
                with self.subTest(offset=offset,relief=relief):
                    recipe = ToolRecipe("offset",height=150,width=45,shank_width=20,
                                        relief_depth=18,relief_height=30,relief_start=5,
                                        relief_side=relief,punch_offset=offset)
                    contour,dims = drawing_model(recipe)
                    arc = next(e for e in contour.segments if isinstance(e,ArcSegment2D))
                    self.assertEqual(arc.center,(0,recipe.radius))
                    self.assertAlmostEqual(arc.midpoint()[0],0,places=8)
                    self.assertAlmostEqual(arc.midpoint()[1],0,places=8)
                    self.assertAlmostEqual(contour.bbox()[0],offset-recipe.width/2)
                    self.assertAlmostEqual(contour.bbox()[2],offset+recipe.width/2)
                    dim = next(d for d in dims if d.key=="punch_offset")
                    self.assertEqual(dim.end[0],offset)
                    self.assertEqual(dim.end[1],recipe.height-recipe.shank_height)
                    shank = next(d for d in dims if d.key=="shank_width")
                    self.assertEqual(shank.start,(-recipe.shank_width/2,recipe.height))
                    self.assertEqual(shank.end,(recipe.shank_width/2,recipe.height))
                    top = [e for e in contour.segments
                           if abs(e.start[1]-recipe.height)<1e-8 and abs(e.end[1]-recipe.height)<1e-8]
                    self.assertEqual(len(top),1)
                    self.assertAlmostEqual(min(top[0].start[0],top[0].end[0]),-recipe.shank_width/2)
                    self.assertAlmostEqual(max(top[0].start[0],top[0].end[0]),recipe.shank_width/2)
                    meta = tooling_metadata(recipe)
                    self.assertEqual(meta["datums"]["tip_point"]["position"],[0,0,0])
                    self.assertEqual(meta["datums"]["mounting_plane"]["origin"],[0,recipe.height,0])
                    self.assertEqual(meta["metadata"]["shank_center_x_mm"],0.0)
                    with TemporaryDirectory() as tmp:
                        path=Path(tmp)/"punch.dxf"
                        path.write_text(dxf_text(contour),encoding="ascii")
                        profile=import_tool_from_dxf(path,"punch")
                        self.assertEqual(profile.validation_status,ValidationStatus.GEOMETRY_VERIFIED)
                        self.assertAlmostEqual(profile.punch_datums.working_tip[0],0,places=5)
                        self.assertAlmostEqual(profile.punch_datums.working_tip[1],0,places=5)
                        ml,mr=profile.punch_datums.mounting_base
                        self.assertAlmostEqual(ml[0],-recipe.shank_width/2,places=5)
                        self.assertAlmostEqual(mr[0],recipe.shank_width/2,places=5)
                        self.assertAlmostEqual(ml[1],recipe.height,places=5)
                        self.assertAlmostEqual(mr[1],recipe.height,places=5)
                        self.assertAlmostEqual(float(profile.punch_datums.included_angle_deg),recipe.angle,places=3)
                        self.assertAlmostEqual(float(profile.tip_radius_mm),recipe.radius,places=5)
        for offset in (-23,23,float("nan")):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                build_contour(ToolRecipe("invalid",punch_offset=offset))

    def test_centered_shank_with_overhang_and_equal_width(self):
        for width,shank,offset in ((40,20,14),(40,20,-14),(40,40,5),(40,40,-5),(40,40,0)):
            with self.subTest(width=width,shank=shank,offset=offset):
                recipe=ToolRecipe("centered",width=width,shank_width=shank,
                                  height=150,punch_offset=offset)
                contour=build_contour(recipe)
                self.assertTrue(contour.is_closed(1e-7))
                with TemporaryDirectory() as tmp:
                    path=Path(tmp)/"centered.dxf"
                    path.write_text(dxf_text(contour),encoding="ascii")
                    profile=import_tool_from_dxf(path,"punch")
                    self.assertEqual(profile.validation_status,ValidationStatus.GEOMETRY_VERIFIED)
                    ml,mr=profile.punch_datums.mounting_base
                    self.assertAlmostEqual(ml[0],-shank/2,places=5)
                    self.assertAlmostEqual(mr[0],shank/2,places=5)
