"""
test_fcstd_fixtures.py - Integration test suite for 16 FreeCAD native fixtures.
Verifies:
1. Manifest exists and is valid according to schema §15.3
2. All three artifacts exist (part_3d.brep, flat_pattern.brep, bends.json)
3. Source source.FCStd exists
4. Content hashes (SHA-256) match exactly
5. bends_count == len(ground_truth.bend_sequence)
6. All negative fixtures have production_eligible == False and negative_reason set
7. 2-1-3-4-(ok) has operator confirmed sequence and requires_gooseneck == True
8. U-10-15-10-(punch2-OK) has requires_gooseneck == True
"""

import hashlib
import json
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class TestFreeCADNativeFixtures(unittest.TestCase):

    def setUp(self):
        self.fixtures_dir = REPO_ROOT / "fixtures" / "freecad"
        self.registry_path = REPO_ROOT / "fixtures" / "registry.json"
        self.assertTrue(self.fixtures_dir.exists(), "fixtures/freecad directory must exist")
        self.assertTrue(self.registry_path.exists(), "fixtures/registry.json must exist")

    def test_registry_contains_all_16_fixtures(self):
        with open(self.registry_path, "r", encoding="utf-8") as f:
            reg = json.load(f)
        fc_fixtures = [f for f in reg.get("fixtures", []) if f.get("class") == "FREECAD_NATIVE"]
        self.assertEqual(len(fc_fixtures), 16, f"Registry must contain 16 FREECAD_NATIVE fixtures, got {len(fc_fixtures)}")

    def test_validate_all_16_manifests_and_hashes(self):
        with open(self.registry_path, "r", encoding="utf-8") as f:
            reg = json.load(f)
        fc_fixtures = [f for f in reg.get("fixtures", []) if f.get("class") == "FREECAD_NATIVE"]

        for item in fc_fixtures:
            manifest_rel = item["manifest"]
            manifest_path = REPO_ROOT / manifest_rel
            self.assertTrue(manifest_path.exists(), f"Manifest missing: {manifest_path}")

            with open(manifest_path, "r", encoding="utf-8") as mf:
                manifest = json.load(mf)

            fixture_id = manifest["fixture_id"]
            fixture_dir = manifest_path.parent

            # Check source FCStd
            source_info = manifest["source"]
            src_fcstd_path = REPO_ROOT / source_info["path"]
            self.assertTrue(src_fcstd_path.exists(), f"source.FCStd missing for {fixture_id}")
            actual_fcstd_hash = compute_sha256(src_fcstd_path)
            self.assertEqual(
                source_info["content_hash"],
                f"sha256:{actual_fcstd_hash}",
                f"FCStd hash mismatch for {fixture_id}",
            )

            # Check input files
            roles_found = set()
            for inp in manifest["input_files"]:
                role = inp["role"]
                roles_found.add(role)
                artifact_path = REPO_ROOT / inp["path"]
                self.assertTrue(artifact_path.exists(), f"{role} file missing: {artifact_path}")
                actual_hash = compute_sha256(artifact_path)
                self.assertEqual(
                    inp["content_hash"],
                    f"sha256:{actual_hash}",
                    f"Hash mismatch for {role} in {fixture_id}",
                )

            self.assertTrue({"PART_3D", "FLAT_PATTERN", "BENDS_METADATA"}.issubset(roles_found))

            # Ground truth consistency
            gt = manifest["ground_truth"]
            bends_seq = gt["bend_sequence"]
            self.assertEqual(
                gt["bends_count"],
                len(bends_seq),
                f"bends_count mismatch in ground_truth for {fixture_id}",
            )

            # Verify bends.json matches ground truth
            bends_json_path = fixture_dir / "bends.json"
            with open(bends_json_path, "r", encoding="utf-8") as bj:
                bends_obj = json.load(bj)
            self.assertEqual(len(bends_obj["bends"]), gt["bends_count"])

            # Verify negative fixtures
            if gt["expected_status"] == "NEGATIVE":
                self.assertFalse(gt["production_eligible"], f"{fixture_id} is NEGATIVE but production_eligible is True")
                self.assertIsNotNone(gt["negative_reason"], f"{fixture_id} is NEGATIVE but negative_reason is None")

            # Specific tests
            if fixture_id == "2_1_3_4_ok":
                self.assertTrue(gt["requires_gooseneck"])
                self.assertTrue(gt["bend_sequence_operator_confirmed"])
                # First 4 bends check
                b_ids = [b["bend_id"] for b in bends_seq[:4]]
                self.assertEqual(b_ids, ["BEND_02", "BEND_01", "BEND_03", "BEND_04"])
                self.assertEqual(bends_seq[0]["angle_deg"], -45.0)
                self.assertEqual(bends_seq[1]["angle_deg"], 45.0)
                self.assertEqual(bends_seq[2]["angle_deg"], 90.0)
                self.assertEqual(bends_seq[3]["angle_deg"], 90.0)
                self.assertTrue(bends_seq[2]["requires_gooseneck"])
                self.assertTrue(bends_seq[3]["requires_gooseneck"])

            if fixture_id == "u_10_15_10_punch2_ok":
                self.assertTrue(gt["requires_gooseneck"])

            if fixture_id == "135_ok":
                self.assertEqual(gt["bends_count"], 2)


if __name__ == "__main__":
    unittest.main()
