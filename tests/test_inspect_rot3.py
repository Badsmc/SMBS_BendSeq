import FreeCAD, Part
b = Part.makeBox(10,10,10)
print("Orig CM:", b.CenterOfMass.x, b.CenterOfMass.y, b.CenterOfMass.z)
b2 = b.copy()
b2.rotate(FreeCAD.Vector(0,0,0), FreeCAD.Vector(0,0,1), 90)
print("b2.rotate(0,0,0) CM:", b2.CenterOfMass.x, b2.CenterOfMass.y, b2.CenterOfMass.z)
