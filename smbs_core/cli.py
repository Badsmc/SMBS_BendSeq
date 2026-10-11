"""
cli.py - Headless Command-Line Interface for SMBS Core Bending Engine.
Usage:
    smbs-cli plan part.step --machine press_100t --tooling-config config.json --output sequence.json
"""

import sys
import os
import argparse
import json
import logging

from smbs_core.Core.orchestrator import BendSeqOrchestrator
from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend

logger = logging.getLogger("smbs_cli")

def main():
    parser = argparse.ArgumentParser(description="SMBS Headless Sheet Metal Bending Sequence Planner")
    parser.add_argument("model", help="Path to STEP or FCStd model file")
    parser.add_argument("--machine", default="press_100t", help="Machine configuration ID")
    parser.add_argument("--tooling-config", default=None, help="Path to tooling configuration JSON file")
    parser.add_argument("--algorithm", default="dfs", choices=["astar", "dfs", "greedy"], help="Planning algorithm")
    parser.add_argument("--output", default="sequence.json", help="Path to write output sequence JSON")
    parser.add_argument("--budget", type=int, default=5000, help="Node exploration limit budget")

    args = parser.parse_args()

    if not os.path.exists(args.model):
        print(f"Error: Model file not found: {args.model}", file=sys.stderr)
        sys.exit(2)

    try:
        backend = PythonOCCBackend()
    except Exception as e:
        print(f"Error initializing PythonOCCBackend: {e}", file=sys.stderr)
        sys.exit(2)

    orchestrator = BendSeqOrchestrator(backend=backend)
    
    punch_path = None
    die_path = None
    if args.tooling_config and os.path.exists(args.tooling_config):
        try:
            with open(args.tooling_config, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                punch_path = cfg.get("punch_path")
                die_path = cfg.get("die_path")
        except Exception as e:
            print(f"Warning: Failed to load tooling config: {e}", file=sys.stderr)

    result = orchestrator.run_planning(
        file_path=args.model,
        alg=args.algorithm,
        max_iterations=args.budget,
        punch_path=punch_path,
        die_path=die_path
    )

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    print(f"Planning complete. Status: {result.get('status')}. Output written to {args.output}")

    status = result.get("status")
    if status == "SUCCESS":
        sys.exit(0)
    elif status == "PARTIAL":
        sys.exit(1)
    else:
        sys.exit(2)

if __name__ == "__main__":
    main()
