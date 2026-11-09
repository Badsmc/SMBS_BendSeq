"""Physical native tooling section regression: real CAD loader spans, no virtual tools."""
import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.verified_tool_spans import _verified_sections
from smbs_core.kinematics2d.tooling_25d import ToolingSetup2D


def loaded_sections(spans, offsets):
    secs = tuple(SimpleNamespace(span_xm=p, offset_xm=x) for p, x in zip(spans, offsets))
    return SimpleNamespace(sections=secs, span_xm=(
        min(s[0] for s in spans), max(s[1] for s in spans)
    ))


class NativeSectionAssemblyTests(unittest.TestCase):
    def test_two_real_100mm_segments_cover_200mm(self):
        offsets = (-50.0, 50.0)
        tool = loaded_sections(((-100.0, 0.0), (0.0, 100.0)), offsets)
        self.assertEqual(
            _verified_sections(tool, "PUNCH", offsets),
            ((-100.0, 0.0), (0.0, 100.0)),
        )

    def test_refuse_physical_gap(self):
        tool = loaded_sections(((-101.0, -1.0), (1.0, 101.0)), (-51.0, 51.0))
        with self.assertRaisesRegex(ValueError, "coverage failed"):
            _verified_sections(tool, "DIE", (-51.0, 51.0))

    def test_refuse_overlap(self):
        tool = loaded_sections(((-99.0, 1.0), (-1.0, 99.0)), (-49.0, 49.0))
        with self.assertRaisesRegex(ValueError, "overlapping"):
            _verified_sections(tool, "PUNCH", (-49.0, 49.0))

    def test_refuse_missing_section(self):
        tool = loaded_sections(((-50.0, 50.0),), (0.0,))
        with self.assertRaisesRegex(ValueError, "expected 2"):
            _verified_sections(tool, "PUNCH", (-50.0, 50.0))

    def test_tooling_setup_explicit_and_not_virtual(self):
        setup = ToolingSetup2D()
        setup.set_native_sections((-50.0, 50.0), (-50.0, 50.0))
        self.assertEqual(setup.punch_section_offsets_xm, (-50.0, 50.0))
        self.assertEqual(setup.die_section_offsets_xm, (-50.0, 50.0))
        setup.set_match_active_bend_length(True, 200.0)
        with self.assertRaisesRegex(ValueError, "incompatible"):
            setup.set_native_sections((-50.0, 50.0), (-50.0, 50.0))

    def test_refuse_duplicate_and_single_offsets(self):
        setup = ToolingSetup2D()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            setup.set_native_sections((1.0, 1.0), ())
        with self.assertRaisesRegex(ValueError, "one offset"):
            setup.set_native_sections((0.0,), ())


if __name__ == "__main__":
    unittest.main()
