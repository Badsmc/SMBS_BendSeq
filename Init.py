"""Temporary legacy entrypoint pending actual FreeCAD 1.1 acceptance tests.

All code lives in the native package; never modifies the import search path.
"""
from freecad.bendseq import enabled
if enabled():
    from freecad.bendseq import headless
