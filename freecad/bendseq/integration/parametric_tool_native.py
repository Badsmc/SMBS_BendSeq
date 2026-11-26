"""Generate exact FCStd solids using FreeCAD's native Part kernel."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from freecad.bendseq.core.kinematics2d.parametric_tool import build_contour, dxf_text, tooling_metadata
from freecad.bendseq.core.kinematics2d.tooling_25d import (
    ArcSegment2D, ToolLibrary25D, ValidationStatus, import_tool_from_dxf,
)


def create_tool(recipe, library_root):
    """Validate DXF and native solid before publishing a new tool, never overwrite."""
    import FreeCAD as App
    import Part

    contour = build_contour(recipe)
    directory = Path(library_root) / recipe.kind
    directory.mkdir(parents=True, exist_ok=True)
    names = [recipe.tool_id + suffix for suffix in
             (".FCStd", ".dxf", ".tooling.json", ".profile25d.json")]
    existing = {p.name.casefold() for p in directory.iterdir()}
    if any(name.casefold() in existing for name in names):
        raise FileExistsError("A tool with this ID already exists; choose a new ID")

    previous = getattr(getattr(App, "ActiveDocument", None), "Name", None)
    vector = lambda p: App.Vector(p[0], p[1], 0)
    published = []
    with TemporaryDirectory(prefix="smbs-tool-", dir=directory) as tmp:
        stage = Path(tmp)
        dxf = stage / names[1]
        dxf.write_text(dxf_text(contour), encoding="ascii")
        profile = import_tool_from_dxf(dxf, recipe.kind, tool_id=recipe.tool_id,
                                       length_mm=recipe.length)
        if profile.validation_status != ValidationStatus.GEOMETRY_VERIFIED:
            raise ValueError("Generated DXF failed geometric datum validation")
        edges = []
        for edge in contour.segments:
            if isinstance(edge, ArcSegment2D):
                edges.append(Part.Arc(vector(edge.start), vector(edge.midpoint()),
                                      vector(edge.end)).toShape())
            else:
                edges.append(Part.makeLine(vector(edge.start), vector(edge.end)))
        wire = Part.Wire(edges)
        if not wire.isClosed():
            raise ValueError("Generated FreeCAD wire is open")
        face = Part.Face(wire)
        shape = face.extrude(App.Vector(0,0,recipe.length))
        if not shape.isValid() or len(shape.Solids) != 1 or shape.Volume <= 0:
            raise ValueError("Generated FreeCAD solid failed validation")
        doc = App.newDocument("SMBSGeneratedTool")
        try:
            obj = doc.addObject("Part::Feature", "Body")
            obj.Label = recipe.tool_id
            obj.Shape = shape
            obj.addProperty("App::PropertyString", "SMBS_Role", "SMBS")
            obj.SMBS_Role = recipe.kind
            obj.addProperty("App::PropertyString", "SMBS_Recipe", "SMBS")
            obj.SMBS_Recipe = json.dumps(asdict(recipe), sort_keys=True)
            doc.recompute()
            doc.saveAs(str(stage / names[0]))
        finally:
            App.closeDocument(doc.Name)
            if previous and previous in App.listDocuments():
                App.setActiveDocument(previous)
        # Use the same FCStd extraction path as the existing library.
        verified = ToolLibrary25D(library_root)._load_legacy_fcstd_tool(
            stage / names[0], profile.kind)
        if verified.validation_status != ValidationStatus.GEOMETRY_VERIFIED:
            raise ValueError("Generated FCStd failed the SMBS native profile check")
        if recipe.kind == "punch":
            if abs(float(verified.punch_datums.included_angle_deg) - recipe.angle) > 0.1 or abs(float(verified.tip_radius_mm) - recipe.radius) > 0.05:
                raise ValueError("Generated FCStd punch dimensions do not match the recipe")
            for checked in (profile, verified):
                datums = checked.punch_datums
                left, right = datums.mounting_base
                if (abs(datums.working_tip[0]) > 0.05
                        or abs(datums.working_tip[1]) > 0.05
                        or abs(left[0]+recipe.shank_width/2) > 0.05
                        or abs(right[0]-recipe.shank_width/2) > 0.05
                        or abs(left[1]-recipe.height) > 0.05
                        or abs(right[1]-recipe.height) > 0.05):
                    raise ValueError("Generated punch tip or mounting shank is not centered")
        else:
            if abs(float(verified.v_opening_mm) - recipe.opening) > 0.05 or abs(float(verified.die_angle_deg) - recipe.angle) > 0.1:
                raise ValueError("Generated FCStd die dimensions do not match the recipe")
        (stage / names[2]).write_text(json.dumps(tooling_metadata(recipe), indent=2)+"\n",
                                     encoding="utf-8")
        # Exclusive creation: publish sidecar and DXF first, FCStd last.
        try:
            for name in (names[1], names[2], names[0]):
                target = directory / name
                with target.open("xb") as output:
                    published.append(target)
                    output.write((stage / name).read_bytes())
        except Exception:
            for target in published:
                target.unlink(missing_ok=True)
            raise
    return directory / names[0]
