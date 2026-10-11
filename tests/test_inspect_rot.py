import FreeCAD, Part
b = Part.makeBox(10,10,10)
rot = FreeCAD.Rotation(FreeCAD.Vector(0,0,1), 90)
p = FreeCAD.Placement(FreeCAD.Vector(0,0,0), rot, FreeCAD.Vector(0,0,0))
b2 = b.copy()
b2.Placement = p.multiply(b2.Placement)
print("Orig CM:", b.CenterOfMass.x, b.CenterOfMass.y, b.CenterOfMass.z)
print("Rot CM:", b2.CenterOfMass.x, b2.CenterOfMass.y, b2.CenterOfMass.z)
