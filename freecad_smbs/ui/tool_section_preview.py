"""Read and draw a transverse section of a saved tooling model."""
from xml.etree import ElementTree
from zipfile import ZipFile


def tool_section_rings(path, *, backgauge=False):
    """Return closed section outlines in the source profile plane."""
    import FreeCAD as App
    import Part

    with ZipFile(path) as archive:
        # Read the saved shape without opening a document or changing the active part.
        document = ElementTree.fromstring(archive.read("Document.xml"))
        types = {obj.get("name"): obj.get("type")
                 for obj in document.findall("./Objects/Object")}
        objects = document.findall("./ObjectData/Object")
        objects.sort(key=lambda obj: (
            types.get(obj.get("name")) != "PartDesign::Body",
            obj.get("name") not in ("Body", "Pad"),
        ))
        for obj in objects:
            saved = obj.find("./Properties/Property[@name='Shape']/Part")
            if saved is None or not saved.get("file"):
                continue
            shape = Part.Shape()
            shape.importBrepFromString(archive.read(saved.get("file")).decode("ascii"))
            if not shape.isNull() and shape.Solids:
                break
        else:
            raise ValueError("No saved solid in the FCStd file")

    bounds = shape.BoundBox
    if backgauge:
        # The backgauge side view retains the stop direction and vertical axis.
        wires = shape.slice(App.Vector(0, 1, 0), (bounds.YMin + bounds.YMax) / 2)
        rings = tuple(tuple((p.x, p.z) for p in wire.discretize(Deflection=0.05))
                      for wire in wires)
    else:
        # The library models extrude their punch/die profiles along CAD Z.
        wires = shape.slice(App.Vector(0, 0, 1), (bounds.ZMin + bounds.ZMax) / 2)
        rings = tuple(tuple((p.x, p.y) for p in wire.discretize(Deflection=0.05))
                      for wire in wires)
    if not rings:
        raise ValueError("The section plane does not intersect the solid")
    return rings


def draw_tool_section(rings, color, QtCore, QtGui, *, mirrored=False, opacity=None):
    """Fit the section to the preview, preserving scale and internal holes."""
    width, height, margin = 220, 145, 8
    path = QtGui.QPainterPath()
    path.setFillRule(QtCore.Qt.OddEvenFill)
    for ring in rings:
        points = [QtCore.QPointF(-x if mirrored else x, -y) for x, y in ring]
        path.addPolygon(QtGui.QPolygonF(points))
        path.closeSubpath()

    bounds = path.boundingRect()
    if bounds.isEmpty():
        raise ValueError("Empty section outline")
    scale = min((width - 2 * margin) / bounds.width(),
                (height - 2 * margin) / bounds.height())
    transform = QtGui.QTransform()
    transform.translate(width / 2, height / 2)
    transform.scale(scale, scale)
    transform.translate(-bounds.center().x(), -bounds.center().y())
    image = QtGui.QPixmap(width, height)
    image.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(image)
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        fill = QtGui.QColor(color)
        fill.setAlpha(45 if opacity is None else int(opacity))
        ink = QtGui.QColor(color)
        if opacity is not None:
            ink.setAlpha(int(opacity))
        painter.setBrush(fill)
        painter.setPen(QtGui.QPen(ink, 1.4))
        painter.drawPath(transform.map(path))
    finally:
        painter.end()
    return image
