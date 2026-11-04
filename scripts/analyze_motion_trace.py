#!/usr/bin/env python3
"""Convenience CLI script for automated motion trace analysis (Stage D4-P2)."""
import os
import sys
from pathlib import Path

# Ensure repo root is in sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from smbs_core.kinematics2d.trace_analyzer import main

if __name__ == "__main__":
    main()

