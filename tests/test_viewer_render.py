"""
test_viewer_render.py - Tests for S.15a HLR offscreen rendering via PIL.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smbs_core.viewer.render import (
    BANNER_TEXT,
    RENDER_KIND_BBOX_FALLBACK,
    RENDER_KIND_HLR,
    RenderError,
    render_frame_png,
)


class TestViewerRender(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="test_render_"))

    def _make_box(self, dx: float = 10.0, dy: float = 20.0, dz: float = 30.0):
        try:
            from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
            return BRepPrimAPI_MakeBox(dx, dy, dz).Shape()
        except ImportError:
            return None

    def test_render_frame_png_real_box(self):
        shape = self._make_box()
        if shape is None:
            self.skipTest("OCC not available")

        out_png = self.tmp_dir / "box.png"
        rk = render_frame_png(
            shape,
            out_png,
            title="step 0 (initial)",
            subtitle="planner=SUCCESS | coll=CLEAR | VERIFIED"
        )
        self.assertTrue(out_png.is_file())
        self.assertGreater(out_png.stat().st_size, 2048)
        self.assertEqual(rk, RENDER_KIND_HLR)

    def test_render_frame_png_dimensions(self):
        shape = self._make_box()
        if shape is None:
            self.skipTest("OCC not available")

        from PIL import Image
        out_png = self.tmp_dir / "dim.png"
        render_frame_png(shape, out_png, width=640, height=480, title="step 0")
        with Image.open(out_png) as img:
            self.assertEqual(img.size, (640, 480))

    def test_render_frame_png_banner_present(self):
        shape = self._make_box()
        if shape is None:
            self.skipTest("OCC not available")

        from PIL import Image
        out_png = self.tmp_dir / "banner.png"
        render_frame_png(shape, out_png, width=800, height=600, title="test banner")
        with Image.open(out_png) as img:
            # Check a pixel inside the top red banner (width // 2, 10)
            r, g, b = img.getpixel((400, 10))[:3]
            self.assertGreater(r, 150)
            self.assertLess(g, 100)
            self.assertLess(b, 100)

    def test_render_frame_png_banner_valid_per_panel(self):
        shape = self._make_box()
        if shape is None:
            self.skipTest("OCC not available")

        from PIL import Image
        out_png = self.tmp_dir / "banner_valid.png"
        render_frame_png(
            shape,
            out_png,
            width=800,
            height=600,
            title="test banner valid",
            geometry_precision="VALID_PER_PANEL",
        )
        with Image.open(out_png) as img:
            # Check a pixel inside the top green banner (width // 2, 10)
            r, g, b = img.getpixel((400, 10))[:3]
            self.assertLess(r, 100)
            self.assertGreater(g, 100)
            self.assertLess(b, 100)

    def test_render_frame_png_empty_shape_raises(self):
        out_png = self.tmp_dir / "err.png"
        with self.assertRaises(RenderError):
            render_frame_png(None, out_png, title="empty")

    def test_render_frame_png_null_shape_raises(self):
        try:
            from OCC.Core.TopoDS import TopoDS_Shape
        except ImportError:
            self.skipTest("OCC not available")

        out_png = self.tmp_dir / "null.png"
        null_shape = TopoDS_Shape()
        with self.assertRaises(RenderError):
            render_frame_png(null_shape, out_png, title="null")

    def test_render_frame_png_degenerate_bbox(self):
        try:
            from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
            from OCC.Core.gp import gp_Pnt
            vertex_shape = BRepBuilderAPI_MakeVertex(gp_Pnt(0, 0, 0)).Shape()
        except ImportError:
            self.skipTest("OCC not available")

        out_png = self.tmp_dir / "degen.png"
        with self.assertRaises(RenderError) as ctx:
            render_frame_png(vertex_shape, out_png, title="degen")
        self.assertIn("degenerate", str(ctx.exception).lower())

    def test_render_frame_png_return_kind_hlr(self):
        shape = self._make_box()
        if shape is None:
            self.skipTest("OCC not available")

        out_png = self.tmp_dir / "kind_hlr.png"
        rk = render_frame_png(shape, out_png, title="hlr")
        self.assertEqual(rk, RENDER_KIND_HLR)

    def test_render_frame_png_bbox_fallback(self):
        shape = self._make_box()
        if shape is None:
            self.skipTest("OCC not available")

        out_png = self.tmp_dir / "fallback.png"
        with patch("smbs_core.viewer.render._render_hlr", side_effect=RuntimeError("Forced HLR failure")):
            rk = render_frame_png(shape, out_png, title="fallback")
            self.assertEqual(rk, RENDER_KIND_BBOX_FALLBACK)
            self.assertTrue(out_png.is_file())

    def test_render_frame_png_default_font_works(self):
        shape = self._make_box()
        if shape is None:
            self.skipTest("OCC not available")

        out_png = self.tmp_dir / "font.png"
        rk = render_frame_png(
            shape,
            out_png,
            title="Step 1: Bend B1 [UP]",
            subtitle="status=VALID | collision=CLEAR | tooling=OK"
        )
        self.assertTrue(out_png.is_file())

    def test_render_frame_png_file_extension_png(self):
        shape = self._make_box()
        if shape is None:
            self.skipTest("OCC not available")

        from PIL import Image
        out_png = self.tmp_dir / "test_format.png"
        render_frame_png(shape, out_png, title="format")
        with Image.open(out_png) as img:
            self.assertEqual(img.format, "PNG")

    def test_render_frame_png_visible_skipped_raises(self):
        shape = self._make_box()
        if shape is None:
            self.skipTest("OCC not available")

        out_png = self.tmp_dir / "vis_skip.png"
        # Mock _render_hlr returning vis_skipped=3
        fake_vis = [[(0.0, 0.0), (1.0, 1.0)]]
        fake_hid = []
        with patch("smbs_core.viewer.render._render_hlr", return_value=(fake_vis, fake_hid, 3, 0)):
            with self.assertRaises(RenderError) as ctx:
                render_frame_png(shape, out_png, title="vis_skip")
            self.assertIn("visible edges failed to discretize", str(ctx.exception))

    def test_render_frame_png_hidden_skipped_in_subtitle(self):
        shape = self._make_box()
        if shape is None:
            self.skipTest("OCC not available")

        out_png = self.tmp_dir / "hid_skip.png"
        fake_vis = [[(0.0, 0.0), (10.0, 10.0)]]
        fake_hid = []
        captured_texts = []

        from PIL.ImageDraw import ImageDraw
        orig_text = ImageDraw.text

        def spy_text(draw_self, xy, text, *args, **kwargs):
            captured_texts.append(text)
            return orig_text(draw_self, xy, text, *args, **kwargs)

        with patch("smbs_core.viewer.render._render_hlr", return_value=(fake_vis, fake_hid, 0, 2)):
            with patch.object(ImageDraw, "text", side_effect=spy_text, autospec=True):
                rk = render_frame_png(shape, out_png, title="Frame 1", subtitle="status=OK")
                self.assertEqual(rk, RENDER_KIND_HLR)
                self.assertTrue(out_png.is_file())
                # Verify that subtitle includes the dropped edges notice
                has_notice = any("[2 hidden edges dropped]" in t for t in captured_texts)
                self.assertTrue(has_notice, f"Expected hidden edges dropped notice in captured texts: {captured_texts}")


if __name__ == "__main__":
    unittest.main()
