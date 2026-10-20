"""S.26-fix: GeometryStatus REAL must serialize as uppercase 'REAL' in
07_search_result.json; previously silently downgraded to 'SIMPLIFIED' due to
case mismatch in worker.py:228-230, and raw 'real' from planner broke schema
at worker.py:278.

Root cause found via Commit 4A pre-check: Coordinator.start_run(real tooling)
raised ContractError "'real' is not one of ['REAL','SIMPLIFIED','MISSING']".
"""
import sys, os, unittest, tempfile, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.test_technological_unfold_single_bend as tt
from smbs_core.coordinator.run_pipeline import Coordinator
from OCC.Core.STEPControl import STEPControl_Writer, STEPControl_AsIs


class TestRealToolingSerialization(unittest.TestCase):
    def test_coordinator_real_tooling_no_contract_error(self):
        """Coordinator.start_run with real tooling must not raise ContractError
        on 07_search_result.json (tooling_status must be uppercase REAL)."""
        punch = str(ROOT / "toollibrary/punch/puancon.FCStd")
        die = str(ROOT / "toollibrary/die/matrica.FCStd")

        td = tempfile.mkdtemp()
        shape = tt.create_l_bracket_shape(90, 1.0, 1.5, 50.0, 95.5, 47.5)
        step = Path(td) / "fixture_l.step"
        w = STEPControl_Writer()
        w.Transfer(shape, STEPControl_AsIs)
        w.Write(str(step))

        coord = Coordinator(workspace_root=td)
        res = coord.start_run(
            source_file_path=step,
            part_id="fixture_l",
            input_mode="NATIVE_SHEETMETAL",
            k_factor_override=0.44,
            machine_config={
                "punch_path": punch, "die_path": die,
                "punch_target_object_name": "Body",
                "die_target_object_name": "Body",
            },
            unknown_policy="EXPLORATORY",
        )

        run_dir = Path(res["run_dir"])
        sr = run_dir / "07_search_result.json"
        self.assertTrue(sr.exists(), "07_search_result.json must be produced")

        data = json.loads(sr.read_text(encoding="utf-8"))
        seq = data["payload"]["candidate_sequence"]
        self.assertGreater(len(seq), 0, "candidate_sequence must be non-empty")

        for step_item in seq:
            status = step_item.get("tooling_status")
            self.assertEqual(
                status, "REAL",
                f"Real tooling must serialize as 'REAL' (uppercase), got "
                f"{status!r} — regression of S.26 silent downgrade to "
                f"'SIMPLIFIED' or schema-breaking lowercase 'real'"
            )

        # Honest state: MACHINE UNKNOWN (KL-16, no machine_frame) → not VERIFIED
        self.assertNotEqual(
            res.get("verification_status"), "VERIFIED",
            "machine_frame absent (KL-16): verifier must not certify"
        )
        self.assertFalse(res.get("production_eligible"))
