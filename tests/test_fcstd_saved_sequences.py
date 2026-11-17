"""Headless FCStd JSON compatibility and saved 2D candidate tests."""
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

from freecad_smbs.integration import fcstd_session_store as store


@dataclass
class _Cycle:
    bend_rate_deg_s: float = 10.0


class EmbeddedCandidatesTests(unittest.TestCase):
    def setUp(self):
        data = {"test_geometry": "same-part"}
        self.manifest = {
            "data": data,
            "sha256": store._sha(store._json_bytes(data)),
        }
        self.session = SimpleNamespace(
            bend_order=("B02", "B01"),
            canonical_order=("B01", "B02"),
        )
        self.tooling = SimpleNamespace(
            punch=None, die=None, match_active_bend_length=False,
            allow_end_zone_collision=False, end_zone_length_mm=0.0,
        )

    def snapshot(self, saved_sequences=(), *, manual_orientation_overrides=None):
        with patch.object(store, "geometry_manifest", return_value=self.manifest):
            return store.build_snapshot(
                session=self.session, document=object(), tooling=self.tooling,
                cycle_config=_Cycle(), execution_mode="BEND_ONLY",
                saved_sequences=saved_sequences,
                manual_orientation_overrides=manual_orientation_overrides,
            )

    def test_candidates_roundtrip_with_selected_order(self):
        found = [{
            "bend_ids": ["B01", "B02"],
            "orientation_by_bend": {"B01": False, "B02": True},
            "origin": "2D_SEARCH", "evidence": "UNVERIFIED",
        }]
        snap = self.snapshot(found)
        restored = store.decode_snapshot(store._json_bytes(snap))
        self.assertEqual(restored["order"]["bend_ids"], ["B02", "B01"])
        self.assertEqual(restored["order"]["found_sequences"], found)
        self.assertEqual(restored["validation"]["on_restore"],
                         "UNVERIFIED_RECHECK_REQUIRED")

    def test_old_v1_snapshot_without_candidates_or_overrides_still_loads(self):
        snap = self.snapshot()
        del snap["order"]["found_sequences"]
        del snap["order"]["manual_orientation_overrides"]
        restored = store.decode_snapshot(store._json_bytes(snap))
        self.assertNotIn("found_sequences", restored["order"])
        self.assertEqual(restored["order"].get("manual_orientation_overrides", {}), {})

    def test_manual_checked_and_unchecked_choices_roundtrip(self):
        overrides = {"B01": True, "B02": False}
        snap = self.snapshot(manual_orientation_overrides=overrides)
        restored = store.decode_snapshot(store._json_bytes(snap))
        self.assertEqual(restored["order"]["manual_orientation_overrides"], overrides)

    def test_invalid_manual_choices_are_rejected_on_save_and_read(self):
        for invalid in ({"OTHER": True}, {"B01": 180}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(store.EmbeddedSessionError):
                    self.snapshot(manual_orientation_overrides=invalid)
                snap = self.snapshot()
                snap["order"]["manual_orientation_overrides"] = invalid
                with self.assertRaises(store.EmbeddedSessionError):
                    store.decode_snapshot(store._json_bytes(snap))

    def test_restore_accepts_legacy_brep_hash_and_rejects_changed_geometry(self):
        snap = self.snapshot()
        changed_manifest = {"sha256": "changed"}
        with patch.object(store, "geometry_manifest", side_effect=[
            changed_manifest, self.manifest,
        ]):
            self.assertEqual(store.validate_restore(snap, self.session, object()),
                             (self.session.bend_order, ()))
        with patch.object(store, "geometry_manifest", return_value=changed_manifest):
            with self.assertRaisesRegex(store.EmbeddedSessionError, "STALE_GEOMETRY"):
                store.validate_restore(snap, self.session, object())

    def test_tool_reference_depends_on_geometry_and_skips_import_timings(self):
        data = {"tip_radius_mm": 0.8, "metrics": {"import_time_ms": 1.0}}
        profile = SimpleNamespace(tool_id="punch", name="Punch", to_dict=lambda: data)
        reference = store.tool_reference(profile)
        data["metrics"]["import_time_ms"] = 5.0
        self.assertEqual(store.tool_reference(profile), reference)
        data["tip_radius_mm"] = 1.0
        self.assertNotEqual(store.tool_reference(profile), reference)

    def test_foreign_candidate_rejected_before_save(self):
        with self.assertRaises(store.EmbeddedSessionError):
            self.snapshot([{"bend_ids": ["B01", "OTHER"]}])

    def test_corrupt_candidate_rejected_on_read(self):
        snap = self.snapshot()
        snap["order"]["found_sequences"] = [
            {"bend_ids": ["B01", "B01"], "orientation_by_bend": {}}
        ]
        with self.assertRaises(store.EmbeddedSessionError):
            store.decode_snapshot(store._json_bytes(snap))


if __name__ == "__main__":
    unittest.main()
