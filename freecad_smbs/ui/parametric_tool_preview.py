"""Live dimensioned drawing; uses the same contour as the FCStd/DXF exporter."""
from __future__ import annotations

from math import hypot


def make_preview(QtCore, QtGui, QtWidgets, parent=None):
    class Preview(QtWidgets.QWidget):
        def __init__(self):
            super().__init__(parent)
            self.setMinimumSize(370, 340)
            self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
            self.model = None
            self.active_key = ""

        def set_model(self, model, active_key=""):
            self.model = model
            self.active_key = active_key
            self.update()

        def paintEvent(self, event):
            painter = QtGui.QPainter(self)
            try:
                self._draw(painter)
            finally:
                painter.end()

        def _draw(self, p):
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            palette = self.palette()
            p.fillRect(self.rect(), palette.base())
            if self.model is None:
                return
            contour, dimensions = self.model
            x0,y0,x1,y1 = contour.bbox()
            # Keep annotation lanes outside the fitted profile. Use one scale for X/Y.
            margin_x = min(128.0, self.width()*0.29)
            margin_y = min(85.0, self.height()*0.22)
            scale = min((self.width()-2*margin_x)/max(x1-x0,1e-6),
                        (self.height()-2*margin_y)/max(y1-y0,1e-6))
            if scale <= 0:
                return
            def pt(xy):
                return QtCore.QPointF(self.width()/2+(xy[0]-(x0+x1)/2)*scale,
                                      self.height()/2-(xy[1]-(y0+y1)/2)*scale)
            left, top = pt((x0,y1)).x(), pt((x0,y1)).y()
            right, bottom = pt((x1,y0)).x(), pt((x1,y0)).y()
            normal = palette.text().color()
            accent = palette.highlight().color()
            # A contrasting highlight works on both native FreeCAD themes.
            active = QtGui.QColor("#e4a11b")
            center_pen = QtGui.QPen(accent, 1, QtCore.Qt.DashLine)
            p.setPen(center_pen)
            axis_x = pt((0,0)).x()
            p.drawLine(QtCore.QPointF(axis_x,top-35),QtCore.QPointF(axis_x,bottom+35))
            path = QtGui.QPainterPath()
            polygon = contour.sample_polygon(arc_segments=64)
            path.moveTo(pt(polygon[0]))
            for xy in polygon[1:]:
                path.lineTo(pt(xy))
            path.closeSubpath()
            fill = QtGui.QColor(accent)
            fill.setAlpha(80)
            p.setBrush(fill)
            p.setPen(QtGui.QPen(accent,1.6))
            p.drawPath(path)
            p.setBrush(QtCore.Qt.NoBrush)
            font = p.font()
            font.setPointSizeF(max(8.0, min(10.0,font.pointSizeF())))
            p.setFont(font)

            def line(a,b):
                p.drawLine(a,b)

            def arrow(a,b):
                dx,dy = b.x()-a.x(),b.y()-a.y()
                length = hypot(dx,dy)
                if length < 0.001:
                    return
                ux,uy = dx/length,dy/length
                for sign in (-1,1):
                    line(a,QtCore.QPointF(a.x()+ux*6-sign*uy*3,
                                          a.y()+uy*6+sign*ux*3))

            def label(x,y,text,center=True):
                metrics = p.fontMetrics()
                width = metrics.horizontalAdvance(text)+8
                h = metrics.height()+4
                rect = QtCore.QRectF(x-width/2 if center else x,y-h/2,width,h)
                p.fillRect(rect,palette.base())
                p.drawText(rect,QtCore.Qt.AlignCenter,text)

            for dim in dimensions:
                selected = dim.key == self.active_key or (dim.key == "working_height" and self.active_key in ("height","shank_height"))
                p.setPen(QtGui.QPen(active if selected else normal, 1.8 if selected else 1))
                text = f"{dim.code} {dim.value:g}" + ("°" if dim.key=="angle" else "")
                a,b = pt(dim.start),pt(dim.end)
                if dim.axis == "h":
                    # Relief depth is an internal measurement; use a local lane.
                    if dim.key == "relief_depth":
                        y = a.y()+17
                    else:
                        y = (top if dim.lane<0 else bottom)+dim.lane*23
                    aa,bb = QtCore.QPointF(a.x(),y),QtCore.QPointF(b.x(),y)
                    line(a,aa); line(b,bb); line(aa,bb); arrow(aa,bb); arrow(bb,aa)
                    label((aa.x()+bb.x())/2,y-8,text)
                elif dim.axis == "v":
                    x = (left if dim.lane<0 else right)+dim.lane*33
                    aa,bb = QtCore.QPointF(x,a.y()),QtCore.QPointF(x,b.y())
                    line(a,aa); line(b,bb); line(aa,bb); arrow(aa,bb); arrow(bb,aa)
                    # Rotate label to follow a vertical dimension, saving side margin.
                    p.save()
                    p.translate(x+8,(aa.y()+bb.y())/2)
                    p.rotate(-90)
                    label(0,0,text)
                    p.restore()
                else:
                    x = left-22 if dim.lane<0 else right+22
                    y = min(bottom+47,max(top-45,a.y()+(-24 if dim.lane<0 else 24)))
                    bend = QtCore.QPointF(x,y)
                    line(a,bend); arrow(a,bend)
                    label(x,y-9,text)
    return Preview()
