"""
Export Worker for SMBS / BendSeq (ТЗ v1.2 §7.2, §7.3, Principle 21).
Enforces 5-point reverse validation checks before generating production part_bendseq.json v1.1.
Writes 10_part_bendseq.json and 11_run_report.json.
"""

from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from smbs_core.protocols.file_store import (
    ConfidenceLevel,
    ContractError,
    ExportStatus,
    FileStore,
    RunStatus,
    VerificationStatus,
)


class ExportWorker:
    """
    Export Worker enforcing §7.3 reverse verification checks.
    Only creates production_eligible part_bendseq.json if all checks pass.
    """

    def __init__(self, file_store: Optional[FileStore] = None):
        self.file_store = file_store or FileStore()

    def execute(
        self,
        search_result_path: Union[str, Path],
        verification_result_path: Union[str, Path],
        canonical_part_path: Union[str, Path]
    ) -> Tuple[Path, Path]:
        """
        Executes reverse verification and exports:
        10_part_bendseq.json + 11_run_report.json.
        """
        search_res = self.file_store.read_json_verified(search_result_path, expected_schema_name="search_result")
        verif_res = self.file_store.read_json_verified(verification_result_path, expected_schema_name="verification_result")
        part = self.file_store.read_json_verified(canonical_part_path, expected_schema_name="canonical_part")

        run_id = search_res["run_id"]
        part_id = search_res["part_id"]
        units = search_res.get("units", "mm")
        run_dir = Path(search_result_path).parent

        # Five Reverse Checks (§7.3):
        # 1. All bend_id match; count matches
        candidate_seq = search_res["payload"].get("candidate_sequence", [])
        canonical_bends = part["payload"]["bends"]
        seq_bends = [s["bend_id"] for s in candidate_seq]
        part_bends = list(canonical_bends.keys())

        check1_passed = (len(candidate_seq) == len(canonical_bends)) and (set(seq_bends) == set(part_bends))

        # 2. Step statuses valid
        check2_passed = all(
            s.get("planner_status") == "VALID" and s.get("collision_status") in ("VALID", "INSUFFICIENT_CLEARANCE")
            for s in candidate_seq
        )

        # 3. Topology not AMBIGUOUS
        check3_passed = part.get("status") == "SUCCESS"

        # 4. Independent verification passed
        check4_passed = (
            verif_res["payload"].get("verification_status") == "VERIFIED" and
            verif_res["payload"].get("production_eligible") is True
        )

        all_checks_passed = check1_passed and check2_passed and check3_passed and check4_passed

        # Format Export Sequence
        export_sequence = []
        for s in candidate_seq:
            step_obj = {
                "step": s["step"],
                "bend_id": s["bend_id"],
                "direction": s["direction"],
                "angle_deg": s["angle_deg"],
                "radius_mm": s["radius_mm"],
                "k_factor": s.get("k_factor", {"value": 0.44, "source": "DEFAULT_POLICY", "confidence": "ESTIMATED"}),
                "backgauge": s["backgauge"],
                "tool_id": s["tool_id"],
                "planner_status": s["planner_status"],
                "collision_status": s["collision_status"],
                "tooling_status": s["tooling_status"],
                "alignment_status": s["alignment_status"],
                "verification_status": "VERIFIED" if all_checks_passed else "VERIFICATION_FAILED"
            }
            export_sequence.append(step_obj)

        planning_status = search_res["payload"]["planning_status"]
        verif_status = verif_res["payload"]["verification_status"]
        export_status = ExportStatus.EXPORTED.value if all_checks_passed else ExportStatus.BLOCKED.value

        part_bendseq_payload = {
            "schema_name": "part_bendseq",
            "schema_version": "1.1",
            "run_id": run_id,
            "part_id": part_id,
            "units": units,
            "input_mode": part["payload"]["input_mode"],
            "planning_status": planning_status,
            "verification_status": verif_status,
            "export_status": export_status,
            "production_eligible": all_checks_passed,
            "sequence": export_sequence,
            "verification": verif_res["payload"]["independent_metrics"],
            "metadata": {
                "solver": "AStar",
                "termination_reason": search_res["payload"]["termination_reason"],
                "tolerance_profile": "standard_fine",
                "commit": "dfb842d",
                "backend": "pythonocc-core"
            },
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        # 5. Schema validation before writing (§7.3)
        out_filename = "10_part_bendseq.json" if all_checks_passed else "10_part_bendseq_candidate.json"
        out_file = run_dir / out_filename
        self.file_store.write_json_atomic(out_file, part_bendseq_payload, schema_name="part_bendseq")

        # 6. Generate 11_run_report.json
        report_payload = {
            "schema_name": "envelope",
            "schema_version": "1.1",
            "run_id": run_id,
            "part_id": part_id,
            "input_fingerprint": search_res["input_fingerprint"],
            "producer": {"name": "export_worker", "version": "1.1"},
            "units": units,
            "frame_convention": "STANDARD_CARTESIAN_MM",
            "status": RunStatus.SUCCESS.value if all_checks_passed else RunStatus.PARTIAL.value,
            "diagnostics": verif_res.get("diagnostics", []),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "reverse_checks": {
                    "all_bends_matched": check1_passed,
                    "step_statuses_valid": check2_passed,
                    "topology_unambiguous": check3_passed,
                    "independent_verification_passed": check4_passed
                },
                "production_eligible": all_checks_passed,
                "exported_file": out_filename,
                "solver_metrics": search_res["payload"]["solver_metrics"],
                "total_steps": len(export_sequence)
            }
        }
        report_file = run_dir / "11_run_report.json"
        self.file_store.write_json_atomic(report_file, report_payload, schema_name="envelope")

        return out_file, report_file
