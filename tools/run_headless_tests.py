"""Run unittest with process-isolated journals, never the operator's Desktop.

Usage: python -m tools.run_headless_tests discover -s tests -p 'test_*.py'
Or pass unittest module names and options as usual. Native OCC acceptance
must be run separately in a genuine FreeCAD interpreter.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    previous = os.environ.get("SMBS_DEBUG_LOG_DIR")
    with tempfile.TemporaryDirectory(prefix="smbs-tests-") as directory:
        os.environ["SMBS_DEBUG_LOG_DIR"] = directory
        from smbs_core.kinematics2d import developer_debug_log as debug
        previous_journal = debug._JOURNAL
        debug._JOURNAL = None
        try:
            program = unittest.main(
                module=None, argv=["smbs-tests", *(args or ["discover", "-s", "tests"])],
                exit=False,
            )
            return 0 if program.result.wasSuccessful() else 1
        finally:
            debug._JOURNAL = previous_journal
            if previous is None:
                os.environ.pop("SMBS_DEBUG_LOG_DIR", None)
            else:
                os.environ["SMBS_DEBUG_LOG_DIR"] = previous


if __name__ == "__main__":
    raise SystemExit(main())
