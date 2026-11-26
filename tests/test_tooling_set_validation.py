import tempfile
import unittest
from pathlib import Path
from freecad.bendseq.core.geometry_backend.io.tooling_set import (
    ToolingSetError, load_set_file, save_set_file, validate_set_dict,
)


class ToolingSetValidationTest(unittest.TestCase):
    def setUp(self):
        self.data = dict(schema_version="1.0", set_id="test", punch_id="P",
                         die_id="D", clamp_positions_mm=[25, 10])

    def test_roundtrip_without_external_dependencies(self):
        obj = validate_set_dict(self.data)
        self.assertEqual(obj.clamp_positions_mm, (10.0, 25.0))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.json"
            save_set_file(path, obj)
            self.assertEqual(load_set_file(path), obj)

    def test_reject_invalid_schema(self):
        for update in ({"schema_version": "2"}, {"set_id": ""},
                       {"metadata": []}, {"unknown": True},
                       {"clamp_positions_mm": [True]},
                       {"clamp_positions_mm": [float("inf")]},
                       {"clamp_positions_mm": "12"}):
            with self.subTest(update=update), self.assertRaises(ToolingSetError):
                validate_set_dict(dict(self.data, **update))
