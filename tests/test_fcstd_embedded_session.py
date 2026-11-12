"""Headless contract tests for SMBS JSON embedded in FreeCAD FCStd.

Mocks only the FreeCAD property-copy boundary; real FreeCAD save/reopen
acceptance is deliberately listed as an additional required integration test.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from types import SimpleNamespace

from freecad_smbs.integration.fcstd_session_store import (
    ATTACHMENT_NAME, OBJECT_NAME, SCHEMA_VERSION, EmbeddedSessionError,
    build_snapshot, decode_snapshot, geometry_manifest, read_included_json,
    validate_restore, write_included_json,
)
from smbs_core.kinematics2d.press_cycle import CycleConfig


@dataclass
class _Bend:
    bend_id: str = "B01"
    fold_angle_deg: float = 90.0
    radius_mm: float = 1.5
    inner_radius_mm: float = 1.5
    thickness_mm: float = 1.5
    bend_allowance_mm: float = 3.0
    length_mm: float = 100.0
    start_xyz: tuple = (0.0, 0.0, 0.0)
    end_xyz: tuple = (100.0, 0.0, 0.0)


class _FakeShape:
    def __init__(self, brep):
        self.brep = brep

    def exportBrepToString(self):
        return self.brep


class _FakeIncludedProperty:
    def __init__(self, directory):
        object.__setattr__(self, "_directory", Path(directory))
        object.__setattr__(self, "_types", {})
        object.__setattr__(self, "_properties", [])

    @property
    def PropertiesList(self):
        return list(self._properties)

    def addProperty(self, kind, name, section, description):
        self._types[name] = kind
        self._properties.append(name)

    def getTypeIdOfProperty(self, name):
        return self._types[name]

    def __setattr__(self, name, value):
        if name == "SessionJSON":
            stored = self._directory / ATTACHMENT_NAME
            shutil.copyfile(str(value), str(stored))
            object.__setattr__(self, name, str(stored))
        else:
            object.__setattr__(self, name, value)


class _FakeDoc:
    Name = "Part"

    def __init__(self, directory):
        self.directory = directory
        self.objects = {
            "Bend001": SimpleNamespace(Shape=_FakeShape("native BREP v1")),
            "Body_Unfold": SimpleNamespace(),
        }

    def getObject(self, name):
        return self.objects.get(name)

    def addObject(self, kind, name):
        assert kind == "App::FeaturePython"
        obj = _FakeIncludedProperty(self.directory)
        obj.Name = name
        obj.Label = name
        obj.ViewObject = SimpleNamespace(Visibility=True)
        self.objects[name] = obj
        return obj


class _FakeTool:
    def __init__(self, name, tool_id):
        self.name = name
        self.tool_id = tool_id

    def to_dict(self):
        return {"id": self.tool_id, "name": self.name, "radius": 0.8}


def _state():
    session = SimpleNamespace(
        unfold_data=SimpleNamespace(
            source_object="Bend001", source_face="Face4",
            unfold_object="Body_Unfold", k_factor_standard="ansi",
            thickness_mm=1.5, bends=(_Bend(), _Bend(
                bend_id="B02", fold_angle_deg=-45,
                start_xyz=(0.0, 20.0, 0.0),
                end_xyz=(100.0, 20.0, 0.0),
            )),
        ),
        canonical_order=("B01", "B02"),
        bend_order=("B02", "B01"),
        panel_vertices_xy={
            "P0": ((0.0, 0.0), (100.0, 0.0), (100.0, 40.0)),
            "P1": ((100.0, 0.0), (120.0, 0.0), (120.0, 40.0)),
        },
    )
    tools = SimpleNamespace(
        punch=_FakeTool("PUNCH", "P1"), die=_FakeTool("DIE", "D1"),
        match_active_bend_length=False,
        allow_end_zone_collision=False, end_zone_length_mm=10.0,
    )
    cfg = CycleConfig()
    return session, tools, cfg


class EmbeddedStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.document = _FakeDoc(self.tmp.name)
        self.session, self.tooling, self.config = _state()

    def _snapshot(self):
        return build_snapshot(
            self.session, self.document, self.tooling, self.config,
            "BEND_ONLY", manual_prefix=("B02",),
            manual_report={
                "status": "CONDITIONAL",
                "accepted_prefix": ["B02"],
                "first_blocker": None,
            }, operation_weights={"flip": 3.0}, retract_warning=True,
        )

    def test_round_trip_stores_actual_json_file_not_string(self):
        payload = self._snapshot()
        obj = write_included_json(self.document, payload)
        self.assertEqual(obj.Name, OBJECT_NAME)
        self.assertEqual(obj.getTypeIdOfProperty("SessionJSON"),
                         "App::PropertyFileIncluded")
        self.assertTrue(Path(obj.SessionJSON).is_file())
        self.assertEqual(obj.SchemaVersion, SCHEMA_VERSION)
        loaded = read_included_json(self.document)
        self.assertEqual(loaded["order"]["bend_ids"], ["B02", "B01"])
        self.assertEqual(loaded["order"]["manual_prefix"], ["B02"])
        self.assertEqual(loaded["simulation"]["execution_mode"], "BEND_ONLY")
        self.assertEqual(loaded["tooling"]["punch"]["id"], "P1")
        self.assertEqual(loaded["diagnostics"]["manual"]["status"], "CONDITIONAL")
        self.assertEqual(loaded["validation"]["on_restore"],
                         "UNVERIFIED_RECHECK_REQUIRED")
        self.assertEqual(validate_restore(loaded, self.session, self.document),
                         (("B02", "B01"), ("B02",)))

    def test_overwrite_updates_included_copy_and_checksum(self):
        first = self._snapshot()
        write_included_json(self.document, first)
        saved_before = self.document.getObject(OBJECT_NAME).PayloadSHA256
        self.session.bend_order = ("B01", "B02")
        second = build_snapshot(
            self.session, self.document, self.tooling, self.config,
            "FULL_CYCLE", manual_prefix=(),
        )
        write_included_json(self.document, second)
        obj = self.document.getObject(OBJECT_NAME)
        self.assertNotEqual(obj.PayloadSHA256, saved_before)
        self.assertEqual(read_included_json(self.document)["order"]["bend_ids"],
                         ["B01", "B02"])
        self.assertEqual(sum(key == OBJECT_NAME for key in self.document.objects),
                         1)

    def test_changed_source_shape_rejects_restore_without_mutating_sequence(self):
        payload = self._snapshot()
        write_included_json(self.document, payload)
        saved = read_included_json(self.document)
        self.document.getObject("Bend001").Shape.brep = "different geometry"
        with self.assertRaisesRegex(EmbeddedSessionError, "STALE_GEOMETRY"):
            validate_restore(saved, self.session, self.document)
        self.assertEqual(self.session.bend_order, ("B02", "B01"))

    def test_changed_bend_radius_rejects_restore(self):
        payload = self._snapshot()
        write_included_json(self.document, payload)
        saved = read_included_json(self.document)
        self.session.unfold_data.bends[0].radius_mm = 1.7
        with self.assertRaisesRegex(EmbeddedSessionError, "STALE_GEOMETRY"):
            validate_restore(saved, self.session, self.document)

    def test_payload_corruption_is_detected(self):
        write_included_json(self.document, self._snapshot())
        obj = self.document.getObject(OBJECT_NAME)
        Path(obj.SessionJSON).write_text('{"corrupted": true}', encoding="utf-8")
        with self.assertRaisesRegex(EmbeddedSessionError, "checksum mismatch"):
            read_included_json(self.document)

    def test_invalid_schema_and_manual_prefix_are_rejected(self):
        payload = self._snapshot()
        payload["schema_version"] = 999
        with self.assertRaisesRegex(EmbeddedSessionError, "Unsupported"):
            decode_snapshot(json.dumps(payload).encode("utf-8"))
        payload = self._snapshot()
        payload["order"]["manual_prefix"] = ["B01"]
        with self.assertRaisesRegex(EmbeddedSessionError, "prefix"):
            decode_snapshot(json.dumps(payload).encode("utf-8"))

    def test_missing_data_object_is_not_an_error(self):
        self.assertIsNone(read_included_json(self.document))


if __name__ == "__main__":
    unittest.main()
