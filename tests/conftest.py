import sys
from pathlib import Path

# Add project root and smbs_core to sys.path
root_dir = Path(__file__).parent.parent
smbs_core_dir = root_dir / "smbs_core"

if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))
if str(smbs_core_dir) not in sys.path:
    sys.path.insert(0, str(smbs_core_dir))
