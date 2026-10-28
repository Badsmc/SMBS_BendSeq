"""
step_reader.py - Validated STEP file loader for smbs_core.
Decoupled from FreeCAD.
"""

import os
from typing import Any
from smbs_core.geometry_backend.base import GeometryBackend

class StepReadError(ValueError):
    """Raised when loading or validating a STEP file fails."""
    pass

class StepReader:
    def __init__(self, backend: GeometryBackend):
        self.backend = backend

    def read_step(self, filepath: str) -> Any:
        """
        Reads and validates a STEP model from disk.
        Returns backend-native Shape.
        """
        if not os.path.exists(filepath):
            raise StepReadError(f"STEP file does not exist: {filepath}")

        try:
            shape = self.backend.load_shape(filepath)
        except Exception as e:
            raise StepReadError(f"Failed to read STEP file '{filepath}': {e}") from e

        if not self.backend.is_valid(shape):
            raise StepReadError(f"STEP file '{filepath}' loaded an invalid or null shape")

        return shape
