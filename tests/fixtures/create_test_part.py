"""
create_test_part.py - Procedural generator for HandMod Phase 1 test fixtures.

Generates 3 canonical regression models:
1. tests/fixtures/test_box.FCStd:
   Box / U-channel with 3 bends, unbent sheet, and unfolded flat pattern.
   Used for testing basic valid sequence calculation from scratch without prior pipeline runs.
2. tests/fixtures/test_pentagon.FCStd:
   Complex multi-bend part with 5 sequential bends, testing order constraints and collision detection.
3. tests/fixtures/test_mirror.FCStd:
   Part with 3 bends plus an inverted / mirrored TechDraw 2D projection view, testing alignment & Kabsch.

Idempotent: Re-running safely updates and verifies the fixtures without error.
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

# Ensure project root and FreeCAD bootstrap are loaded
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from smbs_core.geometry_backend.freecad_bootstrap import ensure_freecad_paths
ensure_freecad_paths()

import FreeCAD as App
import Part


FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
FIXTURES_DIR.mkdir(parents=True, exist_ok=True)


def create_box_3bend_shape(
    thickness: float = 1.5,
    radius: float = 1.0,
    base_w: float = 100.0,
    base_l: float = 80.0,
    flange_h: float = 30.0,
) -> Part.Shape:
    """
    Creates a 3D sheet metal U/box shape with 3 bends (two parallel side flanges + one rear flange).
    All bends have radius=radius, thickness=thickness, angle=90.0 deg.
    """
    base = Part.makeBox(base_w, base_l, thickness)

    # Flange 1: along X-axis at Y=0, bent UP (+Z)
    c1 = Part.makeCylinder(radius + thickness, base_w)
    c1.rotate(App.Vector(0, 0, 0), App.Vector(0, 1, 0), -90)
    c1.translate(App.Vector(base_w, 0, thickness))
    f1 = Part.makeBox(base_w, flange_h, thickness)
    f1.rotate(App.Vector(0, 0, 0), App.Vector(1, 0, 0), 90)
    f1.translate(App.Vector(0, 0, thickness))

    # Flange 2: along X-axis at Y=base_l, bent UP (+Z)
    c2 = Part.makeCylinder(radius + thickness, base_w)
    c2.rotate(App.Vector(0, 0, 0), App.Vector(0, 1, 0), -90)
    c2.translate(App.Vector(base_w, base_l, thickness))
    f2 = Part.makeBox(base_w, flange_h, thickness)
    f2.rotate(App.Vector(0, 0, 0), App.Vector(1, 0, 0), -90)
    f2.translate(App.Vector(0, base_l, thickness))

    # Flange 3: along Y-axis at X=0, bent UP (+Z)
    c3 = Part.makeCylinder(radius + thickness, base_l)
    c3.rotate(App.Vector(0, 0, 0), App.Vector(1, 0, 0), 90)
    c3.translate(App.Vector(0, base_l, thickness))
    f3 = Part.makeBox(flange_h, base_l, thickness)
    f3.rotate(App.Vector(0, 0, 0), App.Vector(0, 1, 0), -90)
    f3.translate(App.Vector(0, 0, thickness))

    shape = base.fuse(c1).fuse(f1).fuse(c2).fuse(f2).fuse(c3).fuse(f3)
    shape = shape.removeSplitter()
    return shape


def create_box_unfold_shape(
    thickness: float = 1.5,
    base_w: float = 100.0,
    base_l: float = 80.0,
    flange_h: float = 30.0,
) -> Part.Shape:
    """
    Creates the true unfolded 2D sheet blank with 8 outer vertices
    and 3 internal bend lines on the top face (Z=thickness) for selection.
    """
    import BOPTools.SplitAPI
    b_center = Part.makeBox(base_w, base_l, thickness)
    b_f1 = Part.makeBox(base_w, flange_h, thickness)
    b_f1.translate(App.Vector(0, -flange_h, 0))
    b_f2 = Part.makeBox(base_w, flange_h, thickness)
    b_f2.translate(App.Vector(0, base_l, 0))
    b_f3 = Part.makeBox(flange_h, base_l, thickness)
    b_f3.translate(App.Vector(-flange_h, 0, 0))
    cross = b_center.fuse(b_f1).fuse(b_f2).fuse(b_f3).removeSplitter()

    # 3 bend line edges on top surface (Z=thickness)
    l1 = Part.makeLine((0, 0, thickness), (base_w, 0, thickness))
    l2 = Part.makeLine((0, base_l, thickness), (base_w, base_l, thickness))
    l3 = Part.makeLine((0, 0, thickness), (0, base_l, thickness))

    sliced = BOPTools.SplitAPI.slice(cross, [l1, l2, l3], "Split")
    return sliced


def create_pentagon_5bend_shape(
    thickness: float = 1.5,
    radius: float = 1.0,
) -> Part.Shape:
    """
    Creates a sheet metal shape with 5 distinct bends:
    - Base panel (120 x 80)
    - Bend 1 & 2: left & right flanges (90 deg)
    - Bend 3: rear flange (90 deg)
    - Bend 4 & 5: upper returns / lips on side flanges (90 deg inward)
    """
    base_w, base_l = 120.0, 80.0
    h1, h2 = 40.0, 20.0

    base = Part.makeBox(base_w, base_l, thickness)

    # Flange 1 (+Y edge, bent +Z)
    c1 = Part.makeCylinder(radius + thickness, base_w)
    c1.rotate(App.Vector(0, 0, 0), App.Vector(0, 1, 0), -90)
    c1.translate(App.Vector(base_w, base_l, thickness))
    f1 = Part.makeBox(base_w, h1, thickness)
    f1.rotate(App.Vector(0, 0, 0), App.Vector(1, 0, 0), -90)
    f1.translate(App.Vector(0, base_l, thickness))

    # Flange 2 (-Y edge, bent +Z)
    c2 = Part.makeCylinder(radius + thickness, base_w)
    c2.rotate(App.Vector(0, 0, 0), App.Vector(0, 1, 0), -90)
    c2.translate(App.Vector(base_w, 0, thickness))
    f2 = Part.makeBox(base_w, h1, thickness)
    f2.rotate(App.Vector(0, 0, 0), App.Vector(1, 0, 0), 90)
    f2.translate(App.Vector(0, 0, thickness))

    # Flange 3 (-X edge, bent +Z)
    c3 = Part.makeCylinder(radius + thickness, base_l)
    c3.rotate(App.Vector(0, 0, 0), App.Vector(1, 0, 0), 90)
    c3.translate(App.Vector(0, base_l, thickness))
    f3 = Part.makeBox(h1, base_l, thickness)
    f3.rotate(App.Vector(0, 0, 0), App.Vector(0, 1, 0), -90)
    f3.translate(App.Vector(0, 0, thickness))

    # Lip 4: on top of Flange 1 (-Z), bent inward (-Y)
    c4 = Part.makeCylinder(radius + thickness, base_w)
    c4.rotate(App.Vector(0, 0, 0), App.Vector(0, 1, 0), -90)
    c4.translate(App.Vector(base_w, base_l, thickness - h1))
    f4 = Part.makeBox(base_w, h2, thickness)
    f4.translate(App.Vector(0, base_l - h2, thickness - h1 - thickness))

    # Lip 5: on top of Flange 2 (+Z), bent inward (+Y)
    c5 = Part.makeCylinder(radius + thickness, base_w)
    c5.rotate(App.Vector(0, 0, 0), App.Vector(0, 1, 0), -90)
    c5.translate(App.Vector(base_w, 0, thickness + h1))
    f5 = Part.makeBox(base_w, h2, thickness)
    f5.translate(App.Vector(0, 0, thickness + h1))

    shape = base.fuse(c1).fuse(f1).fuse(c2).fuse(f2).fuse(c3).fuse(f3).fuse(c4).fuse(f4).fuse(c5).fuse(f5)
    shape = shape.removeSplitter()
    return shape


def create_pentagon_unfold_shape(
    thickness: float = 1.5,
    base_w: float = 120.0,
    base_l: float = 80.0,
    h1: float = 40.0,
    h2: float = 20.0,
) -> Part.Shape:
    """Creates true unfolded blank for 5-bend part with 5 internal bend lines on top face."""
    import BOPTools.SplitAPI
    b_center = Part.makeBox(base_w, base_l, thickness)
    b_f1 = Part.makeBox(base_w, h1 + h2, thickness)
    b_f1.translate(App.Vector(0, base_l, 0))
    b_f2 = Part.makeBox(base_w, h1 + h2, thickness)
    b_f2.translate(App.Vector(0, -(h1 + h2), 0))
    b_f3 = Part.makeBox(h1, base_l, thickness)
    b_f3.translate(App.Vector(-h1, 0, 0))
    shape = b_center.fuse(b_f1).fuse(b_f2).fuse(b_f3).removeSplitter()

    # 5 bend lines on top surface (Z=thickness)
    l1 = Part.makeLine((0, base_l, thickness), (base_w, base_l, thickness))
    l2 = Part.makeLine((0, 0, thickness), (base_w, 0, thickness))
    l3 = Part.makeLine((0, 0, thickness), (0, base_l, thickness))
    l4 = Part.makeLine((0, base_l + h1, thickness), (base_w, base_l + h1, thickness))
    l5 = Part.makeLine((0, -h1, thickness), (base_w, -h1, thickness))

    sliced = BOPTools.SplitAPI.slice(shape, [l1, l2, l3, l4, l5], "Split")
    return sliced


def create_test_box_fcstd(target_path: Path) -> Path:
    """Creates tests/fixtures/test_box.FCStd."""
    doc = App.newDocument("test_box")
    shape = create_box_3bend_shape(thickness=1.5, radius=1.0)
    
    body = doc.addObject("Part::Feature", "Body")
    body.Label = "TestBoxBody"
    body.Shape = shape

    unfold_shape = create_box_unfold_shape(thickness=1.5)
    unfold_obj = doc.addObject("Part::Feature", "Unfold")
    unfold_obj.Label = "Unfold_3Bend"
    unfold_obj.Shape = unfold_shape

    # Add TechDraw page with top view of Unfold (8 vertices)
    try:
        import TechDraw
        page = doc.addObject("TechDraw::DrawPage", "Page")
        template = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
        page.Template = template
        view = doc.addObject("TechDraw::DrawViewPart", "View")
        view.Source = [unfold_obj]
        view.Direction = App.Vector(0, 0, 1)
        view.Scale = 1.0
        page.addView(view)
    except Exception as e:
        print(f"TechDraw note: {e}")

    doc.recompute()
    doc.saveAs(str(target_path))
    App.closeDocument("test_box")
    return target_path


def create_test_pentagon_fcstd(target_path: Path) -> Path:
    """Creates tests/fixtures/test_pentagon.FCStd."""
    doc = App.newDocument("test_pentagon")
    shape = create_pentagon_5bend_shape(thickness=1.5, radius=1.0)
    
    body = doc.addObject("Part::Feature", "Body")
    body.Label = "TestPentagonBody"
    body.Shape = shape

    unfold_shape = create_pentagon_unfold_shape(thickness=1.5)
    unfold_obj = doc.addObject("Part::Feature", "Unfold")
    unfold_obj.Label = "Unfold_5Bend"
    unfold_obj.Shape = unfold_shape

    try:
        import TechDraw
        page = doc.addObject("TechDraw::DrawPage", "Page")
        template = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
        page.Template = template
        view = doc.addObject("TechDraw::DrawViewPart", "View")
        view.Source = [unfold_obj]
        view.Direction = App.Vector(0, 0, 1)
        view.Scale = 1.0
        page.addView(view)
    except Exception as e:
        print(f"TechDraw note: {e}")

    doc.recompute()
    doc.saveAs(str(target_path))
    App.closeDocument("test_pentagon")
    return target_path


def create_test_mirror_fcstd(target_path: Path) -> Path:
    """Creates tests/fixtures/test_mirror.FCStd with inverted/mirrored TechDraw projection."""
    doc = App.newDocument("test_mirror")
    shape = create_box_3bend_shape(thickness=1.5, radius=1.0)
    
    body = doc.addObject("Part::Feature", "Body")
    body.Label = "TestMirrorBody"
    body.Shape = shape

    unfold_shape = create_box_unfold_shape(thickness=1.5)
    unfold_obj = doc.addObject("Part::Feature", "Unfold")
    unfold_obj.Label = "Unfold_Mirror"
    unfold_obj.Shape = unfold_shape

    # Add TechDraw page with mirrored / flipped view projection
    try:
        import TechDraw
        page = doc.addObject("TechDraw::DrawPage", "Page")
        template = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
        page.Template = template
        view = doc.addObject("TechDraw::DrawViewPart", "View")
        view.Source = [unfold_obj]
        # Invert direction (0, 0, -1) to simulate mirrored / flipped bottom projection
        view.Direction = App.Vector(0, 0, -1)
        view.Scale = 1.0
        page.addView(view)
    except Exception as e:
        print(f"TechDraw note: {e}")

    doc.recompute()
    doc.saveAs(str(target_path))
    App.closeDocument("test_mirror")
    return target_path


def generate_all_fixtures() -> dict[str, Path]:
    paths = {
        "test_box": create_test_box_fcstd(FIXTURES_DIR / "test_box.FCStd"),
        "test_pentagon": create_test_pentagon_fcstd(FIXTURES_DIR / "test_pentagon.FCStd"),
        "test_mirror": create_test_mirror_fcstd(FIXTURES_DIR / "test_mirror.FCStd"),
    }
    return paths


if __name__ == "__main__":
    generated = generate_all_fixtures()
    print("Successfully generated all HandMod fixtures:")
    for name, path in generated.items():
        print(f" - {name}: {path} ({path.stat().st_size} bytes)")
