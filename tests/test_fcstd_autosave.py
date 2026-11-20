"""Headless regression tests: native FCStd project persistence and auto-open."""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from freecad_smbs.integration import fcstd_session_store as store
from freecad_smbs.commands.pressbrake_2d import saved_session_unfold_selection
from smbs_core.kinematics2d.backgauge_2d import (
    BackgaugeModel2D, BackgaugePosition2D,
)
from smbs_core.geometry_backend.io.clamp_validator import ClampConfig


@dataclass
class Cycle:
    bend_rate_deg_s: float = 75.0


class ProjectSettingsTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {"data": {"geometry": "part"}}
        self.manifest["sha256"] = store._sha(
            store._json_bytes(self.manifest["data"])
        )
        self.session = SimpleNamespace(
            bend_order=("B01", "B02"), canonical_order=("B01", "B02")
        )
        self.tooling = SimpleNamespace(
            punch=None, die=None, tool_assembly=None,
            match_active_bend_length=False, allow_end_zone_collision=False,
            end_zone_length_mm=0,
        )
        self.backgauge = BackgaugeModel2D(
            width_s_mm=10.0, height_z_mm=15.0,
            positions={"B02": BackgaugePosition2D(33.0, 2.5)},
            part_label="example part", automatic=False,
            cad_geometry_enabled=True, cad_station_xm_mm=75,
            cad_second_station_xm_mm=-75,
            initial_feed_preview_mm=20,
        )
        self.clamps = ClampConfig.from_defaults((50.0, 200.0))

    def snapshot(self, backgauge=None, clamps=None):
        with patch.object(store, "geometry_manifest", return_value=self.manifest):
            return store.build_snapshot(
                self.session, object(), self.tooling, Cycle(),
                "FULL_CYCLE",
                backgauge_model=backgauge,
                clamp_config=clamps,
            )

    def test_backgauge_and_clamps_roundtrip_in_fcstd(self):
        payload = store.decode_snapshot(store._json_bytes(
            self.snapshot(self.backgauge, self.clamps)
        ))
        self.assertEqual(
            BackgaugeModel2D.from_dict(payload["tooling"]["backgauge"]),
            self.backgauge,
        )
        self.assertEqual(
            ClampConfig.from_dict(payload["tooling"]["clamps"]),
            self.clamps,
        )

    def test_missing_legacy_settings_accepted(self):
        payload = self.snapshot()
        del payload["tooling"]["backgauge"]
        del payload["tooling"]["clamps"]
        self.assertIsNotNone(store.decode_snapshot(store._json_bytes(payload)))

    def test_explicit_disabled_backgauge_persists(self):
        payload = store.decode_snapshot(store._json_bytes(self.snapshot()))
        self.assertIsNone(payload["tooling"]["backgauge"])

    def test_corrupt_backgauge_rejected_before_restore(self):
        payload = self.snapshot(self.backgauge, self.clamps)
        payload["tooling"]["backgauge"]["positions"]["OTHER"] = {
            "s_mm": 33, "z_mm": 2.5
        }
        with self.assertRaises(store.EmbeddedSessionError):
            store.decode_snapshot(store._json_bytes(payload))

    def test_corrupt_clamps_rejected_before_restore(self):
        payload = self.snapshot(self.backgauge, self.clamps)
        payload["tooling"]["clamps"]["clamps"]["width_mm"] = -5
        with self.assertRaises(store.EmbeddedSessionError):
            store.decode_snapshot(store._json_bytes(payload))


class SavedUnfoldSelectionTests(unittest.TestCase):
    def test_auto_reopens_exact_saved_unfold(self):
        unfold = SimpleNamespace(Name="Unfold001_Unfold", baseObject=None)
        foreign = SimpleNamespace(Name="Other_Unfold", baseObject=None)
        doc = SimpleNamespace(
            getObject=lambda name: {
                "SMBS_Data": object(), "Unfold001_Unfold": unfold,
                "Other_Unfold": foreign,
            }.get(name)
        )
        payload = {"geometry": {"data": {"unfold_object": unfold.Name}}}
        with patch(
            "freecad_smbs.integration.fcstd_session_store.read_included_json",
            return_value=payload,
        ):
            self.assertEqual(saved_session_unfold_selection(doc), (unfold,))

    def test_no_attachment_does_not_guess_unfold(self):
        doc = SimpleNamespace(getObject=lambda name: None)
        self.assertEqual(saved_session_unfold_selection(doc), ())

    def test_missing_source_does_not_guess_another_unfold(self):
        doc = SimpleNamespace(
            getObject=lambda name: {
                "SMBS_Data": object(),
                "Other_Unfold": SimpleNamespace(Name="Other_Unfold", baseObject=None),
            }.get(name)
        )
        payload = {"geometry": {"data": {"unfold_object": "Deleted_Unfold"}}}
        with patch(
            "freecad_smbs.integration.fcstd_session_store.read_included_json",
            return_value=payload,
        ):
            self.assertEqual(saved_session_unfold_selection(doc), ())


if __name__ == "__main__":
    unittest.main()
