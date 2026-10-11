import FreeCAD, Part
b = Part.makeBox(10,10,10)
c = Part.makeCylinder(5,20)
s = b.fuse(c)
print('Faces:', len(s.Faces))
for f in s.Faces:
    print('Surface:', type(f.Surface).__name__, getattr(f.Surface, 'TypeId', 'NoTypeId'))
