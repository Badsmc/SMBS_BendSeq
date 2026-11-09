"""Source prism reuse must not relax FreeCAD geometry validity requirements."""
import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.native_bend_solid_3d import (
    _source_panel_prism, NativeBendSolidError,
)


class FakePrism:
    def __init__(self, valid=True):
        self.valid = valid
        self.Solids = [self]

    def isNull(self):
        return False

    def isValid(self):
        return self.valid


class FakeFace:
    def __init__(self):
        self.Area = 10
        self.valid = True
        self.extrusions = 0
        self.last_vector = None

    def isNull(self):
        return False

    def isValid(self):
        return self.valid

    def extrude(self, v):
        self.last_vector = v
        self.extrusions += 1
        return FakePrism()


class PanelPrismCacheTests(unittest.TestCase):
    def setUp(self):
        self.App = SimpleNamespace(Vector=lambda x, y, z: (x, y, z))

    def test_cache_reuses_same_source_prism(self):
        face = FakeFace()
        cache = {}
        first = _source_panel_prism("P01", face, 1.5, self.App, cache)
        second = _source_panel_prism("P01", face, 1.5, self.App, cache)
        self.assertIs(first, second)
        self.assertEqual(face.extrusions, 1)
        self.assertEqual(face.last_vector, (0, 0, -1.5))

    def test_uncached_keeps_existing_extrusion_per_call(self):
        face = FakeFace()
        _source_panel_prism("P01", face, 1.5, self.App)
        _source_panel_prism("P01", face, 1.5, self.App)
        self.assertEqual(face.extrusions, 2)

    def test_invalid_source_does_not_pass_on_cache_hit(self):
        face = FakeFace()
        cache = {}
        _source_panel_prism("P01", face, 1.5, self.App, cache)
        face.valid = False
        with self.assertRaises(NativeBendSolidError):
            _source_panel_prism("P01", face, 1.5, self.App, cache)

    def test_invalid_cached_prism_fails_closed(self):
        face = FakeFace()
        cache = {}
        prism = _source_panel_prism("P01", face, 1.5, self.App, cache)
        prism.valid = False
        with self.assertRaises(NativeBendSolidError):
            _source_panel_prism("P01", face, 1.5, self.App, cache)

    def test_independent_cache_per_validator(self):
        face = FakeFace()
        a, b = {}, {}
        _source_panel_prism("P01", face, 1.5, self.App, a)
        _source_panel_prism("P01", face, 1.5, self.App, b)
        self.assertEqual(face.extrusions, 2)


if __name__ == "__main__":
    unittest.main()
