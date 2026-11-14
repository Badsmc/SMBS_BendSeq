import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smbs_core.kinematics2d.parallel_manual_check import check_sequence_parallel
from smbs_core.kinematics2d.step_result_cache import (
    compute_step_cache_key,
    step_cache_get,
    step_cache_put,
)


class TestStageDCacheAndParallel(unittest.TestCase):
    def test_cache_key_order_independent_and_code_sha_sensitive(self):
        base = dict(
            part_sha256="p1",
            punch_sha256="pu1",
            die_sha256="di1",
            config_dict={"arc_segments": 128},
            bend_id="B05",
            reverse_axis=True,
            allow_end_zone_collision=True,
            end_zone_length_mm=20.0,
            match_active_bend_length=True,
            code_sha256="code_v1",
        )
        k1 = compute_step_cache_key(completed_bends=["B07", "B03"], **base)
        k2 = compute_step_cache_key(completed_bends=["B03", "B07"], **base)
        self.assertEqual(k1, k2)

        k3 = compute_step_cache_key(
            completed_bends=["B03", "B07"],
            **{**base, "code_sha256": "code_v2"},
        )
        self.assertNotEqual(k1, k3)

    def test_cache_put_get_rejects_unverified_and_updates_step_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            self.assertFalse(
                step_cache_put("k_unv", {"status": "UNVERIFIED"}, cache_dir=cache_dir)
            )
            self.assertTrue(
                step_cache_put(
                    "k_ok",
                    {
                        "status": "SAMPLED_3D_CLEAR_NOT_CONTINUOUS",
                        "bend_id": "B05",
                        "step_number": 99,
                        "completed_before_step": ["X"],
                    },
                    cache_dir=cache_dir,
                )
            )
            got = step_cache_get(
                "k_ok",
                step_number=3,
                completed_before_step=["B07", "B03"],
                cache_dir=cache_dir,
            )
            self.assertIsNotNone(got)
            self.assertEqual(got["step_number"], 3)
            self.assertEqual(got["completed_before_step"], ["B07", "B03"])
            self.assertTrue(got["cache_hit"])

    def test_parallel_longest_first_and_stop_on_first_blocker(self):
        seen_order = []

        def fake_run(_exe, job, env=None):
            seen_order.append(job["step_number"])
            st = "COLLISION" if job["bend_id"] == "B02" else "SAMPLED_3D_CLEAR_NOT_CONTINUOUS"
            return {
                "step_number": job["step_number"],
                "bend_id": job["bend_id"],
                "status": st,
                "completed_before_step": job["done"],
            }

        with patch("smbs_core.kinematics2d.parallel_manual_check._run_worker", side_effect=fake_run):
            res = check_sequence_parallel(
                "python",
                {"part_path": "a.FCStd", "punch_path": "p.FCStd", "die_path": "d.FCStd"},
                ["B07", "B02", "B05"],
                workers=1,
            )
        # Longest-first: step 3, 2, 1
        self.assertEqual(seen_order, [3, 2, 1])
        self.assertEqual(res["status"], "COLLISION")
        self.assertEqual(res["reason"], "punch_or_die_collision_at:B02")
        self.assertEqual(res["checked_bends"], 2)
        self.assertTrue(res["stopped_early"])


if __name__ == "__main__":
    unittest.main()

