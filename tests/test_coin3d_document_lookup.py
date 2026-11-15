"""Guard missing-document lookup on FreeCAD macOS.

App.getDocument(name) raises for unknown names in some FreeCAD builds;
the Coin viewer must use the nonthrowing listDocuments mapping instead.
"""
import unittest

from freecad_smbs.ui.coin3d_viewer import _document_is_open


class _FreeCADMacOS:
    def __init__(self):
        self.documents = {"Part_01": object(), "SMBS_Animation_01": object()}

    def listDocuments(self):
        return dict(self.documents)

    def getDocument(self, name):
        if name not in self.documents:
            raise RuntimeError("Unknown document '{}'".format(name))
        return self.documents[name]


class TestCoinDocumentLookup(unittest.TestCase):
    def test_existing_document(self):
        app = _FreeCADMacOS()
        self.assertTrue(_document_is_open(app, "Part_01"))

    def test_missing_document_is_not_exception(self):
        app = _FreeCADMacOS()
        self.assertFalse(_document_is_open(app, "SMBS_Animation_02"))
        self.assertFalse(_document_is_open(app, None))

    def test_temporary_name_increments(self):
        app = _FreeCADMacOS()
        number = 1
        while _document_is_open(app, "SMBS_Animation_{:02d}".format(number)):
            number += 1
        self.assertEqual(number, 2)

    def test_closed_document_is_not_found(self):
        app = _FreeCADMacOS()
        del app.documents["SMBS_Animation_01"]
        self.assertFalse(_document_is_open(app, "SMBS_Animation_01"))


if __name__ == "__main__":
    unittest.main()
