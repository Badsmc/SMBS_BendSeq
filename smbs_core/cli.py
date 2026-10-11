"""
cli.py - Headless Command-Line Interface for SMBS Core Bending Sequence Planner (ТЗ v1.2 §4.1).
Usage:
    smbs-cli <model_path> [--mode NATIVE_SHEETMETAL] [--algorithm astar] [--budget 5000] [--output sequence.json]
"""

from __future__ import annotations
import argparse
import json
import logging
import os
import shutil
import sys
from pathlib import Path

from smbs_core.coordinator.run_pipeline import Coordinator
from smbs_core.protocols.file_store import RunStatus

logger = logging.getLogger("smbs_cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SMBS Headless Sheet Metal Bending Sequence Planner")
    parser.add_argument("model", nargs="?", default=None, help="Path to STEP or FCStd model file")
    parser.add_argument("--input", "-i", dest="input_model", default=None, help="Alternative path to model file")
    parser.add_argument(
        "--mode",
        default="NATIVE_SHEETMETAL",
        choices=["NATIVE_SHEETMETAL", "NEUTRAL_BREP", "FEATURE_RECOGNITION"],
        help="Input geometry and feature provenance mode (§1.1)"
    )
    parser.add_argument("--part-id", default=None, help="Identifier for the part")
    parser.add_argument("--machine", default="press_100t", help="Machine configuration ID")
    parser.add_argument("--tooling-config", default=None, help="Path to tooling configuration JSON file")
    parser.add_argument("--algorithm", default="astar", choices=["astar", "dfs"], help="Planning algorithm")
    parser.add_argument("--output", default="sequence.json", help="Path to write output sequence JSON")
    parser.add_argument("--budget", type=int, default=5000, help="Node exploration limit budget")
    parser.add_argument("--timeout", type=float, default=60.0, help="Wall clock timeout in seconds")
    parser.add_argument("--k-factor", type=float, default=None, help="Explicit K-factor override")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    model_path = args.model or args.input_model
    if not model_path:
        print("Error: Model file path must be specified.", file=sys.stderr)
        sys.exit(2)

    model_file = Path(model_path)
    if not model_file.exists():
        print(f"Error: Model file not found: {model_path}", file=sys.stderr)
        sys.exit(2)

    part_id = args.part_id or model_file.stem

    try:
        coordinator = Coordinator()
        res = coordinator.start_run(
            source_file_path=model_file,
            part_id=part_id,
            input_mode=args.mode,
            k_factor_override=args.k_factor,
            search_algorithm=args.algorithm,
            resource_budget={
                "max_nodes": args.budget,
                "max_wall_time_s": args.timeout,
                "max_cache_entries": 10000,
                "max_geometry_ops": 20000,
                "max_trajectory_samples": 500,
                "max_memory_mb": 2048.0
            }
        )
    except Exception as e:
        print(f"Fatal error executing coordinator pipeline: {e}", file=sys.stderr)
        sys.exit(2)

    export_path = Path(res["export_file_path"])
    if export_path.exists():
        shutil.copyfile(export_path, args.output)

    status = res.get("status")
    planning_status = res.get("planning_status")
    verification_status = res.get("verification_status")
    eligible = res.get("production_eligible", False)

    print(
        f"Planning complete. Status: {status} (planning: {planning_status}, "
        f"verification: {verification_status}, eligible: {eligible}). "
        f"Output written to {args.output}"
    )

    if eligible and status == RunStatus.SUCCESS.value:
        sys.exit(0)
    elif status in (RunStatus.PARTIAL.value, RunStatus.TIMEOUT.value, RunStatus.RESOURCE_EXHAUSTED.value, RunStatus.NO_SOLUTION.value):
        sys.exit(1)
    else:
        sys.exit(2)


if __name__ == "__main__":
    main()
