"""D6.3 automated selection must never guess between multiple unfolds."""
import runpy
from pathlib import Path
from types import SimpleNamespace
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "run_d63_single_bend.py"
resolver = runpy.run_path(str(SCRIPT))["resolve_test_selection"]


def unfold(name):
    return SimpleNamespace(Name=name, baseObject=(None, []))


class D63SelectionTests(unittest.TestCase):
    def test_one_unfold_selected_automatically(self):
        u = unfold("Body_Unfold")
        selections, mode = resolver(SimpleNamespace(Objects=[u]), [])
        self.assertEqual(selections, [u])
        self.assertEqual(mode, "AUTO_UNIQUE_SHEETMETAL_UNFOLD")

    def test_explicit_selection_kept(self):
        a, b = unfold("Body_Unfold"), unfold("Body001_Unfold")
        selections, mode = resolver(
            SimpleNamespace(Objects=[a,b]), [b]
        )
        self.assertEqual(selections, [b])
        self.assertEqual(mode, "EXPLICIT_GUI_SELECTION")

    def test_multiple_unfolds_fail_closed(self):
        a, b = unfold("Body_Unfold"), unfold("Body001_Unfold")
        with self.assertRaisesRegex(ValueError, "Multiple SheetMetal"):
            resolver(SimpleNamespace(Objects=[a,b]), [])

    def test_no_open_document_fails(self):
        with self.assertRaisesRegex(ValueError, "No active"):
            resolver(None, [])

    def test_no_unfold_fails(self):
        with self.assertRaisesRegex(ValueError, "No SheetMetal"):
            resolver(SimpleNamespace(Objects=[]), [])


if __name__ == "__main__":
    unittest.main()
