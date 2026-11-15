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

    def snapshot(self, saved_sequences=()):
        with patch.object(store, "geometry_manifest", return_value=self.manifest):
            return store.build_snapshot(
                session=self.session, document=object(), tooling=self.tooling,
                cycle_config=_Cycle(), execution_mode="BEND_ONLY",
                saved_sequences=saved_sequences,
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

    def test_old_v1_snapshot_without_candidates_still_loads(self):
        snap = self.snapshot()
        del snap["order"]["found_sequences"]
        restored = store.decode_snapshot(store._json_bytes(snap))
        self.assertNotIn("found_sequences", restored["order"])

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
