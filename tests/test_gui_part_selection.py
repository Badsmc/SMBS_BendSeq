"""Headless tests for SheetMetal unfold toolbar activation."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from freecad_smbs.integration.part_session import (
    selected_unfold_name, is_unfold_object, SelectionWarning,
)


def object_for(name, document, **attributes):
    return SimpleNamespace(Name=name, Label=name, Document=document, **attributes)


class PartSelectionTests(unittest.TestCase):
    def setUp(self):
        self.doc = SimpleNamespace(Objects=[])
        self.source = object_for("Bend001", self.doc, Shape=object())
        self.unfold = object_for(
            "Body_Unfold", self.doc, baseObject=(self.source, ["Face4"])
        )
        self.doc.Objects = [self.source, self.unfold]

    def test_no_selection_disables_part(self):
        self.assertIsNone(selected_unfold_name(self.doc, []))

    def test_no_document_disables_part(self):
        self.assertIsNone(selected_unfold_name(None, [self.unfold]))

    def test_selected_unfold_enables_part(self):
        self.assertTrue(is_unfold_object(self.unfold))
        self.assertEqual(selected_unfold_name(self.doc, [self.unfold]), "Body_Unfold")

    def test_selected_source_object_resolves_unfold(self):
        self.assertEqual(selected_unfold_name(self.doc, [self.source]), "Body_Unfold")

    def test_bend_sketch_selection_resolves_unfold(self):
        sketch = object_for("Body_Unfold_Sketch_Bends", self.doc)
        self.assertEqual(selected_unfold_name(self.doc, [sketch]), "Body_Unfold")

    def test_other_document_selection_not_accepted(self):
        other = SimpleNamespace(Objects=[])
        self.assertIsNone(selected_unfold_name(other, [self.unfold]))

    def test_ambiguous_unfold_does_not_enable_part(self):
        other = object_for("Other_Unfold", self.doc, baseObject=(self.source, ["Face5"]))
        self.doc.Objects.append(other)
        self.assertIsNone(selected_unfold_name(self.doc, [self.source]))


if __name__ == "__main__":
    unittest.main()
