from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from freecad.bendseq.core.kinematics2d.tooling_25d import ToolLibrary25D, ToolKind
from freecad.bendseq.core.geometry_backend.io.tooling_library import ToolingLibrary


class EmptyToolLibraryTest(unittest.TestCase):
    def test_empty_library_can_be_initialized_and_listed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            profiles = ToolLibrary25D(root)
            self.assertEqual(tuple(profiles.list_tools(ToolKind.PUNCH)), ())
            self.assertEqual(tuple(profiles.list_tools(ToolKind.DIE)), ())
            library = ToolingLibrary(root)
            catalog = library.list_tools()
            self.assertFalse(catalog.punches)
            self.assertFalse(catalog.dies)
