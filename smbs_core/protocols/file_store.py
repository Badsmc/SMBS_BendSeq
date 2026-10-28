"""
Protocols and File Store for SMBS / BendSeq.
Compliant with Architectural Principles 2, 4, 7, 8, 12, 13, 21, 23, 24 and Sections 4, 7 of technical specification v1.2.
"""

from __future__ import annotations
import enum
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union
import jsonschema


class RunStatus(str, enum.Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    NO_SOLUTION = "NO_SOLUTION"
    TIMEOUT = "TIMEOUT"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    INVALID_INPUT = "INVALID_INPUT"
    UNKNOWN = "UNKNOWN"
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    CONTRACT_ERROR = "CONTRACT_ERROR"
    WORKER_ERROR = "WORKER_ERROR"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


class TransitionStatus(str, enum.Enum):
    VALID = "VALID"
    INVALID = "INVALID"
    INSUFFICIENT_CLEARANCE = "INSUFFICIENT_CLEARANCE"
    UNKNOWN = "UNKNOWN"
    ERROR = "ERROR"


class TopologyStatus(str, enum.Enum):
    MATCHED = "MATCHED"
    AMBIGUOUS = "AMBIGUOUS"
    FAILED = "FAILED"


class ConfidenceLevel(str, enum.Enum):
    CONFIRMED = "CONFIRMED"
    ESTIMATED = "ESTIMATED"
    UNKNOWN = "UNKNOWN"


class ParameterSource(str, enum.Enum):
    SHEETMETAL_NATIVE = "SHEETMETAL_NATIVE"
    GEOMETRIC_INFERENCE = "GEOMETRIC_INFERENCE"
    USER_INPUT = "USER_INPUT"
    CONFIGURATION = "CONFIGURATION"
    DEFAULT_POLICY = "DEFAULT_POLICY"
    UNKNOWN = "UNKNOWN"


class ToolingStatus(str, enum.Enum):
    REAL = "REAL"
    SIMPLIFIED = "SIMPLIFIED"
    MISSING = "MISSING"


class AlignmentStatus(str, enum.Enum):
    OK = "OK"
    CANNOT_VERIFY = "CANNOT_VERIFY"
    FRAME_AMBIGUOUS = "FRAME_AMBIGUOUS"


class VerificationStatus(str, enum.Enum):
    VERIFIED = "VERIFIED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    NOT_RUN = "NOT_RUN"


class ExportStatus(str, enum.Enum):
    EXPORTED = "EXPORTED"
    BLOCKED = "BLOCKED"
    NOT_RUN = "NOT_RUN"


class ContractError(Exception):
    """Raised when data violates schema, missing required provenance, or content hash mismatch."""
    pass


def canonicalize_json_dict(data: Dict[str, Any], exclude_content_hash: bool = True) -> str:
    """
    Serializes a dictionary to canonical JSON string for SHA-256 hashing.
    Keys are sorted, separators are compact (',', ':'), UTF-8 encoded without extra spaces.
    Excludes the 'content_hash' key itself if present.
    """
    to_hash = dict(data)
    if exclude_content_hash and "content_hash" in to_hash:
        to_hash.pop("content_hash", None)

    return json.dumps(
        to_hash,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False
    )


def compute_json_content_hash(data: Dict[str, Any]) -> str:
    """Computes SHA-256 of canonical JSON representation."""
    canonical_str = canonicalize_json_dict(data, exclude_content_hash=True)
    return hashlib.sha256(canonical_str.encode('utf-8')).hexdigest()


def compute_file_sha256(file_path: Union[str, Path]) -> str:
    """Computes SHA-256 for binary files (e.g. BREP, STEP, FCStd)."""
    h = hashlib.sha256()
    with open(file_path, 'rb') as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class FileStore:
    """
    Atomic, versioned, checksum-verifying file store for inter-worker contracts.
    Implements §4.2, §4.5 of technical specification v1.2.
    """

    def __init__(self, schemas_dir: Optional[Union[str, Path]] = None):
        if schemas_dir is None:
            self.schemas_dir = Path(__file__).resolve().parent.parent / "contracts" / "schemas"
        else:
            self.schemas_dir = Path(schemas_dir)
        self._schema_cache: Dict[str, Dict[str, Any]] = {}

    def get_schema(self, schema_name: str) -> Dict[str, Any]:
        """Loads and caches JSON Schema by schema_name."""
        if schema_name in self._schema_cache:
            return self._schema_cache[schema_name]

        schema_file = self.schemas_dir / f"{schema_name}.schema.json"
        if not schema_file.exists():
            raise ContractError(f"Schema file not found for schema '{schema_name}': {schema_file}")

        with open(schema_file, 'r', encoding='utf-8') as f:
            schema = json.load(f)
        self._schema_cache[schema_name] = schema
        return schema

    def validate(self, data: Dict[str, Any], schema_name: Optional[str] = None) -> None:
        """Validates dictionary against schema_name (or data['schema_name'])."""
        name = schema_name or data.get("schema_name")
        if not name:
            raise ContractError("Document does not define 'schema_name' and none was provided.")

        schema = self.get_schema(name)
        try:
            jsonschema.validate(instance=data, schema=schema)
        except jsonschema.ValidationError as e:
            raise ContractError(f"Contract validation error for schema '{name}': {e.message} at path {list(e.path)}")

    def write_json_atomic(
        self,
        file_path: Union[str, Path],
        data: Dict[str, Any],
        schema_name: Optional[str] = None,
        validate: bool = True
    ) -> str:
        """
        Calculates SHA-256 hash, validates against schema, writes to *.tmp and atomically renames.
        Returns the computed content_hash.
        """
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        payload_to_write = dict(data)
        # Compute SHA-256 canonical hash
        content_hash = compute_json_content_hash(payload_to_write)
        payload_to_write["content_hash"] = content_hash

        if validate:
            self.validate(payload_to_write, schema_name)

        # Atomic write pattern: *.tmp -> replace
        nonce = uuid.uuid4().hex[:8]
        tmp_path = path.parent / f"{path.name}.{nonce}.tmp"

        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                f.write(canonicalize_json_dict(payload_to_write, exclude_content_hash=False))
                f.flush()
                os.fsync(f.fileno())

            # Atomic rename
            os.replace(tmp_path, path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

        return content_hash

    def read_json_verified(
        self,
        file_path: Union[str, Path],
        expected_schema_name: Optional[str] = None,
        validate: bool = True
    ) -> Dict[str, Any]:
        """
        Reads JSON file, checks content_hash integrity, validates against JSON Schema.
        Raises ContractError on tampering, corruption, or schema violation.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Contract file not found: {path}")

        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ContractError(f"Expected JSON object in {path}, got {type(data).__name__}")

        declared_hash = data.get("content_hash")
        if not declared_hash:
            raise ContractError(f"File {path} is missing mandatory 'content_hash'.")

        computed_hash = compute_json_content_hash(data)
        if declared_hash != computed_hash:
            raise ContractError(
                f"Integrity check failed for {path}: declared {declared_hash} != computed {computed_hash}"
            )

        if validate:
            self.validate(data, expected_schema_name)

        return data
