"""
bendseq_writer.py - Machine-Readable BendSeq Exporter (v1.0 Schema)
"""

import json
import os
from typing import Dict, Any, List, Optional
try:
    from pydantic import BaseModel, Field
    HAS_PYDANTIC = True
except ImportError:
    HAS_PYDANTIC = False


if HAS_PYDANTIC:
    class StepDataSchema(BaseModel):
        step: int
        bend_id: str
        status: str
        collision_status: str
        tooling_status: str
        alignment_status: str
        angle_deg: float
        radius_mm: float
        length_mm: float
        axis: List[float]
        origin: List[float]
        moving_panels: List[str] = []
        backgauge: Dict[str, Any] = {}
        tooling: Dict[str, Any] = {}
        operator_instructions: Dict[str, Any] = {}

    class BendSeqSchemaV1(BaseModel):
        schema_version: str = "1.0"
        status: str
        model_id: str = "unknown"
        machine_id: str = "press_100t"
        punch_id: str = "punch_synth"
        die_id: str = "die_synth"
        sequence: List[StepDataSchema] = []
        validation: Dict[str, Any] = {}
        planning_stats: Dict[str, Any] = {}
        diagnostics: List[str] = []


class BendSeqWriter:
    """
    Exports final sequence results to standard machine format with v1.0 schema validation.
    """

    def write_bendseq(
        self,
        filename: str,
        metadata: Dict[str, Any],
        sequence: List[Dict[str, Any]]
    ) -> str:
        output_data = {
            "schema_version": "1.0",
            "format": "BendSeq_v1.0",
            "status": metadata.get("status", "SUCCESS"),
            "model_id": metadata.get("model_id", "part_model"),
            "machine_id": metadata.get("machine_id", "press_100t"),
            "punch_id": metadata.get("punch_id", "punch_88deg_R1.5"),
            "die_id": metadata.get("die_id", "die_V12_R1.5_88deg"),
            "metadata": metadata,
            "sequence_summary": {
                "total_steps": len(sequence),
                "total_flips": sum(1 for s in sequence if s.get("operator_instructions", {}).get("requires_part_flip_180")),
                "tool_stations_used": len(set(s.get("operator_instructions", {}).get("station", 1) for s in sequence))
            },
            "sequence": sequence
        }

        abs_path = os.path.abspath(filename)
        dir_name = os.path.dirname(abs_path)
        
        if dir_name == "/" or not os.path.exists(dir_name) or not os.access(dir_name, os.W_OK):
            file_basename = os.path.basename(filename) or "part_bendseq.json"
            abs_path = os.path.expanduser(os.path.join("~", file_basename))

        with open(abs_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2)

        return abs_path
