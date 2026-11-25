import sys
from pathlib import Path

# Resolve repository root from symlink/install location so FreeCAD GUI/CLI finds smbs_core
_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from smbs_core import __version__ as __version__
