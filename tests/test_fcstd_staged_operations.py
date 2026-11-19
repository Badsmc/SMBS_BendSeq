"""FCStd persistence regression for operation-level pre-bend snapshots."""
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from freecad_smbs.integration import fcstd_session_store as store
from test_staged_bends import controller


@dataclass
class _Config:
    bend_rate_deg_s: float = 90.0


class StagedFcstdTests(unittest.TestCase):
    def setUp(self):
        self.c = controller()
        self.c.add_prebend("B01", 35)
        self.c.reorder_operations(("B01.1", "B02", "B03", "B01.2"))
        self.session = SimpleNamespace(
            bend_order=self.c.order,
            canonical_order=self.c.canonical_order,
            controller=self.c,
        )
        self.tooling = SimpleNamespace(
            punch=None, die=None, match_active_bend_length=False,
            tool_assembly=None, allow_end_zone_collision=False,
            end_zone_length_mm=0.0,
        )
        self.geometry = {"data": {"demo": 1}}
        self.geometry["sha256"] = store._sha(store._json_bytes(self.geometry["data"]))

    def _snapshot(self):
        with patch.object(store, "geometry_manifest", return_value=self.geometry):
            return store.build_snapshot(
                self.session, object(), self.tooling, _Config(), "FULL_CYCLE"
            )

    def test_operations_survive_json_roundtrip(self):
        payload = store.decode_snapshot(store._json_bytes(self._snapshot()))
        self.assertEqual(payload["order"]["operation_steps"], self.c.operation_snapshot())
        restored = controller()
        restored.restore_operation_snapshot(payload["order"]["operation_steps"])
        self.assertEqual(restored.operation_snapshot(), self.c.operation_snapshot())
        self.assertEqual(restored.operation_start_deg(3), 35.0)

    def test_independent_stage_rotations_survive_fcstd(self):
        with patch.object(store, "geometry_manifest", return_value=self.geometry):
            snapshot = store.build_snapshot(
                self.session, object(), self.tooling, _Config(), "FULL_CYCLE",
                manual_orientation_overrides={"B02": True},
                operation_orientation_overrides={
                    "B01.1": True, "B01.2": False,
                },
            )
        decoded = store.decode_snapshot(store._json_bytes(snapshot))
        self.assertEqual(
            decoded["order"]["operation_orientation_overrides"],
            {"B01.1": True, "B01.2": False},
        )
        self.assertEqual(
            decoded["order"]["manual_orientation_overrides"],
            {"B02": True},
        )

    def test_invalid_stage_rotation_keys_and_types_fail_closed(self):
        for entry in ({"B99.1": True}, {"B01.1": 180}, {"B02": True}):
            with self.subTest(entry=entry):
                with patch.object(store, "geometry_manifest", return_value=self.geometry):
                    with self.assertRaises(store.EmbeddedSessionError):
                        store.build_snapshot(
                            self.session, object(), self.tooling, _Config(), "FULL_CYCLE",
                            operation_orientation_overrides=entry,
                        )
                payload = self._snapshot()
                payload["order"]["operation_orientation_overrides"] = entry
                with self.assertRaises(store.EmbeddedSessionError):
                    store.decode_snapshot(store._json_bytes(payload))

    def test_legacy_fcstd_without_stage_orientation_field_still_loads(self):
        payload = self._snapshot()
        del payload["order"]["operation_orientation_overrides"]
        decoded = store.decode_snapshot(store._json_bytes(payload))
        self.assertEqual(
            decoded["order"].get("operation_orientation_overrides", {}), {}
        )

    def test_invalid_stage_angles_are_rejected_on_restore(self):
        payload = self._snapshot()
        payload["order"]["operation_steps"][0]["target_angle_deg"] = 0
        restored = store.decode_snapshot(store._json_bytes(payload))
        with self.assertRaises(Exception):
            controller().restore_operation_snapshot(restored["order"]["operation_steps"])

    def test_nonfinite_angles_are_rejected_before_restore(self):
        payload = self._snapshot()
        payload["order"]["operation_steps"][0]["target_angle_deg"] = float("nan")
        with self.assertRaises(store.EmbeddedSessionError):
            store.decode_snapshot(store._json_bytes(payload))


if __name__ == "__main__":
    unittest.main()
