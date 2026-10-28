"""
tooling_loader.py - Standalone Tooling Loader using data/tooling database.
"""

import os
from typing import Optional, List, Dict, Any
from smbs_core.models.tooling import Tooling, ToolingKind, GeometryStatus
from smbs_core.geometry_backend.backends.fcstd_loader import FcstdToolingLoader

DEFAULT_TOOLING_DIRS = [
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "tooling")),
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "toollibrary")),
]

class ToolingLoader:
    def __init__(self, search_dirs: Optional[List[str]] = None):
        self.search_dirs = search_dirs or DEFAULT_TOOLING_DIRS
        self.loader = FcstdToolingLoader()

    def find_tooling_file(self, tool_id: str, extension: str = ".FCStd") -> Optional[str]:
        target_name = f"{tool_id}{extension}" if not tool_id.endswith(extension) else tool_id

        for root_dir in self.search_dirs:
            if not os.path.exists(root_dir):
                continue
            for root, _, files in os.walk(root_dir):
                if target_name in files:
                    return os.path.join(root, target_name)
        return None

    def load_tooling(self, tool_id: str, kind: ToolingKind) -> Tooling:
        fcstd_path = self.find_tooling_file(tool_id, ".FCStd")

        if not fcstd_path:
            # Check if JSON sidecar exists alone
            json_path = self.find_tooling_file(tool_id, ".tooling.json")
            tool = Tooling(
                id=tool_id,
                kind=kind,
                source_file=json_path or "",
                shape=None,
                local_frame=None,
                geometry_status=GeometryStatus.MISSING,
                datums={},
                metadata={},
                diagnostics=[f"FCStd model for {tool_id} not found in search paths."]
            )
            if json_path:
                self.loader._load_sidecar(json_path, tool)
            return tool

        return self.loader.load(fcstd_path, kind)
