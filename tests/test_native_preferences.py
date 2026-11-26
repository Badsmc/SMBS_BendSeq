"""Typed parameter migration regression without a FreeCAD installation."""
import unittest
from types import SimpleNamespace

from freecad_smbs.ui.native_preferences import NativePreferences, ROOT


class Group:
    def __init__(self):
        self.values = {}

    def GetContents(self):
        return [(kind, name, value) for (kind, name), value in self.values.items()]

    def __getattr__(self, method):
        action, kind = method[:3], method[3:]
        if action == "Get":
            return lambda name, default: self.values.get((kind, name), default)
        if action == "Set":
            return lambda name, value: self.values.__setitem__((kind, name), value)
        raise AttributeError(method)


class NativePreferencesTest(unittest.TestCase):
    def setUp(self):
        self.groups = {}
        self.legacy = {}
        self.reads = []
        def old_value(key, default, *, type):
            self.reads.append(key)
            return self.legacy.get(key, default)
        core = SimpleNamespace(QSettings=lambda *args: SimpleNamespace(value=old_value))
        self.prefs = NativePreferences(core, lambda path: self.groups.setdefault(path, Group()))

    def test_native_value_wins_over_legacy(self):
        group = self.groups.setdefault(ROOT + "/Diagnostics", Group())
        group.SetBool("Enabled", False)
        self.legacy["debug_log/enabled"] = True
        self.assertFalse(self.prefs.value("debug_log/enabled", True, type=bool))
        self.assertEqual(self.reads, [])
        # Native reset must never resurrect the old true value.
        del group.values[("Bool", "Enabled")]
        self.assertFalse(self.prefs.value("debug_log/enabled", False, type=bool))
        self.assertEqual(self.reads, [])

    def test_migrate_once_and_do_not_resurrect_removed_key(self):
        self.legacy["backgauge/enabled"] = True
        self.assertTrue(self.prefs.value("backgauge/enabled", False, type=bool))
        self.prefs.setValue("backgauge/enabled", False)
        self.assertFalse(self.prefs.value("backgauge/enabled", True, type=bool))
        group = self.groups[ROOT + "/Backgauge"]
        del group.values[("Bool", "Enabled")]
        self.assertFalse(self.prefs.value("backgauge/enabled", False, type=bool))
        self.assertEqual(self.reads, ["backgauge/enabled"])

    def test_list_round_trip(self):
        paths = ["A/part.json", "B/another part.json"]
        self.legacy["sequence_catalog/json_paths"] = paths
        self.assertEqual(self.prefs.value("sequence_catalog/json_paths", [], type=list), paths)
        self.prefs.setValue("sequence_catalog/json_paths", paths[:1])
        self.assertEqual(self.prefs.value("sequence_catalog/json_paths", [], type=list), paths[:1])
        self.assertEqual(self.legacy["sequence_catalog/json_paths"], paths)

    def test_float_and_json_string(self):
        self.legacy["operation_weights/flip"] = 3.5
        self.assertEqual(self.prefs.value("operation_weights/flip", 1.0, type=float), 3.5)
        self.prefs.setValue("pdf/options_v2", '{"large_first": true}')
        self.assertEqual(self.prefs.value("pdf/options_v2", "", type=str), '{"large_first": true}')

    def test_mesh_keys_match_native_designer_preference_page(self):
        self.legacy["mesh/budget_seconds"] = 95
        self.legacy["mesh/min_overlap_mm3"] = 0.002
        self.assertEqual(self.prefs.value("mesh/budget_seconds", 60, type=int), 95)
        self.assertEqual(self.prefs.value("mesh/min_overlap_mm3", 0.0001, type=float), 0.002)
        group = self.groups[ROOT + "/Mesh"]
        self.assertEqual(group.GetInt("BudgetSeconds", 0), 95)
        self.assertEqual(group.GetFloat("MinimumOverlapMM3", 0.0), 0.002)
